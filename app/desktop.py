"""Primary-screen screenshots and real mouse input; no DOM or network interception."""

import ctypes
import hashlib
import sys
from ctypes import wintypes
from io import BytesIO


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

    def capture(self):
        self.check_window()
        image = self.gui.screenshot()
        if image.size != tuple(self.gui.size()):
            raise RuntimeError("截图与鼠标坐标尺度不一致，请检查显示缩放")
        stream = BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue(), image.size

    def point(self, x, y):
        width, height = self.gui.size()
        if not 0 < x < width - 1 or not 0 < y < height - 1:
            raise ValueError("坐标必须在主屏幕内部")

    def click(self, x, y, *, expected=None):
        self.check_window()
        self.point(x, y)
        if expected is not None:
            current, _ = self.capture()
            if hashlib.sha256(current).digest() != hashlib.sha256(expected).digest():
                raise RuntimeError("页面已变化，旧截图坐标作废；请重新识别")
        self.gui.moveTo(x, y, duration=0.3)
        if tuple(self.gui.position()) != (x, y):
            raise RuntimeError("鼠标实际位置与目标坐标不一致，未点击；请检查桌面焦点或缩放")
        self.check_window()
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
