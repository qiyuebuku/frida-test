#!/usr/bin/env python3
"""
Android 逆向工程辅助工具集
用于自动化 APK 分析、加固识别、项目创建、编译部署等操作
"""

import subprocess
import os
import re
import json
import shutil
import sys
from pathlib import Path

# ==================== 配置 ====================
ADB = os.environ.get("ADB", "adb")
DEVICE_SERIAL = os.environ.get("ANDROID_SERIAL")
ANDROID_SDK = os.environ.get("ANDROID_HOME", "/home/yuyang/android-sdk")
NDK = f"{ANDROID_SDK}/ndk/27.0.12077973"
GRADLE = "/home/yuyang/.gradle-dist/gradle-8.9/bin/gradle"
JAVA_HOME = "/usr/lib/jvm/java-17-openjdk-amd64"
REVERSE_ROOT = "/home/yuyang/frida-test"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(SKILL_DIR, "templates")


def adb(*args):
    """执行 ADB 命令"""
    cmd = [ADB]
    if DEVICE_SERIAL:
        cmd.extend(["-s", DEVICE_SERIAL])
    cmd.extend(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def adb_shell(command):
    """在手机上执行 shell 命令"""
    stdout, stderr, code = adb("shell", command)
    return stdout


# ==================== 设备管理 ====================

def check_device():
    """检查设备状态"""
    info = {}

    # ADB 连接
    stdout, _, code = adb("devices")
    info["adb_connected"] = "\tdevice" in stdout

    # Root 检测
    root_check = adb_shell("su -c 'id' 2>/dev/null")
    info["root"] = "uid=0" in root_check

    # Zygisk 检测
    zygisk_check = adb_shell("ls /data/adb/modules/ 2>/dev/null")
    info["zygisk_modules"] = zygisk_check.split("\n") if zygisk_check else []

    # 设备信息
    info["model"] = adb_shell("getprop ro.product.model")
    info["android_version"] = adb_shell("getprop ro.build.version.release")
    info["sdk_version"] = adb_shell("getprop ro.build.version.sdk")
    info["cpu_abi"] = adb_shell("getprop ro.product.cpu.abi")

    return info


# ==================== APK 分析 ====================

# 加固 SO 特征库
PROTECTION_SIGNATURES = {
    "360加固": ["libjiagu.so", "libjiagu_vip.so", "libjiagu_x86.so"],
    "梆梆加固": ["libbangcle_crypto_tool.so", "libDexHelper.so", "libSecShell.so"],
    "腾讯乐固": ["libdexvmp.so", "libshella-", "libshellx-", "libtxprotect.so"],
    "阿里聚安全": ["libsgmain.so", "libsgsecuritybody.so", "libmobisec.so"],
    "爱加密": ["libDexHelper", "libexec.so", "libexecmain.so"],
    "网易易盾": ["libnesec.so"],
}


def detect_protection(apk_path):
    """检测 APK 加固类型"""
    result = subprocess.run(
        ["unzip", "-l", apk_path],
        capture_output=True, text=True
    )

    so_files = []
    for line in result.stdout.split("\n"):
        if line.strip().endswith(".so"):
            so_files.append(line.strip().split()[-1])

    detected = []
    for name, signatures in PROTECTION_SIGNATURES.items():
        for sig in signatures:
            for so in so_files:
                if sig in so:
                    detected.append(name)
                    break
            if name in detected:
                break

    return {
        "protections": list(set(detected)) or ["未加固"],
        "so_files": so_files,
    }


def analyze_apk(apk_path):
    """分析 APK 基本信息"""
    result = subprocess.run(
        ["aapt", "dump", "badging", apk_path],
        capture_output=True, text=True
    )
    output = result.stdout

    info = {}

    # 包名
    m = re.search(r"package: name='([^']+)'", output)
    if m:
        info["package"] = m.group(1)

    # 版本
    m = re.search(r"versionName='([^']+)'", output)
    if m:
        info["version"] = m.group(1)

    # 应用名
    m = re.search(r"application-label:'([^']+)'", output)
    if m:
        info["label"] = m.group(1)

    # minSdk
    m = re.search(r"sdkVersion:'(\d+)'", output)
    if m:
        info["min_sdk"] = m.group(1)

    # targetSdk
    m = re.search(r"targetSdkVersion:'(\d+)'", output)
    if m:
        info["target_sdk"] = m.group(1)

    # 加固检测
    protection = detect_protection(apk_path)
    info["protection"] = protection["protections"]
    info["native_libs"] = protection["so_files"]

    return info


# ==================== 项目管理 ====================

def create_project(package_name, project_name, tag="hook"):
    """从模板创建新的 Hook 项目"""
    project_dir = os.path.join(REVERSE_ROOT, project_name)
    if os.path.exists(project_dir):
        print(f"项目目录已存在: {project_dir}")
        return project_dir

    # 创建目录结构
    os.makedirs(f"{project_dir}/app/src/main/java/com/yuyang/{tag}hook", exist_ok=True)
    os.makedirs(f"{project_dir}/zygisk/jni", exist_ok=True)
    os.makedirs(f"{project_dir}/zygisk/magisk/zygisk", exist_ok=True)
    os.makedirs(f"{project_dir}/zygisk/magisk/dex", exist_ok=True)
    os.makedirs(f"{project_dir}/zygisk/extracted", exist_ok=True)
    os.makedirs(f"{project_dir}/scripts", exist_ok=True)
    os.makedirs(f"{project_dir}/docs", exist_ok=True)

    module_id = f"{tag}hook_zygisk"
    hook_class = f"com.yuyang.{tag}hook.MainHook"

    # 复制模板文件
    # zygisk.hpp
    shutil.copy(f"{TEMPLATE_DIR}/zygisk/zygisk.hpp", f"{project_dir}/zygisk/jni/")

    # main.cpp（替换占位符）
    with open(f"{TEMPLATE_DIR}/zygisk/main.cpp") as f:
        content = f.read()
    content = content.replace("com.target.package", package_name)
    content = content.replace("com.yuyang.hook.MainHook", hook_class)
    content = content.replace("MODULE_ID", module_id)
    content = content.replace('"ZygiskHook"', f'"{tag.upper()}Hook"')
    with open(f"{project_dir}/zygisk/jni/main.cpp", "w") as f:
        f.write(content)

    # Android.mk & Application.mk
    shutil.copy(f"{TEMPLATE_DIR}/zygisk/Android.mk", f"{project_dir}/zygisk/jni/")
    shutil.copy(f"{TEMPLATE_DIR}/zygisk/Application.mk", f"{project_dir}/zygisk/jni/")

    # module.prop
    with open(f"{TEMPLATE_DIR}/module.prop") as f:
        content = f.read()
    content = content.replace("MODULE_ID", module_id)
    content = content.replace("MODULE_NAME", project_name.title())
    content = content.replace("TARGET_PACKAGE", package_name)
    with open(f"{project_dir}/zygisk/magisk/module.prop", "w") as f:
        f.write(content)

    # build.gradle
    with open(f"{TEMPLATE_DIR}/build.gradle") as f:
        content = f.read()
    content = content.replace("com.yuyang.hook", f"com.yuyang.{tag}hook")
    with open(f"{project_dir}/app/build.gradle", "w") as f:
        f.write(content)

    # MainHook.java
    with open(f"{TEMPLATE_DIR}/MainHook.java") as f:
        content = f.read()
    content = content.replace("package com.yuyang.hook;", f"package com.yuyang.{tag}hook;")
    content = content.replace('"AppHook"', f'"{tag.upper()}Hook"')
    with open(f"{project_dir}/app/src/main/java/com/yuyang/{tag}hook/MainHook.java", "w") as f:
        f.write(content)

    # settings.gradle
    with open(f"{project_dir}/settings.gradle", "w") as f:
        f.write("include ':app'\n")

    # gradle.properties
    with open(f"{project_dir}/gradle.properties", "w") as f:
        f.write("android.useAndroidX=true\n")

    print(f"项目创建完成: {project_dir}")
    print(f"  包名: {package_name}")
    print(f"  模块ID: {module_id}")
    print(f"  Hook类: {hook_class}")
    return project_dir


def build_project(project_dir):
    """编译 Hook 项目"""
    env = os.environ.copy()
    env["JAVA_HOME"] = JAVA_HOME
    env["ANDROID_HOME"] = ANDROID_SDK

    # Gradle 编译
    result = subprocess.run(
        [GRADLE, ":app:assembleDebug"],
        cwd=project_dir,
        capture_output=True, text=True, env=env, timeout=300
    )

    if result.returncode != 0:
        print(f"编译失败:\n{result.stderr}")
        return False

    # 提取 DEX
    apk_path = os.path.join(project_dir, "app/build/outputs/apk/debug/app-debug.apk")
    extracted_dir = os.path.join(project_dir, "zygisk/extracted")
    subprocess.run(
        ["unzip", "-o", apk_path, "classes*.dex", "-d", extracted_dir],
        capture_output=True, timeout=30
    )

    print("编译成功，DEX 已提取")
    return True


def build_zygisk_so(project_dir):
    """编译 Zygisk C++ 模块"""
    clang = f"{NDK}/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang++"
    src = os.path.join(project_dir, "zygisk/jni/main.cpp")
    out = os.path.join(project_dir, "zygisk/magisk/zygisk/arm64-v8a.so")

    result = subprocess.run(
        [clang, "-shared", "-fPIC", "-std=c++17", "-O2", "-s",
         "-I", os.path.join(project_dir, "zygisk/jni"),
         "-o", out, src, "-llog", "-ldl"],
        capture_output=True, text=True, timeout=60
    )

    if result.returncode != 0:
        print(f"编译失败:\n{result.stderr}")
        return False

    print(f"Zygisk SO 编译成功: {out}")
    return True


# ==================== 截屏与日志 ====================

def screenshot(output_path="/tmp/screenshot.png"):
    """截取手机屏幕"""
    adb("exec-out", "screencap", "-p", ">", output_path)
    print(f"截图保存到: {output_path}")
    return output_path


def collect_logs(tag, duration_seconds=10):
    """采集指定 TAG 的 logcat 日志"""
    adb("logcat", "-c")  # 清空
    result = subprocess.run(
        [ADB, "-s", DEVICE_SERIAL, "logcat", "-v", "threadtime", "-d"],
        capture_output=True, text=True,
        timeout=duration_seconds + 5
    )
    lines = [l for l in result.stdout.split("\n") if tag in l]
    return lines


# ==================== 主入口 ====================

def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python reverse_helper.py check              # 检查设备")
        print("  python reverse_helper.py analyze <apk>      # 分析 APK")
        print("  python reverse_helper.py create <pkg> <name> [tag]  # 创建项目")
        print("  python reverse_helper.py build <project_dir>  # 编译项目")
        print("  python reverse_helper.py screenshot          # 截屏")
        return

    cmd = sys.argv[1]

    if cmd == "check":
        info = check_device()
        print(json.dumps(info, indent=2, ensure_ascii=False))

    elif cmd == "analyze" and len(sys.argv) >= 3:
        info = analyze_apk(sys.argv[2])
        print(json.dumps(info, indent=2, ensure_ascii=False))

    elif cmd == "create" and len(sys.argv) >= 4:
        tag = sys.argv[4] if len(sys.argv) >= 5 else "hook"
        create_project(sys.argv[2], sys.argv[3], tag)

    elif cmd == "build" and len(sys.argv) >= 3:
        build_project(sys.argv[2])

    elif cmd == "screenshot":
        screenshot()

    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
