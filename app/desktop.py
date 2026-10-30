"""Primary-screen screenshots and real mouse input; no DOM or network interception."""

import ctypes
import hashlib
import sys
from ctypes import wintypes
from io import BytesIO

from PIL import Image


class Desktop:
    def __init__(self, title="BOSS直聘"):
        if sys.platform != "win32":
            raise RuntimeError("此执行器目前仅支持 Windows 主屏幕")
        # Must precede PyAutoGUI imports and coordinate queries.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        import pyautogui

        self.gui = pyautogui
        self.gui.FAILSAFE = True
        self.gui.PAUSE = 0.3
        self.title = title

    def check_window(self):
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        handle = user32.GetForegroundWindow()
        text = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(handle, text, len(text))
        if self.title.casefold() not in text.value.casefold():
            raise RuntimeError("当前前台窗口不是配置的 BOSS直聘窗口，已停止")
        return handle

    def window_signature(self):
        handle = self.check_window()
        rect = wintypes.RECT()
        get_rect = ctypes.windll.user32.GetWindowRect
        get_rect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        if not get_rect(handle, ctypes.byref(rect)):
            raise RuntimeError("无法读取浏览器窗口位置，已停止")
        return (handle, rect.left, rect.top, rect.right, rect.bottom)

    def capture(self):
        signature = self.window_signature()
        image = self.gui.screenshot()
        if image.size != tuple(self.gui.size()):
            raise RuntimeError("截图与鼠标坐标尺度不一致，请检查显示缩放")
        stream = BytesIO()
        image.save(stream, format="PNG")
        if self.window_signature() != signature:
            raise RuntimeError("截图时窗口位置已变化，请重新识别")
        data = stream.getvalue()
        self._last_frame = (hashlib.sha256(data).digest(), signature)
        return data, image.size

    def point(self, x, y):
        width, height = self.gui.size()
        if not 0 < x < width - 1 or not 0 < y < height - 1:
            raise ValueError("坐标必须在主屏幕内部")

    def click(self, x, y, *, expected=None, target_only=False, diagnostics=None):
        self.check_window()
        self.point(x, y)
        if expected is not None:
            old_frame = getattr(self, "_last_frame", None)
            current, _ = self.capture()
            try:
                if target_only:
                    if old_frame is None or old_frame[0] != hashlib.sha256(expected).digest():
                        raise RuntimeError("缺少目标截图的窗口上下文，请重新识别")
                    if old_frame[1] != self._last_frame[1]:
                        raise RuntimeError("浏览器窗口已移动、缩放或切换，请重新识别")
                    verify_target_image(expected, current, x, y)
                elif hashlib.sha256(current).digest() != hashlib.sha256(expected).digest():
                    raise RuntimeError("页面已变化，旧截图坐标作废；请重新识别")
            except RuntimeError:
                if diagnostics is not None:
                    (diagnostics / "guard-current.png").write_bytes(current)
                raise
        self.gui.moveTo(x, y, duration=0.3)
        if tuple(self.gui.position()) != (x, y):
            raise RuntimeError("鼠标实际位置与目标坐标不一致，未点击；请检查桌面焦点或缩放")
        self.check_window()
        if (
            expected is not None
            and old_frame is not None
            and self.window_signature() != old_frame[1]
        ):
            raise RuntimeError("鼠标移动期间浏览器位置已变化，未点击")
        self.gui.click()

    def scroll(self, x, y, ticks=-3):
        if not -10 <= ticks <= 10 or ticks == 0:
            raise ValueError("每次滚动刻度须为 -10..-1 或 1..10")
        self.check_window()
        self.point(x, y)
        self.gui.moveTo(x, y, duration=0.3)
        self.gui.scroll(ticks)


def pixel(point, size):
    """Model coordinates use 0..1000 relative to the full original screenshot."""
    x, y = point
    if not 0 < x < 1000 or not 0 < y < 1000:
        raise ValueError("模型坐标越界")
    return round(x * size[0] / 1000), round(y * size[1] / 1000)


def verify_target_image(expected, current, x, y):
    """Card navigation only: exact equality near the target, ignoring unrelated regions."""
    with Image.open(BytesIO(expected)) as before, Image.open(BytesIO(current)) as after:
        if before.size != after.size:
            raise RuntimeError("屏幕尺寸已变化，请重新识别")
        region = (
            max(0, x - 220),
            max(0, y - 32),
            min(before.width, x + 220),
            min(before.height, y + 32),
        )
        if (
            before.crop(region).convert("RGB").tobytes()
            != after.crop(region).convert("RGB").tobytes()
        ):
            raise RuntimeError("目标标题区域已变化，未点击；请查看 guard-current.png 并重新识别")
