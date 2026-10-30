"""Windows input report; read-only unless --probe explicitly requests one move."""

import argparse
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path


def collect():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    dpi_bound = bool(user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)))
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]

    def name(handle):
        if not handle:
            return None
        text = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        ok = user32.GetUserObjectInformationW(
            handle, 2, text, ctypes.sizeof(text), ctypes.byref(needed)
        )
        return text.value if ok else None

    thread_desktop = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    ctypes.set_last_error(0)
    desktop = user32.OpenInputDesktop(0, False, 0x0081)  # READOBJECTS | WRITEOBJECTS
    error = ctypes.get_last_error() if not desktop else 0
    try:
        session = wintypes.DWORD()
        kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
        result = {
            "python": sys.executable,
            "python_base": sys.base_prefix,
            "python_version": sys.version.split()[0],
            "process_session": session.value,
            "thread_desktop": name(thread_desktop),
            "window_station": name(user32.GetProcessWindowStation()),
            "dpi_thread_bound": dpi_bound,
            "input_desktop": name(desktop),
            "input_desktop_access": bool(desktop),
            "input_desktop_error": error,
            "primary_size": [user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)],
            "virtual_bounds": [user32.GetSystemMetrics(i) for i in (76, 77, 78, 79)],
        }
    finally:
        if desktop:
            user32.CloseDesktop(desktop)
    return result


def probe(x, y):
    """One explicit move, on a BOSS foreground window; never click or retry."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HANDLE
    user32.GetWindowTextW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, ctypes.c_int]
    user32.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.SetPhysicalCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetPhysicalCursorPos.restype = wintypes.BOOL
    print("5秒内切换到 BOSS直聘窗口。只移动一次，不点击、不调用模型。", flush=True)
    time.sleep(5)
    title = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(user32.GetForegroundWindow(), title, len(title))
    if "BOSS直聘".casefold() not in title.value.casefold():
        raise RuntimeError("前台不是 BOSS直聘，未移动")
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    if not 0 < x < width - 1 or not 0 < y < height - 1:
        raise ValueError("目标必须在主屏内部")

    def position():
        point = wintypes.POINT()
        if not user32.GetPhysicalCursorPos(ctypes.byref(point)):
            raise RuntimeError("无法读取位置，停止")
        return [point.x, point.y]

    before = position()
    ctypes.set_last_error(0)
    accepted = bool(user32.SetPhysicalCursorPos(x, y))
    error = ctypes.get_last_error()
    time.sleep(0.05)
    return {
        "target": [x, y],
        "before": before,
        "api_success": accepted,
        "win32_error": error,
        "after": position(),
    }


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Windows only")
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="显式测试一次鼠标移动，默认只读")
    parser.add_argument("--x", type=int, default=553)
    parser.add_argument("--y", type=int, default=716)
    args = parser.parse_args()
    report = collect()
    if args.probe:
        report["probe"] = probe(args.x, args.y)
    path = Path(__file__).resolve().parents[1] / "artifacts" / "boss-desktop" / "environment.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Report:", path)
