"""
B站风格弹幕覆盖层 - Danmaku Overlay
在游戏画面之上显示从右往左滚动的弹幕
透明窗口 + 鼠标穿透（可切换编辑模式调整位置/大小）
"""

import ctypes
import json
import os
import random
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

from bilibili_live import BilibiliLiveClient

# ──────────────────────────────────────────────
# 配置文件路径（兼容 PyInstaller 打包）
# ──────────────────────────────────────────────

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

CONFIG_PATH = BASE_DIR / "config.json"

# ──────────────────────────────────────────────
# Windows API 常量和函数
# ──────────────────────────────────────────────

GWL_EXSTYLE = -20
GWLP_WNDPROC = -4
GA_ROOT = 2
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_FRAMECHANGED = 0x0020
SWP_DRAWFRAME = 0x0020
SWP_NOCOPYBITS = 0x0100
SW_HIDE = 0
SW_SHOW = 5
HWND_TOPMOST = -1
RDW_FRAME = 0x0400
RDW_INVALIDATE = 0x0001
RDW_UPDATENOW = 0x0100

LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

try:
    GetWindowLongPtr = user32.GetWindowLongPtrW
    SetWindowLongPtr = user32.SetWindowLongPtrW
except AttributeError:
    GetWindowLongPtr = user32.GetWindowLongW
    SetWindowLongPtr = user32.SetWindowLongW

CallWindowProc = user32.CallWindowProcW
DefWindowProc = user32.DefWindowProcW

GetWindowLongPtr.argtypes = [ctypes.c_void_p, ctypes.c_int]
GetWindowLongPtr.restype = ctypes.c_void_p
SetWindowLongPtr.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
SetWindowLongPtr.restype = ctypes.c_void_p
CallWindowProc.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
]
CallWindowProc.restype = ctypes.c_ssize_t
DefWindowProc.argtypes = [
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
]
DefWindowProc.restype = ctypes.c_ssize_t

# 设置 DPI 感知（避免高 DPI 屏幕下的缩放问题）
try:
    user32.SetProcessDPIAware()
except Exception:
    pass

# ──────────────────────────────────────────────
# 配置管理
# ──────────────────────────────────────────────

DEFAULT_CONFIG = {
    "window": {"x": 200, "y": 100, "width": 800, "height": 400},
    "danmaku": {
        "font_size": 26, "speed": 200, "opacity": 0.85,
        "blocked_keywords": [],
        "blocked_user_ids": [],
    },
    "bilibili": {"room_id": 0, "cookie": ""},
    "mode": "edit",
}

COLORREF_TRANSPARENT = 0x00010101  # RGB(1,1,1) 颜色键
TRANSPARENT_COLOR_HEX = "#010101"  # tkinter 使用的颜色字符串
TRANSPARENT_COLOR_RGB = (1, 1, 1)
LINUX_BG_COLOR = "#1a1a1a"


def load_config():
    if CONFIG_PATH.exists():
        try:
            raw = CONFIG_PATH.read_text(encoding="utf-8")
            try:
                cfg = json.loads(raw)
            except json.JSONDecodeError:
                cfg = _repair_broken_config(raw)
                if cfg is None:
                    raise
                save_config(cfg)

            for section, values in DEFAULT_CONFIG.items():
                if section not in cfg:
                    cfg[section] = {} if isinstance(values, dict) else values
                if isinstance(values, dict):
                    for k, v in values.items():
                        if k not in cfg[section]:
                            cfg[section][k] = v
            cfg.setdefault("bilibili", {})["cookie"] = normalize_cookie(
                cfg.get("bilibili", {}).get("cookie", "")
            )
            cfg.setdefault("danmaku", {})["blocked_keywords"] = normalize_blocked_keywords(
                cfg.get("danmaku", {}).get("blocked_keywords", [])
            )
            cfg.setdefault("danmaku", {})["blocked_user_ids"] = normalize_blocked_user_ids(
                cfg.get("danmaku", {}).get("blocked_user_ids", [])
            )
            return cfg
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg):
    try:
        cfg.setdefault("bilibili", {})["cookie"] = normalize_cookie(
            cfg.get("bilibili", {}).get("cookie", "")
        )
        cfg.setdefault("danmaku", {})["blocked_keywords"] = normalize_blocked_keywords(
            cfg.get("danmaku", {}).get("blocked_keywords", [])
        )
        cfg.setdefault("danmaku", {})["blocked_user_ids"] = normalize_blocked_user_ids(
            cfg.get("danmaku", {}).get("blocked_user_ids", [])
        )
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def normalize_cookie(cookie):
    if not cookie:
        return ""
    parts = []
    for line in str(cookie).replace("\r", "\n").split("\n"):
        for piece in line.split(";"):
            piece = piece.strip()
            if piece:
                parts.append(piece)
    return "; ".join(parts)


def normalize_blocked_keywords(value):
    if not value:
        return []
    if isinstance(value, str):
        pieces = re.split(r"[\n,，;；]+", value)
    else:
        pieces = []
        for item in value:
            pieces.extend(re.split(r"[\n,，;；]+", str(item)))

    keywords = []
    seen = set()
    for piece in pieces:
        keyword = piece.strip()
        key = keyword.lower()
        if keyword and key not in seen:
            keywords.append(keyword)
            seen.add(key)
    return keywords


def normalize_blocked_user_ids(value):
    if not value:
        return []
    if isinstance(value, str):
        pieces = re.split(r"[\n,，;；\s]+", value)
    else:
        pieces = []
        for item in value:
            pieces.extend(re.split(r"[\n,，;；\s]+", str(item)))

    user_ids = []
    seen = set()
    for piece in pieces:
        user_id = piece.strip()
        if user_id and user_id not in seen:
            user_ids.append(user_id)
            seen.add(user_id)
    return user_ids


def _repair_broken_config(raw):
    room_match = re.search(r'"room_id"\s*:\s*(\d+)', raw)
    mode_match = re.search(r'"mode"\s*:\s*"([^"]+)"', raw)
    cookie_match = re.search(r'"cookie"\s*:\s*"([\s\S]*?)"\s*\n\s*}\s*,\s*\n\s*"mode"', raw)

    if not room_match:
        return None

    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["bilibili"]["room_id"] = int(room_match.group(1))
    if mode_match:
        cfg["mode"] = mode_match.group(1)
    if cookie_match:
        cfg["bilibili"]["cookie"] = normalize_cookie(cookie_match.group(1))
    return cfg


# ──────────────────────────────────────────────
# 弹幕预设
# ──────────────────────────────────────────────

PRESET_DANMAKU = [
    "666666", "太强了！", "哈哈哈哈", "前方高能",
    "？？？", "妙啊", "23333", "来了来了",
    "打卡", "第一", "牛啊牛啊", "帅！",
    "这个操作我服", "教练我想学这个", "学到了",
    "基操勿6", "有手就行", "好家伙", "不愧是你",
    "泪目了", "经典", "名场面", "高能预警",
    "不要停下来啊", "这才是真正的玩家", "大佬666",
    "太细了", "丝滑", "无敌", "天秀",
    "啊？", "好闪", "眼花缭乱", "这就没了？",
]

DANMAKU_COLORS = [
    "#FFFFFF", "#FF4444", "#44CCFF", "#FFCC00",
    "#44FF66", "#FF66CC", "#FF9933", "#CC66FF",
    "#00FFCC", "#FF6699",
]


# ──────────────────────────────────────────────
# 弹幕引擎
# ──────────────────────────────────────────────

class DanmakuItem:
    __slots__ = ("text", "color", "font_size", "x", "y",
                 "speed", "canvas_id", "width")

    def __init__(self, text, color, font_size, x, y, speed):
        self.text = text
        self.color = color
        self.font_size = font_size
        self.x = x
        self.y = y
        self.speed = speed
        self.canvas_id = None
        self.width = 0


class DanmakuEngine:
    def __init__(self, canvas, config):
        self.canvas = canvas
        self.cfg = config
        self.items = []
        self.last_auto_time = time.time()
        self._font_cache = {}
        self._ready = False
        self._line_occupied_until = {}  # line_index -> x_pos 该行弹幕右边缘

    def set_ready(self):
        self._ready = True

    def _get_font(self, size):
        if size not in self._font_cache:
            try:
                self._font_cache[size] = tkfont.Font(
                    family="Microsoft YaHei",
                    size=size,
                    weight="bold",
                )
            except Exception:
                self._font_cache[size] = tkfont.Font(
                    size=size,
                    weight="bold",
                )
        return self._font_cache[size]

    def measure_text(self, text, font_size):
        font = self._get_font(font_size)
        return font.measure(text)

    def add_danmaku(self, text=None, color=None, font_size=None):
        if not self._ready:
            return
        if text is None:
            text = random.choice(PRESET_DANMAKU)
        if color is None:
            color = random.choice(DANMAKU_COLORS)
        if font_size is None:
            font_size = self.cfg["danmaku"]["font_size"]

        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        if canvas_w < 100 or canvas_h < 30:
            return

        text_width = self.measure_text(text, font_size)
        line_height = font_size + 10
        max_lines = max(1, int(canvas_h // line_height))

        # 找空闲行（该行上没有正在显示的弹幕）
        line = self._find_free_line(max_lines)
        y = line * line_height + font_size // 2 + 4

        base_speed = self.cfg["danmaku"]["speed"]
        speed = base_speed * (0.85 + random.random() * 0.3)

        item = DanmakuItem(
            text=text, color=color, font_size=font_size,
            x=canvas_w + 5, y=y, speed=speed,
        )
        item.width = text_width
        # 标记该行被占用（弹幕从入屏到出屏所需时间）
        travel_time = (canvas_w + text_width) / speed
        self._line_occupied_until[line] = time.time() + travel_time * 0.8
        self._render_item(item)
        self.items.append(item)

    def _find_free_line(self, max_lines):
        """找到当前空闲的行，避免重叠"""
        now = time.time()
        expired = [ln for ln, until in self._line_occupied_until.items() if now > until]
        for ln in expired:
            del self._line_occupied_until[ln]

        occupied = set(self._line_occupied_until.keys())
        if len(occupied) >= max_lines:
            return random.randint(0, max_lines - 1)

        free_lines = [ln for ln in range(max_lines) if ln not in occupied]
        return random.choice(free_lines) if free_lines else 0

    def _render_item(self, item):
        try:
            font = self._get_font(item.font_size)
            cid = self.canvas.create_text(
                item.x, item.y,
                text=item.text,
                fill=item.color,
                font=font,
                anchor="w",
                state="normal",
            )
            item.canvas_id = cid
        except Exception:
            pass

    def update(self, dt):
        to_remove = []
        for item in self.items:
            item.x -= item.speed * dt
            if item.x + item.width < -10:
                to_remove.append(item)
            elif item.canvas_id is not None:
                try:
                    self.canvas.coords(item.canvas_id, item.x, item.y)
                except Exception:
                    pass
        for item in to_remove:
            self._destroy_item(item)
            if item in self.items:
                self.items.remove(item)

    def _destroy_item(self, item):
        if item.canvas_id is not None:
            try:
                self.canvas.delete(item.canvas_id)
            except Exception:
                pass
            item.canvas_id = None

    def clear_all(self):
        for item in self.items:
            self._destroy_item(item)
        self.items.clear()


# ──────────────────────────────────────────────
# 覆盖窗口
# ──────────────────────────────────────────────

class OverlayWindow:
    def __init__(self, config):
        self.cfg = config
        self.root = tk.Tk()
        self.root.title("DanmakuOverlay")

        wcfg = self.cfg["window"]
        geo = f"{wcfg['width']}x{wcfg['height']}+{wcfg['x']}+{wcfg['y']}"
        self.root.geometry(geo)

        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT_COLOR_HEX)

        self.canvas = tk.Canvas(
            self.root,
            bg=TRANSPARENT_COLOR_HEX,
            highlightthickness=0, bd=0, relief="flat",
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.engine = DanmakuEngine(self.canvas, self.cfg)

        self._hwnd = None
        self._style_hwnds = []
        self._wndproc = None
        self._old_wndprocs = {}
        self._last_frame_time = time.time()
        self._drag_data = {"x": 0, "y": 0, "edge": None, "start_geo": None}
        self._border_rect = None
        self._edit_bg_rect = None

        self.bilibili_client = BilibiliLiveClient(
            on_danmaku=self._on_bili_danmaku,
            on_error=self._on_bili_error,
            on_status=self._on_bili_status,
        )

        self._bind_edit_events()

        # 初始隐藏，等分层属性设置完毕后显示
        self.root.withdraw()
        self.root.update_idletasks()
        self.root.update()
        self._apply_window_attrs()
        self.root.after(16, self._main_loop)

    # ── Win32 窗口管理 ────────────────────────

    def _get_hwnd(self):
        hwnds = self._get_style_hwnds()
        return hwnds[0] if hwnds else None

    def _get_style_hwnds(self):
        if self._style_hwnds:
            return self._style_hwnds
        self.root.update_idletasks()
        self.root.update()
        hwnds = []

        def add_hwnd(value):
            if value and value not in hwnds:
                hwnds.append(value)

        widget_hwnd = self.root.winfo_id()
        if widget_hwnd and widget_hwnd != 0:
            hwnd = user32.GetAncestor(widget_hwnd, GA_ROOT)
            add_hwnd(hwnd)
            add_hwnd(user32.GetParent(widget_hwnd))
            add_hwnd(widget_hwnd)
        hwnd = user32.FindWindowW(None, "DanmakuOverlay")
        add_hwnd(hwnd)

        self._style_hwnds = hwnds
        self._hwnd = hwnds[0] if hwnds else None
        return hwnds

    def _install_nchittest_hook(self):
        if self._wndproc is None:
            wndproc_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t,
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
            )

            def window_proc(hwnd, msg, wparam, lparam):
                if msg == WM_NCHITTEST and self.cfg.get("mode") == "danmaku":
                    return HTTRANSPARENT
                old_proc = self._old_wndprocs.get(hwnd)
                if old_proc:
                    return CallWindowProc(old_proc, hwnd, msg, wparam, lparam)
                return DefWindowProc(hwnd, msg, wparam, lparam)

            self._wndproc = wndproc_type(window_proc)

        proc_ptr = ctypes.cast(self._wndproc, ctypes.c_void_p).value
        for hwnd in self._get_style_hwnds():
            if hwnd in self._old_wndprocs:
                continue
            old_proc = SetWindowLongPtr(hwnd, GWLP_WNDPROC, proc_ptr)
            if old_proc:
                self._old_wndprocs[hwnd] = old_proc

    def _restore_nchittest_hook(self):
        for hwnd, old_proc in list(self._old_wndprocs.items()):
            try:
                SetWindowLongPtr(hwnd, GWLP_WNDPROC, old_proc)
            except Exception:
                pass
        self._old_wndprocs.clear()

    def _refresh_win(self, hwnd):
        """强制窗口样式刷新"""
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        user32.RedrawWindow(hwnd, None, None, RDW_FRAME | RDW_INVALIDATE | RDW_UPDATENOW)

    def _apply_window_attrs(self):
        hwnd = self._get_hwnd()
        if not hwnd:
            self.root.after(200, self._apply_window_attrs)
            return

        # Tk 在 Windows 下会创建顶层窗口和内部子窗口，样式必须同步应用。
        for target_hwnd in self._get_style_hwnds():
            ex_style = user32.GetWindowLongW(target_hwnd, GWL_EXSTYLE)
            ex_style |= WS_EX_LAYERED | WS_EX_TOPMOST
            user32.SetWindowLongW(target_hwnd, GWL_EXSTYLE, ex_style)
        self._install_nchittest_hook()

        self.engine.set_ready()

        self.set_mode(self.cfg.get("mode", "edit"))
        self.root.deiconify()

    def set_mode(self, mode):
        """edit=实色幕布+可拖拽, danmaku=透明幕布+穿透"""
        self.cfg["mode"] = mode

        if mode == "danmaku":
            # 透明幕布 + 鼠标穿透
            self.canvas.configure(bg=TRANSPARENT_COLOR_HEX)
            self.root.configure(bg=TRANSPARENT_COLOR_HEX)
            for target_hwnd in self._get_style_hwnds():
                style = user32.GetWindowLongW(target_hwnd, GWL_EXSTYLE)
                style |= WS_EX_LAYERED | WS_EX_TOPMOST
                style |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
                user32.SetWindowLongW(target_hwnd, GWL_EXSTYLE, style)
                opacity = self.cfg["danmaku"]["opacity"]
                user32.SetLayeredWindowAttributes(
                    target_hwnd, COLORREF_TRANSPARENT, int(opacity * 255),
                    LWA_COLORKEY | LWA_ALPHA,
                )
                self._refresh_win(target_hwnd)
            self._hide_border()
            self._hide_edit_bg()
            self.canvas.configure(cursor="")
        else:
            # 实色幕布 + 正常鼠标事件
            self.canvas.configure(bg="#1a1a1a")
            self.root.configure(bg="#1a1a1a")
            for target_hwnd in self._get_style_hwnds():
                style = user32.GetWindowLongW(target_hwnd, GWL_EXSTYLE)
                style |= WS_EX_LAYERED | WS_EX_TOPMOST
                style &= ~WS_EX_TRANSPARENT
                style &= ~WS_EX_NOACTIVATE
                user32.SetWindowLongW(target_hwnd, GWL_EXSTYLE, style)
                user32.SetLayeredWindowAttributes(target_hwnd, 0, 255, LWA_ALPHA)
                self._refresh_win(target_hwnd)
            self._show_border()
            self._show_edit_bg()
            self.canvas.configure(cursor="fleur")

        save_config(self.cfg)

    def toggle_mode(self):
        new_mode = "edit" if self.cfg["mode"] == "danmaku" else "danmaku"
        self.set_mode(new_mode)

    def set_opacity(self, value):
        self.cfg["danmaku"]["opacity"] = max(0.1, min(1.0, value))
        self.set_mode(self.cfg["mode"])
        save_config(self.cfg)

    # ── 编辑模式：背景/边框 ──────────────────

    def _show_edit_bg(self):
        if self._edit_bg_rect is not None:
            return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        self._edit_bg_rect = self.canvas.create_rectangle(
            0, 0, w, h,
            fill="#1a1a1a",
            outline="",
            stipple="gray25",
        )
        self.canvas.tag_lower(self._edit_bg_rect)

    def _hide_edit_bg(self):
        if self._edit_bg_rect is not None:
            try:
                self.canvas.delete(self._edit_bg_rect)
            except Exception:
                pass
            self._edit_bg_rect = None

    def _show_border(self):
        if self._border_rect is not None:
            return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        self._border_rect = self.canvas.create_rectangle(
            0, 0, w - 1, h - 1,
            outline="#00FF00",
            width=2,
            dash=(10, 4),
        )

    def _hide_border(self):
        if self._border_rect is not None:
            try:
                self.canvas.delete(self._border_rect)
            except Exception:
                pass
            self._border_rect = None

    def _refresh_border(self):
        self._hide_border()
        self._show_border()
        self._hide_edit_bg()
        self._show_edit_bg()

    # ── 编辑模式：鼠标交互 ───────────────────

    EDGE_SIZE = 12

    def _bind_edit_events(self):
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<Motion>", self._on_move)
        self.canvas.tag_bind("all", "<ButtonPress-1>", self._on_press)
        self.canvas.tag_bind("all", "<ButtonRelease-1>", self._on_release)
        self.canvas.tag_bind("all", "<B1-Motion>", self._on_motion)
        self.canvas.tag_bind("all", "<Motion>", self._on_move)
        # 绑定到 root，避免 canvas item 命中时事件丢失
        self.root.bind("<ButtonPress-1>", self._on_press)
        self.root.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<B1-Motion>", self._on_motion)
        self.root.bind("<Motion>", self._on_move)

    def _get_edge(self, x, y):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        s = self.EDGE_SIZE
        left, right = x <= s, x >= w - s
        top, bottom = y <= s, y >= h - s
        if left and top:
            return "nw"
        if right and top:
            return "ne"
        if left and bottom:
            return "sw"
        if right and bottom:
            return "se"
        if left:
            return "w"
        if right:
            return "e"
        if top:
            return "n"
        if bottom:
            return "s"
        return None

    def _on_move(self, event):
        if self.cfg["mode"] != "edit":
            return
        edge = self._get_edge(event.x, event.y)
        cursors = {
            "nw": "size_nw_se", "se": "size_nw_se",
            "ne": "size_ne_sw", "sw": "size_ne_sw",
            "n": "size_ns", "s": "size_ns",
            "w": "size_we", "e": "size_we",
        }
        self.canvas.configure(cursor=cursors.get(edge, "fleur"))

    def _on_press(self, event):
        if self.cfg["mode"] != "edit":
            return
        edge = self._get_edge(event.x, event.y)
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root
        self._drag_data["edge"] = edge
        self._drag_data["start_geo"] = (
            self._get_geometry() if edge else None
        )

    def _on_release(self, event):
        if self.cfg["mode"] != "edit":
            return
        self._drag_data["edge"] = None
        self._drag_data["start_geo"] = None
        self._save_window_config()

    def _on_motion(self, event):
        if self.cfg["mode"] != "edit":
            return
        dx = event.x_root - self._drag_data["x"]
        dy = event.y_root - self._drag_data["y"]
        edge = self._drag_data["edge"]

        if edge and self._drag_data["start_geo"]:
            ox, oy, ow, oh = self._drag_data["start_geo"]
            min_w, min_h = 200, 60

            if "e" in edge:
                ow = max(min_w, ow + dx)
            if "w" in edge:
                new_w = max(min_w, ow - dx)
                ox = ox + (ow - new_w)
                ow = new_w
            if "s" in edge:
                oh = max(min_h, oh + dy)
            if "n" in edge:
                new_h = max(min_h, oh - dy)
                oy = oy + (oh - new_h)
                oh = new_h

            self.root.geometry(f"{ow}x{oh}+{ox}+{oy}")
            self._refresh_border()
        else:
            x = self.root.winfo_x() + dx
            y = self.root.winfo_y() + dy
            self.root.geometry(f"+{x}+{y}")
            self._drag_data["x"] = event.x_root
            self._drag_data["y"] = event.y_root

    def _get_geometry(self):
        return (
            self.root.winfo_x(),
            self.root.winfo_y(),
            self.root.winfo_width(),
            self.root.winfo_height(),
        )

    def _save_window_config(self):
        g = self._get_geometry()
        self.cfg["window"]["x"] = g[0]
        self.cfg["window"]["y"] = g[1]
        self.cfg["window"]["width"] = g[2]
        self.cfg["window"]["height"] = g[3]
        save_config(self.cfg)

    # ── B站直播回调 ──────────────────────────

    def _on_bili_danmaku(self, text, username, color, user_id=""):
        display_text = f"{username}: {text}" if username else text
        if self._is_blocked_danmaku(text, display_text, user_id):
            return
        self.root.after(0, lambda: self.engine.add_danmaku(text=display_text, color=color))

    def _is_blocked_danmaku(self, text, display_text, user_id=""):
        danmaku_cfg = self.cfg.get("danmaku", {})
        blocked_user_ids = danmaku_cfg.get("blocked_user_ids", [])
        if user_id and str(user_id) in blocked_user_ids:
            return True

        keywords = danmaku_cfg.get("blocked_keywords", [])
        if not keywords:
            return False
        target = f"{text}\n{display_text}".lower()
        return any(keyword.lower() in target for keyword in keywords)

    def _on_bili_error(self, error):
        print(f"[Bili Error] {error}")

    def _on_bili_status(self, status):
        print(f"[Bili] {status}")

    # ── 主循环 ────────────────────────────────

    def _main_loop(self):
        try:
            now = time.time()
            dt = now - self._last_frame_time
            self._last_frame_time = now
            if dt > 0.1:
                dt = 0.05
            self.engine.update(dt)
        except Exception:
            pass
        self.root.after(16, self._main_loop)

    def run(self):
        self.root.mainloop()

    def destroy(self):
        try:
            self._restore_nchittest_hook()
            self.bilibili_client.disconnect()
            self.root.destroy()
        except Exception:
            pass


# ──────────────────────────────────────────────
# 系统托盘
# ──────────────────────────────────────────────

def create_tray_icon(overlay, config):
    try:
        from PIL import Image, ImageDraw
        import pystray
    except ImportError:
        return None

    icon_img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_img)
    try:
        draw.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(0, 170, 255, 255))
    except AttributeError:
        draw.ellipse([4, 4, 60, 60], fill=(0, 170, 255, 255))
    draw.text((16, 14), "D", fill=(255, 255, 255, 255))

    state = {"overlay": overlay, "config": config}

    def _tk_call(func):
        overlay.root.after(0, func)

    def on_toggle(icon, item):
        _tk_call(overlay.toggle_mode)

    def _set_mode(mode):
        def _f():
            _tk_call(lambda: overlay.set_mode(mode))
        return _f

    def on_opacity(v):
        def _f():
            _tk_call(lambda: overlay.set_opacity(v))
        return _f

    def on_speed(s):
        def _f():
            _tk_call(lambda s_=s: _apply_speed(s_))
        return _f

    def _apply_speed(s):
        config["danmaku"]["speed"] = s
        save_config(config)

    def on_font_size(s):
        def _f():
            _tk_call(lambda s_=s: _apply_font_size(s_))
        return _f

    def _apply_font_size(s):
        config["danmaku"]["font_size"] = s
        save_config(config)

    def on_bili_connect(icon, item):
        _tk_call(_show_bili_dialog)

    def on_block_keywords(icon, item):
        _tk_call(_show_block_keywords_dialog)

    def on_block_user_ids(icon, item):
        _tk_call(_show_block_user_ids_dialog)

    _bili_connected = [False]

    def _show_block_keywords_dialog():
        dialog = tk.Toplevel(overlay.root)
        dialog.title("弹幕屏蔽关键字")
        dialog.geometry("360x300")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        try:
            dialog.transient(overlay.root)
        except Exception:
            pass

        tk.Label(dialog, text="每行一个关键字，也可用逗号/分号分隔:").pack(pady=(10, 4))
        text_box = tk.Text(dialog, width=42, height=10)
        text_box.pack(padx=10, pady=4)
        keywords = config.get("danmaku", {}).get("blocked_keywords", [])
        text_box.insert("1.0", "\n".join(keywords))

        status_label = tk.Label(dialog, text="", fg="gray")
        status_label.pack()

        def on_save():
            raw = text_box.get("1.0", tk.END)
            keywords = normalize_blocked_keywords(raw)
            config.setdefault("danmaku", {})["blocked_keywords"] = keywords
            overlay.cfg.setdefault("danmaku", {})["blocked_keywords"] = keywords
            save_config(config)
            status_label.configure(text=f"已保存 {len(keywords)} 个屏蔽词", fg="#33AA33")

        def on_clear():
            text_box.delete("1.0", tk.END)
            on_save()

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="保存", command=on_save, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="清空", command=on_clear, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="关闭", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def _show_block_user_ids_dialog():
        dialog = tk.Toplevel(overlay.root)
        dialog.title("ID弹幕屏蔽")
        dialog.geometry("360x300")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        try:
            dialog.transient(overlay.root)
        except Exception:
            pass

        tk.Label(dialog, text="每行一个用户ID，也可用逗号/分号分隔:").pack(pady=(10, 4))
        text_box = tk.Text(dialog, width=42, height=10)
        text_box.pack(padx=10, pady=4)
        user_ids = config.get("danmaku", {}).get("blocked_user_ids", [])
        text_box.insert("1.0", "\n".join(user_ids))

        status_label = tk.Label(dialog, text="", fg="gray")
        status_label.pack()

        def on_save():
            user_ids = normalize_blocked_user_ids(text_box.get("1.0", tk.END))
            config.setdefault("danmaku", {})["blocked_user_ids"] = user_ids
            overlay.cfg.setdefault("danmaku", {})["blocked_user_ids"] = user_ids
            save_config(config)
            status_label.configure(text=f"已保存 {len(user_ids)} 个屏蔽ID", fg="#33AA33")

        def on_clear():
            text_box.delete("1.0", tk.END)
            on_save()

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="保存", command=on_save, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="清空", command=on_clear, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="关闭", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def _show_bili_dialog():
        dialog = tk.Toplevel(overlay.root)
        dialog.title("B站直播间")
        dialog.geometry("360x200")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        try:
            dialog.transient(overlay.root)
        except Exception:
            pass

        tk.Label(dialog, text="直播间房间号:").pack(pady=(6, 0))
        f = tk.Frame(dialog)
        f.pack(pady=3)
        saved_room = config.get("bilibili", {}).get("room_id", "")
        entry = tk.Entry(f, width=22)
        entry.pack(side=tk.LEFT, padx=(0, 5))
        if saved_room:
            entry.insert(0, str(saved_room))

        tk.Label(dialog, text="Cookie (选填，填了显示完整昵称):").pack(pady=(2, 0))
        f2 = tk.Frame(dialog)
        f2.pack(pady=3)
        saved_cookie = config.get("bilibili", {}).get("cookie", "")
        cookie_entry = tk.Entry(f2, width=22, show="*")
        cookie_entry.pack(side=tk.LEFT, padx=(0, 5))
        if saved_cookie:
            cookie_entry.insert(0, saved_cookie)

        status_label = tk.Label(dialog, text="", fg="gray")
        status_label.pack()

        def on_toggle():
            if overlay.bilibili_client.is_connected:
                overlay.bilibili_client.disconnect()
            else:
                raw = entry.get().strip()
                if not raw:
                    status_label.configure(text="请输入房间号", fg="red")
                    return
                try:
                    room_id = int(raw)
                except ValueError:
                    try:
                        room_id = int(raw.split("/")[-1])
                    except Exception:
                        status_label.configure(text="房间号格式错误", fg="red")
                        return
                cookie = normalize_cookie(cookie_entry.get())
                config["bilibili"]["room_id"] = room_id
                config["bilibili"]["cookie"] = cookie
                save_config(config)
                overlay.bilibili_client.connect(room_id, cookie=cookie)
            overlay.root.after(300, update_status)

        btn = tk.Button(
            dialog, text="连接" if not overlay.bilibili_client.is_connected else "断开",
            command=on_toggle, width=8,
        )
        btn.pack(pady=4)

        def _poll_status():
            if dialog.winfo_exists():
                if overlay.bilibili_client.is_connected:
                    btn.configure(text="断开")
                    status_label.configure(text="已连接", fg="#33FF66")
                else:
                    btn.configure(text="连接")
                    status_label.configure(text="未连接", fg="gray")
                dialog.after(1000, _poll_status)

        dialog.after(500, _poll_status)

    def on_exit(icon, item):
        icon.stop()
        overlay.destroy()
        os._exit(0)

    def _checked_opacity(v):
        return abs(config["danmaku"]["opacity"] - v) < 0.01

    def _checked_speed(s):
        return config["danmaku"]["speed"] == s

    def _checked_font_size(s):
        return config["danmaku"]["font_size"] == s

    menu = pystray.Menu(
        pystray.MenuItem("弹幕透明度", pystray.Menu(
            *[pystray.MenuItem(
                f"{int(v * 100)}%",
                on_opacity(v),
                checked=lambda item, v=v: _checked_opacity(v),
                radio=True,
            ) for v in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 1.0]]
        )),
        pystray.MenuItem("弹幕速度", pystray.Menu(
            *[pystray.MenuItem(
                label,
                on_speed(s),
                checked=lambda item, s=s: _checked_speed(s),
                radio=True,
            ) for s, label in [(100, "慢"), (200, "中"), (350, "快"), (500, "极快")]]
        )),
        pystray.MenuItem("字体大小", pystray.Menu(
            *[pystray.MenuItem(
                f"{s}px",
                on_font_size(s),
                checked=lambda item, s=s: _checked_font_size(s),
                radio=True,
            ) for s in [14, 16, 18, 20, 22, 24, 26, 28, 32, 36, 40, 48, 56]]
        )),
        pystray.MenuItem("弹幕屏蔽关键字...", on_block_keywords),
        pystray.MenuItem("ID弹幕屏蔽...", on_block_user_ids),
        pystray.MenuItem("连接B站直播间...", on_bili_connect),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("切换 编辑/穿透 模式", on_toggle),
        pystray.MenuItem("→ 穿透模式 (默认)", _set_mode("danmaku")),
        pystray.MenuItem("→ 编辑模式 (调整大小/位置)", _set_mode("edit")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_exit),
    )

    icon = pystray.Icon("danmaku_overlay", icon_img, "B站弹幕机", menu)
    return icon


# ──────────────────────────────────────────────
# 主程序入口
# ──────────────────────────────────────────────

def main():
    config = load_config()
    save_config(config)

    overlay = OverlayWindow(config)

    tray_icon = create_tray_icon(overlay, config)

    if tray_icon:
        tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
        tray_thread.start()
    else:
        print("[提示] pystray/Pillow 未安装，系统托盘不可用")
        print("[提示] 右键任务栏关闭，或 Ctrl+C 退出")

    # 自动连接 B站（如果有保存的 cookie）
    bili_cfg = config.get("bilibili", {})
    saved_room = bili_cfg.get("room_id", 0)
    saved_cookie = bili_cfg.get("cookie", "")
    if saved_room and saved_cookie:
        print(f"[INFO] 配置中有保存的 Cookie，自动连接房间 {saved_room}")
        overlay.root.after(2000, lambda: overlay.bilibili_client.connect(saved_room, cookie=saved_cookie))

    try:
        overlay.run()
    except KeyboardInterrupt:
        pass
    finally:
        if tray_icon:
            try:
                tray_icon.stop()
            except Exception:
                pass
        overlay.destroy()


if __name__ == "__main__":
    main()
