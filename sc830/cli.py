"""Command line entry point. No writes without an explicit settings command."""

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .device import DeviceError, Mouse, devices
from .profiles import (applied_record, compile_profile, default_profile, load_profile, save_json)
from .protocol import (DPI_VALUES, LIGHT_MODES, action_bytes, button_command,
                       dpi_command, lighting_command, macro_action, macro_commands,
                       polling_command)


def parser():
    root = argparse.ArgumentParser(prog="sc830", description="AULA SC830 Linux 配置工具")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--device", help="配置接口，例如 /dev/hidraw3")
    root.add_argument("--dry-run", action="store_true", help="只打印报文")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出设备")
    sub.add_parser("status", help="读取电量、传感器和在线状态")
    sub.add_parser("dpi-values", help="列出 PAW3212 可用 DPI")
    sub.add_parser("gui", help="打开图形界面")
    p = sub.add_parser("default-profile", help="导出默认配置，不更改设备")
    p.add_argument("file", type=Path)
    p = sub.add_parser("convert", help="将 Windows PSF 转为 Linux JSON")
    p.add_argument("source", type=Path); p.add_argument("destination", type=Path)
    p = sub.add_parser("apply", help="应用一个配置文件")
    p.add_argument("file", type=Path)
    p.add_argument("--sections", nargs="+", choices=("dpi", "polling", "lighting", "buttons", "macros"))
    p = sub.add_parser("dpi", help="设置 1–5 档 DPI")
    p.add_argument("values", nargs="+", type=int)
    p.add_argument("--active", type=int, default=1, help="当前档位，1 开始")
    p = sub.add_parser("polling", help="设置回报率")
    p.add_argument("hz", type=int, choices=(125, 250, 500, 1000))
    p = sub.add_parser("lighting", help="设置灯效")
    p.add_argument("mode", choices=tuple(LIGHT_MODES))
    p.add_argument("--speed", type=int, default=2)
    p.add_argument("--brightness", type=int, default=2)
    p.add_argument("--colors", nargs="+", default=["#FF0000"])
    p.add_argument("--direction", type=int, default=0)
    p = sub.add_parser("button", help="设置按键；1–6 对应左/右/中/前进/后退/DPI")
    p.add_argument("index", type=int); p.add_argument("action", help="如 back、key:ctrl+c、media:mute")
    p = sub.add_parser("macro", help="下载宏事件 JSON，并可绑定按键")
    p.add_argument("slot", type=int); p.add_argument("file", type=Path)
    p.add_argument("--bind", type=int)
    p.add_argument("--loop", choices=("release", "press", "count"), default="count")
    p.add_argument("--repeat", type=int, default=1)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "list":
            print(json.dumps(devices(), ensure_ascii=False, indent=2)); return 0
        if args.command == "status":
            with Mouse(args.device) as mouse:
                print(json.dumps(mouse.status(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "dpi-values":
            print(" ".join(map(str, DPI_VALUES))); return 0
        if args.command == "gui":
            from .gui import main as gui_main
            gui_main(); return 0
        if args.command == "default-profile":
            save_json(args.file, default_profile()); return 0
        if args.command == "convert":
            save_json(args.destination, load_profile(args.source)); return 0
        profile = None
        if args.command == "apply":
            profile = load_profile(args.file)
            commands = compile_profile(profile, args.sections)
        elif args.command == "dpi":
            commands = [dpi_command(args.values, args.active - 1)]
        elif args.command == "polling":
            commands = [polling_command(args.hz)]
        elif args.command == "lighting":
            commands = [lighting_command(args.mode, args.speed, args.brightness, args.colors, args.direction)]
        elif args.command == "button":
            if args.index == 1 and args.action != "left":
                raise ValueError("单键命令保留左键；需改左键时用完整配置，并保留其他左键")
            commands = [button_command(args.index - 1, action_bytes(args.action))]
        elif args.command == "macro":
            value = json.loads(args.file.read_text(encoding="utf-8"))
            events = value["events"] if isinstance(value, dict) else value
            commands = macro_commands(args.slot, events)
            action = macro_action(args.slot, args.loop, args.repeat)
            if args.bind is not None:
                if args.bind == 1:
                    raise ValueError("单键命令保留左键；改左键请用完整配置")
                commands.append(button_command(args.bind - 1, action))
        else:
            raise ValueError("无效命令")
        if args.dry_run:
            for seq, command in enumerate(commands):
                print(f"{command.description}: {command.packet(seq & 255).hex(' ')}")
            return 0
        with Mouse(args.device) as mouse:
            status, packets = mouse.apply(commands)
        if profile:
            applied_record(profile, args.sections or {"dpi", "polling", "lighting", "buttons", "macros"}, status)
        print(f"已发送 {len(packets)} 个配置报文；电量 {status['battery_percent']}%")
        return 0
    except (DeviceError, ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
