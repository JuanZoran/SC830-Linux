"""Chinese native desktop UI. Hardware work runs outside the Tk event loop."""

import copy
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from .device import Mouse
from .profiles import (applied_record, compile_profile, default_profile, load_profile,
                       profile_path, save_json)
from .protocol import (BUTTON_NAMES, DPI_VALUES, LIGHT_MODES, MEDIA_USAGES,
                       key_usage, macro_events)
from .theme import Raster, Theme, mix

ACTION_TYPES = {"左键": "left", "右键": "right", "中键": "middle", "前进": "forward",
                "后退": "back", "DPI 循环": "dpi-cycle", "DPI 增加": "dpi-up",
                "DPI 减少": "dpi-down", "禁用": "disabled", "快捷键": "key:",
                "多媒体": "media:", "火力键": "fire:", "宏": "macro:", "原始编码": "raw:"}
ARGUMENT_HINTS = {"key:": "组合键，例如 ctrl+c", "media:": "例如 mute、play-pause、next",
                  "fire:": "间隔:次数，例如 50:3", "macro:": "宏槽位 1–6", "raw:": "原程序的十六进制编码"}
LOOPS = {"指定次数": "count", "直到松开按键": "release", "直到再次按键": "press"}
SECTIONS = {"dpi", "polling", "lighting", "buttons", "macros"}
PAGES = (("dpi", "DPI 与回报率", "icon_dpi", "灵敏度档位、指示灯颜色与回报率"),
         ("lighting", "灯效", "icon_light", "灯光模式、速度、亮度与颜色"),
         ("buttons", "按键", "icon_buttons", "为 6 个可编程按键分配功能"),
         ("macros", "宏", "icon_macros", "录制并编辑鼠标端的 6 个宏槽位"))


class App:
    def __init__(self, root):
        self.root = root
        self.theme = Theme(root)
        p = self.px = self.theme.px
        self.root.title("SC830 · 鼠标控制中心")
        self.root.geometry(f"{p(1060)}x{p(740)}")
        self.root.minsize(p(940), p(660))
        self.profile = default_profile()
        self.busy = False
        self.jobs = queue.Queue()
        self.action_buttons = []
        self.swatches, self.swatch_updaters = {}, []
        self.device_var = tk.StringVar(value="正在连接…")
        self.device_detail = tk.StringVar(value="正在查询设备状态")
        self.note_var = tk.StringVar(value="当前显示本地配置；修改后点击应用。")
        self.page_var = tk.StringVar(value="dpi")
        shell = ttk.Frame(root, style="Window.TFrame")
        shell.pack(fill="both", expand=True)
        self.build_sidebar(shell)
        ttk.Frame(shell, style="Hair.TFrame", width=1).pack(side="left", fill="y")
        content = ttk.Frame(shell, style="Window.TFrame")
        content.pack(side="left", fill="both", expand=True)
        footer = ttk.Frame(content, style="Window.TFrame", padding=(p(32), p(14), p(32), p(16)))
        footer.pack(side="bottom", fill="x")
        ttk.Frame(content, style="Hair.TFrame", height=1).pack(side="bottom", fill="x")
        self.button(footer, "应用全部配置", lambda: self.apply(SECTIONS), "Window", primary=True).pack(side="right")
        self.button(footer, "应用当前页", self.apply_current, "Window").pack(side="right", padx=(0, p(10)))
        ttk.Label(footer, textvariable=self.note_var, style="Window.Secondary.TLabel", wraplength=p(440),
                  justify="left").pack(side="left", fill="x", expand=True)
        host = ttk.Frame(content, style="Window.TFrame")
        host.pack(fill="both", expand=True)
        host.grid_rowconfigure(0, weight=1)
        host.grid_columnconfigure(0, weight=1)
        self.tabs = {}
        for key, title, _, subtitle in PAGES:
            page = ttk.Frame(host, style="Window.TFrame", padding=(p(32), p(26), p(32), p(18)))
            page.grid(row=0, column=0, sticky="nsew")
            ttk.Label(page, text=title, style="Window.Title.TLabel").pack(anchor="w")
            ttk.Label(page, text=subtitle, style="Window.Secondary.TLabel").pack(anchor="w", pady=(p(2), p(20)))
            self.tabs[key] = page
        self.build_dpi()
        self.build_lighting()
        self.build_buttons()
        self.build_macros()
        self.show_page()
        self.theme.listen(self.retheme)
        self.retheme(self.theme.palette)
        self.fill(self.profile)
        if profile_path(1).exists():
            try:
                self.load_slot()
            except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
                self.note_var.set(f"本地配置读取失败：{exc}；当前显示默认配置。")
        self.root.after(100, self.refresh)
        self.root.after(50, self.finish_jobs)
        self.root.after(1500, self.theme.watch)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def guarded(self, callback):
        def invoke(*args):
            if self.busy:
                return
            try:
                return callback(*args)
            except (ValueError, OSError, KeyError, TypeError, IndexError, tk.TclError) as exc:
                messagebox.showerror("请检查设置", str(exc), parent=self.root)
        return invoke

    # -- building blocks -----------------------------------------------------

    def button(self, parent, text, callback, surface, primary=False, plain=False):
        kind = "Primary.TButton" if primary else "Plain.TButton" if plain else "TButton"
        widget = ttk.Button(parent, text=text, command=self.guarded(callback), style=f"{surface}.{kind}")
        self.action_buttons.append(widget)
        return widget

    def entry(self, parent, var, surface="Card", **options):
        return ttk.Entry(parent, textvariable=var, style=f"{surface}.TEntry", font=self.theme.fonts["body"], **options)

    def combo(self, parent, var, values, width):
        widget = ttk.Combobox(parent, values=values, textvariable=var, state="readonly", width=width, height=12,
                              style="Card.TCombobox", font=self.theme.fonts["body"])
        self.theme.register_combo(widget)
        return widget

    def switch(self, parent, var, text=""):
        return ttk.Checkbutton(parent, text=text, variable=var, style="Card.Switch.TCheckbutton", takefocus=False)

    def segmented(self, parent, var, options, surface="Card", command=None, width=None, compact=False):
        track = ttk.Frame(parent, style=f"{surface}.Track.TFrame", padding=self.px(2))
        padding = (self.px(4), self.px(3)) if compact else None
        for value, label in options:
            ttk.Radiobutton(track, text=label, value=value, variable=var, command=command, width=width,
                            padding=padding, style="Seg.TRadiobutton", takefocus=False).pack(
                side="left", fill="x", expand=True)
        return track

    def stepper(self, parent, var, low, high):
        box = ttk.Frame(parent, style="Card.TFrame")

        def step(delta):
            try:
                value = var.get()
            except tk.TclError:
                value = low
            var.set(max(low, min(high, value + delta)))
        ttk.Button(box, text="−", width=2, command=lambda: step(-1), style="Card.Small.TButton",
                   takefocus=False).pack(side="left")
        self.entry(box, var, width=4, justify="center").pack(side="left", padx=self.px(6))
        ttk.Button(box, text="+", width=2, command=lambda: step(1), style="Card.Small.TButton",
                   takefocus=False).pack(side="left")
        return box

    def group(self, parent, title=None, footnote=None, expand=False):
        p = self.px
        if title:
            ttk.Label(parent, text=title, style="Window.Section.TLabel").pack(anchor="w", padx=p(6), pady=(0, p(6)))
        card = ttk.Frame(parent, style="Group.TFrame", padding=(p(16), p(4)))
        card.pack(fill="both" if expand else "x", expand=expand)
        if footnote:
            ttk.Label(parent, text=footnote, style="Window.Caption.TLabel", wraplength=p(700),
                      justify="left").pack(anchor="w", padx=p(6), pady=(p(7), 0))
        ttk.Frame(parent, style="Window.TFrame", height=p(20)).pack(fill="x")
        return card

    def row(self, card, label=None, detail=None, textvariable=None):
        """One inset-grouped row; controls are packed on its right side."""
        wrap = ttk.Frame(card, style="Card.TFrame")
        if card.pack_slaves():
            ttk.Frame(wrap, style="Hair.TFrame", height=1).pack(fill="x")
        wrap.pack(fill="x")
        row = ttk.Frame(wrap, style="Card.TFrame", padding=(0, self.px(9)))
        row.pack(fill="x")
        if label is not None:
            text = ttk.Frame(row, style="Card.TFrame")
            text.pack(side="left")
            ttk.Label(text, text=label, style="Card.TLabel").pack(anchor="w")
            row.caption = ttk.Label(text, text=detail or "", textvariable=textvariable, style="Card.Caption.TLabel")
            if detail:
                row.caption.pack(anchor="w")
        return wrap, row

    def swatch_image(self, color, size):
        key = (color.upper(), size)
        if key not in self.swatches:
            c, s = self.theme.palette, self.px(size)
            raster = Raster(s, s)
            raster.circle(s / 2, s / 2, s / 2, mix(c.card, c.text, 0.22))
            try:
                raster.circle(s / 2, s / 2, s / 2 - max(1, self.theme.scale), color)
            except ValueError:
                raster.circle(s / 2, s / 2, s / 2 - max(1, self.theme.scale), c.control)
            self.swatches[key] = tk.PhotoImage(master=self.root, data=raster.png(), format="png")
        return self.swatches[key]

    def swatch(self, parent, var, command, size=20):
        label = ttk.Label(parent, style="Card.TLabel", cursor="hand2")

        def update(*_):
            label.configure(image=self.swatch_image(var.get(), size))
        var.trace_add("write", update)
        update()
        label.bind("<Button-1>", lambda _: self.guarded(command)())
        self.swatch_updaters.append(update)
        return label

    # -- layout --------------------------------------------------------------

    def build_sidebar(self, parent):
        p, images = self.px, self.theme.images
        side = ttk.Frame(parent, style="Side.TFrame", width=p(250), padding=(p(14), p(22), p(14), p(18)))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        device = ttk.Frame(side, style="Side.TFrame", padding=(p(6), 0))
        device.pack(fill="x")
        ttk.Label(device, image=images["icon_device"], style="Side.TLabel").grid(
            row=0, column=0, rowspan=2, sticky="w", padx=(0, p(12)))
        ttk.Label(device, text="SC830", style="Side.Device.TLabel").grid(row=0, column=1, sticky="sw")
        status = ttk.Frame(device, style="Side.TFrame")
        status.grid(row=1, column=1, sticky="nw")
        self.device_dot = ttk.Label(status, image=images["dot_wait"], style="Side.TLabel")
        self.device_dot.pack(side="left", padx=(0, p(6)))
        ttk.Label(status, textvariable=self.device_var, style="Side.Caption.TLabel").pack(side="left")
        ttk.Label(side, textvariable=self.device_detail, style="Side.Caption.TLabel", wraplength=p(208),
                  justify="left").pack(fill="x", padx=p(6), pady=(p(12), 0))
        self.button(side, "刷新设备", self.refresh, "Side", plain=True).pack(anchor="w", padx=p(4), pady=(p(2), p(18)))
        for key, title, icon, _ in PAGES:
            ttk.Radiobutton(side, text="  " + title, image=images[icon], compound="left", value=key,
                            variable=self.page_var, command=self.show_page, style="Side.Nav.TRadiobutton",
                            takefocus=False).pack(fill="x", pady=p(1))
        bottom = ttk.Frame(side, style="Side.TFrame", padding=(p(6), 0))
        bottom.pack(side="bottom", fill="x")
        ttk.Label(bottom, text="本地配置", style="Side.Section.TLabel").pack(anchor="w", pady=(0, p(8)))
        self.slot_var = tk.StringVar(value="1")
        self.segmented(bottom, self.slot_var, [(str(i), str(i)) for i in range(1, 6)], "Side",
                       compact=True).pack(fill="x")
        self.name_var = tk.StringVar()
        self.entry(bottom, self.name_var, "Side").pack(fill="x", pady=p(8))
        actions = ttk.Frame(bottom, style="Side.TFrame")
        actions.pack(fill="x")
        actions.columnconfigure((0, 1), weight=1, uniform="profile")
        self.button(actions, "读取", self.load_slot, "Side").grid(row=0, column=0, sticky="ew", padx=(0, p(4)))
        self.button(actions, "保存", self.save_slot, "Side").grid(row=0, column=1, sticky="ew", padx=(p(4), 0))
        files = ttk.Frame(bottom, style="Side.TFrame")
        files.pack(fill="x", pady=(p(6), 0))
        self.button(files, "导入…", self.import_profile, "Side", plain=True).pack(side="left")
        self.button(files, "导出…", self.export_profile, "Side", plain=True).pack(side="right")

    def build_dpi(self):
        p, tab = self.px, self.tabs["dpi"]
        self.polling = tk.StringVar(value="500")
        card = self.group(tab, "传感器")
        _, row = self.row(card, "回报率", "每秒向电脑报告位置的次数")
        self.segmented(row, self.polling, [(v, f"{v} Hz") for v in ("125", "250", "500", "1000")]).pack(side="right")
        card = self.group(tab, "DPI 档位", "PAW3212 使用离散 DPI 档位。圆点选择当前档位，开启的档位参与 DPI 键循环，"
                          "点击色块修改该档的指示灯颜色。")
        self.dpi_count = tk.IntVar(value=5)
        _, row = self.row(card, "档位数量")
        self.segmented(row, self.dpi_count, [(i, str(i)) for i in range(1, 6)], width=3).pack(side="right")
        self.active = tk.IntVar()
        self.dpi_rows, self.dpi_wraps = [], []
        for i in range(5):
            enabled, value, color = tk.BooleanVar(), tk.StringVar(), tk.StringVar()
            wrap, row = self.row(card)
            self.swatch(row, color, lambda v=color: self.pick_color(v)).pack(side="left", padx=(0, p(12)))
            ttk.Label(row, text=f"档位 {i + 1}", style="Card.TLabel").pack(side="left")
            self.switch(row, enabled, "启用  ").pack(side="right")
            ttk.Radiobutton(row, text=" 当前", variable=self.active, value=i, style="Card.Radio.TRadiobutton",
                            takefocus=False).pack(side="right", padx=(0, p(28)))
            self.combo(row, value, DPI_VALUES, 7).pack(side="right", padx=(0, p(28)))
            self.dpi_rows.append((enabled, value, color))
            self.dpi_wraps.append(wrap)
        self.dpi_count.trace_add("write", lambda *_: self.show_dpi_rows())

    def show_dpi_rows(self):
        try:
            count = self.dpi_count.get()
        except tk.TclError:
            return
        for wrap in self.dpi_wraps:
            wrap.pack_forget()
        for wrap in self.dpi_wraps[:count]:
            wrap.pack(fill="x")
        if count and self.active.get() >= count:
            self.active.set(count - 1)

    def build_lighting(self):
        p, tab = self.px, self.tabs["lighting"]
        self.light_mode = tk.StringVar()
        self.light_speed, self.light_brightness = tk.IntVar(), tk.IntVar()
        self.light_direction = tk.IntVar()
        self.light_colors = tk.StringVar()
        card = self.group(tab, "灯光")
        _, row = self.row(card, "模式")
        self.combo(row, self.light_mode, [title for _, title in LIGHT_MODES.values()], 10).pack(side="right")
        for label, var in (("速度", self.light_speed), ("亮度", self.light_brightness)):
            _, row = self.row(card, label)
            self.segmented(row, var, [(i, str(i)) for i in range(1, 5)], width=3).pack(side="right")
        _, row = self.row(card, "反向流动", "仅混彩流光模式生效")
        self.switch(row, self.light_direction).pack(side="right")
        card = self.group(tab, "颜色", "点击色块修改颜色，右键删除。呼吸和混彩支持最多 7 色；单色常亮使用第一个颜色。")
        _, row = self.row(card)
        self.color_strip = ttk.Frame(row, style="Card.TFrame")
        self.color_strip.pack(side="left")
        self.button(row, "添加颜色", self.add_light_color, "Card").pack(side="right")
        self.light_colors.trace_add("write", lambda *_: self.draw_light_colors())

    def light_color_list(self):
        return [s.strip() for s in self.light_colors.get().split(",") if s.strip()]

    def draw_light_colors(self):
        for child in self.color_strip.winfo_children():
            child.destroy()
        colors = self.light_color_list()
        for i, color in enumerate(colors):
            label = ttk.Label(self.color_strip, image=self.swatch_image(color, 28), style="Card.TLabel", cursor="hand2")
            label.pack(side="left", padx=(0, self.px(10)))
            label.bind("<Button-1>", lambda _, i=i: self.guarded(self.edit_light_color)(i))
            label.bind("<Button-3>", lambda _, i=i: self.guarded(self.remove_light_color)(i))
        if not colors:
            ttk.Label(self.color_strip, text="尚未设置颜色", style="Card.Caption.TLabel").pack(side="left")

    def build_buttons(self):
        p, tab = self.px, self.tabs["buttons"]
        self.bindings = []
        card = self.group(tab, "按键映射", "快捷键：ctrl+c / alt+tab / super+d    宏：槽位 1–6\n"
                          "多媒体：" + ", ".join(MEDIA_USAGES) + "\n火力键：间隔:次数，例如 50:3；次数 0 为按住连发。")
        for label in BUTTON_NAMES:
            kind, argument, hint = tk.StringVar(), tk.StringVar(), tk.StringVar()
            _, row = self.row(card, label, textvariable=hint)
            combo = self.combo(row, kind, list(ACTION_TYPES), 9)
            combo.pack(side="right")
            entry = self.entry(row, argument, width=20)

            def update(*_, kind=kind, entry=entry, combo=combo, hint=hint, caption=row.caption):
                action = ACTION_TYPES.get(kind.get(), "")
                hint.set(ARGUMENT_HINTS.get(action, ""))
                if action.endswith(":"):
                    entry.pack(side="right", padx=(p(10), 0), before=combo)
                    caption.pack(anchor="w")
                else:
                    entry.pack_forget()
                    caption.pack_forget()
            kind.trace_add("write", update)
            self.bindings.append((kind, argument))

    def build_macros(self):
        p, tab = self.px, self.tabs["macros"]
        self.macro_slot = tk.StringVar(value="1")
        self.macro_name = tk.StringVar()
        self.macro_loop, self.macro_repeat = tk.StringVar(), tk.IntVar()
        card = self.group(tab, "宏槽位")
        _, row = self.row(card, "槽位", "鼠标端共 6 个槽位")
        self.segmented(row, self.macro_slot, [(str(i), str(i)) for i in range(1, 7)],
                       command=self.load_macro, width=3).pack(side="right")
        _, row = self.row(card, "名称")
        self.entry(row, self.macro_name, width=18).pack(side="right")
        _, row = self.row(card, "循环方式")
        self.stepper(row, self.macro_repeat, 1, 255).pack(side="right")
        ttk.Label(row, text="次数", style="Card.Secondary.TLabel").pack(side="right", padx=(p(20), p(8)))
        self.combo(row, self.macro_loop, list(LOOPS), 11).pack(side="right")
        card = self.group(tab, "事件", "编辑事件 JSON；支持 key、mouse、wheel、move_x、move_y 和 delay。"
                          "每个宏最多 125 个动作，单步延时上限 4095 毫秒。修改后先保存此宏，再到按键页绑定。",
                          expand=True)
        _, row = self.row(card)
        self.button(row, "录制宏…", self.record_macro, "Card").pack(side="left", padx=(0, p(8)))
        self.button(row, "保存此宏", self.save_macro, "Card", primary=True).pack(side="left")
        for label, callback in [("删除此宏", self.delete_macro), ("导出…", self.export_macro),
                                ("导入…", self.import_macro)]:
            self.button(row, label, callback, "Card", plain=True).pack(side="right", padx=(p(14), 0))
        ttk.Frame(card, style="Hair.TFrame", height=1).pack(fill="x")
        holder = ttk.Frame(card, style="Card.TFrame")
        holder.pack(fill="both", expand=True, pady=(p(8), p(10)))
        self.editor = tk.Text(holder, height=6, relief="flat", borderwidth=0, highlightthickness=0, wrap="none",
                              padx=p(2), pady=p(2), undo=True, font=self.theme.fonts["mono"])
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.editor.yview,
                               style="Card.Vertical.TScrollbar")
        self.editor.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.editor.pack(side="left", fill="both", expand=True)

    def show_page(self):
        self.tabs[self.page_var.get()].tkraise()

    def retheme(self, palette):
        self.swatches.clear()
        for update in self.swatch_updaters:
            update()
        self.draw_light_colors()
        self.editor.configure(bg=palette.card, fg=palette.text, insertbackground=palette.text,
                              selectbackground=palette.accent_soft, selectforeground=palette.text,
                              inactiveselectbackground=palette.accent_soft)

    # -- actions -------------------------------------------------------------

    def pick_color(self, var):
        result = colorchooser.askcolor(var.get(), parent=self.root)[1]
        if result:
            var.set(result.upper())

    def add_light_color(self):
        colors = self.light_color_list()
        if len(colors) >= 7:
            raise ValueError("最多可设置 7 个颜色")
        result = colorchooser.askcolor(colors[-1] if colors else "#FFFFFF", parent=self.root)[1]
        if result:
            self.light_colors.set(", ".join(colors + [result.upper()]))

    def edit_light_color(self, index):
        colors = self.light_color_list()
        result = colorchooser.askcolor(colors[index], parent=self.root)[1]
        if result:
            colors[index] = result.upper()
            self.light_colors.set(", ".join(colors))

    def remove_light_color(self, index):
        colors = self.light_color_list()
        colors.pop(index)
        self.light_colors.set(", ".join(colors))

    def fill(self, profile):
        self.profile = copy.deepcopy(profile)
        self.name_var.set(profile["name"])
        dpi = profile["dpi"]
        self.dpi_count.set(len(dpi["values"]))
        self.active.set(dpi["active"])
        for i, (enabled, value, color) in enumerate(self.dpi_rows):
            enabled.set(dpi.get("enabled", [True] * len(dpi["values"]))[i] if i < len(dpi["values"]) else False)
            value.set(str(dpi["values"][i]) if i < len(dpi["values"]) else "4000")
            color.set(dpi["colors"][i] if i < len(dpi["colors"]) else "#FFFFFF")
        self.polling.set(str(profile["polling_rate"]))
        lighting = profile["lighting"]
        self.light_mode.set(LIGHT_MODES[lighting["mode"]][1])
        self.light_speed.set(lighting["speed"])
        self.light_brightness.set(lighting["brightness"])
        self.light_direction.set(lighting.get("direction", 0))
        self.light_colors.set(", ".join(lighting["colors"]))
        reverse = {v: k for k, v in ACTION_TYPES.items()}
        for (kind, argument), binding in zip(self.bindings, profile["buttons"]):
            if isinstance(binding, dict):
                kind.set("宏"); argument.set(str(binding["macro"]))
            elif binding in reverse:
                kind.set(reverse[binding]); argument.set("")
            else:
                prefix, value = binding.split(":", 1)
                kind.set(reverse[prefix + ":"]); argument.set(value)
        self.load_macro()

    def collect(self):
        result = copy.deepcopy(self.profile)
        result["name"] = self.name_var.get().strip() or "未命名配置"
        count = self.dpi_count.get()
        if not 1 <= count <= 5:
            raise ValueError("DPI 档位数量必须为 1–5")
        rows = self.dpi_rows[:count]
        result["dpi"] = {"values": [int(v.get()) for _, v, _ in rows], "active": self.active.get(),
                         "enabled": [e.get() for e, _, _ in rows], "colors": [c.get() for _, _, c in rows]}
        result["polling_rate"] = int(self.polling.get())
        mode = next(k for k, (_, title) in LIGHT_MODES.items() if title == self.light_mode.get())
        result["lighting"] = {"mode": mode, "speed": self.light_speed.get(),
                              "brightness": self.light_brightness.get(), "direction": self.light_direction.get(),
                              "colors": self.light_color_list()}
        result["buttons"] = []
        for kind, argument in self.bindings:
            action = ACTION_TYPES[kind.get()]
            if action == "macro:":
                result["buttons"].append({"macro": int(argument.get())})
            else:
                result["buttons"].append(action + argument.get().strip() if action.endswith(":") else action)
        return result

    def load_slot(self):
        slot = int(self.slot_var.get())
        path = profile_path(slot)
        profile = load_profile(path) if path.exists() else default_profile()
        if not path.exists():
            profile["name"] = f"配置{slot}"
        self.fill(profile)
        self.note_var.set(f"已读取本地配置 {slot}，点击应用后写入鼠标。")

    def save_slot(self):
        profile = self.collect()
        compile_profile(profile)
        save_json(profile_path(int(self.slot_var.get())), profile)
        self.profile = profile
        self.note_var.set("本地配置已保存。")

    def import_profile(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("配置", "*.json *.psf"), ("全部文件", "*")])
        if path:
            self.fill(load_profile(path)); self.note_var.set("配置已导入，点击应用后写入鼠标。")

    def export_profile(self):
        profile = self.collect()
        compile_profile(profile)
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile="SC830.json")
        if path:
            save_json(path, profile); self.note_var.set("配置已导出。")

    def load_macro(self):
        macro = self.profile.get("macros", {}).get(self.macro_slot.get(), {})
        self.macro_name.set(macro.get("name", f"宏 {self.macro_slot.get()}"))
        self.macro_loop.set(next(k for k, v in LOOPS.items() if v == macro.get("loop", "count")))
        self.macro_repeat.set(macro.get("repeat", 1))
        events = macro.get("events", [{"type": "key", "key": "a", "state": "down", "delay_ms": 50},
                                      {"type": "key", "key": "a", "state": "up", "delay_ms": 0}])
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", json.dumps(events, ensure_ascii=False, indent=2))

    def save_macro(self):
        events = json.loads(self.editor.get("1.0", "end"))
        macro_events(events)
        self.profile.setdefault("macros", {})[self.macro_slot.get()] = {
            "name": self.macro_name.get(), "loop": LOOPS[self.macro_loop.get()],
            "repeat": self.macro_repeat.get(), "events": events}
        compile_profile(self.collect(), {"macros"})
        self.note_var.set(f"宏 {self.macro_slot.get()} 已加入当前配置。")

    def delete_macro(self):
        slot = int(self.macro_slot.get())
        bindings = self.collect()["buttons"]
        if any(isinstance(b, dict) and b.get("macro") == slot for b in bindings):
            raise ValueError("请先取消按键对此宏的绑定")
        self.profile["macros"].pop(str(slot), None)
        self.load_macro()
        self.note_var.set(f"已从本地配置删除宏 {slot}。")

    def import_macro(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("宏 JSON", "*.json")])
        if path:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
            events = value["events"] if isinstance(value, dict) else value
            macro_events(events)
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", json.dumps(events, ensure_ascii=False, indent=2))

    def export_macro(self):
        events = json.loads(self.editor.get("1.0", "end"))
        macro_events(events)
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile="macro.json")
        if path:
            save_json(path, events)

    def record_macro(self):
        Recorder(self.root, self.theme, lambda events: (self.editor.delete("1.0", "end"),
                 self.editor.insert("1.0", json.dumps(events, ensure_ascii=False, indent=2))))

    def work(self, function, done):
        self.busy = True
        for button in self.action_buttons:
            button.state(["disabled"])
        def run():
            try:
                self.jobs.put((True, function(), done))
            except Exception as exc:
                self.jobs.put((False, str(exc), done))
        threading.Thread(target=run, daemon=False).start()

    def finish_jobs(self):
        try:
            ok, result, done = self.jobs.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            for button in self.action_buttons:
                button.state(["!disabled"])
            if ok:
                done(result)
            else:
                self.note_var.set(result)
                self.set_device("error", "连接/配置未完成", result)
        self.root.after(50, self.finish_jobs)

    def set_device(self, state, title, detail):
        self.device_dot.configure(image=self.theme.images[f"dot_{state}"])
        self.device_var.set(title)
        self.device_detail.set(detail)

    def show_status(self, status):
        charging = " · 充电中" if status["charging"] else ""
        self.set_device("ok", f"已连接 · 电量 {status['battery_percent']}%{charging}",
                        f"{status['sensor']} · {status['device']}")

    def refresh(self):
        def query():
            with Mouse() as mouse:
                return mouse.status()
        self.set_device("wait", "正在连接…", "正在查询设备状态")
        self.work(query, self.show_status)

    def apply_current(self):
        key = self.page_var.get()
        self.apply({"dpi", "polling"} if key == "dpi" else {key})

    def apply(self, sections):
        profile = self.collect()
        commands = compile_profile(profile, sections)
        if not commands:
            self.note_var.set("当前页没有需要写入的内容。")
            return
        self.note_var.set(f"正在写入 {len(commands)} 个配置报文…")
        def send():
            with Mouse() as mouse:
                status, _ = mouse.apply(commands)
            applied_record(profile, sections, status)
            return status
        def done(status):
            self.show_status(status)
            self.profile = profile
            self.note_var.set("设置已发送到鼠标。")
        self.work(send, done)

    def close(self):
        if self.busy:
            self.note_var.set("正在与鼠标通信，完成后即可关闭。")
        else:
            self.root.destroy()


class Recorder:
    """Focused-window recorder compatible with Wayland, without input grabbing."""

    def __init__(self, parent, theme, callback):
        p = theme.px
        self.window = tk.Toplevel(parent)
        self.window.title("录制宏")
        self.window.geometry(f"{p(600)}x{p(400)}")
        self.window.minsize(p(480), p(340))
        self.window.transient(parent)
        self.window.configure(bg=theme.palette.window)
        self.callback = callback
        self.events, self.pressed = [], set()
        self.last = None
        self.recording = False
        body = ttk.Frame(self.window, style="Window.TFrame", padding=(p(26), p(22)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="录制宏", style="Window.Device.TLabel").pack(anchor="w")
        ttk.Label(body, text="点击开始后，保持此窗口聚焦，在下方区域操作键盘和鼠标。Esc 停止。"
                  "鼠标位移可在事件编辑器中添加。", style="Window.Secondary.TLabel", wraplength=p(540),
                  justify="left").pack(anchor="w", pady=(p(4), p(14)))
        self.count = tk.StringVar(value="尚未开始")
        self.area = ttk.Label(body, text="录制区域", style="Zone.TLabel", takefocus=True)
        self.area.pack(fill="both", expand=True)
        bar = ttk.Frame(body, style="Window.TFrame")
        bar.pack(fill="x", pady=(p(14), 0))
        ttk.Label(bar, textvariable=self.count, style="Window.Secondary.TLabel").pack(side="left")
        ttk.Button(bar, text="停止并采用", command=self.finish, style="Window.Primary.TButton").pack(side="right")
        ttk.Button(bar, text="开始 / 重新录制", command=self.start, style="Window.TButton").pack(side="right", padx=p(8))
        ttk.Button(bar, text="取消", command=self.window.destroy, style="Window.TButton").pack(side="right")
        self.area.bind("<KeyPress>", lambda e: self.key(e, "down"))
        self.area.bind("<KeyRelease>", lambda e: self.key(e, "up"))
        self.area.bind("<ButtonPress>", lambda e: self.mouse(e, "down"))
        self.area.bind("<ButtonRelease>", lambda e: self.mouse(e, "up"))
        self.window.bind("<FocusOut>", self.lost_focus)
        recolor = theme.listen(lambda c: self.window.configure(bg=c.window))
        self.window.bind("<Destroy>", lambda e: e.widget is self.window and recolor in theme.listeners
                         and theme.listeners.remove(recolor), add="+")

    def start(self):
        self.events, self.pressed = [], set()
        self.last = None
        self.recording = True
        self.area.focus_set()
        self.area.configure(text="正在录制\n按 Esc 停止")
        self.count.set("录制中 · 0 / 125 个动作")

    def add(self, event):
        if not self.recording:
            return
        now = time.monotonic()
        warning = ""
        if self.events:
            delta = round((now - self.last) * 1000)
            if delta > 4095:
                warning = " · 长停顿已按鼠标上限记为 4095 毫秒"
            self.events[-1]["delay_ms"] = min(delta, 4095)
        event["delay_ms"] = 0
        self.events.append(event)
        self.last = now
        self.count.set(f"录制中 · {len(self.events)} / 125 个动作" + warning)
        if len(self.events) >= 125:
            self.recording = False
            self.area.configure(text="已达到动作上限")
            self.count.set("达到 125 动作上限；请检查是否包含完整的按下和松开。")

    def key(self, event, state):
        if event.keysym == "Escape":
            self.finish()
            return "break"
        aliases = {"Control_L": "ctrl", "Control_R": "rctrl", "Shift_L": "shift", "Shift_R": "rshift",
                   "Alt_L": "alt", "Alt_R": "ralt", "Super_L": "super", "Super_R": "rsuper"}
        key = aliases.get(event.keysym, event.keysym.lower())
        try:
            key_usage(key)
        except ValueError:
            self.count.set(f"键名 {event.keysym} 请在编辑器中手动填写")
            return "break"
        identity = ("key", key)
        if state == "down" and identity in self.pressed:
            return "break"
        if state == "up" and identity not in self.pressed:
            return "break"
        if self.recording:
            (self.pressed.add if state == "down" else self.pressed.discard)(identity)
        self.add({"type": "key", "key": key, "state": state})
        return "break"

    def mouse(self, event, state):
        if event.num in (4, 5):
            if state == "down":
                self.add({"type": "wheel", "value": 1 if event.num == 4 else -1})
            return
        names = {1: "left", 2: "middle", 3: "right", 8: "back", 9: "forward"}
        if event.num in names:
            button = names[event.num]
            identity = ("mouse", button)
            if self.recording:
                (self.pressed.add if state == "down" else self.pressed.discard)(identity)
            self.add({"type": "mouse", "button": button, "state": state})

    def lost_focus(self, event):
        if self.recording and self.window.focus_displayof() is None:
            self.release_all()
            self.recording = False
            self.area.configure(text="录制已停止")
            self.count.set("焦点离开，录制已停止。点击停止并采用保存。")

    def release_all(self):
        for kind, key in sorted(self.pressed):
            if len(self.events) < 125:
                self.add({"type": kind, "key" if kind == "key" else "button": key, "state": "up"})
        self.pressed.clear()

    def finish(self):
        self.release_all()
        self.recording = False
        if self.events:
            self.callback(self.events)
        self.window.destroy()


def main():
    # A fixed WM class lets Hyprland window rules match this app.
    root = tk.Tk(className="sc830")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
