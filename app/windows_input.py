"""Checked physical cursor movement and SendInput buttons; respects OS restrictions."""

import ctypes
import time
from ctypes import wintypes


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput)]


class Input(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", InputUnion)]


def absolute_point(x, y, bounds):
    left, top, width, height = bounds
    if width <= 0 or height <= 0 or not left <= x < left + width or not top <= y < top + height:
        raise ValueError("目标不在有效的虚拟桌面范围")
    return min(65535, int((x - left + 0.5) * 65536 / width)), min(
        65535, int((y - top + 0.5) * 65536 / height)
    )


class WindowsMouse:
    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.GetPhysicalCursorPos.restype = wintypes.BOOL
        self.user32.SetPhysicalCursorPos.argtypes = [wintypes.INT, wintypes.INT]
        self.user32.SetPhysicalCursorPos.restype = wintypes.BOOL
        self.last_event = None

    def send(self, events):
        packets = (Input * len(events))()
        for packet, (x, y, data, flags) in zip(packets, events, strict=True):
            packet.type = 0  # INPUT_MOUSE
            packet.mi = MouseInput(x, y, data, flags, 0, 0)
        ctypes.set_last_error(0)
        sent = self.user32.SendInput(len(packets), packets, ctypes.sizeof(Input))
        error = ctypes.get_last_error()
        self.last_event = {
            "backend": "SendInput",
            "requested": len(events),
            "accepted": sent,
            "win32_error": error,
            "input_struct_size": ctypes.sizeof(Input),
        }
        if sent != len(events):
            raise RuntimeError(
                f"Windows未完整接受鼠标输入：{sent}/{len(events)}，Win32错误={error}。"
                "已停止，不自动重试；仅凭此错误不能确定是否为权限或桌面会话限制"
            )

    def position(self):
        point = wintypes.POINT()
        if not self.user32.GetPhysicalCursorPos(ctypes.byref(point)):
            raise RuntimeError(f"无法读取鼠标物理坐标，Win32错误={ctypes.get_last_error()}")
        return point.x, point.y

    def move(self, x, y):
        before = self.position()
        ctypes.set_last_error(0)
        accepted = bool(self.user32.SetPhysicalCursorPos(x, y))
        error = ctypes.get_last_error()
        self.last_event = {
            "backend": "SetPhysicalCursorPos",
            "target": [x, y],
            "before": list(before),
            "api_success": accepted,
            "win32_error": error,
            "samples": [],
        }
        if not accepted:
            raise RuntimeError(f"Windows拒绝物理坐标移动，Win32错误={error}；未点击，不自动重试")
        for _ in range(10):
            actual = self.position()
            self.last_event["samples"].append(list(actual))
            if actual == (x, y):
                return actual
            time.sleep(0.02)
        return actual

    def move_sendinput(self, x, y):
        """Kept for isolated backend tests; not used for production pointer movement."""
        bounds = tuple(self.user32.GetSystemMetrics(index) for index in (76, 77, 78, 79))
        nx, ny = absolute_point(x, y, bounds)
        self.send([(nx, ny, 0, 0x0001 | 0x8000 | 0x4000)])
        # SendInput insertion is synchronous, display processing can lag briefly.
        for _ in range(10):
            actual = self.position()
            if actual == (x, y):
                return actual
            time.sleep(0.02)
        return actual

    def click(self):
        # No blind retry: the return count can indicate a partial button sequence.
        self.send([(0, 0, 0, 0x0002), (0, 0, 0, 0x0004)])

    def scroll(self, ticks):
        self.send([(0, 0, (ticks * 120) & 0xFFFFFFFF, 0x0800)])
