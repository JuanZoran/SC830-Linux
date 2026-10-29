"""Omarchy-following palette and an Apple-style ttk theme.

Rounded shapes are rendered here as antialiased RGBA PNGs. ttk paints a
style's background before its image elements, so each style's background is
set to the surface it sits on and the alpha edges blend into it.
"""

import base64
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import tkinter as tk
from tkinter import font as tkfont, ttk
import tomllib
import zlib

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy/current"
SURFACES = ("Window", "Side", "Card")
POLL_MS = 1500
# Used when no Omarchy theme is present: macOS dark appearance.
FALLBACK = {"background": "#1c1c1e", "foreground": "#f5f5f7", "accent": "#0a84ff",
            "red": "#ff453a", "green": "#30d158", "yellow": "#ffd60a", "orange": "#ff9f0a",
            "magenta": "#bf5af2", "blue": "#0a84ff"}


def rgb(value):
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) not in (6, 8):
        raise ValueError(f"无效颜色 {value}")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def hexcolor(channels):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v))) for v in channels)


def mix(a, b, t):
    return hexcolor(x + (y - x) * t for x, y in zip(rgb(a), rgb(b)))


def luminance(color):
    def linear(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(v) for v in rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def theme_file():
    override = os.environ.get("SC830_THEME")
    return Path(override).expanduser() if override else STATE_DIR / "theme" / "colors.toml"


def theme_signature():
    try:
        st = theme_file().stat()
    except OSError:
        return None
    return st.st_ino, st.st_mtime_ns, st.st_size


def read_theme():
    """Return the theme colours, {} without a theme, or None if unreadable."""
    path = theme_file()
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    colors = {}
    for key, value in data.items():
        if key == "mode" and value in ("light", "dark"):
            colors[key] = value
        elif isinstance(value, str):
            try:
                rgb(value)
            except ValueError:
                continue
            colors[key] = "#" + value.strip().lstrip("#")[:6]
    return colors


class Palette:
    def __init__(self, colors=None):
        given = colors or {}

        def pick(*keys):
            for key in keys:
                if key in given:
                    return given[key]
            return FALLBACK[keys[0]]

        bg = pick("background", "color0")
        fg = pick("foreground", "color15", "color7")
        self.light = given.get("mode", "light" if luminance(bg) > 0.4 else "dark") == "light"
        self.accent = pick("accent", "blue", "color4")
        self.red = pick("red", "color1")
        self.green = pick("green", "color2")
        self.yellow = pick("yellow", "color3")
        self.icons = [pick("blue", "color4"), pick("orange", "yellow", "color3"),
                      pick("green", "color2"), pick("magenta", "color5")]
        if self.light:
            self.window = mix(bg, fg, 0.035)
            self.side = mix(bg, fg, 0.065)
            self.card = mix(bg, "#ffffff", 0.5)
            self.control = mix(self.card, fg, 0.06)
            self.field = mix(self.card, "#ffffff", 0.6)
            self.button = self.field
            self.thumb = mix(self.card, "#ffffff", 0.8)
            self.edge = mix(self.card, fg, 0.13)
            self.switch_off = mix(self.card, fg, 0.13)
            self.knob = "#ffffff"
        else:
            self.window = bg
            self.side = mix(bg, fg, 0.035)
            self.card = mix(bg, fg, 0.065)
            self.control = mix(self.card, fg, 0.09)
            self.field = self.control
            self.button = mix(self.card, fg, 0.13)
            self.thumb = mix(self.card, fg, 0.24)
            self.edge = None
            self.switch_off = mix(self.card, fg, 0.17)
            self.knob = mix(fg, "#ffffff", 0.6)
        self.background, self.text = bg, fg
        self.secondary = mix(fg, self.card, 0.38)
        self.tertiary = mix(fg, self.card, 0.6)
        self.separator = mix(self.card, fg, 0.1)
        self.side_hover = mix(self.side, fg, 0.07)
        self.accent_text = self.on(self.accent)
        self.accent_hover = mix(self.accent, "#000000" if self.light else "#ffffff", 0.1)
        self.accent_press = mix(self.accent, "#000000", 0.18)
        self.accent_soft = mix(self.card, self.accent, 0.25)
        self.disabled = mix(self.card, fg, 0.05)
        self.surfaces = {"Window": self.window, "Side": self.side, "Card": self.card}

    def on(self, color):
        """Pick a legible foreground for text drawn on a filled colour."""
        if contrast("#ffffff", color) >= 3:
            return "#ffffff"
        dark = self.background if not self.light else self.text
        return max((dark, "#000000", "#ffffff"), key=lambda c: contrast(c, color))


class Raster:
    """Tiny premultiplied RGBA canvas; shapes are painted from signed distances."""

    def __init__(self, width, height):
        self.w, self.h = width, height
        self.px = [0.0] * (width * height * 4)

    def paint(self, color, distance, alpha=1.0, box=None):
        r, g, b = (v / 255 for v in rgb(color))
        x0, y0, x1, y1 = box or (0, 0, self.w, self.h)
        for y in range(max(0, int(y0) - 1), min(self.h, int(math.ceil(y1)) + 1)):
            for x in range(max(0, int(x0) - 1), min(self.w, int(math.ceil(x1)) + 1)):
                a = alpha * max(0.0, min(1.0, 0.5 - distance(x + 0.5, y + 0.5)))
                if a <= 0:
                    continue
                i, keep = (y * self.w + x) * 4, 1 - a
                px = self.px
                px[i] = r * a + px[i] * keep
                px[i + 1] = g * a + px[i + 1] * keep
                px[i + 2] = b * a + px[i + 2] * keep
                px[i + 3] = a + px[i + 3] * keep

    def rect(self, x0, y0, x1, y1, radius, color, alpha=1.0):
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
        hx, hy = (x1 - x0) / 2 - radius, (y1 - y0) / 2 - radius

        def distance(x, y):
            qx, qy = abs(x - cx) - hx, abs(y - cy) - hy
            return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
        self.paint(color, distance, alpha, (x0, y0, x1, y1))

    def framed(self, x0, y0, x1, y1, radius, fill, edge=None, width=1.0):
        if edge:
            self.rect(x0, y0, x1, y1, radius, edge)
            x0, y0, x1, y1, radius = x0 + width, y0 + width, x1 - width, y1 - width, radius - width
        self.rect(x0, y0, x1, y1, max(radius, 0), fill)

    def circle(self, cx, cy, radius, color, alpha=1.0):
        self.rect(cx - radius, cy - radius, cx + radius, cy + radius, radius, color, alpha)

    def ring(self, cx, cy, radius, width, color):
        self.paint(color, lambda x, y: abs(math.hypot(x - cx, y - cy) - radius) - width / 2,
                   box=(cx - radius - width, cy - radius - width, cx + radius + width, cy + radius + width))

    def line(self, ax, ay, bx, by, width, color):
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy or 1e-9

        def distance(x, y):
            t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length))
            return math.hypot(x - ax - t * dx, y - ay - t * dy) - width / 2
        pad = width
        self.paint(color, distance, box=(min(ax, bx) - pad, min(ay, by) - pad,
                                         max(ax, bx) + pad, max(ay, by) + pad))

    def rounded_outline(self, x0, y0, x1, y1, radius, width, color):
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        hx, hy = (x1 - x0) / 2 - radius, (y1 - y0) / 2 - radius

        def distance(x, y):
            qx, qy = abs(x - cx) - hx, abs(y - cy) - hy
            d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
            return abs(d) - width / 2
        self.paint(color, distance, box=(x0 - width, y0 - width, x1 + width, y1 + width))

    def png(self):
        rows = bytearray()
        for y in range(self.h):
            rows.append(0)
            for x in range(self.w):
                i = (y * self.w + x) * 4
                a = self.px[i + 3]
                if a <= 0:
                    rows += b"\0\0\0\0"
                    continue
                rows += bytes((round(min(1, self.px[i] / a) * 255), round(min(1, self.px[i + 1] / a) * 255),
                               round(min(1, self.px[i + 2] / a) * 255), round(a * 255)))

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        header = struct.pack(">IIBBBBB", self.w, self.h, 8, 6, 0, 0, 0)
        return base64.b64encode(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                                + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))


def ui_scale():
    """XWayland under force_zero_scaling renders at 1x; follow the monitor scale."""
    override = os.environ.get("SC830_SCALE")
    if override:
        try:
            return max(0.75, min(3.0, float(override)))
        except ValueError:
            pass
    try:
        zero = json.loads(subprocess.run(["hyprctl", "-j", "getoption", "xwayland:force_zero_scaling"],
                                         capture_output=True, text=True, timeout=1).stdout)
        if not zero.get("bool", zero.get("int")):
            return 1.0
        monitors = json.loads(subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True,
                                             text=True, timeout=1).stdout)
        focused = next((m for m in monitors if m.get("focused")), monitors[0])
        return max(1.0, min(3.0, float(focused["scale"])))
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
        return 1.0


class Theme:
    def __init__(self, root):
        self.root = root
        self.scale = ui_scale()
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") * self.scale / 72)
        self.style = ttk.Style(root)
        self.images = {}
        self.listeners = []
        self.combos = []
        self.fonts = self.make_fonts()
        self.signature = theme_signature()
        self.palette = Palette(read_theme())
        self.built = False
        self.apply()

    def px(self, value):
        return max(1, round(value * self.scale))

    # -- fonts ---------------------------------------------------------------

    def make_fonts(self):
        available = set(tkfont.families(self.root))

        def first(*names):
            return next((n for n in names if n in available), None)
        ui = first("PingFang SC", "SF Pro Text", "HarmonyOS Sans SC", "MiSans",
                   "Noto Sans CJK SC", "Microsoft YaHei UI") or "TkDefaultFont"
        medium = first(ui + " Medium", "Noto Sans CJK SC Medium", "MiSans Medium")
        specs = {"body": (ui, 10, "normal"), "small": (ui, 9, "normal"),
                 "headline": (medium or ui, 10, "normal" if medium else "bold"),
                 "section": (medium or ui, 9, "normal" if medium else "bold"),
                 "title": (ui, 19, "bold"), "device": (ui, 13, "bold"),
                 "mono": ("monospace", 10, "normal")}
        return {key: tkfont.Font(self.root, name=f"SC830{key}", family=family, size=size, weight=weight)
                for key, (family, size, weight) in specs.items()}

    # -- images --------------------------------------------------------------

    def image(self, name, raster):
        data = raster.png()
        if name in self.images:
            self.images[name].configure(data=data, format="png")
        else:
            self.images[name] = tk.PhotoImage(master=self.root, data=data, format="png")
        return self.images[name]

    def plate(self, radius, fill, edge=None, width=1.0, middle=48):
        # ttk tiles the centre of a sliced image, and Tk blends every tile that
        # has partial alpha in software; a wide centre keeps the tile count low.
        side = 2 * radius + self.px(middle)
        raster = Raster(side, side)
        raster.framed(0, 0, side, side, radius, fill, edge, width)
        return raster

    def render(self):
        c, p = self.palette, self.px
        r = p(7)
        light_edge = c.edge
        self.image("clear", Raster(2 * r + p(48), 2 * r + p(48)))
        self.image("btn", self.plate(r, c.button, light_edge))
        self.image("btn_hover", self.plate(r, mix(c.button, c.text, 0.06), light_edge))
        self.image("btn_press", self.plate(r, mix(c.button, c.text, 0.12), light_edge))
        self.image("btn_off", self.plate(r, c.disabled))
        self.image("pbtn", self.plate(r, c.accent))
        self.image("pbtn_hover", self.plate(r, c.accent_hover))
        self.image("pbtn_press", self.plate(r, c.accent_press))
        self.image("pbtn_off", self.plate(r, mix(c.accent, c.card, 0.6)))
        self.image("field", self.plate(r, c.field, light_edge))
        self.image("field_focus", self.plate(r, c.field, mix(c.card, c.accent, 0.8), width=p(2)))
        self.image("field_off", self.plate(r, c.disabled))
        self.image("track", self.plate(p(8), c.control))
        self.image("seg_on", self.plate(p(6.5), c.thumb, mix(c.card, c.text, 0.1) if c.light else None))
        self.image("seg_hover", self.plate(p(6.5), mix(c.control, c.text, 0.05)))
        self.image("nav_on", self.plate(p(8), c.accent))
        self.image("nav_hover", self.plate(p(8), c.side_hover))
        group = p(12)
        self.image("group", self.plate(group, c.card, mix(c.card, c.text, 0.07) if c.light else None, middle=160))
        self.image("zone", self.plate(group, c.card, c.separator, middle=160))
        self.image("zone_focus", self.plate(group, mix(c.card, c.accent, 0.06), c.accent, width=p(2), middle=160))
        for name, amount in (("thumb", 0.28), ("thumb_hover", 0.45)):
            thumb = Raster(p(6), p(54))
            thumb.rect(0, 0, p(6), p(54), p(3), mix(c.card, c.text, amount))
            self.image(name, thumb)
        self.image("trough", Raster(p(10), p(120)))
        self.render_popup()
        self.render_switches()
        self.render_dots()
        self.render_icons()

    def render_popup(self):
        c, p = self.palette, self.px
        size = p(18)
        for name, fill, glyph in (("popup", c.accent, c.accent_text),
                                  ("popup_off", c.switch_off, c.tertiary)):
            raster = Raster(size, size)
            raster.rect(0, 0, size, size, p(5), fill)
            u, w = size / 18, max(1.2, 1.6 * self.scale)
            raster.line(5.5 * u, 7.5 * u, 9 * u, 4.5 * u, w, glyph)
            raster.line(9 * u, 4.5 * u, 12.5 * u, 7.5 * u, w, glyph)
            raster.line(5.5 * u, 10.5 * u, 9 * u, 13.5 * u, w, glyph)
            raster.line(9 * u, 13.5 * u, 12.5 * u, 10.5 * u, w, glyph)
            self.image(name, raster)

    def render_switches(self):
        c, p = self.palette, self.px
        w, h = p(38), p(22)
        for name, on, faded in (("switch_on", True, False), ("switch_off", False, False),
                                ("switch_on_off", True, True), ("switch_off_off", False, True)):
            raster = Raster(w, h)
            track = c.accent if on else c.switch_off
            raster.rect(0, 0, w, h, h / 2, mix(track, c.card, 0.5) if faded else track)
            knob = h / 2 - p(2)
            cx = w - h / 2 if on else h / 2
            raster.circle(cx, h / 2 + 0.6 * self.scale, knob + 0.4, "#000000", 0.18)
            raster.circle(cx, h / 2, knob, mix(c.knob, c.card, 0.4) if faded else c.knob)
            self.image(name, raster)

    def render_dots(self):
        c, p = self.palette, self.px
        size = p(18)
        mid = size / 2
        raster = Raster(size, size)
        raster.circle(mid, mid, mid, mix(c.card, c.text, 0.3))
        raster.circle(mid, mid, mid - max(1, self.scale), c.field)
        self.image("radio_off", raster)
        raster = Raster(size, size)
        raster.circle(mid, mid, mid, c.accent)
        raster.circle(mid, mid, size * 0.19, c.accent_text)
        self.image("radio_on", raster)
        for name, color in (("dot_ok", c.green), ("dot_error", c.red), ("dot_wait", c.tertiary)):
            raster = Raster(p(8), p(8))
            raster.circle(p(8) / 2, p(8) / 2, p(8) / 2, color)
            self.image(name, raster)

    def render_icons(self):
        c, p = self.palette, self.px
        for name, color, draw in zip(("icon_dpi", "icon_light", "icon_buttons", "icon_macros"),
                                     c.icons, (self.draw_target, self.draw_sun, self.draw_mouse, self.draw_keys)):
            size = p(22)
            raster = Raster(size, size)
            raster.rect(0, 0, size, size, p(6), color)
            draw(raster, size / 22, c.on(color))
            self.image(name, raster)
        size = p(40)
        raster = Raster(size, size)
        raster.rect(0, 0, size, size, p(10), c.accent)
        self.draw_mouse(raster, size / 22, c.accent_text, 1.3)
        self.image("icon_device", raster)

    def draw_target(self, r, u, ink):
        w = 1.5 * u
        r.ring(11 * u, 11 * u, 5.5 * u, w, ink)
        for ax, ay, bx, by in ((11, 2.5, 11, 6.5), (11, 15.5, 11, 19.5), (2.5, 11, 6.5, 11), (15.5, 11, 19.5, 11)):
            r.line(ax * u, ay * u, bx * u, by * u, w, ink)
        r.circle(11 * u, 11 * u, 1.5 * u, ink)

    def draw_sun(self, r, u, ink):
        r.circle(11 * u, 11 * u, 3.2 * u, ink)
        for i in range(8):
            a = i * math.pi / 4
            r.line((11 + 5.6 * math.cos(a)) * u, (11 + 5.6 * math.sin(a)) * u,
                   (11 + 7.6 * math.cos(a)) * u, (11 + 7.6 * math.sin(a)) * u, 1.5 * u, ink)

    def draw_mouse(self, r, u, ink, weight=1.0):
        w = 1.5 * u * weight
        r.rounded_outline(6.8 * u, 3.8 * u, 15.2 * u, 18.2 * u, 4.2 * u, w, ink)
        r.line(11 * u, 4.5 * u, 11 * u, 8.5 * u, w, ink)

    def draw_keys(self, r, u, ink):
        r.rounded_outline(3.5 * u, 6 * u, 18.5 * u, 16 * u, 2.2 * u, 1.4 * u, ink)
        for x in (7, 10, 13, 16):
            r.circle((x - 0.5) * u, 9.6 * u, 0.95 * u, ink)
        r.line(7.5 * u, 12.8 * u, 14.5 * u, 12.8 * u, 1.4 * u, ink)

    # -- ttk theme -----------------------------------------------------------

    def build(self):
        st, i, p = self.style, self.images, self.px
        st.theme_create("sc830", parent="clam")
        st.theme_use("sc830")
        r = p(7)

        def element(name, default, *states, border=r, padding=None, sticky="nsew", size=None):
            options = {"border": border, "sticky": sticky}
            if padding is not None:
                options["padding"] = padding
            # Without an explicit size the (deliberately large) image would be
            # the element's minimum size.
            if size is None and sticky == "nsew":
                edge = max(border) if isinstance(border, tuple) else border
                size = (2 * edge + 2, 2 * edge + 2)
            if size:
                options["width"], options["height"] = size
            st.element_create(name, "image", i[default], *[(s, i[n]) for s, n in states], **options)

        element("Btn.bg", "btn", ("disabled", "btn_off"), ("pressed", "btn_press"), ("active", "btn_hover"))
        element("PBtn.bg", "pbtn", ("disabled", "pbtn_off"), ("pressed", "pbtn_press"), ("active", "pbtn_hover"))
        element("Field.bg", "field", ("disabled", "field_off"), ("focus", "field_focus"), padding=p(3))
        element("Popup.bg", "field", ("disabled", "field_off"), padding=p(3))
        element("Popup.arrow", "popup", ("disabled", "popup_off"), border=0, sticky="")
        element("Track.bg", "track", border=p(8))
        element("Seg.bg", "clear", ("selected", "seg_on"), ("active", "seg_hover"), border=p(6.5))
        element("Nav.bg", "clear", ("selected", "nav_on"), ("active", "nav_hover"), border=p(8))
        element("Group.bg", "group", border=p(12))
        element("Zone.bg", "zone", ("focus", "zone_focus"), border=p(12))
        element("Switch.ind", "switch_off", ("disabled selected", "switch_on_off"), ("disabled", "switch_off_off"),
                ("selected", "switch_on"), border=0, sticky="")
        element("Radio.ind", "radio_off", ("selected", "radio_on"), border=0, sticky="")
        element("Slim.trough", "trough", border=0, padding=p(2), sticky="ns", size=(p(10), p(10)))
        element("Slim.thumb", "thumb", ("active", "thumb_hover"), ("pressed", "thumb_hover"), border=(0, p(3)),
                size=(p(6), p(8)))

        def label(container, kind="Label"):
            return [(container, {"sticky": "nsew", "children": [
                (f"{kind}.padding", {"sticky": "nsew", "children": [(f"{kind}.label", {"sticky": "nsew"})]})]})]
        st.layout("TButton", label("Btn.bg", "Button"))
        st.layout("Primary.TButton", label("PBtn.bg", "Button"))
        st.layout("Plain.TButton", [("Button.padding", {"sticky": "nsew", "children": [
            ("Button.label", {"sticky": "nsew"})]})])
        st.layout("Seg.TRadiobutton", label("Seg.bg", "Radiobutton"))
        st.layout("Nav.TRadiobutton", label("Nav.bg", "Radiobutton"))
        st.layout("Zone.TLabel", label("Zone.bg"))
        st.layout("Group.TFrame", [("Group.bg", {"sticky": "nsew"})])
        st.layout("Track.TFrame", [("Track.bg", {"sticky": "nsew"})])
        st.layout("TEntry", [("Field.bg", {"sticky": "nsew", "children": [
            ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
        st.layout("TCombobox", [("Popup.bg", {"sticky": "nsew", "children": [
            ("Popup.arrow", {"side": "right", "sticky": ""}),
            ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]})]})])
        st.layout("Switch.TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
            ("Switch.ind", {"side": "right", "sticky": ""}),
            ("Checkbutton.label", {"side": "right", "sticky": "nsew"})]})])
        st.layout("Radio.TRadiobutton", [("Radiobutton.padding", {"sticky": "nsew", "children": [
            ("Radio.ind", {"side": "left", "sticky": ""}),
            ("Radiobutton.label", {"side": "left", "sticky": "nsew"})]})])
        st.layout("Vertical.TScrollbar", [("Slim.trough", {"sticky": "ns", "children": [
            ("Slim.thumb", {"expand": "1", "sticky": "nsew"})]})])

    def configure(self):
        st, c, f, p = self.style, self.palette, self.fonts, self.px
        st.configure(".", background=c.window, foreground=c.text, font=f["body"], bordercolor=c.separator,
                     lightcolor=c.card, darkcolor=c.card, troughcolor=c.card, focuscolor=c.accent,
                     selectbackground=c.accent_soft, selectforeground=c.text, insertcolor=c.text,
                     fieldbackground=c.field)
        labels = {"TLabel": (f["body"], c.text), "Title.TLabel": (f["title"], c.text),
                  "Device.TLabel": (f["device"], c.text), "Headline.TLabel": (f["headline"], c.text),
                  "Secondary.TLabel": (f["body"], c.secondary), "Caption.TLabel": (f["small"], c.secondary),
                  "Section.TLabel": (f["section"], c.secondary)}
        for name, (font, color) in labels.items():
            st.configure(name, font=font, foreground=color, padding=0)
        st.configure("TButton", padding=(p(14), p(5)), anchor="center", foreground=c.text)
        st.map("TButton", foreground=[("disabled", c.tertiary)])
        st.configure("Primary.TButton", foreground=c.accent_text)
        st.map("Primary.TButton", foreground=[("disabled", mix(c.accent_text, c.accent, 0.4))])
        st.configure("Small.TButton", padding=(p(9), p(2)))
        st.configure("Plain.TButton", padding=(p(2), p(2)), foreground=c.accent)
        st.map("Plain.TButton", foreground=[("disabled", c.tertiary), ("pressed", c.accent_press),
                                            ("active", c.accent_hover)])
        st.configure("TEntry", padding=(p(8), p(3)), foreground=c.text, insertcolor=c.text,
                     selectbackground=c.accent_soft, selectforeground=c.text)
        st.map("TEntry", foreground=[("disabled", c.tertiary)])
        st.configure("TCombobox", padding=(p(8), p(2), p(4), p(2)), foreground=c.text, arrowsize=0)
        st.map("TCombobox", foreground=[("disabled", c.tertiary)],
               selectbackground=[("readonly", c.field)], selectforeground=[("readonly", c.text)],
               fieldbackground=[("readonly", c.field)])
        st.configure("Seg.TRadiobutton", background=c.control, foreground=c.secondary, anchor="center",
                     padding=(p(12), p(3)), font=f["body"])
        st.map("Seg.TRadiobutton", foreground=[("selected", c.text), ("active", c.text)])
        st.configure("Nav.TRadiobutton", foreground=c.text, padding=(p(8), p(6)), anchor="w",
                     font=f["body"])
        st.map("Nav.TRadiobutton", foreground=[("selected", c.accent_text)])
        st.configure("Switch.TCheckbutton", foreground=c.secondary, padding=0)
        st.configure("Radio.TRadiobutton", foreground=c.secondary, padding=0)
        st.map("Radio.TRadiobutton", foreground=[("selected", c.text)])
        st.configure("Zone.TLabel", background=c.window, foreground=c.secondary, anchor="center",
                     justify="center", padding=p(20))
        st.map("Zone.TLabel", foreground=[("focus", c.text)])
        st.configure("Group.TFrame", background=c.window)
        st.configure("Hair.TFrame", background=c.separator)
        st.configure("Vertical.TScrollbar", background=c.card, troughcolor=c.card)
        st.configure("ComboboxPopdownFrame", background=c.card, bordercolor=c.separator,
                     relief="solid", borderwidth=1)
        bases = ("TFrame", "Track.TFrame", "TButton", "Primary.TButton", "Small.TButton", "Plain.TButton",
                 "TEntry", "TCombobox", "Switch.TCheckbutton", "Radio.TRadiobutton", "Nav.TRadiobutton",
                 "Vertical.TScrollbar", *labels)
        for surface in SURFACES:
            for base in bases:
                st.configure(f"{surface}.{base}", background=c.surfaces[surface])

    def apply(self):
        c = self.palette
        self.render()
        if not self.built:
            self.build()
            self.built = True
        self.configure()
        self.root.tk_setPalette(background=c.window, foreground=c.text, activeBackground=c.side_hover,
                                activeForeground=c.text, highlightColor=c.accent,
                                highlightBackground=c.window, selectBackground=c.accent,
                                selectForeground=c.accent_text, insertBackground=c.text,
                                troughColor=c.control, disabledForeground=c.tertiary)
        for option, value in (("background", c.card), ("foreground", c.text),
                              ("selectBackground", c.accent), ("selectForeground", c.accent_text),
                              ("font", self.fonts["body"]), ("borderWidth", 0), ("highlightThickness", 0)):
            self.root.option_add(f"*TCombobox*Listbox.{option}", value)
        for combo in self.combos:
            self.style_popdown(combo)
        for listener in list(self.listeners):
            listener(c)

    def style_popdown(self, combo):
        c = self.palette
        try:
            popdown = self.root.tk.call("ttk::combobox::PopdownWindow", combo)
            self.root.tk.call(f"{popdown}.f.l", "configure", "-background", c.card, "-foreground", c.text,
                              "-selectbackground", c.accent, "-selectforeground", c.accent_text,
                              "-font", self.fonts["body"], "-borderwidth", 0, "-highlightthickness", 0,
                              "-activestyle", "none")
        except tk.TclError:
            pass

    def register_combo(self, combo):
        self.combos.append(combo)
        self.style_popdown(combo)

    def listen(self, callback):
        self.listeners.append(callback)
        return callback

    def watch(self):
        """Follow `omarchy theme set`, which swaps the current theme directory."""
        signature = theme_signature()
        if signature != self.signature:
            colors = read_theme()
            if colors is not None:
                self.signature = signature
                self.palette = Palette(colors)
                self.apply()
        self.root.after(POLL_MS, self.watch)
