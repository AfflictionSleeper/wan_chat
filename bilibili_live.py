"""
Bilibili 直播弹幕 WebSocket 客户端
仅支持认证模式：WBI 签名 + Cookie，获取完整昵称
"""

import hashlib
import json
import re
import struct
import threading
import time
import urllib.request

try:
    import brotli
except ImportError:
    brotli = None

try:
    import websocket
except ImportError:
    websocket = None

# ── 协议常量 ──────────────────────────────────

HEADER_STRUCT = struct.Struct(">I2H2I")
HEADER_LENGTH = 16

OP_AUTH = 7
OP_AUTH_REPLY = 8
OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_MESSAGE = 5

VER_BROTLI = 3
VER_ZLIB = 2

# ── WBI 签名 ──────────────────────────────────

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _get_mixin_key(orig):
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _enc_wbi(params, img_key, sub_key):
    mixin_key = _get_mixin_key(img_key + sub_key)
    params["wts"] = int(time.time())
    params = dict(sorted(params.items()))
    filtered = {}
    for k, v in params.items():
        sv = str(v)
        for ch in "!'()*":
            sv = sv.replace(ch, "")
        filtered[k] = sv
    query = "&".join(f"{k}={v}" for k, v in filtered.items())
    sign = hashlib.md5((query + mixin_key).encode()).hexdigest()
    filtered["w_rid"] = sign
    return filtered


def _fetch_wbi_keys(cookie):
    """从导航接口获取 WBI 签名密钥对"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": cookie,
    }
    try:
        req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav", headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data.get("code") == 0:
            nd = data["data"]
            img_url = nd.get("wbi_img", {}).get("img_url", "")
            sub_url = nd.get("wbi_img", {}).get("sub_url", "")
            img_key = re.search(r"/([^/]+)\.png", img_url)
            sub_key = re.search(r"/([^/]+)\.png", sub_url)
            if img_key and sub_key:
                return img_key.group(1), sub_key.group(1)
    except Exception:
        pass
    return "", ""


# ── 工具函数 ──────────────────────────────────


def _build_headers(cookie):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://live.bilibili.com/",
        "Origin": "https://live.bilibili.com",
        "Cookie": cookie,
    }


def _http_get_json(url, params, cookie):
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    try:
        req = urllib.request.Request(url, headers=_build_headers(cookie))
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except Exception:
        return None


def _parse_cookie(cookie):
    """从 cookie 字符串提取 uid 和 buvid3"""
    uid = 0
    buvid3 = ""
    m = re.search(r"DedeUserID=(\d+)", cookie)
    if m:
        uid = int(m.group(1))
    m = re.search(r"buvid3=([^;]+)", cookie)
    if m:
        buvid3 = m.group(1)
    return uid, buvid3


def _get_danmu_info(room_id, cookie):
    """WBI 签名请求 getDanmuInfo，返回 {"token", "host_list"} 或 None"""
    img_key, sub_key = _fetch_wbi_keys(cookie)
    if not img_key or not sub_key:
        return None
    params = _enc_wbi({"id": room_id, "type": 0}, img_key, sub_key)
    data = _http_get_json(
        "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo",
        params, cookie,
    )
    if data and data.get("code") == 0:
        return data["data"]
    return None


def _resolve_room_id(room_input):
    """解析短号到真实房间号"""
    url = f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_input}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data.get("code") == 0:
            return data["data"]["room_id"]
    except Exception:
        pass
    return int(room_input)


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
            if brotli is None:
                break
            try:
                packets.extend(parse_packets(brotli.decompress(body)))
            except Exception:
                break
        elif ver == VER_ZLIB:
            try:
                import zlib
                packets.extend(parse_packets(zlib.decompress(body)))
            except Exception:
                break
        else:
            packets.append((op, body))
        offset += total
    return packets


def decimal_color_to_hex(decimal_color):
    rgb = decimal_color & 0xFFFFFF
    return f"#{rgb:06X}"


def extract_username(info):
    """从 DANMU_MSG 的 info 数组提取完整昵称"""
    if len(info) > 2 and isinstance(info[2], (list, tuple)) and len(info[2]) > 1:
        name = info[2][1]
        if isinstance(name, str) and name.strip():
            return name.strip()
    if len(info) > 15 and isinstance(info[15], dict):
        for key in ("uname", "nickname", "name", "username"):
            val = info[15].get(key, "")
            if isinstance(val, str) and val.strip():
                return val.strip()
    if len(info) > 0 and isinstance(info[0], (list, tuple)):
        for i in range(13, len(info[0])):
            val = info[0][i]
            if isinstance(val, str) and len(val) > 2:
                return val.strip()
    return ""


def extract_user_id(info):
    """从 DANMU_MSG 的 info 数组提取发送者 UID"""
    if len(info) > 2 and isinstance(info[2], (list, tuple)) and len(info[2]) > 0:
        uid = info[2][0]
        if uid:
            return str(uid).strip()
    if len(info) > 15 and isinstance(info[15], dict):
        for key in ("uid", "mid", "user_id"):
            uid = info[15].get(key, "")
            if uid:
                return str(uid).strip()
    return ""


# ── WebSocket 客户端 ─────────────────────────


class BilibiliLiveClient:
    def __init__(self, on_danmaku=None, on_error=None, on_status=None):
        self.on_danmaku = on_danmaku
        self.on_error = on_error
        self.on_status = on_status
        self.ws = None
        self.keep_running = False
        self._thread = None
        self.room_id = 0
        self._debug_logged = False

    def connect(self, room_id, cookie):
        if not cookie:
            self._error("需要 B站 Cookie 才能连接")
            return
        if websocket is None:
            self._error("缺少 websocket-client 库")
            return
        if brotli is None:
            self._error("缺少 brotli 库")
            return
        if self._thread and self._thread.is_alive():
            if self.ws:
                self.disconnect()
            return

        self.room_id = int(room_id)
        self._status("获取连接参数...")

        # 解析短号到真实房间号
        real_room_id = _resolve_room_id(self.room_id)
        if real_room_id != self.room_id:
            self._status(f"房间 {self.room_id} → 真实房间 {real_room_id}")
            self.room_id = real_room_id

        # 获取 token / host（仅 WBI + cookie 方式）
        danmu_info = _get_danmu_info(self.room_id, cookie)
        if not danmu_info or not danmu_info.get("token"):
            self._error("获取连接参数失败，请检查 Cookie 是否有效")
            return

        token = danmu_info["token"]
        host_list = danmu_info.get("host_list", [])
        host = host_list[0] if host_list else {"host": "broadcastlv.chat.bilibili.com", "wss_port": 2245}
        ws_url = f"wss://{host['host']}:{host.get('wss_port', 2245)}/sub"

        uid, buvid3 = _parse_cookie(cookie)
        self._status(f"已登录  UID:{uid}")

        self._status("连接中...")

        auth_body = {
            "uid": uid,
            "roomid": self.room_id,
            "protover": 3,
            "platform": "web",
            "type": 2,
            "key": token,
        }
        if buvid3:
            auth_body["buvid"] = buvid3

        def _on_open(ws):
            ws.send(
                create_packet(json.dumps(auth_body).encode("utf-8"), OP_AUTH, ver=1),
                opcode=websocket.ABNF.OPCODE_BINARY,
            )
            self.keep_running = True
            self._status("已连接")

            def _heartbeat():
                while self.keep_running:
                    try:
                        ws.send(
                            create_packet(b"", OP_HEARTBEAT, ver=1),
                            opcode=websocket.ABNF.OPCODE_BINARY,
                        )
                    except Exception:
                        break
                    time.sleep(30)

            threading.Thread(target=_heartbeat, daemon=True).start()

        def _on_message(ws, message):
            try:
                for op, body in parse_packets(message):
                    if op == OP_HEARTBEAT_REPLY:
                        if len(body) >= 4:
                            online = struct.unpack(">I", body[:4])[0]
                            self._status(f"已连接 | 人气 {online}")
                    elif op == OP_MESSAGE:
                        try:
                            body_str = body.decode("utf-8", errors="replace")
                            data = json.loads(body_str)
                            cmd = data.get("cmd", "")
                            pos = cmd.find(":")
                            if pos != -1:
                                cmd = cmd[:pos]
                            if cmd == "DANMU_MSG":
                                info_arr = data.get("info", [])
                                text = info_arr[1] if len(info_arr) > 1 else ""
                                username = extract_username(info_arr)
                                user_id = extract_user_id(info_arr)
                                color_raw = info_arr[0][3] if len(info_arr) > 0 and len(info_arr[0]) > 3 else 16777215
                                color = decimal_color_to_hex(color_raw)

                                if not self._debug_logged:
                                    import json as _json
                                    print("[Bili Debug] 首条 DANMU_MSG:")
                                    print(_json.dumps(info_arr, ensure_ascii=False, indent=2)[:2000])
                                    self._debug_logged = True

                                if self.on_danmaku:
                                    self.on_danmaku(text, username, color, user_id)
                        except Exception:
                            pass
                    elif op == OP_AUTH_REPLY:
                        try:
                            j = json.loads(body.decode("utf-8"))
                            if j.get("code") == 0:
                                self._status("已加入房间")
                            else:
                                self._error(f"加房失败: {j}")
                        except Exception:
                            pass
            except Exception:
                pass

        def _on_error(ws, error):
            self._error(str(error))

        def _on_close(ws, close_status_code, close_msg):
            self.keep_running = False
            self._status("已断开")

        def _run():
            ws_headers = [
                "Origin: https://live.bilibili.com",
                "Referer: https://live.bilibili.com/",
                "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ]
            self.ws = websocket.WebSocketApp(
                ws_url, header=ws_headers,
                on_open=_on_open, on_message=_on_message,
                on_error=_on_error, on_close=_on_close,
            )
            self.ws.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def disconnect(self):
        self.keep_running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
        self._status("已断开")

    @property
    def is_connected(self):
        return self.keep_running

    def _status(self, msg):
        if self.on_status:
            self.on_status(msg)

    def _error(self, msg):
        if self.on_error:
            self.on_error(msg)
