"""
RK87 Core - RK-H87 RGB keyboard lighting controller over HID.

Protocol reverse-engineered from the Rangoli project (GPLv3, github.com/rnayabed/rangoli),
which itself derived it by sniffing the official RK Software with Wireshark.
Cross-checked against live GET_FEATURE probing of the local RK-H87 (VID_258A / PID_00EF).

Device identity (verified on real hardware):
  VID 0x258A  PID 0x00EF  REV 0x1009
  manufacturer "SINO WEALTH", product "Bluetooth Keyboard"
  Vendor-defined collection: Col05, usage_page 0xFF00, usage 0x0001
  Lighting channel: 65-byte Feature report, report ID 0x0A

SAFETY (important):
  Only the 0x0A lighting Feature report is ever written.  SINO WEALTH keyboards
  of this family can enter their ISP (firmware-flash) bootloader via a magic
  Feature report on a different report ID.  Blind writes to unknown reports risk
  soft-bricking the keyboard.  This module deliberately never sends anything
  except the known-safe 0x0A lighting buffer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None


VID_RK = 0x258A
PID_H87 = 0x00EF
REPORT_ID_LIGHT = 0x0A
BUFFER_SIZE = 65

# Lighting mode bits, from Rangoli src/modemodel.h (enum class RGBModes : int).
# Values are the exact bytes written into buffer[5].
MODE_NEON_STREAM = 1
MODE_RIPPLES_SHINING = 2
MODE_ROTATING_WINDMILL = 3
MODE_SINE_WAVE = 4
MODE_RAINBOW_ROULETTE = 5
MODE_STARS_TWINKLE = 6
MODE_LAYER_UPON_LAYER = 7
MODE_RICH_AND_HONORED = 8
MODE_MARQUEE_EFFECT = 9
MODE_ROTATING_STORM = 10
MODE_SERPENTINE_HORSE_RACE = 11
MODE_RETRO_SNAKE = 12
MODE_DIAGONAL_TRANSFORMATION = 13
MODE_CUSTOM = 14
MODE_AMBILIGHT = 15
MODE_STREAMER = 16
MODE_STEADY = 17
MODE_BREATHING = 18
MODE_NEON = 19
MODE_SHADOW_DISAPPEAR = 20
MODE_FLASH_AWAY = 21

# Friendly Chinese names in the same display order as Rangoli's m_rgbModes list.
# (name, modeBit) — order matches the upstream UI so users' muscle memory carries over.
LIGHT_MODES: list[tuple[str, int]] = [
    ("自定义", MODE_CUSTOM),
    ("氛围灯", MODE_AMBILIGHT),
    ("呼吸", MODE_BREATHING),
    ("对角变换", MODE_DIAGONAL_TRANSFORMATION),
    ("闪现", MODE_FLASH_AWAY),
    ("层层叠叠", MODE_LAYER_UPON_LAYER),
    ("跑马灯", MODE_MARQUEE_EFFECT),
    ("霓虹", MODE_NEON),
    ("霓虹流光", MODE_NEON_STREAM),
    ("彩虹轮盘", MODE_RAINBOW_ROULETTE),
    ("贪吃蛇", MODE_RETRO_SNAKE),
    ("雍容华贵", MODE_RICH_AND_HONORED),
    ("涟漪闪耀", MODE_RIPPLES_SHINING),
    ("旋转风暴", MODE_ROTATING_STORM),
    ("旋转风车", MODE_ROTATING_WINDMILL),
    ("蛇形赛马", MODE_SERPENTINE_HORSE_RACE),
    ("影渐消失", MODE_SHADOW_DISAPPEAR),
    ("正弦波", MODE_SINE_WAVE),
    ("星光闪烁", MODE_STARS_TWINKLE),
    ("常亮", MODE_STEADY),
    ("流光溢彩", MODE_STREAMER),
]

BRIGHTNESS_LABELS = ["关闭", "很低", "低", "中", "高", "全亮"]  # 0..5
ANIMATION_LABELS = ["", "很慢", "慢", "正常", "快", "很快"]       # 1..5
SLEEP_LABELS = ["", "5 分钟", "10 分钟", "20 分钟", "30 分钟", "不休眠"]  # 1..5


class DeviceNotFound(RuntimeError):
    pass


class DeviceWriteError(RuntimeError):
    pass


@dataclass
class DeviceInfo:
    path: bytes
    vendor_id: int
    product_id: int
    product: str = ""
    manufacturer: str = ""
    usage_page: int = 0
    usage: int = 0
    interface_number: int = 0

    @property
    def vid_pid(self) -> str:
        return f"{self.vendor_id:04X}:{self.product_id:04X}"


def find_keyboard() -> DeviceInfo:
    """Locate the RK-H87 vendor-defined lighting collection (FF00, Col05).

    We require usage_page == 0xFF00 so we never accidentally open the standard
    boot-keyboard interface (which would capture keystrokes).
    """
    if hid is None:
        raise RuntimeError("缺少 hidapi 模块，请先安装: pip install hidapi")
    candidates = []
    for dev in hid.enumerate():
        if dev.get("vendor_id") != VID_RK:
            continue
        if dev.get("product_id") != PID_H87:
            continue
        if dev.get("usage_page") != 0xFF00:
            continue
        candidates.append(dev)
    if not candidates:
        raise DeviceNotFound("未找到 RK-H87 灯光通道 (VID_258A / PID_00EF / usage 0xFF00)，请确认键盘已用有线模式连接")

    # The H87 exposes TWO FF00 collections: Col03 (read errors) and Col05 (works).
    # Path is bytes on Windows and contains "Col05" for the live lighting channel.
    def to_info(dev):
        return DeviceInfo(
            path=dev["path"],
            vendor_id=dev["vendor_id"],
            product_id=dev["product_id"],
            product=dev.get("product_string") or "",
            manufacturer=dev.get("manufacturer_string") or "",
            usage_page=dev.get("usage_page", 0),
            usage=dev.get("usage", 0),
            interface_number=dev.get("interface_number", 0),
        )

    for dev in candidates:
        if b"Col05" in dev["path"] or b"col05" in dev["path"].lower():
            return to_info(dev)
    # Fallback: return first candidate; open() will validate with a read.
    return to_info(candidates[0])


def build_light_buffer(
    mode_bit: int,
    brightness: int,
    animation: int,
    sleep: int,
    colour: tuple[int, int, int] = (255, 255, 255),
    random_colours: bool = False,
) -> bytearray:
    """Build the 65-byte 0x0A lighting Feature report.

    Layout reverse-engineered from Rangoli::setStandardLightBufferBetter():
      [0] 0x0A  report id
      [1] 0x01
      [2] 0x01
      [3] 0x02
      [4] 0x29  command
      [5] mode bit
      [7] animation speed (1..5)
      [8] brightness (0..5)
      [9..11] R, G, B  (only meaningful when random_colours is False)
      [12] random-colour flag (1 = random)
      [13] sleep timer (1..5)
    All other bytes stay zero.
    """
    buf = bytearray(BUFFER_SIZE)
    buf[0] = REPORT_ID_LIGHT
    buf[1] = 0x01
    buf[2] = 0x01
    buf[3] = 0x02
    buf[4] = 0x29
    buf[5] = mode_bit & 0xFF
    buf[7] = max(0, min(5, animation))
    buf[8] = max(0, min(5, brightness))
    if not random_colours:
        r, g, b = colour
        buf[9] = r & 0xFF
        buf[10] = g & 0xFF
        buf[11] = b & 0xFF
    buf[12] = 0x01 if random_colours else 0x00
    buf[13] = max(1, min(5, sleep))
    return buf


class RK87Controller:
    """Thread-safe controller for the RK-H87 lighting channel."""

    def __init__(self, info: Optional[DeviceInfo] = None):
        self.info = info or find_keyboard()
        self._dev = None
        self._lock = threading.RLock()

    @property
    def alive(self) -> bool:
        return self._dev is not None

    def open(self) -> None:
        if hid is None:
            raise RuntimeError("缺少 hidapi 模块")
        with self._lock:
            dev = hid.device()
            dev.open_path(self.info.path)
            # Windows hidapi: use a non-zero/short nonblocking mode so a stray
            # read can never hang the calling thread (learned from g304 project).
            try:
                dev.set_nonblocking(1)
            except Exception:
                pass
            self._dev = dev

    def close(self) -> None:
        with self._lock:
            if self._dev is not None:
                try:
                    self._dev.close()
                finally:
                    self._dev = None

    def read_status(self, length: int = BUFFER_SIZE) -> bytes:
        """GET_FEATURE(0x0A, 65) — read back current lighting state.

        On the real H87 this returns 65 bytes beginning with the 8-byte status
        pattern 01 01 01 01 01 00 00 00 repeated.  Read-only; safe.
        """
        with self._lock:
            if self._dev is None:
                self.open()
            data = self._dev.get_feature_report(REPORT_ID_LIGHT, length)
            return bytes(data)

    def apply(
        self,
        mode_bit: int,
        brightness: int,
        animation: int,
        sleep: int,
        colour: tuple[int, int, int] = (255, 255, 255),
        random_colours: bool = False,
    ) -> bytes:
        """Write the lighting Feature report (SEND_FEATURE 0x0A).

        This is the ONLY write this module performs.  It changes the keyboard's
        lighting configuration but cannot trigger the ISP bootloader.
        """
        buf = build_light_buffer(mode_bit, brightness, animation, sleep, colour, random_colours)
        with self._lock:
            if self._dev is None:
                self.open()
            written = self._dev.send_feature_report(bytes(buf))
            if written != BUFFER_SIZE:
                raise DeviceWriteError(f"写入未完成: 期望 {BUFFER_SIZE} 字节，实际 {written}")
            return bytes(buf)


class MockController:
    """No-hardware controller for UI development and regression testing."""

    def __init__(self):
        self.info = DeviceInfo(
            path=b"<mock>", vendor_id=VID_RK, product_id=PID_H87,
            product="RK-H87RGB (演示)", manufacturer="SINO WEALTH",
            usage_page=0xFF00, usage=0x0001,
        )
        self._last = None

    @property
    def alive(self) -> bool:
        return True

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def read_status(self, length: int = BUFFER_SIZE) -> bytes:
        return bytes([0x01] * 5 + [0x00] * 3) * 8 + b"\x00" * (length - 64)

    def apply(self, mode_bit, brightness, animation, sleep, colour=(255, 255, 255), random_colours=False):
        self._last = build_light_buffer(mode_bit, brightness, animation, sleep, colour, random_colours)
        return bytes(self._last)


if __name__ == "__main__":
    # Quick self-test: enumerate + read-only status dump. No writes.
    try:
        info = find_keyboard()
        print(f"找到设备: {info.vid_pid} | {info.manufacturer} | {info.product}")
        print(f"通道: usage_page=0x{info.usage_page:04X} usage=0x{info.usage:04X} iface={info.interface_number}")
        ctrl = RK87Controller(info)
        status = ctrl.read_status()
        print(f"状态回读 ({len(status)} 字节): {status[:16].hex(' ')} ...")
        ctrl.close()
    except Exception as exc:
        print(f"自检失败: {type(exc).__name__}: {exc}")
