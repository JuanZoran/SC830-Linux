"""Linux hidraw transport with interface fingerprinting and bounded I/O."""

import fcntl
import os
from pathlib import Path
import time

from .protocol import (VENDOR_ID, PRODUCT_IDS, REPORT_ID, REPORT_SIZE,
                       parse_status, status_command)


class DeviceError(RuntimeError):
    pass


def devices(sysfs=Path("/sys/class/hidraw")):
    found = []
    for node in sorted(sysfs.glob("hidraw*")):
        try:
            info = dict(line.split("=", 1) for line in (node / "device/uevent").read_text().splitlines())
            bus, vid, pid = (int(v, 16) for v in info["HID_ID"].split(":"))
            if bus != 3 or vid != VENDOR_ID or pid not in PRODUCT_IDS:
                continue
            descriptor = (node / "device/report_descriptor").read_bytes()
            # Vendor Feature 0x0c, 32-byte body. Excludes mouse and status input interfaces.
            if b"\x85\x0c" not in descriptor or b"\x95\x20\xb1\x02" not in descriptor:
                continue
            path = Path("/dev") / node.name
            found.append({"path": str(path), "vendor_id": vid, "product_id": pid,
                          "name": info.get("HID_NAME", "SC830"),
                          "writable": os.access(path, os.R_OK | os.W_OK)})
        except (OSError, ValueError, KeyError):
            continue
    return found


class Mouse:
    def __init__(self, path=None):
        available = devices()
        if path:
            available = [d for d in available if d["path"] == str(path)]
        if not available:
            raise DeviceError("未找到 SC830 配置接口，请切换到 2.4G 或有线模式")
        if path is None and len(available) > 1:
            wired = [d for d in available if d["product_id"] == 0x5B2E]
            if len(wired) == 1:
                available = wired
        if len(available) > 1:
            raise DeviceError("发现多个配置接口，请使用 --device 指定设备")
        self.path = available[0]["path"]
        self.sequence = 0
        self.fd = None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
            # Prevent concurrent GUI/CLI sessions from interleaving device transactions.
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            if isinstance(exc, PermissionError):
                raise DeviceError(f"无权访问 {self.path}；请运行项目中的 install-permissions.sh") from exc
            raise DeviceError(f"打开设备失败（或另一程序正在配置）：{exc}") from exc
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def send(self, command, pause=0.06):
        if self.fd is None:
            raise DeviceError("设备尚未打开")
        packet = command.packet(self.sequence)
        self.sequence = (self.sequence + 1) & 255
        try:
            fcntl.ioctl(self.fd, 0xC0004806 | (REPORT_SIZE << 16), bytearray(packet))
            time.sleep(pause)
        except OSError as exc:
            raise DeviceError(f"发送 {command.description or hex(command.code)} 失败：{exc}") from exc
        return packet

    def status(self):
        error = None
        for _ in range(4):
            sent = self.send(status_command())
            reply = bytearray([REPORT_ID] + [255] * (REPORT_SIZE - 1))
            try:
                length = fcntl.ioctl(self.fd, 0xC0004807 | (REPORT_SIZE << 16), reply, True)
                if length != REPORT_SIZE:
                    raise ValueError("设备状态报文长度无效")
                result = parse_status(reply, sent[4])
                if not result["online"]:
                    raise ValueError("鼠标处于睡眠或离线状态，请移动鼠标后重试")
                result["device"] = self.path
                return result
            except (OSError, ValueError) as exc:
                error = exc
        raise DeviceError(str(error))

    def apply(self, commands, progress=None):
        state = self.status()
        if state["sensor_id"] != 0x12:
            raise DeviceError(f"当前传感器为 {state['sensor']}，此版本的 DPI 表适用于 PAW3212")
        packets = []
        for i, command in enumerate(commands):
            packets.append(self.send(command))
            if progress:
                progress(i + 1, len(commands), command.description)
        return self.status(), packets
