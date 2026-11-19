"""
RK87 Light - instrument-grade lighting control surface for the RK-H87 keyboard.

UI: a custom-drawn Tk canvas with frosted surfaces, an RGB signal accent and
tactile states.  An offscreen proxy window owns the real taskbar button so the
drawn surface can stay borderless and rounded.  The device layer (rk87core) and
tray/autostart logic follow the proven g304-lite architecture.

The app starts in DEMO mode.  Writing to real hardware requires an explicit
"连接真机" click, and every write goes only through the safe 0x0A lighting
Feature report (see rk87core for the safety rationale).
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import colorchooser, messagebox
from typing import Callable, Optional

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

try:
    import pystray
except ImportError:  # pragma: no cover
    pystray = None

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # pragma: no cover
    Image = ImageDraw = ImageTk = None

try:
    from rk87core import (
        ANIMATION_LABELS,
        BRIGHTNESS_LABELS,
        LIGHT_MODES,
        SLEEP_LABELS,
        DeviceNotFound,
        DeviceWriteError,
        MockController,
        RK87Controller,
        find_keyboard,
    )
except ImportError:  # pragma: no cover
    from .rk87core import (
        ANIMATION_LABELS, BRIGHTNESS_LABELS, LIGHT_MODES, SLEEP_LABELS,
        DeviceNotFound, DeviceWriteError, MockController, RK87Controller, find_keyboard,
    )


APP_NAME = "RK87 Light"
W, H = 940, 620

# Instrument palette - tinted neutrals, one RGB-ish signal colour, one status colour.
BG = "#0F1319"
KEY = "#000001"          # transparency key: never used by a real UI pixel
HEADER = "#151B23"
PANEL = "#1B222C"
PANEL_ALT = "#222A35"
PRESSED = "#2B3542"
STROKE = "#35404F"
STROKE_SOFT = "#2A333F"
TEXT = "#F3F5F7"
MUTED = "#A8B2C0"
FAINT = "#6E7B8C"
ACCENT = "#6EE7C8"       # mint signal colour
ACCENT_DEEP = "#1E7A63"
ACCENT_TINT = "#14322C"
AMBER = "#F2B544"
AMBER_TINT = "#3A2A14"
RED = "#F27777"

F_TITLE = ("Bahnschrift SemiBold", 21, "bold")
F_SUB = ("Microsoft YaHei UI", 9)
F_LABEL = ("Microsoft YaHei UI", 10, "bold")
F_BODY = ("Microsoft YaHei UI", 10)
F_SMALL = ("Microsoft YaHei UI", 9)
F_MODE = ("Microsoft YaHei UI", 10)
F_PILL = ("Microsoft YaHei UI", 9, "bold")
F_LOG = ("Consolas", 9)
F_BIG = ("Bahnschrift SemiBold", 16, "bold")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "RK87Light"

DEFAULT_CONFIG = {
    "mode_bit": 17,          # Steady / 常亮
    "brightness": 5,
    "animation": 3,
    "sleep": 5,
    "colour": [110, 231, 200],
    "random_colours": False,
}


def resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg["colour"] = list(DEFAULT_CONFIG["colour"])
    try:
        with open(config_path(), "r", encoding="utf-8") as handle:
            cfg.update(json.load(handle))
    except Exception:
        pass
    return cfg


def save_config(config: dict) -> None:
    try:
        with open(config_path(), "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


def autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'


def autostart_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if winreg is None:
        raise RuntimeError("当前系统不支持开机自启")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass


class RK87LightApp:
    def __init__(self, surface: tk.Toplevel, proxy: tk.Tk):
        self.root = surface
        self.proxy = proxy
        self.root.title(APP_NAME)
        self.root.configure(bg=KEY)
        self.root.overrideredirect(True)
        self.root.attributes("-transparentcolor", KEY)
        try:
            icon = resource_path("assets/rk87-light.ico")
            self.root.iconbitmap(icon)
            self.proxy.iconbitmap(icon)
        except Exception:
            pass
        self.proxy.title(APP_NAME)
        self.proxy.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.proxy.bind("<Map>", lambda _e: self.root.after(100, self._sync_surface))
        self.proxy.bind("<Unmap>", lambda _e: self.root.after(100, self._sync_surface))
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.geometry(
            f"{W}x{H}+{(surface.winfo_screenwidth() - W) // 2}+{(surface.winfo_screenheight() - H) // 3}")
        self.root.minsize(W, H)
        self.root.resizable(False, False)

        self.config = load_config()
        self.controller = None
        self.device_info = None
        self.mock = True
        self.busy = False
        self.busy_kind = ""
        self.messages: queue.Queue = queue.Queue()
        self.log_lines: list[str] = []
        self.closing = False
        self.tray_icon = None
        self._tray_hint_shown = False
        self._drag_screen = (0, 0)
        self._drag_window = (0, 0)
        self.ui: dict = {}
        self.mode_items: list[dict] = []
        self._rect_spec: dict = {}
        self._round_cache: dict = {}

        self.canvas = tk.Canvas(self.root, width=W, height=H, bg=KEY, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.bg_photo = tk.PhotoImage(file=resource_path("assets/ui-bg.png"))
        self.canvas.create_image(0, 0, image=self.bg_photo, anchor="nw")

        self._build_ui()
        self._bind_keys()
        self._build_tray()
        self._render_all()
        self.root.after(120, self._pump)
        self.root.after(150, self._sync_surface)
        # Start in demo mode silently; user opts in to real hardware.
        self._connect_demo()

    # ---------- canvas primitives (proven from g304-lite) ----------
    def _round_photo(self, w, h, radius, fill, outline, width):
        key = (w, h, radius, fill, outline, width)
        cached = self._round_cache.get(key)
        if cached is not None:
            return cached
        scale = 4
        img = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(
            (0, 0, w * scale - 1, h * scale - 1),
            radius=radius * scale, fill=fill,
            outline=outline or fill, width=max(1, width * scale),
        )
        img = img.resize((w, h), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        self._round_cache[key] = photo
        return photo

    def _rect(self, x, y, w, h, fill, outline="", radius=12, width=1):
        outline = outline or fill
        item = self.canvas.create_image(
            x, y, image=self._round_photo(w, h, radius, fill, outline, width), anchor="nw")
        self._rect_spec[item] = (x, y, w, h, radius, fill, outline, width)
        return item

    def _set_rect(self, item, *, fill=None, outline=None, width=None):
        spec = self._rect_spec.get(item)
        if spec is None:
            return
        x, y, w, h, radius, old_fill, old_outline, old_width = spec
        fill = old_fill if fill is None else fill
        outline = old_outline if outline is None else outline
        width = old_width if width is None else width
        self._rect_spec[item] = (x, y, w, h, radius, fill, outline, width)
        self.canvas.itemconfigure(item, image=self._round_photo(w, h, radius, fill, outline, width))

    def _text(self, x, y, text, font=F_BODY, fill=TEXT, anchor="w", tag=None):
        return self.canvas.create_text(x, y, text=text, font=font, fill=fill,
                                       anchor=anchor, tags=(tag,) if tag else ())

    def _panel(self, x, y, w, h, title, subtitle=""):
        self._rect(x, y, w, h, PANEL, STROKE, radius=18)
        self._text(x + 20, y + 27, title, F_LABEL, TEXT)
        if subtitle:
            self._text(x + 20, y + 50, subtitle, F_SMALL, MUTED)

    def _button(self, key, x, y, w, h, text, command, *, fill=PANEL_ALT,
                fg=TEXT, outline=STROKE_SOFT, font=F_BODY, radius=10):
        rect = self._rect(x, y, w, h, fill, outline, radius=radius)
        label = self._text(x + w / 2, y + h / 2, text, font, fg, anchor="center")
        btn = {"rect": rect, "label": label, "fill": fill, "fg": fg,
               "outline": outline, "x": x, "y": y, "w": w, "h": h}
        self.ui[key] = btn
        for item in (rect, label):
            self.canvas.tag_bind(item, "<Button-1>", lambda _e, c=command, b=btn: self._press(b, c))
            self.canvas.tag_bind(item, "<Enter>", lambda _e, b=btn: self._hover(b, True))
            self.canvas.tag_bind(item, "<Leave>", lambda _e, b=btn: self._hover(b, False))
        return btn

    def _style_button(self, btn, fill=None, fg=None, outline=None, text=None):
        if fill is not None:
            btn["fill"] = fill
            self._set_rect(btn["rect"], fill=fill)
        if outline is not None:
            btn["outline"] = outline
            self._set_rect(btn["rect"], outline=outline)
        if fg is not None:
            btn["fg"] = fg
            self.canvas.itemconfigure(btn["label"], fill=fg)
        if text is not None:
            self.canvas.itemconfigure(btn["label"], text=text)

    def _hover(self, btn, on: bool):
        base = btn["fill"]
        hover = "#2E3A48" if base in (PANEL_ALT, PRESSED, STROKE_SOFT) else base
        self._set_rect(btn["rect"], fill=hover if on else base)

    def _press(self, btn, command):
        self._set_rect(btn["rect"], fill=PRESSED)
        self.root.after(90, lambda: self._set_rect(btn["rect"], fill=btn["fill"]))
        command()

    # ---------- layout ----------
    def _build_ui(self) -> None:
        c = self.canvas
        header = self._rect(24, 20, W - 48, 66, HEADER, STROKE_SOFT, radius=16)
        c.tag_bind(header, "<ButtonPress-1>", self._start_move)
        c.tag_bind(header, "<B1-Motion>", self._on_move)
        self.title_items = [
            self._text(46, 44, "RK87", F_TITLE, TEXT),
            self._text(150, 47, "LIGHT", ("Bahnschrift SemiBold", 12, "bold"), ACCENT),
            self._text(46, 69, "三模机械键盘 · RGB 灯光控制台", F_SUB, FAINT),
        ]
        for item in self.title_items:
            c.tag_bind(item, "<ButtonPress-1>", self._start_move)
            c.tag_bind(item, "<B1-Motion>", self._on_move)

        # Right-hand group, laid out right-to-left (16px inset kept from header edge).
        # close right edge = 900 = header right edge (916) - 16.
        self._button("close", 872, 36, 28, 32, "×", self.hide_to_tray,
                     font=("Segoe UI", 14, "bold"), radius=8)
        self._button("minimize", 836, 36, 28, 32, "—", self.minimize_to_taskbar,
                     font=("Segoe UI", 14, "bold"), radius=8)
        self.pill_rect = self._rect(644, 36, 176, 32, AMBER_TINT, AMBER, radius=16)
        self.pill_text = self._text(732, 52, "● 演示模式", F_PILL, AMBER, anchor="center")
        self._button("link", 516, 36, 120, 32, "连接真机", self.toggle_link, font=F_SMALL)
        self._button("refresh", 436, 36, 72, 32, "回读", self.refresh, font=F_SMALL)

        # ---- Left: lighting mode grid ----
        self._panel(24, 104, 560, 402, "灯效模式", "21 种内置灯效 · 点击即预览（演示模式不写硬件）")
        cols = 4
        cell_w, cell_h = 128, 62
        gap_x, gap_y = 10, 8
        for i, (name, mode_bit) in enumerate(LIGHT_MODES):
            row, col = divmod(i, cols)
            x = 44 + col * (cell_w + gap_x)
            y = 166 + row * (cell_h + gap_y)
            rect = self._rect(x, y, cell_w, cell_h, PANEL_ALT, STROKE_SOFT, radius=11)
            label = self._text(x + cell_w / 2, y + cell_h / 2, name, F_MODE, TEXT, anchor="center")
            bit_text = self._text(x + cell_w - 8, y + 8, str(mode_bit), ("Consolas", 8), FAINT, anchor="ne")
            item = {"rect": rect, "label": label, "bit": bit_text, "index": i, "mode_bit": mode_bit}
            self.mode_items.append(item)
            for el in (rect, label, bit_text):
                c.tag_bind(el, "<Button-1>", lambda _e, idx=i: self.set_mode(idx))
                c.tag_bind(el, "<Enter>", lambda _e, it=item: self._set_rect(it["rect"], fill=PRESSED))
                c.tag_bind(el, "<Leave>", lambda _e, it=item: self._hover_mode(it, False))

        # ---- Right top: brightness / animation / sleep ----
        self._panel(600, 104, 316, 246, "灯光参数", "亮度 · 速度 · 休眠定时")
        self.slider_rows: dict = {}
        self._build_slider("brightness", 600, 166, BRIGHTNESS_LABELS, 0, 5)
        self._build_slider("animation", 600, 238, ANIMATION_LABELS, 1, 5)
        self._build_slider("sleep", 600, 310, SLEEP_LABELS, 1, 5)

        # ---- Right bottom: colour ----
        self._panel(600, 362, 316, 144, "颜色", "关闭随机色后可自定义 RGB")
        self.colour_swatch = self._rect(620, 428, 60, 60, ACCENT, STROKE, radius=12)
        c.tag_bind(self.colour_swatch, "<Button-1>", lambda _e: self.pick_colour())
        self.colour_hex = self._text(696, 448, "#6EE7C8", F_BIG, TEXT)
        self._button("pick", 696, 480, 108, 30, "选择颜色 →", self.pick_colour, font=F_SMALL)
        self.random_rect = self._rect(812, 480, 16, 16, PANEL_ALT, STROKE, radius=4)
        self.random_mark = c.create_line(816, 488, 819, 491, 825, 483, fill=ACCENT, width=2, state="hidden")
        self.random_text = self._text(836, 489, "随机色", F_SMALL, TEXT)
        for el in (self.random_rect, self.random_mark, self.random_text):
            c.tag_bind(el, "<Button-1>", lambda _e: self.toggle_random())

        # ---- Apply bar ----
        self._rect(24, 518, 892, 58, PANEL, STROKE, radius=14)
        self._button("apply", 40, 530, 200, 34, "应用到键盘", self.apply_now,
                     fill=ACCENT_DEEP, fg="#FFFFFF", outline=ACCENT, font=F_LABEL)
        self.apply_note = self._text(256, 547,
                                     "演示模式：改动仅存本地，不会写入硬件", F_SMALL, AMBER)

        self.check_rect = self._rect(700, 540, 18, 18, PANEL_ALT, STROKE, radius=5)
        self.check_mark = c.create_line(704, 549, 707, 552, 713, 544, fill=ACCENT, width=2, state="hidden")
        self.autostart_text = self._text(726, 549, "开机自启", F_BODY, TEXT)
        for el in (self.check_rect, self.check_mark, self.autostart_text):
            c.tag_bind(el, "<Button-1>", lambda _e: self.toggle_autostart())

        # ---- Log ----
        self._text(30, 600, "事件", F_LABEL, TEXT)
        self.log_item = self._text(72, 600, "", F_LOG, MUTED, anchor="w")
        self._text(916, 600, "1-4 亮度 · Q/W 速度 · Space 应用", F_SMALL, FAINT, anchor="e")

    def _build_slider(self, key, px, py, labels, lo, hi):
        """A canvas slider: minus / value+label / plus, all clickable."""
        c = self.canvas
        name = {"brightness": "亮度", "animation": "速度", "sleep": "休眠"}[key]
        self._text(px + 20, py + 18, name, F_BODY, TEXT)
        self._button(f"{key}_dn", px + 84, py + 2, 30, 30, "−", lambda: self.nudge(key, -1),
                     font=("Segoe UI", 14), radius=8)
        val = self._text(px + 158, py + 18, "", F_LABEL, ACCENT, anchor="center")
        self._button(f"{key}_up", px + 232, py + 2, 30, 30, "+", lambda: self.nudge(key, 1),
                     font=("Segoe UI", 14), radius=8)
        self.slider_rows[key] = {"value": val, "labels": labels, "lo": lo, "hi": hi}

    # ---------- binding / sync ----------
    def _bind_keys(self) -> None:
        self.root.bind("1", lambda _e: self.config.update(brightness=1) or self._on_config_changed())
        self.root.bind("2", lambda _e: self.config.update(brightness=2) or self._on_config_changed())
        self.root.bind("3", lambda _e: self.config.update(brightness=3) or self._on_config_changed())
        self.root.bind("4", lambda _e: self.config.update(brightness=5) or self._on_config_changed())
        self.root.bind("q", lambda _e: self.nudge("animation", -1))
        self.root.bind("w", lambda _e: self.nudge("animation", 1))
        self.root.bind("<space>", lambda _e: self.apply_now())
        self.root.bind("<F5>", lambda _e: self.refresh())
        self.root.bind("<Escape>", lambda _e: self.hide_to_tray())
        self.root.focus_set()

    def _sync_surface(self) -> None:
        if self.closing:
            return
        state = self.proxy.state()
        if state in ("iconic", "withdrawn"):
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

    def minimize_to_taskbar(self) -> None:
        self.proxy.iconify()
        self.root.after(80, self._sync_surface)

    def _start_move(self, event) -> None:
        if event.x > 420:
            return
        self._drag_screen = (event.x_root, event.y_root)
        self._drag_window = (self.root.winfo_x(), self.root.winfo_y())

    def _on_move(self, event) -> None:
        dx = event.x_root - self._drag_screen[0]
        dy = event.y_root - self._drag_screen[1]
        self.root.geometry(f"+{self._drag_window[0] + dx}+{self._drag_window[1] + dy}")

    # ---------- rendering ----------
    def _hover_mode(self, item, on: bool):
        selected = item["mode_bit"] == self.config["mode_bit"]
        fill = ACCENT_TINT if selected else PANEL_ALT
        self._set_rect(item["rect"], fill=PRESSED if on else fill,
                       outline=ACCENT if selected else STROKE_SOFT)

    def _render_all(self) -> None:
        # modes
        for item in self.mode_items:
            selected = item["mode_bit"] == self.config["mode_bit"]
            self._set_rect(item["rect"],
                           fill=ACCENT_TINT if selected else PANEL_ALT,
                           outline=ACCENT if selected else STROKE_SOFT,
                           width=2 if selected else 1)
            self.canvas.itemconfigure(item["label"], fill=ACCENT if selected else TEXT)
        # sliders
        for key, row in self.slider_rows.items():
            val = self.config[key]
            label = row["labels"][val] if val < len(row["labels"]) and row["labels"][val] else str(val)
            self.canvas.itemconfigure(row["value"], text=f"{val}  {label}")
        # colour
        rgb = tuple(self.config["colour"])
        hexcode = "#{:02X}{:02X}{:02X}".format(*rgb)
        self._set_rect(self.colour_swatch, fill=hexcode, outline=STROKE)
        self.canvas.itemconfigure(self.colour_hex, text=hexcode)
        # random toggle
        rnd = self.config["random_colours"]
        self._set_rect(self.random_rect, fill=ACCENT if rnd else PANEL_ALT)
        self.canvas.itemconfigure(self.random_mark, state="normal" if rnd else "hidden")
        # autostart
        enabled = autostart_enabled()
        self._set_rect(self.check_rect, fill=ACCENT if enabled else PANEL_ALT)
        self.canvas.itemconfigure(self.check_mark, state="normal" if enabled else "hidden")
        # connection pill
        if self.mock:
            self._set_rect(self.pill_rect, fill=AMBER_TINT, outline=AMBER)
            self.canvas.itemconfigure(self.pill_text, text="● 演示模式", fill=AMBER)
        else:
            self._set_rect(self.pill_rect, fill=ACCENT_TINT, outline=ACCENT)
            self.canvas.itemconfigure(self.pill_text, text="● 已连接真机", fill=ACCENT)

    def _render_log(self) -> None:
        last = self.log_lines[-1] if self.log_lines else ""
        self.canvas.itemconfigure(self.log_item, text=last)

    # ---------- tray ----------
    def _build_tray(self) -> None:
        if pystray is None or Image is None:
            self._post("托盘依赖缺失，状态栏不可用")
            return

        def call(fn: Callable, *args):
            def callback(_icon, _item):
                self.root.after(0, lambda: fn(*args))
            return callback

        def checked_mode(_item, mode_bit: int) -> bool:
            return self.config["mode_bit"] == mode_bit

        mode_menu = pystray.Menu(*[
            pystray.MenuItem(name, call(self.set_mode_by_bit, bit),
                             checked=lambda item, b=bit: checked_mode(item, b), radio=True)
            for name, bit in LIGHT_MODES
        ])
        menu = pystray.Menu(
            pystray.MenuItem("显示主界面", call(self.show_window), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("灯效模式", mode_menu),
            pystray.MenuItem("应用当前设置", call(self.apply_now)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("开机自启", call(self.toggle_autostart),
                             checked=lambda _item: autostart_enabled()),
            pystray.MenuItem("退出", call(self.quit_app)),
        )
        image = Image.open(resource_path("assets/rk87-light.png"))
        self.tray_icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _refresh_tray_menu(self) -> None:
        if self.tray_icon is not None:
            try:
                self.tray_icon.update_menu()
            except Exception:
                pass

    def show_window(self) -> None:
        if self.closing:
            return
        self.proxy.deiconify()
        self._sync_surface()

    def hide_to_tray(self) -> None:
        if self.closing:
            return
        self.root.withdraw()
        self.proxy.withdraw()
        if self.tray_icon is not None and not self._tray_hint_shown:
            self._tray_hint_shown = True
            try:
                self.tray_icon.notify("已收进状态栏，右键图标可打开菜单或退出。", APP_NAME)
            except Exception:
                pass
        self._post("窗口已收进状态栏；右键状态栏图标可恢复")

    def quit_app(self) -> None:
        self.closing = True
        if self.controller is not None:
            try:
                self.controller.close()
            except Exception:
                pass
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.after(0, self.proxy.destroy)

    # ---------- config actions ----------
    def _on_config_changed(self) -> None:
        save_config(self.config)
        self._render_all()
        self._refresh_tray_menu()

    def set_mode(self, index: int) -> None:
        name, bit = LIGHT_MODES[index]
        self.config["mode_bit"] = bit
        self._on_config_changed()
        self._post(f"灯效 → {name}（未应用，点『应用到键盘』写入）")

    def set_mode_by_bit(self, bit: int) -> None:
        for name, b in LIGHT_MODES:
            if b == bit:
                self.set_mode(LIGHT_MODES.index((name, b)))
                return

    def nudge(self, key: str, delta: int) -> None:
        row = self.slider_rows[key]
        self.config[key] = max(row["lo"], min(row["hi"], self.config[key] + delta))
        self._on_config_changed()

    def pick_colour(self) -> None:
        rgb = self.config["colour"]
        initial = "#{:02X}{:02X}{:02X}".format(*rgb)
        chosen = colorchooser.askcolor(color=initial, title="选择灯光颜色", parent=self.root)
        if chosen and chosen[0]:
            r, g, b = [int(v) for v in chosen[0]]
            self.config["colour"] = [r, g, b]
            self.config["random_colours"] = False
            self._on_config_changed()
            self._post(f"颜色 → #{r:02X}{g:02X}{b:02X}")

    def toggle_random(self) -> None:
        self.config["random_colours"] = not self.config["random_colours"]
        self._on_config_changed()
        self._post("随机色 " + ("开启" if self.config["random_colours"] else "关闭"))

    def toggle_autostart(self) -> None:
        target = not autostart_enabled()
        try:
            set_autostart(target)
            self._post(f"开机自启已{'开启' if target else '关闭'}")
        except Exception as exc:
            self._post(f"开机自启设置失败：{exc}")
        self._render_all()
        self._refresh_tray_menu()

    # ---------- device plumbing ----------
    def _post(self, text: str) -> None:
        self.messages.put(text)

    def _pump(self) -> None:
        try:
            while True:
                text = self.messages.get_nowait()
                self.log_lines.append(text)
                self._render_log()
        except queue.Empty:
            pass
        self.root.after(150, self._pump)

    def _run(self, work: Callable, done: Optional[Callable] = None, kind: str = "操作") -> None:
        if self.busy:
            self._post(f"正在{self.busy_kind}，请稍候")
            return
        self.busy = True
        self.busy_kind = kind

        def runner():
            try:
                result = work()
                self.root.after(0, lambda: self._finish(result, done, None))
            except Exception as exc:
                self.root.after(0, lambda: self._finish(None, done, exc))

        threading.Thread(target=runner, daemon=True).start()

    def _finish(self, result, done, error) -> None:
        self.busy = False
        self.busy_kind = ""
        if error is not None:
            self._post(f"✗ {error}")
            if isinstance(error, DeviceNotFound) and self.root.state() not in ("withdrawn", "iconic"):
                messagebox.showwarning(APP_NAME, str(error))
            return
        if done is not None:
            done(result)

    def _connect_demo(self) -> None:
        self.controller = MockController()
        self.mock = True
        self._render_all()
        self._post("演示模式已就绪：改动只存本地，不写硬件")

    def toggle_link(self) -> None:
        if not self.mock:
            # Disconnect real hardware, fall back to demo.
            if self.controller is not None:
                try:
                    self.controller.close()
                except Exception:
                    pass
            self._connect_demo()
            self._post("已断开真机，回到演示模式")
            return
        # Opt in to real hardware.
        self._run(self._open_real, self._after_connect, kind="连接真机")

    def _open_real(self):
        info = find_keyboard()
        ctrl = RK87Controller(info)
        ctrl.open()
        return ctrl, info

    def _after_connect(self, result) -> None:
        self.controller, self.device_info = result
        self.mock = False
        self._render_all()
        self._post(f"已连接真机 {self.device_info.vid_pid} · 写入仅走 0x0A 灯光通道")
        self.refresh()

    def current_buffer_args(self):
        return dict(
            mode_bit=self.config["mode_bit"],
            brightness=self.config["brightness"],
            animation=self.config["animation"],
            sleep=self.config["sleep"],
            colour=tuple(self.config["colour"]),
            random_colours=self.config["random_colours"],
        )

    def apply_now(self) -> None:
        if self.controller is None:
            self._post("尚未连接控制器")
            return
        args = self.current_buffer_args()
        self._run(lambda: self.controller.apply(**args),
                  lambda buf: self._after_apply(buf), kind="写入灯光")

    def _after_apply(self, buf: bytes) -> None:
        hexhead = " ".join(f"{b:02X}" for b in buf[:14])
        mode_name = next((n for n, b in LIGHT_MODES if b == self.config["mode_bit"]), "?")
        if self.mock:
            self._post(f"演示写入完成（未发硬件）· {mode_name} · [{hexhead}]")
            self.canvas.itemconfigure(self.apply_note, text="演示模式：改动仅存本地，不会写入硬件", fill=AMBER)
        else:
            self._post(f"✓ 已写入键盘 · {mode_name} · [{hexhead}]")
            self.canvas.itemconfigure(self.apply_note, text="✓ 已写入键盘灯光配置", fill=ACCENT)

    def refresh(self) -> None:
        if self.controller is None:
            self._post("尚未连接设备")
            return
        self._run(lambda: self.controller.read_status(), self._after_refresh, kind="回读状态")

    def _after_refresh(self, status: bytes) -> None:
        head = " ".join(f"{b:02X}" for b in status[:16])
        self._post(f"回读 {len(status)} 字节 · [{head}]")


def main() -> int:
    proxy = tk.Tk()
    proxy.geometry("1x1+-30000+-30000")
    proxy.update_idletasks()
    surface = tk.Toplevel(proxy)
    RK87LightApp(surface, proxy)
    proxy.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
