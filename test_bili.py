"""
B站直播弹幕调试脚本 v3 - 多策略尝试 + 跟踪
"""

import json
import struct
import sys
import threading
import time
import urllib.request

try:
    import brotli
    import websocket
except ImportError as e:
    print(f"[错误] 缺少依赖: {e}")
    sys.exit(1)

HEADER_STRUCT = struct.Struct(">I2H2I")
HEADER_LENGTH = 16
OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_MESSAGE = 5
OP_JOIN_ROOM = 7
OP_JOIN_ROOM_REPLY = 8
VER_BROTLI = 3


def create_packet(body, op, ver=0):
    header = HEADER_STRUCT.pack(HEADER_LENGTH + len(body), HEADER_LENGTH, ver, op, 1)
    return header + body


def parse_packets(data):
    offset = 0
    packets = []
    while offset + HEADER_LENGTH <= len(data):
        total, hlen, ver, op, seq = HEADER_STRUCT.unpack_from(data, offset)
        body = data[offset + hlen : offset + total]
        if ver == VER_BROTLI:
            try:
                packets.extend(parse_packets(brotli.decompress(body)))
            except Exception:
                break
        else:
            packets.append((op, body))
        offset += total
    return packets


def extract_username(info):
    if len(info) > 2 and isinstance(info[2], (list, tuple)) and len(info[2]) > 1:
        name = info[2][1]
        if isinstance(name, str) and name.strip():
            return name.strip()
    if len(info) > 15 and isinstance(info[15], dict):
        for key in ("uname", "nickname", "name", "username"):
            val = info[15].get(key, "")
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def get_room_info(room_id):
    url = f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    if data.get("code") != 0:
        raise Exception(f"房间不存在: {room_id}")
    return data["data"]


# ── 多策略尝试连接 ───────────────────────────

def try_connect(ws_url, real_room_id, label, join_body, join_ver=0, protover=0):
    """尝试一种连接策略，返回是否成功"""
    result = {"success": False, "reason": ""}
    event = threading.Event()
    messages = []

    def on_open(ws):
        ws.send(create_packet(join_body, OP_JOIN_ROOM, ver=join_ver),
                opcode=websocket.ABNF.OPCODE_BINARY)
        print(f"   [{label}] 已发送加房包")

        def heartbeat():
            while ws.keep_running:
                try:
                    ws.send(create_packet(b"", OP_HEARTBEAT, ver=1),
                            opcode=websocket.ABNF.OPCODE_BINARY)
                except Exception:
                    break
                time.sleep(30)
        threading.Thread(target=heartbeat, daemon=True).start()

    def on_message(ws, message):
        for op, body in parse_packets(message):
            if op == OP_HEARTBEAT_REPLY:
                online = struct.unpack(">I", body[:4])[0]
                print(f"   [{label}] 心跳回复: 人气 {online}")
                result["success"] = True
                event.set()
            elif op == OP_JOIN_ROOM_REPLY:
                try:
                    j = json.loads(body.decode("utf-8"))
                    print(f"   [{label}] 加房回复: {j}")
                except Exception:
                    print(f"   [{label}] 加房回复(原始): {body[:100]}")
            elif op == OP_MESSAGE:
                messages.append(body)

    def on_error(ws, error):
        result["reason"] = str(error)

    def on_close(ws, code, msg):
        if not event.is_set():
            result["reason"] = f"closed(code={code})"

    ws = websocket.WebSocketApp(
        ws_url, on_open=on_open, on_message=on_message,
        on_error=on_error, on_close=on_close,
    )

    t = threading.Thread(target=lambda: ws.run_forever())
    t.daemon = True
    t.start()

    if event.wait(timeout=8):
        # 收到心跳回复，连接成功
        result["success"] = True
    else:
        # 超时或断开
        ws.close()
        t.join(timeout=1)

    return result, messages


def main():
    room_input = sys.argv[1] if len(sys.argv) > 1 else "7777"

    # 获取房间信息
    print(f"获取房间信息: {room_input}")
    room_info = get_room_info(room_input)
    real_room_id = room_info["room_id"]
    print(f"  真实房间号: {real_room_id}")
    print(f"  标题: {room_info['title']}")
    print(f"  直播中: {'是' if room_info['live_status'] == 1 else '否'}")

    # 构造不同策略的加房包
    strategies = [
        {
            "label": "老版-仅roomid",
            "join_body": json.dumps({"roomid": real_room_id}).encode(),
            "join_ver": 0,
        },
        {
            "label": "老版-roomid+uid",
            "join_body": json.dumps({"roomid": real_room_id, "uid": 0}).encode(),
            "join_ver": 0,
        },
        {
            "label": "老版-protover=1",
            "join_body": json.dumps({"roomid": real_room_id, "uid": 0, "protover": 1}).encode(),
            "join_ver": 0,
        },
        {
            "label": "老版-protover=2",
            "join_body": json.dumps({"roomid": real_room_id, "uid": 0, "protover": 2}).encode(),
            "join_ver": 0,
        },
        {
            "label": "老版-protover=3",
            "join_body": json.dumps({"roomid": real_room_id, "uid": 0, "protover": 3}).encode(),
            "join_ver": 0,
        },
        {
            "label": "新版-version=1",
            "join_body": json.dumps({
                "uid": 0, "roomid": real_room_id, "protover": 3,
                "platform": "web", "type": 2,
            }).encode(),
            "join_ver": 1,
        },
    ]

    ws_url = "wss://broadcastlv.chat.bilibili.com:2245/sub"
    print(f"\n连接: {ws_url}")
    print(f"{'=' * 50}")

    for s in strategies:
        print(f"\n尝试: [{s['label']}]")
        result, msgs = try_connect(ws_url, real_room_id, **s)
        if result["success"]:
            print(f"  ✓ [{s['label']}] 连接成功！心跳正常")
            print(f"  收到 {len(msgs)} 条消息，等待弹幕...")
            # 保存成功的连接信息
            print(f"\n{'=' * 50}")
            print(f"[成功] 该策略可连接，继续接收弹幕...")
            print(f"{'=' * 50}")

            # 持续接收弹幕
            # 重新用此策略连接并保持
            _keep_receiving(ws_url, real_room_id, **s)
            return
        else:
            print(f"  ✗ [{s['label']}] 失败: {result['reason']}")

    print("\n所有策略都失败，可能原因:")
    print("  1. B站 WebSocket 协议已更新")
    print("  2. 需要登录获取 token/cookie")
    print("  3. 网络连接被拦截")


def _keep_receiving(ws_url, real_room_id, join_body, join_ver, label):
    """成功连接后持续接收弹幕"""
    count = [0]

    def on_open(ws):
        ws.send(create_packet(join_body, OP_JOIN_ROOM, ver=join_ver),
                opcode=websocket.ABNF.OPCODE_BINARY)
        print(f"[{label}] 已加入房间")

        def heartbeat():
            while ws.keep_running:
                try:
                    ws.send(create_packet(b"", OP_HEARTBEAT, ver=1),
                            opcode=websocket.ABNF.OPCODE_BINARY)
                except Exception:
                    break
                time.sleep(30)
        threading.Thread(target=heartbeat, daemon=True).start()

    def on_message(ws, message):
        for op, body in parse_packets(message):
            if op == OP_HEARTBEAT_REPLY:
                online = struct.unpack(">I", body[:4])[0]
                print(f"\r[人气] {online} | 收到 {count[0]} 条弹幕", end="", flush=True)
            elif op == OP_MESSAGE:
                try:
                    data = json.loads(body.decode("utf-8", errors="replace"))
                    cmd = data.get("cmd", "")
                    if cmd == "DANMU_MSG":
                        count[0] += 1
                        info = data.get("info", [])
                        text = info[1] if len(info) > 1 else ""
                        username = extract_username(info)
                        print(f"\n[{count[0]}] [{username}]: {text}")
                        if count[0] == 1:
                            print("\n" + "=" * 60)
                            print("完整 info 结构:")
                            print(json.dumps(info, ensure_ascii=False, indent=2)[:3000])
                            print("=" * 60 + "\n")
                except Exception:
                    pass

    def on_error(ws, error):
        print(f"\n[错误] {error}")

    def on_close(ws, code, msg):
        print(f"\n[断开] code={code}")

    ws = websocket.WebSocketApp(
        ws_url, on_open=on_open, on_message=on_message,
        on_error=on_error, on_close=on_close,
    )
    try:
        ws.run_forever()
    except KeyboardInterrupt:
        print("\n[退出]")


if __name__ == "__main__":
    main()
