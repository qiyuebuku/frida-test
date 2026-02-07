#!/usr/bin/env python3
"""ADB TCP relay - 在 WSL2 中创建本地端口转发到手机端口"""
import socket
import subprocess
import threading
import sys
import time

ADB = "/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe"
SERIAL = "3B15BJ00GZL00000"
LOCAL_PORT = 9998
REMOTE_PORT = 9999

def relay(client_sock, phone_ip="127.0.0.1"):
    """通过 adb exec-out nc 建立到手机的双向通道"""
    try:
        # 使用 adb shell 中的 nc 建立连接
        proc = subprocess.Popen(
            [ADB, "-s", SERIAL, "shell", f"nc 127.0.0.1 {REMOTE_PORT}"],
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
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", LOCAL_PORT))
    server.listen(5)
    print(f"[*] ADB relay listening on 127.0.0.1:{LOCAL_PORT} -> phone:{REMOTE_PORT}")
    print(f"[*] Use: frida -H 127.0.0.1:{LOCAL_PORT} ...")

    while True:
        client, addr = server.accept()
        print(f"[+] Connection from {addr}")
        t = threading.Thread(target=relay, args=(client,), daemon=True)
        t.start()

if __name__ == "__main__":
    main()
