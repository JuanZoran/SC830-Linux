"""Local profiles, shared validation, and Windows PSF import."""

import copy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile

from .protocol import (action_bytes, button_command, dpi_colors_command, dpi_command,
                       lighting_command, macro_action, macro_commands, polling_command)

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sc830"


def default_profile():
    return {"schema": 1, "name": "配置一", "dpi": {
        "values": [800, 1200, 1600, 2400, 4000], "active": 1,
        "enabled": [True] * 5,
        "colors": ["#FF0000", "#00FF00", "#0000FF", "#FF00FF", "#FFFF00"]},
        "polling_rate": 500,
        "lighting": {"mode": "stream", "speed": 2, "brightness": 2,
                     "colors": ["#FF0000"], "direction": 0},
        "buttons": ["left", "right", "middle", "forward", "back", "dpi-cycle"],
        "macros": {}}


def compile_profile(profile, sections=None):
    if profile.get("schema") != 1:
        raise ValueError("不支持的配置文件版本")
    sections = {"dpi", "polling", "lighting", "buttons", "macros"} if sections is None else set(sections)
    if sections - {"dpi", "polling", "lighting", "buttons", "macros"}:
        raise ValueError("配置分区无效")
    commands = []
    if "dpi" in sections:
        dpi = profile["dpi"]
        commands.append(dpi_command(dpi["values"], dpi["active"], dpi.get("enabled")))
        if len(dpi["colors"]) != len(dpi["values"]):
            raise ValueError("DPI 颜色数量须与档位数量一致")
        commands.append(dpi_colors_command(dpi["colors"]))
    if "polling" in sections:
        commands.append(polling_command(profile["polling_rate"]))
    if "lighting" in sections:
        commands.append(lighting_command(**profile["lighting"]))
    macros = profile.get("macros", {})
    bindings = profile.get("buttons", [])
    if len(bindings) != 6:
        raise ValueError("配置须包含 6 个按键")
    actions = []
    needed = set()
    for binding in bindings:
        if isinstance(binding, dict):
            slot = binding["macro"]
            macro = macros.get(str(slot))
            if macro is None:
                raise ValueError(f"按键引用了不存在的宏 {slot}")
            actions.append(macro_action(slot, macro.get("loop", "count"), macro.get("repeat", 1)))
            needed.add(str(slot))
        else:
            actions.append(action_bytes(binding))
    if bytes((0x10, 1, 0)) not in actions:
        raise ValueError("请保留至少一个鼠标左键")
    if "macros" in sections or "buttons" in sections:
        # Upload referenced macro data before binding buttons to it.
        slots = set(macros) if "macros" in sections else needed
        for slot in sorted(slots, key=int):
            commands.extend(macro_commands(int(slot), macros[slot]["events"]))
    if "buttons" in sections:
        commands.extend(button_command(i, action) for i, action in enumerate(actions))
    return commands


def save_json(path, value):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_profile(path):
    path = Path(path).expanduser()
    if path.suffix.lower() == ".psf":
        value = import_psf(path)
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
    compile_profile(value)
    return value


def profile_path(slot):
    if not 1 <= slot <= 5:
        raise ValueError("本地配置槽位为 1–5")
    return CONFIG_DIR / f"profile{slot}.json"


def applied_record(profile, sections, status):
    save_json(CONFIG_DIR / "last-applied.json", {
        "at": datetime.now().astimezone().isoformat(), "sections": sorted(sections),
        "profile": copy.deepcopy(profile), "status": status,
    })


def import_psf(path):
    """Import Windows DPI, colors, polling, button codes and lighting.

    Windows MacroLibraryFile.mlf is separate from PSF; reject macro bindings
    with a clear error instead of silently replacing them.
    """
    data = Path(path).read_bytes()
    text = data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("utf-8-sig")
    if "[Setting]" not in text:
        raise ValueError("文件缺少 PSF 的 [Setting] 配置段")
    fields = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.strip().split("=", 1)
            fields[key] = value
    result = default_profile()
    result["name"] = fields.get("ProfileName", Path(path).stem)
    from .protocol import DPI_VALUES, LIGHT_MODES
    mask = int(fields.get("DPIOffOnFlagValue", "1F"), 16)
    count = max(mask.bit_length(), 1)
    if count > 5:
        raise ValueError("此 PSF 包含超过 5 档 DPI；请使用原程序为 SC830 导出的配置")
    indices = [int(fields.get(f"XYSetting{i + 1}Value", "00"), 16) for i in range(count)]
    if any(i >= len(DPI_VALUES) for i in indices):
        raise ValueError("此 PSF 的 DPI 编码超出 PAW3212 范围")
    result["dpi"] = {
        "values": [DPI_VALUES[i] for i in indices],
        "active": int(fields.get("CurrentDPILevelValue", "00"), 16),
        "enabled": [bool(mask & (1 << i)) for i in range(count)],
        "colors": ["#" + fields.get(f"DPILevelIndication{i}Valuer", "FFFFFF") for i in range(count)]}
    result["polling_rate"] = (125, 250, 500, 1000)[int(fields.get("PollingRateValue", "020000")[:2], 16)]
    for i in range(6):
        code = fields.get(f"Key{i}Value")
        if code:
            if code.startswith("90"):
                raise ValueError("此 PSF 使用独立宏库；请先在 Linux 宏编辑器重建该宏，再导入按键")
            result["buttons"][i] = "raw:" + code
    mode = int(fields.get("Lighting1ModeValue", "010000")[:2], 16)
    mode_key = next((key for key, (idx, _) in LIGHT_MODES.items() if idx == mode), None)
    if mode_key is None:
        raise ValueError(f"此 PSF 使用 SC830 菜单未提供的灯效编号 {mode}")
    if fields.get("Lighting1OffOn") == "00":
        mode_key = "off"
    result["lighting"]["mode"] = mode_key
    for field, target in [("Lighting1Speed", "speed"), ("Lighting1Brightness", "brightness")]:
        values = fields.get(field, "01").split("|")
        result["lighting"][target] = int(values[min(mode, len(values) - 1)], 16) + 1
    palettes = fields.get("Lighting1FixedColor", "FF0000").split(";")
    palette = palettes[min(mode, len(palettes) - 1)].split("|")
    selections = fields.get("Lighting1ColorSelected", "01").split(";")
    flags = selections[min(mode, len(selections) - 1)].split("|")
    colors = ["#" + color for i, color in enumerate(palette)
              if re.fullmatch(r"[0-9a-fA-F]{6}", color)
              and (mode == 3 and i == 0 or mode != 3 and i < len(flags) and flags[i] == "01")]
    if colors:
        result["lighting"]["colors"] = colors[:7]
    return result
