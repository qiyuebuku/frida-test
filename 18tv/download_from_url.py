#!/usr/bin/env python3
"""一键取证流水线（自包含）：URL → HTML → m3u8 → MP4

用法:
    python3 download_from_url.py <页面URL>
    python3 download_from_url.py <页面URL> -o out.mp4 --check --keep-html

默认输出: ./video/<id>.mp4（id 从 URL 末段提取）
默认工作目录: ./video/（HTML + 报告也存这里）

流程:
  [1/3] 抓取页面 HTML
  [2/3] 提取 m3u8 地址（支持 5 种常见注入模式）
  [3/3] 下载视频（AES-128 解密 + 多线程 + ffmpeg remux）
"""

import os
import re
import sys
import json
import shutil
import time
import hashlib
import tempfile
import argparse
import threading
import concurrent.futures
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse

# 清理代理（WSL2）
for _k in list(os.environ.keys()):
    if _k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(_k, None)

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# AES-128 解密（可选，仅加密流需要）
try:
    from Crypto.Cipher import AES
    _HAS_AES = True
except ImportError:
    _HAS_AES = False


# ============== 通用 ==============

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 完整 Chrome 桌面端请求头（含 sec-ch-ua / Sec-Fetch-*）
HEADERS = {
    "User-Agent": UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8,"
               "application/signed-exchange;v=b3;q=0.7"),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
    "Priority": "u=0, i",
}

# 分片下载用普通 requests session（CDN 一般不查 TLS 指纹）
_session = requests.Session()
_retries = Retry(total=5, backoff_factor=0.5,
                 status_forcelist=[429, 500, 502, 503, 504])
_session.mount("https://", HTTPAdapter(max_retries=_retries))
_session.mount("http://", HTTPAdapter(max_retries=_retries))
_session.headers.update(HEADERS)

# 优先用 curl_cffi：模拟 Chrome 完整 TLS+HTTP2 指纹，绕过 Cloudflare JA3 检测
try:
    from curl_cffi import requests as cc_requests
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

_key_cache = {}
_key_lock = threading.Lock()


def fmt_size(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def fmt_time(sec: float) -> str:
    sec = int(sec)
    h, sec = divmod(sec, 3600)
    m, sec = divmod(sec, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def log(msg: str):
    print(msg, file=sys.stderr)


def extract_id(url: str) -> str:
    """从 URL 末段提取 ID（如 https://18j.tv/v/2164/ -> 2164）"""
    name = Path(urlparse(url).path).name
    if not name:
        name = urlparse(url).netloc.replace(".", "_")
    return name.split("?", 1)[0] or "video"


# ============== Step 1: 抓取 HTML ==============

def fetch_page(url: str, timeout: int = 30, referer: str = None,
               proxy: str = None):
    """抓 HTML，优先用 curl_cffi 绕 Cloudflare TLS 指纹检测，自带重试"""
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    proxies = {"http": proxy, "https": proxy} if proxy else None

    last_err = None
    for attempt in range(3):
        try:
            if _HAS_CURL_CFFI:
                return cc_requests.get(
                    url, headers=headers, timeout=timeout,
                    impersonate="chrome131", allow_redirects=True,
                    proxies=proxies,
                )
            return _session.get(url, headers=headers,
                                timeout=timeout, allow_redirects=True,
                                proxies=proxies)
        except Exception as e:
            last_err = e
            log(f"[!] 抓取失败 (尝试 {attempt + 1}/3): {type(e).__name__}: {e}")
            time.sleep(0.8 * (attempt + 1))
    raise last_err


def step_fetch(url: str, work_dir: Path, vid: str, proxy: str = None) -> dict:
    log("=" * 64)
    log(f"[1/3] 抓取页面: {url}")
    if proxy:
        log(f"      通过代理: {proxy}")
    if _HAS_CURL_CFFI:
        log(f"      使用 curl_cffi chrome131 指纹")
    log("=" * 64)

    resp = fetch_page(url, proxy=proxy)
    if resp.status_code != 200:
        log(f"[!] 非 200 响应（{resp.status_code}），停止")
        sys.exit(2)

    enc = resp.encoding
    if enc is None or enc.lower() == "iso-8859-1":
        enc = getattr(resp, "apparent_encoding", None) or "utf-8"

    work_dir.mkdir(parents=True, exist_ok=True)
    html_path = work_dir / f"{vid}.html"
    html_path.write_bytes(resp.content)

    sha256 = hashlib.sha256(resp.content).hexdigest()
    md5 = hashlib.md5(resp.content).hexdigest()
    text = resp.content.decode(enc, errors="ignore")

    log(f"      HTTP {resp.status_code}  {len(resp.content)} bytes  enc={enc}")
    log(f"      最终 URL: {resp.url}")
    log(f"      保存: {html_path}")
    log(f"      sha256: {sha256[:16]}...  md5: {md5}")

    return {
        "request_url": url,
        "final_url": resp.url,
        "status_code": resp.status_code,
        "content_length": len(resp.content),
        "encoding": enc,
        "sha256": sha256,
        "md5": md5,
        "saved_to": str(html_path),
        "html_path": html_path,
        "html_text": text,
    }


# ============== Step 2: 提取 m3u8 ==============

PATTERNS = [
    ("js_var_source",
     re.compile(
         r"""(?:const|let|var)\s+(?:source|file|url|src|video|hlsUrl|playUrl|videoUrl)\s*=\s*['"]([^'"]+\.m3u8[^'"]*)['"]""",
         re.IGNORECASE)),
    ("hls_loadSource",
     re.compile(
         r"""(?:loadSource|attach(?:Hls|Source))\s*\(\s*['"]([^'"]+\.m3u8[^'"]*)['"]""",
         re.IGNORECASE)),
    ("js_object_prop",
     re.compile(
         r"""(?:source|file|url|src|video|hlsUrl|playUrl|videoUrl)\s*:\s*['"]([^'"]+\.m3u8[^'"]*)['"]""",
         re.IGNORECASE)),
    ("html_source_tag",
     re.compile(
         r"""<source[^>]+src=["']([^"']+\.m3u8[^"']*)["']""",
         re.IGNORECASE)),
    ("raw_url",
     re.compile(
         r"""https?://[^\s'"<>)\\]+\.m3u8[^\s'"<>)\\]*""",
         re.IGNORECASE)),
]


def extract_m3u8(text: str):
    findings = []
    seen = set()
    for kind, pat in PATTERNS:
        for m in pat.finditer(text):
            url = m.group(1) if m.groups() else m.group(0)
            url = url.rstrip(".,;)]}")
            if not url or url in seen:
                continue
            seen.add(url)
            line = text.count("\n", 0, m.start()) + 1
            ctx_start = max(0, m.start() - 40)
            ctx_end = min(len(text), m.end() + 40)
            ctx = re.sub(r"\s+", " ", text[ctx_start:ctx_end]).strip()
            findings.append({
                "url": url, "pattern": kind, "line": line, "context": ctx,
            })
    return findings


def check_url_live(url: str, timeout: int = 6) -> dict:
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True,
                          headers={"User-Agent": UA})
        return {
            "live": True,
            "status": r.status_code,
            "content_type": r.headers.get("Content-Type", ""),
            "content_length": r.headers.get("Content-Length", ""),
            "final_url": r.url,
        }
    except Exception as e:
        return {"live": False, "error": f"{type(e).__name__}: {e}"}


def step_extract(html_text: str, do_check: bool) -> dict:
    log("")
    log("=" * 64)
    log("[2/3] 提取 m3u8 地址")
    log("=" * 64)

    findings = extract_m3u8(html_text)
    if not findings:
        log("[!] 未找到 m3u8 地址，停止")
        return {"findings": [], "selected": None}

    log(f"      找到 {len(findings)} 个候选:")
    for f in findings:
        log(f"        [{f['pattern']}] L{f['line']}: {f['url']}")

    selected = findings[0]
    m3u8_url = selected["url"]

    if do_check:
        log("\n      HEAD 检查存活 ...")
        chk = check_url_live(m3u8_url)
        selected["check"] = chk
        if chk.get("live"):
            log(f"        HTTP {chk['status']}  ✓ 存活")
        else:
            log(f"        ✗ 不可访问: {chk.get('error', '?')}")
            log("        （仍会尝试下载，可能只是 HEAD 不被允许）")

    log(f"\n      选择下载: {m3u8_url}")
    return {"findings": findings, "selected": m3u8_url}


# ============== Step 3: 下载 m3u8 ==============

def parse_attrs(attr_str: str) -> dict:
    out = {}
    buf, in_q = "", False
    for c in attr_str:
        if c == '"':
            in_q = not in_q
            buf += c
        elif c == "," and not in_q:
            k, _, v = buf.partition("=")
            if k:
                out[k.strip()] = v.strip().strip('"')
            buf = ""
        else:
            buf += c
    if buf:
        k, _, v = buf.partition("=")
        if k:
            out[k.strip()] = v.strip().strip('"')
    return out


def parse_iv(iv_str: str) -> bytes:
    if iv_str.startswith(("0x", "0X")):
        iv_str = iv_str[2:]
    iv = bytes.fromhex(iv_str)
    return iv.ljust(16, b"\x00") if len(iv) < 16 else iv[:16]


def parse_m3u8_playlist(text: str, base_url: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise ValueError("不是合法的 m3u8 文件")

    result = {
        "is_master": False, "variants": [],
        "segments": [], "init_segment": None,
    }
    current_key = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXT-X-STREAM-INF"):
            result["is_master"] = True
            attrs = parse_attrs(line[len("#EXT-X-STREAM-INF:"):])
            bw = int(attrs.get("BANDWIDTH", "0") or "0")
            res = attrs.get("RESOLUTION", "")
            if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                vurl = urljoin(base_url, lines[i + 1])
                result["variants"].append((bw, res, vurl))
            i += 2
            continue
        if line.startswith("#EXT-X-KEY"):
            attrs = parse_attrs(line[len("#EXT-X-KEY:"):])
            method = attrs.get("METHOD", "NONE")
            if method == "NONE":
                current_key = None
            else:
                key_uri = attrs.get("URI", "")
                if key_uri:
                    key_uri = urljoin(base_url, key_uri)
                iv_str = attrs.get("IV")
                current_key = {
                    "method": method, "uri": key_uri,
                    "iv": parse_iv(iv_str) if iv_str else None,
                }
        elif line.startswith("#EXT-X-MAP"):
            attrs = parse_attrs(line[len("#EXT-X-MAP:"):])
            init_uri = attrs.get("URI", "")
            if init_uri:
                result["init_segment"] = urljoin(base_url, init_uri)
        elif not line.startswith("#"):
            seg_url = urljoin(base_url, line)
            result["segments"].append({"url": seg_url, "key": current_key})
        i += 1
    return result


def select_best_variant(variants):
    return sorted(variants, key=lambda v: v[0], reverse=True)[0][2]


def decrypt_segment(data: bytes, key_info: dict) -> bytes:
    if not key_info:
        return data
    method = key_info["method"].upper()
    if method == "AES-128":
        if not _HAS_AES:
            raise RuntimeError("需要 pycryptodome 才能解密 AES-128")
        key_url = key_info["uri"]
        with _key_lock:
            if key_url not in _key_cache:
                _key_cache[key_url] = _session.get(key_url, timeout=30).content
        key = _key_cache[key_url]
        iv = key_info["iv"] or b"\x00" * 16
        cipher = AES.new(key, AES.MODE_CBC, iv)
        plain = cipher.decrypt(data)
        if plain:
            pad = plain[-1]
            if 1 <= pad <= 16 and plain[-pad:] == bytes([pad]) * pad:
                plain = plain[:-pad]
        return plain
    return data


def download_one(seg_url: str, save_path: Path,
                 key_info: dict = None, retries: int = 6) -> bool:
    if save_path.exists() and save_path.stat().st_size > 0:
        return True
    last_err = None
    for attempt in range(retries):
        try:
            r = _session.get(seg_url, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            data = r.content
            if key_info:
                data = decrypt_segment(data, key_info)
            save_path.write_bytes(data)
            return True
        except Exception as e:
            last_err = e
            time.sleep(0.4 * (attempt + 1))
    print(f"\n[失败] {save_path.name}: {last_err}", file=sys.stderr)
    return False


def download_m3u8(url: str, output: str, workers: int = 16):
    out_path = Path(output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log("[1/5] 获取 m3u8 ...")
    text = _session.get(url, timeout=30).text
    parsed = parse_m3u8_playlist(text, url)

    if parsed["is_master"]:
        vs = parsed["variants"]
        log(f"      发现 {len(vs)} 个画质，选最高")
        text = _session.get(select_best_variant(vs), timeout=30).text
        parsed = parse_m3u8_playlist(text, select_best_variant(vs))

    segs = parsed["segments"]
    if not segs:
        raise RuntimeError("m3u8 中没有分片")

    encrypted = sum(1 for s in segs if s["key"])
    if encrypted:
        log(f"      加密分片: {encrypted}/{len(segs)}（AES-128）")
    log(f"      共 {len(segs)} 个分片，并发 {workers}")

    work_dir = Path(tempfile.mkdtemp(prefix="m3u8_"))
    log(f"[2/5] 工作目录: {work_dir}")

    init_path = None
    if parsed["init_segment"]:
        init_path = work_dir / "init.mp4"
        log("      下载 init segment ...")
        download_one(parsed["init_segment"], init_path)

    log("[3/5] 下载分片 ...")
    total = len(segs)
    done = [0]
    bytes_counter = [0]
    lock = threading.Lock()
    start_ts = time.time()

    def task(idx_seg):
        idx, seg = idx_seg
        seg_path = work_dir / f"seg_{idx:05d}.ts"
        ok = download_one(seg["url"], seg_path, seg["key"])
        with lock:
            done[0] += 1
            if ok and seg_path.exists():
                bytes_counter[0] += seg_path.stat().st_size
            cur = done[0]
            tot_b = bytes_counter[0]
            el = time.time() - start_ts
            speed = tot_b / el if el > 0 else 0
            eta = (total - cur) * (el / cur) if cur > 0 else 0
            pct = cur * 100 // total
            sys.stdout.write(
                f"\r      [{cur}/{total}] {pct:3d}%  "
                f"{fmt_size(tot_b)}  {fmt_size(speed)}/s  ETA {fmt_time(eta)}  "
            )
            sys.stdout.flush()
        return idx, ok

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(task, list(enumerate(segs))))
    print()

    failed = [idx for idx, ok in results if not ok]
    if failed:
        log(f"[警告] {len(failed)} 个分片失败: {failed[:5]}{'...' if len(failed)>5 else ''}")

    pieces = []
    if init_path and init_path.exists():
        pieces.append(init_path)
    for idx, ok in results:
        if ok:
            p = work_dir / f"seg_{idx:05d}.ts"
            if p.exists():
                pieces.append(p)
    if not pieces:
        raise RuntimeError("没有可用分片")

    log(f"[4/5] 合并 {len(pieces)} 个分片 ...")
    merged_ts = work_dir / "merged.ts"
    with open(merged_ts, "wb") as fout:
        for p in pieces:
            with open(p, "rb") as fin:
                shutil.copyfileobj(fin, fout, length=1024 * 1024)
    log(f"      合并完成: {fmt_size(merged_ts.stat().st_size)}")

    log("[5/5] 输出文件 ...")
    ext = out_path.suffix.lower()
    ffmpeg = shutil.which("ffmpeg")

    if ext == ".mp4" and ffmpeg:
        cmd = (f'"{ffmpeg}" -y -hide_banner -loglevel error '
               f'-i "{merged_ts}" -c copy -bsf:a aac_adtstoasc "{out_path}"')
        log("      ffmpeg remux -> MP4")
        rc = os.system(cmd)
        if rc != 0 or not out_path.exists():
            log(f"      [警告] ffmpeg 失败 (rc={rc})，回退为 .ts")
            out_path = out_path.with_suffix(".ts")
            shutil.copy2(merged_ts, out_path)
    else:
        if ext == ".mp4" and not ffmpeg:
            log("      未找到 ffmpeg，输出 .ts")
            out_path = out_path.with_suffix(".ts")
        shutil.copy2(merged_ts, out_path)

    shutil.rmtree(work_dir, ignore_errors=True)
    return out_path


def step_download(m3u8_url: str, output: str, workers: int) -> dict:
    log("")
    log("=" * 64)
    log("[3/3] 下载视频")
    log("=" * 64)
    result = download_m3u8(m3u8_url, output, workers)
    return {"m3u8_url": m3u8_url, "output": str(result),
            "size": result.stat().st_size}


# ============== 流水线 ==============

def save_report(report: dict, work_dir: Path, vid: str):
    p = work_dir / f"{vid}_report.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def run(url: str, output: str, work_dir: Path,
        keep_html: bool, do_check: bool, workers: int,
        proxy: str = None, from_html: str = None):
    vid = extract_id(url)
    work_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "tool": "download_from_url",
        "version": "2.1",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "input_url": url,
        "video_id": vid,
        "steps": {},
    }

    if from_html:
        # 离线模式：直接读取本地 HTML
        html_file = Path(from_html)
        if not html_file.exists():
            log(f"[!] HTML 文件不存在: {html_file}")
            sys.exit(2)
        content = html_file.read_bytes()
        log("=" * 64)
        log(f"[1/3] 读取本地 HTML: {html_file}  ({len(content)} bytes)")
        log("=" * 64)
        sha256 = hashlib.sha256(content).hexdigest()
        md5 = hashlib.md5(content).hexdigest()
        # 复制到工作目录（保留取证链）
        html_path = work_dir / f"{vid}.html"
        if html_file.resolve() != html_path.resolve():
            html_path.write_bytes(content)
        html_text = content.decode("utf-8", errors="ignore")
        f1 = {
            "request_url": url,
            "final_url": url,
            "status_code": 0,
            "content_length": len(content),
            "encoding": "utf-8",
            "sha256": sha256,
            "md5": md5,
            "saved_to": str(html_path),
            "html_path": html_path,
            "html_text": html_text,
            "from_html": str(html_file),
        }
    else:
        f1 = step_fetch(url, work_dir, vid, proxy=proxy)
    report["steps"]["fetch"] = {k: v for k, v in f1.items()
                                if k not in ("html_path", "html_text")}

    f2 = step_extract(f1["html_text"], do_check)
    report["steps"]["extract"] = f2
    if not f2["selected"]:
        save_report(report, work_dir, vid)
        sys.exit(3)

    try:
        f3 = step_download(f2["selected"], output, workers)
    except Exception as e:
        log(f"[!] 下载失败: {type(e).__name__}: {e}")
        report["steps"]["download"] = {"error": str(e)}
        save_report(report, work_dir, vid)
        sys.exit(4)
    report["steps"]["download"] = f3

    if not keep_html:
        try:
            f1["html_path"].unlink()
            log(f"\n      已清理临时 HTML: {f1['html_path'].name}")
        except OSError:
            pass
    else:
        log(f"\n      保留 HTML: {f1['html_path']}")

    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    report["output"] = f3["output"]
    report["output_size"] = f3["size"]
    save_report(report, work_dir, vid)

    log("")
    log("=" * 64)
    log(f"✓ 完成: {f3['output']}  ({fmt_size(f3['size'])})")
    log(f"  报告: {work_dir / f'{vid}_report.json'}")
    log("=" * 64)
    return f3["output"]


def main():
    ap = argparse.ArgumentParser(
        description="一键取证流水线：URL → HTML → m3u8 → MP4",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("url", help="视频页面 URL")
    ap.add_argument("-o", "--output",
                    help="输出 MP4（默认 <work_dir>/<id>.mp4）")
    ap.add_argument("-d", "--work-dir", default="./video",
                    help="工作目录（HTML + 报告 + 默认输出）")
    ap.add_argument("-w", "--workers", type=int, default=16, help="下载并发")
    ap.add_argument("--check", action="store_true",
                    help="下载前 HEAD 检查 m3u8 存活")
    ap.add_argument("--keep-html", action="store_true",
                    help="保留抓取的 HTML（默认清理）")
    ap.add_argument("--proxy",
                    help="HTTP/HTTPS 代理（如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080）")
    ap.add_argument("--from-html",
                    help="跳过抓取，直接用本地 HTML 文件做提取+下载")
    args = ap.parse_args()

    work_dir = Path(args.work_dir)
    vid = extract_id(args.url)
    output = args.output if args.output else str(work_dir / f"{vid}.mp4")

    run(args.url, output, work_dir,
        keep_html=args.keep_html, do_check=args.check, workers=args.workers,
        proxy=args.proxy, from_html=args.from_html)


if __name__ == "__main__":
    main()
