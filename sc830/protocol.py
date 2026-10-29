"""M601 / SC830 protocol, independently implemented from GamingMS V1.1.8.

Pure packet encoding: no device access or GUI dependencies. Byte offsets include
the report ID. See docs/protocol.md for original code addresses and wire format.
"""

from dataclasses import dataclass
import re
import struct

REPORT_ID = 0x0C
REPORT_SIZE = 33
VENDOR_ID = 0x248A
PRODUCT_IDS = (0x5B2E, 0x5B2F)
DPI_VALUES = tuple(n * 100 for n in (*range(4, 50, 2), *range(60, 98, 2)))
BUTTON_NAMES = ("左键", "右键", "中键", "前进", "后退", "DPI 键")
# Hardware order differs from the Windows profile / visual order.
BUTTON_WIRE_INDEX = (0, 1, 2, 4, 3, 5)
LIGHT_MODES = {
    "off": (0, "关闭"), "stream": (1, "混彩流光"),
    "breathing": (2, "呼吸"), "steady": (3, "单色常亮"),
    "neon": (4, "霓虹"), "snake": (5, "跑马"),
    "colorful": (6, "混彩常亮"), "wave": (9, "七彩波浪"),
}
MOUSE_ACTIONS = {
    "left": (0x10, 1, 0), "right": (0x10, 2, 0),
    "middle": (0x10, 4, 0), "back": (0x10, 8, 0),
    "forward": (0x10, 16, 0), "dpi-cycle": (0x40, 1, 0),
    "dpi-up": (0x40, 2, 0), "dpi-down": (0x40, 3, 0),
    "disabled": (0, 0, 0),
}
MEDIA_USAGES = {
    "player": 0x183, "previous": 0xB6, "next": 0xB5,
    "stop": 0xB7, "play-pause": 0xCD, "mute": 0xE2,
    "volume-up": 0xE9, "volume-down": 0xEA, "mail": 0x18A,
    "calculator": 0x192,
}
KEY_USAGES = {chr(97 + i): 4 + i for i in range(26)}
KEY_USAGES.update({str(i): 29 + i for i in range(1, 10)})
KEY_USAGES.update({"0": 39, "enter": 40, "escape": 41, "backspace": 42,
                   "tab": 43, "space": 44, "minus": 45, "equal": 46,
                   "leftbracket": 47, "rightbracket": 48, "backslash": 49,
                   "semicolon": 51, "apostrophe": 52, "grave": 53,
                   "comma": 54, "period": 55, "slash": 56, "capslock": 57,
                   "printscreen": 70, "scrolllock": 71, "pause": 72,
                   "insert": 73, "home": 74, "pageup": 75, "delete": 76,
                   "end": 77, "pagedown": 78, "right": 79, "left": 80,
                   "down": 81, "up": 82, "numlock": 83})
KEY_USAGES.update({f"f{i}": 57 + i for i in range(1, 13)})
MODIFIERS = {"ctrl": 1, "shift": 2, "alt": 4, "super": 8,
             "rctrl": 16, "rshift": 32, "ralt": 64, "rsuper": 128}
ALIASES = {"control": "ctrl", "win": "super", "meta": "super", "esc": "escape",
           "return": "enter", "prior": "pageup", "next": "pagedown",
           "backspace": "backspace", "caps_lock": "capslock",
           "page_up": "pageup", "page_down": "pagedown",
           "bracketleft": "leftbracket", "bracketright": "rightbracket",
           "quoteleft": "grave"}


def bounded(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} 必须是 {low}–{high} 的整数")
    return value


def key_usage(key):
    key = str(key).lower().strip()
    key = ALIASES.get(key, key)
    if key in KEY_USAGES:
        return KEY_USAGES[key]
    if key in MODIFIERS:
        return 0xE0 + (MODIFIERS[key].bit_length() - 1)
    if key.startswith("0x"):
        return bounded(int(key, 16), 1, 255, "HID 键码")
    raise ValueError(f"不支持的键名：{key}")


def shortcut(text):
    parts = [ALIASES.get(s.strip().lower(), s.strip().lower()) for s in text.split("+")]
    modifiers = 0
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise ValueError(f"组合键修饰键无效：{part}")
        modifiers |= MODIFIERS[part]
    if not parts[-1]:
        raise ValueError("组合键缺少按键")
    return bytes((0x70, modifiers, key_usage(parts[-1])))


def color_bytes(value):
    if not isinstance(value, str) or not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        raise ValueError("颜色必须使用 #RRGGBB 格式")
    return bytes.fromhex(value.lstrip("#"))


@dataclass(frozen=True)
class Command:
    code: int
    payload: bytes = b""
    page: int = 1
    description: str = ""

    def packet(self, sequence=0):
        bounded(sequence, 0, 255, "序号")
        bounded(self.code, 0, 255, "命令")
        bounded(self.page, 0, 255, "分包页")
        if len(self.payload) > 26:
            raise ValueError("每包最多携带 26 字节")
        header = bytes((REPORT_ID, 1, self.code, 0, sequence, self.page, len(self.payload)))
        return (header + self.payload).ljust(REPORT_SIZE, b"\0")


def status_command():
    return Command(0x20, page=0, description="读取设备状态")


def parse_status(data, sequence=None):
    data = bytes(data)
    if len(data) != REPORT_SIZE or data[:4] != b"\x0c\x01\x20\x00":
        raise ValueError("设备状态报文头无效")
    if sequence is not None and data[4] != sequence:
        raise ValueError("设备返回了旧状态报文")
    if data[6] < 16 or data[8:12] != b"M601":
        raise ValueError("鼠标未响应，请移动鼠标唤醒后重试")
    state = data[19]
    return {"model": "AULA SC830", "protocol": "M601", "sensor": f"PAW32{data[15]:02X}",
            "sensor_id": data[15], "battery_percent": 100 if state == 2 else min(data[18], 100),
            "charging": state == 1, "external_power": state == 2,
            "online": bool(data[21]), "raw": data.hex()}


def dpi_command(values, active=0, enabled=None):
    if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 5:
        raise ValueError("SC830 的 DPI 档位数量必须为 1–5")
    bounded(active, 0, len(values) - 1, "当前档位索引")
    enabled = [True] * len(values) if enabled is None else list(enabled)
    if len(enabled) != len(values) or any(type(v) is not bool for v in enabled):
        raise ValueError("DPI 启用标记须与档位一一对应")
    if not enabled[active]:
        raise ValueError("当前 DPI 档位必须启用")
    codes = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value not in DPI_VALUES:
            raise ValueError(f"PAW3212 不支持 DPI {value}；可选值见 sc830 dpi-values")
        codes.append(DPI_VALUES.index(value) + 1)
    mask = sum(1 << i for i, flag in enumerate(enabled) if flag)
    payload = bytes((((active + 1) << 4) | sum(enabled), mask))
    payload += struct.pack("<8H", *(codes + [1] * (5 - len(codes)) + [0] * 3))
    return Command(5, payload, description="DPI 档位")


def dpi_colors_command(colors):
    if not 1 <= len(colors) <= 8:
        raise ValueError("DPI 颜色数量必须为 1–8")
    payload = b"".join(map(color_bytes, colors)).ljust(24, b"\0")
    return Command(6, payload, description="DPI 指示灯颜色")


def polling_command(hz):
    if not isinstance(hz, int) or hz not in (125, 250, 500, 1000) or isinstance(hz, bool):
        raise ValueError("回报率必须是 125、250、500 或 1000 Hz")
    return Command(7, bytes((1000 // hz,)), description="回报率")


def lighting_command(mode="stream", speed=2, brightness=2, colors=None, direction=0):
    if mode not in LIGHT_MODES:
        raise ValueError("不支持的灯效")
    bounded(speed, 1, 4, "灯效速度")
    bounded(brightness, 1, 4, "灯效亮度")
    bounded(direction, 0, 1, "灯效方向")
    colors = ["#FF0000"] if colors is None else colors
    colors = list(map(color_bytes, colors))
    index = LIGHT_MODES[mode][0]
    effect = bytes((index, (brightness << 4) | speed))
    if index == 1:
        effect += bytes((direction,))
    elif index == 2:
        colors = [c for c in colors if c != b"\0\0\0"]
        if not 1 <= len(colors) <= 7:
            raise ValueError("呼吸灯需选择 1–7 个非黑色颜色")
        effect += bytes((len(colors),)) + b"".join(colors)
    elif index == 3:
        if len(colors) != 1:
            raise ValueError("单色常亮需选择一个颜色")
        effect += colors[0]
    elif index == 6:
        if not 1 <= len(colors) <= 7:
            raise ValueError("此灯效需选择 1–7 个颜色")
        palette = colors + [b"\0\0\0"] * (7 - len(colors))
        effect += b"".join(palette)
    return Command(8, effect, description="灯效")


def button_command(index, action):
    bounded(index, 0, 5, "按键索引")
    action = bytes(action)
    if len(action) != 3:
        raise ValueError("按键动作必须为三字节")
    return Command(4, bytes((BUTTON_WIRE_INDEX[index],)) + action, description=BUTTON_NAMES[index])


def action_bytes(action):
    if action in MOUSE_ACTIONS:
        return bytes(MOUSE_ACTIONS[action])
    if action.startswith("key:"):
        return shortcut(action[4:])
    if action.startswith("media:"):
        name = action[6:]
        if name not in MEDIA_USAGES:
            raise ValueError(f"不支持的多媒体动作：{name}")
        return bytes((0x80,)) + struct.pack("<H", MEDIA_USAGES[name])
    if action.startswith("fire:"):
        parts = action[5:].split(":")
        if len(parts) != 2:
            raise ValueError("火力键格式为 fire:间隔:次数")
        interval, count = map(int, parts)
        bounded(interval, 5, 255, "火力间隔")
        bounded(count, 0, 255, "火力次数")
        return bytes((0x30, interval, count))
    if action.startswith("raw:"):
        result = bytes.fromhex(action[4:])
        if len(result) == 3:
            return result
    raise ValueError(f"不支持的按键动作：{action}")


def macro_action(slot, loop="count", repeat=1):
    bounded(slot, 1, 6, "宏槽位")
    if loop not in ("release", "press", "count"):
        raise ValueError("宏循环方式须为 release、press 或 count")
    bounded(repeat, 1, 255, "宏循环次数")
    mode = ("release", "press", "count").index(loop)
    return bytes((0x90, (mode << 4) | slot, repeat if mode == 2 else 0))


def macro_events(events):
    """Encode up to 125 firmware events, 3 bytes each (12-bit delay after event)."""
    encoded = bytearray()
    for event in events:
        kind = event.get("type")
        if kind == "delay":
            if not encoded:
                raise ValueError("宏必须从按键或移动事件开始；延时写在对应事件之后")
            delay = bounded(event.get("ms"), 0, 4095, "延时")
            encoded[-3] = (encoded[-3] & 0xF0) | (delay >> 8)
            encoded[-2] = delay & 255
            continue
        delay = bounded(event.get("delay_ms", 0), 0, 4095, "延时")
        state = event.get("state", "down")
        if state not in ("down", "up"):
            raise ValueError("宏按键状态须为 down 或 up")
        release = state == "up"
        flag = 0x80 if release else 0
        if kind == "key":
            value = key_usage(event["key"])
            if 0xE0 <= value <= 0xE7:
                flag |= 0x20
                value = 1 << (value - 0xE0)
            elif value < 0x91:
                flag |= 0x30
            else:
                raise ValueError("宏键码须为标准键盘键或修饰键")
        elif kind == "mouse":
            buttons = {"left": 1, "right": 2, "middle": 4, "back": 8, "forward": 16}
            if event["button"] not in buttons:
                raise ValueError("不支持的鼠标宏按键")
            flag |= 0x10
            value = buttons[event["button"]]
        elif kind in ("move_x", "move_y", "wheel"):
            value = bounded(event.get("value"), -127, 127, "移动量") & 255
            flag = {"move_x": 0x50, "move_y": 0x60, "wheel": 0x40}[kind]
        else:
            raise ValueError(f"不支持的宏事件：{kind}")
        encoded += bytes((flag | (delay >> 8), delay & 255, value))
    if not 1 <= len(encoded) // 3 <= 125:
        raise ValueError("每个宏需包含 1–125 个按键或移动事件")
    return bytes(encoded)


def macro_commands(slot, events):
    bounded(slot, 1, 6, "宏槽位")
    data = macro_events(events)
    # Original code: ceil(event_count * 3 / 25); header carries slot + 25 bytes.
    chunks = [data[i:i + 25] for i in range(0, len(data), 25)]
    if len(chunks) > 15:
        raise ValueError("宏最多占用 15 个分包")
    return [Command(0x0E, bytes((slot,)) + chunk, (i << 4) | len(chunks), f"宏 {slot}")
            for i, chunk in enumerate(chunks)]
