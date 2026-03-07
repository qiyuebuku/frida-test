#!/usr/bin/env python3
"""
微信消息实时监控客户端

通过 ADB forward 的 TCP 端口与手机端 WXHook RPC 通信。

用法:
    python wxmonitor.py ping                          # 测试连接
    python wxmonitor.py contacts                      # 列出联系人
    python wxmonitor.py contacts --filter "西柚"       # 搜索联系人
    python wxmonitor.py conversations                  # 列出会话
    python wxmonitor.py history "我想要两颗西柚"        # 查看历史消息
    python wxmonitor.py history "我想要两颗西柚" -n 100 # 查看最近100条
    python wxmonitor.py monitor "我想要两颗西柚"        # 监控指定联系人
    python wxmonitor.py monitor                        # 监控所有消息
    python wxmonitor.py save "我想要两颗西柚"           # 导出全部对话+媒体
    python wxmonitor.py save "我想要两颗西柚" -n 100    # 导出最近100条
    python wxmonitor.py save "我想要两颗西柚" -o ./out/ # 指定输出目录
"""

import argparse
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


class WxRPC:
    """WXHook RPC 客户端"""

    RPC_HOST = "127.0.0.1"
    RPC_PORT = 9900
    ADB_CMD = ["/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe", "-s", "3B15BJ00GZL00000"]
    PKG = "com.tencent.mm"

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def _rpc(self, cmd: str, timeout: int = 0, **kwargs) -> dict:
        """发送 RPC 命令并接收响应"""
        request = {"cmd": cmd, **kwargs}
        request_bytes = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
        effective_timeout = timeout or self.timeout

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(effective_timeout)
        try:
            sock.connect((self.RPC_HOST, self.RPC_PORT))
            sock.sendall(request_bytes)

            buf = b""
            deadline = time.time() + effective_timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError("RPC timeout")
                sock.settimeout(min(remaining, 5))
                try:
                    chunk = sock.recv(4194304)  # 4MB buffer for media
                    if not chunk:
                        break
                    buf += chunk
                    if b"\n" in buf:
                        break
                except socket.timeout:
                    continue

            line = buf.split(b"\n")[0].decode("utf-8", errors="replace").strip()
            if not line:
                raise Exception("Empty response")
            return json.loads(line)
        finally:
            sock.close()

    def setup_forward(self):
        """设置 ADB 端口转发"""
        try:
            subprocess.run(
                self.ADB_CMD + ["forward", f"tcp:{self.RPC_PORT}", f"localabstract:wxhook_rpc"],
                check=True, capture_output=True, timeout=5
            )
            pprint(f"ADB forward 已设置: tcp:{self.RPC_PORT} -> localabstract:wxhook_rpc")
        except Exception as e:
            pprint(f"ADB forward 设置失败: {e}")
            pprint("请手动执行: adb forward tcp:9900 localabstract:wxhook_rpc")

    def ping(self) -> dict:
        return self._rpc("ping")

    def get_contacts(self, filter_: str = "", real_only: bool = True) -> list:
        r = self._rpc("get_contacts", filter=filter_, real_only=real_only)
        if r.get("success"):
            return r["data"]
        raise Exception(r.get("error", "Unknown error"))

    def get_conversations(self, limit: int = 50) -> list:
        r = self._rpc("get_conversations", limit=limit)
        if r.get("success"):
            return r["data"]
        raise Exception(r.get("error", "Unknown error"))

    def get_history(self, talker: str = "", limit: int = 50, before_id: int = 0) -> list:
        kwargs = {"talker": talker, "limit": limit}
        if before_id > 0:
            kwargs["before_id"] = before_id
        r = self._rpc("get_history", **kwargs)
        if r.get("success"):
            return r["data"]
        raise Exception(r.get("error", "Unknown error"))

    def get_new_messages(self, after_id: int = 0, talker: str = "") -> dict:
        r = self._rpc("get_new_messages", after_id=after_id, talker=talker)
        if r.get("success"):
            return r["data"]
        raise Exception(r.get("error", "Unknown error"))

    def resolve_media(self, msg_id: int) -> dict:
        r = self._rpc("resolve_media", msgId=msg_id, timeout=30)
        if r.get("success"):
            return r
        raise Exception(r.get("error", "Unknown error"))

    def get_media(self, path: str) -> dict:
        r = self._rpc("get_media", path=path, timeout=60)
        if r.get("success"):
            return r
        raise Exception(r.get("error", "Unknown error"))

    def get_all_history(self, talker: str, limit_per_page: int = 500) -> list:
        """分页拉取全部消息"""
        all_messages = []
        before_id = 2**63 - 1  # Long.MAX_VALUE
        while True:
            batch = self.get_history(talker=talker, limit=limit_per_page, before_id=before_id)
            if not batch:
                break
            all_messages.extend(batch)
            # batch 是 DESC 排序，最后一条是最小 msgId
            before_id = min(m["msgId"] for m in batch)
        # 反转为时间正序
        all_messages.reverse()
        return all_messages


def pprint(*args, **kwargs):
    """Print with flush"""
    print(*args, **kwargs, flush=True)


def format_message(msg: dict) -> str:
    """格式化消息用于显示"""
    time_str = msg.get("time", "")
    name = msg.get("talkerName", msg.get("talker", "?"))
    is_send = msg.get("isSend", 0)
    content = msg.get("content", "")
    type_name = msg.get("typeName", "")

    direction = "➡️ 我" if is_send == 1 else f"⬅️ {name}"

    if type_name == "text":
        body = content
    elif type_name == "image":
        body = "[图片]"
    elif type_name == "voice":
        body = "[语音]"
    elif type_name == "video":
        body = "[视频]"
    elif type_name == "emoji":
        body = "[表情]"
    elif type_name == "location":
        body = "[位置]"
    elif type_name == "app_message":
        # Try to extract title from XML
        if "<title>" in content:
            try:
                title = content.split("<title>")[1].split("</title>")[0]
                body = f"[链接] {title}"
            except:
                body = "[链接/文件]"
        else:
            body = "[应用消息]"
    elif type_name == "contact_card":
        body = "[名片]"
    elif type_name == "system":
        body = f"[系统] {content}"
    elif type_name == "revoke":
        body = "[撤回消息]"
    else:
        body = f"[{type_name}] {content[:50] if content else ''}"

    return f"  {time_str}  {direction}: {body}"


def cmd_ping(rpc: WxRPC, args):
    r = rpc.ping()
    pprint(f"连接成功! DB就绪: {r.get('dbReady')}, 联系人: {r.get('contacts')}")


def cmd_contacts(rpc: WxRPC, args):
    contacts = rpc.get_contacts(filter_=args.filter or "")
    if not contacts:
        pprint("没有找到联系人")
        return
    pprint(f"\n联系人列表 ({len(contacts)} 个):")
    pprint(f"{'用户名':<30} {'昵称':<15} {'备注':<15} {'类型'}")
    pprint("-" * 80)
    for c in contacts:
        username = c.get("username", "")
        nickname = c.get("nickname", "")
        remark = c.get("remark", "")
        type_ = c.get("type", 0)
        type_name = {1: "自己", 3: "好友", 4: "群聊"}.get(type_, str(type_))
        pprint(f"  {username:<28} {nickname:<14} {remark:<14} {type_name}")


def cmd_conversations(rpc: WxRPC, args):
    convs = rpc.get_conversations(limit=args.limit)
    if not convs:
        pprint("没有会话记录")
        return
    pprint(f"\n会话列表 ({len(convs)} 个):")
    pprint(f"{'联系人':<20} {'消息数':>6}  {'最后消息时间'}")
    pprint("-" * 60)
    for c in convs:
        name = c.get("name", c.get("talker", ""))
        count = c.get("count", 0)
        last = c.get("lastMessage", "")
        pprint(f"  {name:<18} {count:>6}  {last}")


def cmd_history(rpc: WxRPC, args):
    talker = args.target
    limit = args.limit
    messages = rpc.get_history(talker=talker, limit=limit)
    if not messages:
        pprint(f"没有找到与 {talker} 的聊天记录")
        return

    # Messages are in DESC order, reverse for display
    messages.reverse()

    name = messages[0].get("talkerName", talker) if messages else talker
    pprint(f"\n与 {name} 的聊天记录 (最近 {len(messages)} 条):")
    pprint("-" * 60)
    for msg in messages:
        pprint(format_message(msg))
    pprint("-" * 60)


def cmd_monitor(rpc: WxRPC, args):
    talker = args.target or ""
    if talker:
        pprint(f"\n开始监控: {talker}")
    else:
        pprint("\n开始监控所有消息")
    pprint("按 Ctrl+C 停止\n")
    pprint("-" * 60)

    # Get current max msgId first
    try:
        result = rpc.get_new_messages(after_id=0, talker=talker)
        last_id = result.get("last_id", 0)
        # Show last few messages as context
        messages = result.get("messages", [])
        if messages:
            # Show at most last 5 as context
            context = messages[-5:] if len(messages) > 5 else messages
            for msg in context:
                pprint(format_message(msg))
            pprint("-" * 60)
            pprint("(以上为历史消息，以下为实时消息)\n")
    except Exception as e:
        pprint(f"获取初始消息失败: {e}")
        last_id = 0

    retry_count = 0
    while True:
        try:
            result = rpc.get_new_messages(after_id=last_id, talker=talker)
            messages = result.get("messages", [])
            new_last_id = result.get("last_id", last_id)

            if messages:
                for msg in messages:
                    pprint(format_message(msg))
                last_id = new_last_id

            retry_count = 0
            time.sleep(1)

        except KeyboardInterrupt:
            pprint("\n\n监控已停止")
            break
        except Exception as e:
            retry_count += 1
            if retry_count <= 3:
                pprint(f"\n连接失败 ({e}), {retry_count}/3 重试中...")
                time.sleep(2)
            else:
                pprint(f"\n连接失败超过3次，退出: {e}")
                break


def cmd_save(rpc: WxRPC, args):
    talker = args.target
    limit = args.limit
    out_dir = args.output

    pprint(f"\n开始导出与 {talker} 的聊天记录...")

    # 1. 拉取全部消息
    if limit > 0:
        pprint(f"拉取最近 {limit} 条消息...")
        messages = rpc.get_history(talker=talker, limit=limit)
        messages.reverse()  # DESC -> ASC
    else:
        pprint("拉取全部消息（分页）...")
        messages = rpc.get_all_history(talker=talker)

    if not messages:
        pprint(f"没有找到与 {talker} 的聊天记录")
        return

    # 用第一条消息确定实际联系人名称
    contact_name = messages[0].get("talkerName", talker)
    pprint(f"共 {len(messages)} 条消息，联系人: {contact_name}")

    # 2. 创建输出目录
    export_dir = Path(out_dir) / contact_name
    media_dir = export_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    # 3. 筛选媒体消息并下载
    media_types = {3, 34, 43, 47}  # image, voice, video, emoji
    media_messages = [m for m in messages if m.get("type") in media_types]
    pprint(f"媒体消息: {len(media_messages)} 条")

    media_map = {}  # msgId -> [local_file_paths]
    downloaded = 0
    skipped = 0
    failed = 0

    for i, msg in enumerate(media_messages):
        msg_id = msg["msgId"]
        type_name = msg.get("typeName", "unknown")
        progress = f"[{i+1}/{len(media_messages)}]"

        try:
            result = rpc.resolve_media(msg_id)
            files = result.get("files", [])
            if not files:
                skipped += 1
                continue

            local_files = []
            for finfo in files:
                file_type = finfo.get("type", "file")
                label = finfo.get("label", "unknown")

                if file_type == "url":
                    # 表情包 CDN URL，记录但不下载
                    local_files.append({
                        "label": label,
                        "url": finfo.get("url", ""),
                        "type": "url"
                    })
                    continue

                if not finfo.get("exists", False):
                    continue

                path = finfo["path"]
                size = finfo.get("size", 0)
                ext = _guess_ext(path, type_name, label)
                local_name = f"{msg_id}_{label}{ext}"
                local_path = media_dir / local_name

                # 跳过已下载的文件
                if local_path.exists() and local_path.stat().st_size == size:
                    local_files.append({
                        "label": label,
                        "file": local_name,
                        "size": size,
                        "type": "file"
                    })
                    downloaded += 1
                    continue

                pprint(f"  {progress} 下载 {type_name}/{label} ({_fmt_size(size)})...")
                try:
                    media_result = rpc.get_media(path)
                    b64_data = media_result["base64"]
                    file_data = base64.b64decode(b64_data)

                    # 验证 MD5
                    actual_md5 = hashlib.md5(file_data).hexdigest()
                    expected_md5 = media_result.get("md5", "")
                    if expected_md5 and actual_md5 != expected_md5:
                        pprint(f"    MD5 不匹配! expected={expected_md5} actual={actual_md5}")

                    local_path.write_bytes(file_data)
                    local_files.append({
                        "label": label,
                        "file": local_name,
                        "size": len(file_data),
                        "md5": actual_md5,
                        "type": "file"
                    })
                    downloaded += 1
                except Exception as e:
                    pprint(f"    下载失败: {e}")
                    failed += 1

            if local_files:
                media_map[str(msg_id)] = local_files

        except Exception as e:
            pprint(f"  {progress} resolve_media 失败 (msgId={msg_id}): {e}")
            failed += 1

    pprint(f"\n媒体下载完成: 成功 {downloaded}, 跳过 {skipped}, 失败 {failed}")

    # 4. 生成 chat.json
    export_data = {
        "contact": contact_name,
        "talker": messages[0].get("talker", talker),
        "exportTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "messageCount": len(messages),
        "mediaCount": downloaded,
        "messages": messages,
        "mediaMap": media_map
    }
    chat_json_path = export_dir / "chat.json"
    chat_json_path.write_text(
        json.dumps(export_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 5. 生成 chat.txt
    chat_txt_path = export_dir / "chat.txt"
    with open(chat_txt_path, "w", encoding="utf-8") as f:
        f.write(f"与 {contact_name} 的聊天记录\n")
        f.write(f"导出时间: {export_data['exportTime']}\n")
        f.write(f"消息总数: {len(messages)}\n")
        f.write("=" * 60 + "\n\n")
        for msg in messages:
            f.write(format_message(msg) + "\n")
            # 如果有媒体文件，附注
            mid = str(msg["msgId"])
            if mid in media_map:
                for mf in media_map[mid]:
                    if mf.get("type") == "url":
                        f.write(f"    📎 [{mf['label']}] {mf.get('url', '')}\n")
                    else:
                        f.write(f"    📎 [{mf['label']}] media/{mf.get('file', '')}\n")

    pprint(f"\n导出完成!")
    pprint(f"  目录: {export_dir}")
    pprint(f"  chat.json: {_fmt_size(chat_json_path.stat().st_size)}")
    pprint(f"  chat.txt:  {_fmt_size(chat_txt_path.stat().st_size)}")
    media_files = list(media_dir.iterdir())
    if media_files:
        total_media_size = sum(f.stat().st_size for f in media_files)
        pprint(f"  media/:    {len(media_files)} 个文件, {_fmt_size(total_media_size)}")


def _guess_ext(path: str, type_name: str, label: str) -> str:
    """根据路径和类型猜测文件扩展名"""
    # 先从路径取
    if "." in path.split("/")[-1]:
        ext = "." + path.split("/")[-1].rsplit(".", 1)[1]
        return ext
    # 按类型猜
    if type_name == "image" or label in ("original", "medium", "thumbnail"):
        return ".jpg"
    if type_name == "voice" or label == "voice":
        return ".amr"
    if type_name == "video" and label == "video":
        return ".mp4"
    if type_name == "video" and label == "video_thumb":
        return ".jpg"
    return ""


def _fmt_size(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size/1024:.1f}KB"
    return f"{size/1024/1024:.1f}MB"


def main():
    parser = argparse.ArgumentParser(description="微信消息实时监控")
    subparsers = parser.add_subparsers(dest="command")

    # ping
    subparsers.add_parser("ping", help="测试连接")

    # contacts
    p_contacts = subparsers.add_parser("contacts", help="列出联系人")
    p_contacts.add_argument("--filter", "-f", default="", help="搜索关键词")

    # conversations
    p_conv = subparsers.add_parser("conversations", help="列出会话")
    p_conv.add_argument("--limit", "-n", type=int, default=50, help="数量限制")

    # history
    p_hist = subparsers.add_parser("history", help="查看聊天记录")
    p_hist.add_argument("target", help="联系人名称或wxid")
    p_hist.add_argument("--limit", "-n", type=int, default=50, help="数量限制")

    # monitor
    p_mon = subparsers.add_parser("monitor", help="实时监控消息")
    p_mon.add_argument("target", nargs="?", default="", help="联系人名称或wxid (不指定则监控所有)")

    # save
    p_save = subparsers.add_parser("save", help="导出聊天记录+媒体到本地")
    p_save.add_argument("target", help="联系人名称或wxid")
    p_save.add_argument("--limit", "-n", type=int, default=0, help="消息数量限制 (0=全部)")
    p_save.add_argument("--output", "-o", default="./wx_export", help="输出目录 (默认 ./wx_export)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    rpc = WxRPC()

    # Auto setup adb forward
    rpc.setup_forward()

    handlers = {
        "ping": cmd_ping,
        "contacts": cmd_contacts,
        "conversations": cmd_conversations,
        "history": cmd_history,
        "monitor": cmd_monitor,
        "save": cmd_save,
    }

    try:
        handlers[args.command](rpc, args)
    except ConnectionRefusedError:
        pprint("\n连接被拒绝! 请检查:")
        pprint("  1. 微信是否在运行")
        pprint("  2. WXHook 模块是否已加载")
        pprint(f"  3. ADB forward 是否已设置 (tcp:{rpc.RPC_PORT} -> localabstract:wxhook_rpc)")
    except Exception as e:
        pprint(f"\n错误: {e}")


if __name__ == "__main__":
    main()
