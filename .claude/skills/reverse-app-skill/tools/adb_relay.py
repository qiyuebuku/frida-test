#!/usr/bin/env python3
"""
ADB TCP Relay — WSL2 下的端口转发替代方案

WSL2 中 adb forward 经常不生效，本工具通过 adb shell nc 建立双向 TCP 中继，
将 WSL2 本地端口的流量转发到手机端口。

用法:
  python3 adb_relay.py                          # 默认 9998 → 手机 9999
  python3 adb_relay.py 18900 18900              # 自定义端口（本地 → 手机）
  python3 adb_relay.py 27042 27042              # 转发 frida-server 端口
"""
import socket
import subprocess
import threading
import sys
import os

ADB = os.environ.get("ADB", "adb")
SERIAL = os.environ.get("ANDROID_SERIAL")


def adb_command(*args):
    command = [ADB]
    if SERIAL:
        command.extend(["-s", SERIAL])
    command.extend(args)
    return command


def relay(client_sock, remote_port):
    """通过 adb shell nc 建立到手机的双向通道"""
    try:
        proc = subprocess.Popen(
            adb_command("shell", f"nc 127.0.0.1 {remote_port}"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def client_to_phone():
            try:
                while True:
                    data = client_sock.recv(4096)
                    if not data:
                        break
                    proc.stdin.write(data)
                    proc.stdin.flush()
            except Exception:
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        def phone_to_client():
            try:
                while True:
                    data = proc.stdout.read(4096)
                    if not data:
                        break
                    client_sock.sendall(data)
            except Exception:
                pass
            finally:
                client_sock.close()

        t1 = threading.Thread(target=client_to_phone, daemon=True)
        t2 = threading.Thread(target=phone_to_client, daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        proc.terminate()
    except Exception as e:
        print(f"[relay] Error: {e}")
    finally:
        client_sock.close()


def main():
    local_port = int(sys.argv[1]) if len(sys.argv) > 1 else 9998
    remote_port = int(sys.argv[2]) if len(sys.argv) > 2 else 9999

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", local_port))
    server.listen(5)
    print(f"[*] ADB relay: 127.0.0.1:{local_port} → phone:{remote_port}")
    print(f"[*] Device: {SERIAL or 'adb default device'}")

    while True:
        client, addr = server.accept()
        print(f"[+] Connection from {addr}")
        t = threading.Thread(target=relay, args=(client, remote_port), daemon=True)
        t.start()


if __name__ == "__main__":
    main()
