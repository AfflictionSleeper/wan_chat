"""
B站风格弹幕覆盖层 - Linux/WSL 兼容版本
用于在 WSL/Linux 环境中调试弹幕显示效果
完整穿透功能请使用 Windows 版本 (main.py)
"""

import json
import html
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
# 平台检测
# ──────────────────────────────────────────────

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

# ──────────────────────────────────────────────
# X11 透明/穿透支持 (Linux only)
# ──────────────────────────────────────────────

_x11_xshape_available = False
_x11_display = None
_x11_shape_input = 2   # ShapeInput
_x11_shape_set = 0     # ShapeSet

if IS_LINUX:
    try:
        import ctypes
        _xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
        _xlib.XOpenDisplay.restype = ctypes.c_void_p
        _xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]

        _display = _xlib.XOpenDisplay(None)
        if _display:
            try:
                _xext = ctypes.cdll.LoadLibrary("libXext.so.6")
                _xext.XShapeQueryExtension.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_int),
                ]
                _xext.XShapeQueryExtension.restype = ctypes.c_int

                _xext.XShapeCombineRectangles.argtypes = [
                    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int,
                    ctypes.c_void_p, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int,
                ]

                event_base = ctypes.c_int()
                error_base = ctypes.c_int()
                if _xext.XShapeQueryExtension(
                    _display,
                    ctypes.byref(event_base),
                    ctypes.byref(error_base),
                ):
                    _x11_xshape_available = True
                    _x11_display = _display
            except Exception:
                pass
    except Exception:
        pass


def x11_set_input_rect(window_id, rect_tuple):
    """设置 X11 窗口的输入区域 (x, y, w, h) 或 None 表示穿透"""
    if not _x11_xshape_available or not _x11_display:
        return
    try:
        if rect_tuple is None:
            _xext.XShapeCombineRectangles(
                _x11_display, window_id, _x11_shape_input,
                0, 0, None, 0,
                _x11_shape_set, 0,  # YXBanded
            )
        else:
            x, y, w, h = rect_tuple
            rect_type = ctypes.c_int * 4
            rect = rect_type(x, y, w, h)
            _xext.XShapeCombineRectangles(
                _x11_display, window_id, _x11_shape_input,
                0, 0, rect, 1,
                _x11_shape_set, 0,
            )
    except Exception:
        pass


# ──────────────────────────────────────────────
# Windows API (仅 Windows 平台)
# ──────────────────────────────────────────────

if IS_WINDOWS:
    import ctypes as _ctypes
    import ctypes.wintypes as _wintypes

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOPMOST = 0x00000008
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    HWND_TOPMOST = -1
    LWA_COLORKEY = 0x00000001
    LWA_ALPHA = 0x00000002

    _user32 = _ctypes.windll.user32
    _kernel32 = _ctypes.windll.kernel32

    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass


def win_set_layered(hwnd, color_ref, alpha_byte):
    if not IS_WINDOWS:
        return
    ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex_style |= WS_EX_LAYERED | WS_EX_TOPMOST
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)
    flags = LWA_COLORKEY | LWA_ALPHA
    _user32.SetLayeredWindowAttributes(hwnd, color_ref, alpha_byte, flags)
    _user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )


def win_set_click_through(hwnd, enable):
    if not IS_WINDOWS:
        return
    ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enable:
        ex_style |= WS_EX_TRANSPARENT
    else:
        ex_style &= ~WS_EX_TRANSPARENT
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)

# ──────────────────────────────────────────────
# 配置管理
# ──────────────────────────────────────────────

DEFAULT_CONFIG = {
    "window": {"x": 200, "y": 100, "width": 800, "height": 400},
    "danmaku": {
        "font_size": 26,
        "speed": 200,
        "opacity": 0.85,
        "auto_interval": 3.0,
        "auto_enabled": True,
        "blocked_keywords": [],
        "blocked_user_ids": [],
    },
    "bilibili": {"room_id": 0, "cookie": ""},
    "mode": "edit",
}

TRANSPARENT_COLOR_HEX = "#010101"
COLORREF_TRANSPARENT = 0x00010101
LINUX_BG_COLOR = "#1a1a1a"  # Linux 下无透明色键时的背景色


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for section, values in DEFAULT_CONFIG.items():
                if section not in cfg:
                    cfg[section] = {}
                if isinstance(values, dict):
                    for k, v in values.items():
                        if k not in cfg[section]:
                            cfg[section][k] = v
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


def is_blocked_danmaku(config, text, display_text, user_id=""):
    danmaku_cfg = config.get("danmaku", {})
    blocked_user_ids = danmaku_cfg.get("blocked_user_ids", [])
    if user_id and str(user_id) in blocked_user_ids:
        return True

    keywords = danmaku_cfg.get("blocked_keywords", [])
    if not keywords:
        return False
    target = f"{text}\n{display_text}".lower()
    return any(keyword.lower() in target for keyword in keywords)


# ──────────────────────────────────────────────
# 弹幕预设
# ──────────────────────────────────────────────

PRESET_DANMAKU = [
    "666666", "太强了！", "哈哈哈哈", "前方高能",
    "???", "妙啊", "23333", "来了来了",
    "打卡", "第一", "牛啊牛啊", "帅！",
    "这个操作我服", "教练我想学这个", "学到了",
    "基操勿6", "有手就行", "好家伙", "不愧是你",
    "泪目了", "经典", "名场面", "高能预警",
    "不要停下来啊", "这才是真正的玩家", "大佬666",
    "太细了", "丝滑", "无敌", "天秀",
    "啊?", "好闪", "眼花缭乱", "这就没了?",
]

DANMAKU_COLORS = [
    "#FFFFFF", "#FF4444", "#44CCFF", "#FFCC00",
    "#44FF66", "#FF66CC", "#FF9933", "#CC66FF",
    "#00FFCC", "#FF6699",
]

HTML_COLOR_NAMES = {
    "black": "#000000", "white": "#FFFFFF", "red": "#FF0000",
    "green": "#008000", "blue": "#0000FF", "yellow": "#FFFF00",
    "cyan": "#00FFFF", "aqua": "#00FFFF", "magenta": "#FF00FF",
    "fuchsia": "#FF00FF", "orange": "#FFA500", "purple": "#800080",
    "pink": "#FFC0CB", "gray": "#808080", "grey": "#808080",
}


def parse_html_font_tag(text, fallback_color, fallback_size):
    if not isinstance(text, str) or "<" not in text:
        return text, fallback_color, fallback_size

    color = fallback_color
    font_size = fallback_size
    match = re.search(r"<font\b([^>]*)>", text, re.IGNORECASE)
    if match:
        attrs = _parse_html_attrs(match.group(1))
        if "color" in attrs:
            color = _normalize_html_color(attrs["color"], color)
        if "size" in attrs:
            font_size = _normalize_html_font_size(attrs["size"], font_size)
        if "style" in attrs:
            style = attrs["style"]
            style_color = re.search(r"color\s*:\s*([^;]+)", style, re.IGNORECASE)
            if style_color:
                color = _normalize_html_color(style_color.group(1), color)
            style_size = re.search(r"font-size\s*:\s*([^;]+)", style, re.IGNORECASE)
            if style_size:
                font_size = _normalize_html_font_size(style_size.group(1), font_size)

    clean_text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    return clean_text or text, color, font_size


def _parse_html_attrs(raw_attrs):
    attrs = {}
    pattern = r"([a-zA-Z_:][-a-zA-Z0-9_:]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)"
    for key, value in re.findall(pattern, raw_attrs):
        attrs[key.lower()] = value.strip().strip("'\"")
    return attrs


def _normalize_html_color(value, fallback):
    value = str(value).strip().lower()
    if value in HTML_COLOR_NAMES:
        return HTML_COLOR_NAMES[value]
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        return "#" + "".join(ch * 2 for ch in value[1:]).upper()
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return value.upper()
    return fallback


def _normalize_html_font_size(value, fallback):
    match = re.search(r"\d+", str(value))
    if not match:
        return fallback
    return max(8, min(96, int(match.group(0))))


# ──────────────────────────────────────────────
# 弹幕引擎
# ──────────────────────────────────────────────

class DanmakuItem:
    __slots__ = ("text", "color", "font_size", "x", "y",
                 "speed", "canvas_id", "outline_ids", "width")

    def __init__(self, text, color, font_size, x, y, speed):
        self.text = text
        self.color = color
        self.font_size = font_size
        self.x = x
        self.y = y
        self.speed = speed
        self.canvas_id = None
        self.outline_ids = []
        self.width = 0


class DanmakuEngine:
    def __init__(self, canvas, config):
        self.canvas = canvas
        self.cfg = config
        self.items = []
        self.last_auto_time = time.time()
        self._font_cache = {}
        self._ready = False
        self._line_occupied_until = {}

    def set_ready(self):
        self._ready = True

    def _get_font(self, size):
        if size not in self._font_cache:
            try:
                self._font_cache[size] = tkfont.Font(
                    family="Microsoft YaHei",
                    size=size, weight="bold",
                )
            except Exception:
                try:
                    self._font_cache[size] = tkfont.Font(
                        family="WenQuanYi Micro Hei",
                        size=size, weight="bold",
                    )
                except Exception:
                    self._font_cache[size] = tkfont.Font(
                        size=size, weight="bold",
                    )
        return self._font_cache[size]

    def measure_text(self, text, font_size):
        return self._get_font(font_size).measure(text)

    def add_danmaku(self, text=None, color=None, font_size=None):
        if not self._ready:
            return
        if text is None:
            text = random.choice(PRESET_DANMAKU)
        if color is None:
            color = random.choice(DANMAKU_COLORS)
        if font_size is None:
            font_size = self.cfg["danmaku"]["font_size"]
        text, color, font_size = parse_html_font_tag(text, color, font_size)

        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        if canvas_w < 100 or canvas_h < 30:
            return

        text_width = self.measure_text(text, font_size)
        line_height = font_size + 10
        max_lines = max(1, int(canvas_h // line_height))
        line = self._find_free_line(max_lines)
        y = line * line_height + font_size // 2 + 4

        base_speed = self.cfg["danmaku"]["speed"]
        speed = base_speed * (0.85 + random.random() * 0.3)

        item = DanmakuItem(
            text=text, color=color, font_size=font_size,
            x=canvas_w + 5, y=y, speed=speed,
        )
        item.width = text_width
        travel_time = (canvas_w + text_width) / speed
        self._line_occupied_until[line] = time.time() + travel_time * 0.8
        self._render_item(item)
        self.items.append(item)

    def _find_free_line(self, max_lines):
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
            for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                oid = self.canvas.create_text(
                    item.x + ox, item.y + oy, text=item.text,
                    fill="#000000", font=font,
                    anchor="w", state="normal",
                )
                item.outline_ids.append(oid)
            cid = self.canvas.create_text(
                item.x, item.y, text=item.text,
                fill=item.color, font=font,
                anchor="w", state="normal",
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
                    for oid, (ox, oy) in zip(item.outline_ids, [(-1, 0), (1, 0), (0, -1), (0, 1)]):
                        self.canvas.coords(oid, item.x + ox, item.y + oy)
                    self.canvas.coords(item.canvas_id, item.x, item.y)
                except Exception:
                    pass
        for item in to_remove:
            self._destroy_item(item)
            if item in self.items:
                self.items.remove(item)

    def _destroy_item(self, item):
        for oid in item.outline_ids:
            try:
                self.canvas.delete(oid)
            except Exception:
                pass
        item.outline_ids = []
        if item.canvas_id is not None:
            try:
                self.canvas.delete(item.canvas_id)
            except Exception:
                pass
            item.canvas_id = None

    def auto_tick(self):
        if not self.cfg.get("danmaku", {}).get("auto_enabled", True):
            return
        if not self._ready:
            return
        now = time.time()
        interval = self.cfg["danmaku"]["auto_interval"]
        if now - self.last_auto_time >= interval:
            self.add_danmaku()
            self.last_auto_time = now

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

        if IS_WINDOWS:
            self.root.configure(bg=TRANSPARENT_COLOR_HEX)
            canvas_bg = TRANSPARENT_COLOR_HEX
            self._linux_transparent = False
        else:
            try:
                self.root.wm_attributes("-transparentcolor", TRANSPARENT_COLOR_HEX)
                self.root.configure(bg=TRANSPARENT_COLOR_HEX)
                canvas_bg = TRANSPARENT_COLOR_HEX
                self._linux_transparent = True
                print("[INFO] 透明色键可用，背景将完全透明")
            except tk.TclError:
                self.root.configure(bg=LINUX_BG_COLOR)
                canvas_bg = LINUX_BG_COLOR
                self._linux_transparent = False
                print("[INFO] 透明色键不可用 (WSLg/Wayland 限制)，使用半透明背景")

        self.canvas = tk.Canvas(
            self.root,
            bg=canvas_bg,
            highlightthickness=0, bd=0, relief="flat",
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.engine = DanmakuEngine(self.canvas, self.cfg)

        self._last_frame_time = time.time()
        self._drag_data = {"x": 0, "y": 0, "edge": None, "start_geo": None}
        self._border_rect = None
        self._edit_bg_rect = None

        self._bind_edit_events()

        self.root.after(200, self._apply_window_attrs)
        self.root.after(16, self._main_loop)

    # ── HWND / Window ID ──────────────────────

    def _get_window_id(self):
        self.root.update_idletasks()
        self.root.update()
        return self.root.winfo_id()

    def _get_hwnd(self):
        """Windows: 获取 HWND"""
        if not IS_WINDOWS:
            return None
        wid = self._get_window_id()
        if wid:
            return wid
        try:
            import ctypes
            return ctypes.windll.user32.FindWindowW(None, "DanmakuOverlay")
        except Exception:
            return None

    # ── 窗口属性 ──────────────────────────────

    def _apply_window_attrs(self):
        if IS_WINDOWS:
            hwnd = self._get_hwnd()
            if not hwnd:
                self.root.after(300, self._apply_window_attrs)
                return
            self._update_layered_attrs()
            self.set_mode(self.cfg.get("mode", "danmaku"))
        else:
            self._update_layered_attrs()
            self.set_mode(self.cfg.get("mode", "danmaku"))

        self.engine.set_ready()

    def _update_layered_attrs(self):
        opacity = self.cfg["danmaku"]["opacity"]
        alpha_byte = int(opacity * 255)

        if IS_WINDOWS:
            hwnd = self._get_hwnd()
            if hwnd:
                win_set_layered(hwnd, COLORREF_TRANSPARENT, alpha_byte)
        else:
            self.root.wm_attributes("-alpha", opacity)

    def _set_click_through(self, enable):
        if IS_WINDOWS:
            hwnd = self._get_hwnd()
            if hwnd:
                win_set_click_through(hwnd, enable)
        elif IS_LINUX and _x11_xshape_available:
            wid = self._get_window_id()
            if wid:
                if enable:
                    x11_set_input_rect(wid, None)
                else:
                    w = self.root.winfo_width()
                    h = self.root.winfo_height()
                    x11_set_input_rect(wid, (0, 0, w, h))

    # ── 模式切换 ──────────────────────────────

    def set_mode(self, mode):
        self.cfg["mode"] = mode
        if mode == "danmaku":
            self._set_click_through(True)
            self._hide_border()
            self._hide_edit_bg()
            self.canvas.configure(cursor="")
        else:
            self._set_click_through(False)
            self._show_border()
            self._show_edit_bg()
            self.canvas.configure(cursor="fleur")
        save_config(self.cfg)

    def toggle_mode(self):
        new_mode = "edit" if self.cfg["mode"] == "danmaku" else "danmaku"
        self.set_mode(new_mode)

    def set_opacity(self, value):
        self.cfg["danmaku"]["opacity"] = max(0.1, min(1.0, value))
        self._update_layered_attrs()
        save_config(self.cfg)

    # ── 编辑模式背景/边框 ────────────────────

    def _show_edit_bg(self):
        if self._edit_bg_rect is not None:
            return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        self._edit_bg_rect = self.canvas.create_rectangle(
            0, 0, w, h, fill="#1a1a1a", outline="", stipple="gray25",
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
            outline="#00FF00", width=2, dash=(10, 4),
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

    EDGE_SIZE = 8

    def _bind_edit_events(self):
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<Motion>", self._on_move)

    def _get_edge(self, x, y):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        s = self.EDGE_SIZE
        left, right = x < s, x > w - s
        top, bottom = y < s, y > h - s
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
        self._drag_data["start_geo"] = self._get_geometry() if edge else None

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
            if self.cfg["mode"] == "edit" and IS_LINUX and _x11_xshape_available:
                x11_set_input_rect(self._get_window_id(), (0, 0, ow, oh))
        else:
            x = self.root.winfo_x() + dx
            y = self.root.winfo_y() + dy
            self.root.geometry(f"+{x}+{y}")
            self._drag_data["x"] = event.x_root
            self._drag_data["y"] = event.y_root

    def _get_geometry(self):
        return (
            self.root.winfo_x(), self.root.winfo_y(),
            self.root.winfo_width(), self.root.winfo_height(),
        )

    def _save_window_config(self):
        g = self._get_geometry()
        self.cfg["window"]["x"] = g[0]
        self.cfg["window"]["y"] = g[1]
        self.cfg["window"]["width"] = g[2]
        self.cfg["window"]["height"] = g[3]
        save_config(self.cfg)

    # ── 主循环 ────────────────────────────────

    def _main_loop(self):
        try:
            now = time.time()
            dt = now - self._last_frame_time
            self._last_frame_time = now
            if dt > 0.1:
                dt = 0.05
            self.engine.auto_tick()
            self.engine.update(dt)
        except Exception:
            pass
        self.root.after(16, self._main_loop)

    def run(self):
        self.root.mainloop()

    def destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass


# ──────────────────────────────────────────────
# 控制面板 (Linux/WSL 调试用)
# ──────────────────────────────────────────────

class ControlPanel:
    """Linux/WSL 下的简单控制面板，代替系统托盘"""

    def __init__(self, overlay, config):
        self.overlay = overlay
        self.config = config
        self.bilibili_client = BilibiliLiveClient(
            on_danmaku=self._on_bili_danmaku,
            on_error=self._on_bili_error,
            on_status=self._on_bili_status,
        )
        self.win = tk.Toplevel(overlay.root)
        self.win.title("弹幕机控制面板")
        self.win.geometry("360x650")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()

    def _build_ui(self):
        p = self.win
        pad = {"padx": 10, "pady": 4}

        # 模式
        tk.Label(p, text="━━ 模式控制 ━━", font=("", 10, "bold")).pack(**pad)
        f_mode = tk.Frame(p)
        f_mode.pack(fill=tk.X, **pad)

        self._mode_var = tk.StringVar(
            value="穿透 (穿透)" if self.config["mode"] == "danmaku" else "编辑 (编辑)"
        )
        tk.Button(f_mode, text="切换编辑/穿透模式", command=self._toggle_mode,
                  bg="#4a90d9", fg="white", width=28).pack()

        mode_label_text = (
            "当前: 穿透模式 (鼠标穿透，弹幕可见)"
            if self.config["mode"] == "danmaku"
            else "当前: 编辑模式 (可拖动调整，边框可见)"
        )
        self._mode_label = tk.Label(p, text=mode_label_text, fg="gray")
        self._mode_label.pack()

        # 透明度
        tk.Label(p, text="━━ 弹幕透明度 ━━", font=("", 10, "bold")).pack(**pad)
        f_op = tk.Frame(p)
        f_op.pack(fill=tk.X, **pad)
        self._op_var = tk.DoubleVar(value=self.config["danmaku"]["opacity"])
        tk.Scale(f_op, from_=0.1, to=1.0, resolution=0.05,
                 orient=tk.HORIZONTAL, variable=self._op_var,
                 command=self._on_opacity_change).pack(fill=tk.X)
        self._op_label = tk.Label(
            p, text=f"当前: {int(self._op_var.get() * 100)}%"
        )
        self._op_label.pack()

        # 速度
        tk.Label(p, text="━━ 弹幕速度 ━━", font=("", 10, "bold")).pack(**pad)
        f_sp = tk.Frame(p)
        f_sp.pack(fill=tk.X, **pad)
        speeds = [("慢", 100), ("中", 200), ("快", 350), ("极快", 500)]
        self._speed_var = tk.IntVar(value=self.config["danmaku"]["speed"])
        for label, val in speeds:
            tk.Radiobutton(f_sp, text=label, variable=self._speed_var,
                           value=val, command=self._on_speed_change).pack(
                side=tk.LEFT, padx=4)

        # 字体大小
        tk.Label(p, text="━━ 字体大小 ━━", font=("", 10, "bold")).pack(**pad)
        f_fs = tk.Frame(p)
        f_fs.pack(fill=tk.X, **pad)
        self._fs_var = tk.IntVar(value=self.config["danmaku"]["font_size"])
        for s in [8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 26, 28, 32, 36, 40, 48, 56]:
            tk.Radiobutton(f_fs, text=f"{s}px", variable=self._fs_var,
                           value=s, command=self._on_font_size_change).pack(
                side=tk.LEFT, padx=3)

        # 自动弹幕
        tk.Label(p, text="━━ 自动弹幕 ━━", font=("", 10, "bold")).pack(**pad)
        self._auto_var = tk.BooleanVar(
            value=self.config["danmaku"].get("auto_enabled", True)
        )
        tk.Checkbutton(p, text="启用自动弹幕", variable=self._auto_var,
                       command=self._on_auto_toggle).pack()
        tk.Button(p, text="编辑屏蔽关键字", command=self._show_block_keywords_dialog,
                  width=28).pack(pady=(2, 6))
        tk.Button(p, text="编辑屏蔽用户ID", command=self._show_block_user_ids_dialog,
                  width=28).pack(pady=(0, 6))

        # B站直播间
        tk.Label(p, text="━━ B站直播间 ━━", font=("", 10, "bold")).pack(**pad)
        f_bili = tk.Frame(p)
        f_bili.pack(fill=tk.X, **pad)
        tk.Label(f_bili, text="房间号:").pack(side=tk.LEFT, padx=(0, 4))
        saved_room = self.config.get("bilibili", {}).get("room_id", "")
        self._room_entry = tk.Entry(f_bili, width=18)
        self._room_entry.pack(side=tk.LEFT, padx=(0, 4))
        if saved_room:
            self._room_entry.insert(0, str(saved_room))
        self._room_entry.bind("<Return>", lambda e: self._on_bili_toggle())
        self._bili_btn = tk.Button(
            f_bili, text="连接", command=self._on_bili_toggle,
            bg="#e84c3d", fg="white", width=8,
        )
        self._bili_btn.pack(side=tk.LEFT)

        f_cookie = tk.Frame(p)
        f_cookie.pack(fill=tk.X, **pad)
        tk.Label(f_cookie, text="Cookie(选填):").pack(side=tk.LEFT, padx=(0, 4))
        saved_cookie = self.config.get("bilibili", {}).get("cookie", "")
        self._cookie_entry = tk.Entry(f_cookie, width=28, show="*")
        self._cookie_entry.pack(side=tk.LEFT)
        if saved_cookie:
            self._cookie_entry.insert(0, saved_cookie)
        tk.Button(f_cookie, text="显/隐", command=lambda: self._cookie_entry.configure(
            show="" if self._cookie_entry.cget("show") == "*" else "*"),
            width=4).pack(side=tk.LEFT, padx=(4, 0))

        self._bili_status = tk.Label(p, text="未连接", fg="gray")
        self._bili_status.pack()

        # 手动发送
        tk.Label(p, text="━━ 手动发送弹幕 ━━", font=("", 10, "bold")).pack(**pad)
        f_send = tk.Frame(p)
        f_send.pack(fill=tk.X, **pad)
        self._entry = tk.Entry(f_send, width=28)
        self._entry.pack(side=tk.LEFT, padx=(0, 4))
        self._entry.bind("<Return>", lambda e: self._send_danmaku())
        tk.Button(f_send, text="发送", command=self._send_danmaku,
                  bg="#4a90d9", fg="white").pack(side=tk.LEFT)

        tk.Label(p, text="提示: 在编辑模式下拖动窗口边框调整大小",
                 fg="gray", font=("", 8)).pack(pady=(8, 0))

    def _toggle_mode(self):
        self.overlay.toggle_mode()
        mode = self.config["mode"]
        self._mode_label.configure(
            text="当前: 穿透模式 (鼠标穿透，弹幕可见)"
            if mode == "danmaku"
            else "当前: 编辑模式 (可拖动调整，边框可见)"
        )

    def _on_opacity_change(self, val):
        v = float(val)
        self.overlay.set_opacity(v)
        self._op_label.configure(text=f"当前: {int(v * 100)}%")

    def _on_speed_change(self):
        s = self._speed_var.get()
        self.config["danmaku"]["speed"] = s
        save_config(self.config)

    def _on_font_size_change(self):
        s = self._fs_var.get()
        self.config["danmaku"]["font_size"] = s
        save_config(self.config)

    def _on_auto_toggle(self):
        self.config["danmaku"]["auto_enabled"] = self._auto_var.get()
        save_config(self.config)

    def _show_block_keywords_dialog(self):
        dialog = tk.Toplevel(self.win)
        dialog.title("弹幕屏蔽关键字")
        dialog.geometry("360x300")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)

        tk.Label(dialog, text="每行一个关键字，也可用逗号/分号分隔:").pack(pady=(10, 4))
        text_box = tk.Text(dialog, width=42, height=10)
        text_box.pack(padx=10, pady=4)
        keywords = self.config.get("danmaku", {}).get("blocked_keywords", [])
        text_box.insert("1.0", "\n".join(keywords))

        status_label = tk.Label(dialog, text="", fg="gray")
        status_label.pack()

        def on_save():
            keywords = normalize_blocked_keywords(text_box.get("1.0", tk.END))
            self.config.setdefault("danmaku", {})["blocked_keywords"] = keywords
            save_config(self.config)
            status_label.configure(text=f"已保存 {len(keywords)} 个屏蔽词", fg="#33AA33")

        def on_clear():
            text_box.delete("1.0", tk.END)
            on_save()

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="保存", command=on_save, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="清空", command=on_clear, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="关闭", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def _show_block_user_ids_dialog(self):
        dialog = tk.Toplevel(self.win)
        dialog.title("ID弹幕屏蔽")
        dialog.geometry("360x300")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)

        tk.Label(dialog, text="每行一个用户ID，也可用逗号/分号分隔:").pack(pady=(10, 4))
        text_box = tk.Text(dialog, width=42, height=10)
        text_box.pack(padx=10, pady=4)
        user_ids = self.config.get("danmaku", {}).get("blocked_user_ids", [])
        text_box.insert("1.0", "\n".join(user_ids))

        status_label = tk.Label(dialog, text="", fg="gray")
        status_label.pack()

        def on_save():
            user_ids = normalize_blocked_user_ids(text_box.get("1.0", tk.END))
            self.config.setdefault("danmaku", {})["blocked_user_ids"] = user_ids
            save_config(self.config)
            status_label.configure(text=f"已保存 {len(user_ids)} 个屏蔽ID", fg="#33AA33")

        def on_clear():
            text_box.delete("1.0", tk.END)
            on_save()

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="保存", command=on_save, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="清空", command=on_clear, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="关闭", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def _send_danmaku(self):
        text = self._entry.get().strip()
        if text:
            self.overlay.engine.add_danmaku(
                text=text, color=random.choice(DANMAKU_COLORS),
            )
            self._entry.delete(0, tk.END)

    # ── B站直播 ────────────────────────────────

    def _on_bili_toggle(self):
        if self.bilibili_client.is_connected:
            self.bilibili_client.disconnect()
            self._bili_btn.configure(text="连接", bg="#e84c3d")
            return
        raw = self._room_entry.get().strip()
        if not raw:
            self._bili_status.configure(text="请输入房间号", fg="red")
            return
        try:
            room_id = int(raw)
        except ValueError:
            try:
                raw2 = raw.split("/")[-1]
                room_id = int(raw2)
            except Exception:
                self._bili_status.configure(text="房间号格式错误", fg="red")
                return
        cookie = self._cookie_entry.get().strip()
        self.config.setdefault("bilibili", {})["room_id"] = room_id
        self.config["bilibili"]["cookie"] = cookie
        save_config(self.config)
        self.bilibili_client.connect(room_id, cookie=cookie)
        self._bili_btn.configure(text="断开", bg="#666")

    def _on_bili_danmaku(self, text, username, color, user_id=""):
        display_text = f"{username}: {text}" if username else text
        if is_blocked_danmaku(self.config, text, display_text, user_id):
            return
        self.overlay.root.after(
            0,
            lambda: self.overlay.engine.add_danmaku(
                text=display_text,
                color=color,
            ),
        )

    def _on_bili_error(self, error):
        self.overlay.root.after(
            0,
            lambda: self._bili_status.configure(
                text=f"错误: {error[:20]}", fg="red"
            ),
        )

    def _on_bili_status(self, status):
        self.overlay.root.after(
            0,
            lambda: self._bili_status.configure(
                text=status,
                fg="#33FF66" if "连接" in status or "人气" in status or "加入" in status or "登录" in status else "gray",
            ),
        )
        # 同步按钮文字
        self.overlay.root.after(0, self._sync_bili_btn)

    def _sync_bili_btn(self):
        if self.bilibili_client.is_connected:
            self._bili_btn.configure(text="断开", bg="#666")
        else:
            self._bili_btn.configure(text="连接", bg="#e84c3d")

    def _on_close(self):
        self.bilibili_client.disconnect()
        self.win.destroy()


# ──────────────────────────────────────────────
# 系统托盘 (Windows 优先, Linux 尝试)
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
        draw.rounded_rectangle([4, 4, 60, 60], radius=14,
                               fill=(0, 170, 255, 255))
    except AttributeError:
        draw.ellipse([4, 4, 60, 60], fill=(0, 170, 255, 255))
    draw.text((16, 14), "D", fill=(255, 255, 255, 255))

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
            _tk_call(lambda: _apply_speed(s))
        return _f

    def _apply_speed(s):
        config["danmaku"]["speed"] = s
        save_config(config)

    def on_font_size(s):
        def _f():
            _tk_call(lambda: _apply_font_size(s))
        return _f

    def _apply_font_size(s):
        config["danmaku"]["font_size"] = s
        save_config(config)

    def on_auto_toggle(icon, item):
        def _f():
            enabled = config["danmaku"].get("auto_enabled", True)
            config["danmaku"]["auto_enabled"] = not enabled
            save_config(config)
        _tk_call(_f)

    def on_send(icon, item):
        _tk_call(_show_send_dialog)

    def on_block_keywords(icon, item):
        _tk_call(_show_block_keywords_dialog)

    def on_block_user_ids(icon, item):
        _tk_call(_show_block_user_ids_dialog)

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
            keywords = normalize_blocked_keywords(text_box.get("1.0", tk.END))
            config.setdefault("danmaku", {})["blocked_keywords"] = keywords
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

    def _show_send_dialog():
        dialog = tk.Toplevel(overlay.root)
        dialog.title("发送弹幕")
        dialog.geometry("320x140")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        try:
            dialog.transient(overlay.root)
        except Exception:
            pass
        tk.Label(dialog, text="输入弹幕内容:").pack(pady=(10, 0))
        entry = tk.Entry(dialog, width=36)
        entry.pack(pady=5)
        entry.focus_set()

        def send():
            text = entry.get().strip()
            if text:
                overlay.engine.add_danmaku(
                    text=text, color=random.choice(DANMAKU_COLORS),
                )
            dialog.destroy()

        entry.bind("<Return>", lambda e: send())
        tk.Button(dialog, text="发送", command=send).pack(pady=5)

    def on_exit(icon, item):
        icon.stop()
        overlay.destroy()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("发送弹幕...", on_send, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("弹幕透明度", pystray.Menu(
            *[pystray.MenuItem(
                f"{int(v * 100)}%",
                on_opacity(v),
                checked=lambda item, v=v: abs(config["danmaku"]["opacity"] - v) < 0.01,
                radio=True,
            ) for v in [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0]]
        )),
        pystray.MenuItem("弹幕速度", pystray.Menu(
            *[pystray.MenuItem(
                label,
                on_speed(s),
                checked=lambda item, s=s: config["danmaku"]["speed"] == s,
                radio=True,
            ) for s, label in [(100, "慢"), (200, "中"), (350, "快"), (500, "极快")]]
        )),
        pystray.MenuItem("字体大小", pystray.Menu(
            *[pystray.MenuItem(
                f"{s}px",
                on_font_size(s),
                checked=lambda item, s=s: config["danmaku"]["font_size"] == s,
                radio=True,
            ) for s in [8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 26, 28, 32, 36, 40, 48, 56]]
        )),
        pystray.MenuItem(
            "自动弹幕", on_auto_toggle,
            checked=lambda item: config["danmaku"].get("auto_enabled", True),
        ),
        pystray.MenuItem("弹幕屏蔽关键字...", on_block_keywords),
        pystray.MenuItem("ID弹幕屏蔽...", on_block_user_ids),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("切换 编辑/穿透 模式", on_toggle),
        pystray.MenuItem("→ 穿透模式", _set_mode("danmaku")),
        pystray.MenuItem("→ 编辑模式", _set_mode("edit")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_exit),
    )

    icon = pystray.Icon("danmaku_overlay", icon_img, "B站弹幕机", menu)
    return icon


# ──────────────────────────────────────────────
# 主程序入口
# ──────────────────────────────────────────────

def main():
    print(f"[INFO] 运行平台: {'Windows' if IS_WINDOWS else 'Linux/WSL'}")
    print(f"[INFO] X11 Shape 穿透: {'可用' if _x11_xshape_available else '不可用'}")

    config = load_config()
    save_config(config)

    overlay = OverlayWindow(config)

    tray_icon = create_tray_icon(overlay, config)

    if tray_icon:
        tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
        tray_thread.start()
        print("[INFO] 系统托盘已启动")
    else:
        print("[INFO] 系统托盘不可用，使用控制面板代替")
        panel = ControlPanel(overlay, config)

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
