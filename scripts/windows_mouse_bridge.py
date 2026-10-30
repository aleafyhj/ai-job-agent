"""Standard-library mouse worker, compatible with the tested Python 3.9 runtime."""

import ctypes
import hmac
import json
import os
import sys
from ctypes import wintypes
from multiprocessing.connection import Client, Listener
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def execute(request):
    from mouse_doctor import collect, probe

    # Reuse the same standalone initialization and move function that passed
    # the real Python 3.9 comparison, rather than another implementation.
    collect()
    if request["action"] == "move":
        result = probe(
            request["x"],
            request["y"],
            wait_seconds=0,
            announce=False,
            expected_title=request["title"],
        )
        if not result["api_success"] or result["after"] != [request["x"], request["y"]]:
            raise RuntimeError("Python3.9鼠标移动未成功：" + json.dumps(result, ensure_ascii=True))
        return {"ok": True, "position": result["after"], "event": result}

    from app.windows_input import WindowsMouse

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    user32.GetForegroundWindow.restype = wintypes.HANDLE
    user32.GetWindowTextW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, ctypes.c_int]
    title = ctypes.create_unicode_buffer(1024)
    handle = user32.GetForegroundWindow()
    user32.GetWindowTextW(handle, title, len(title))
    if request["title"].casefold() not in title.value.casefold():
        raise RuntimeError("鼠标工作进程：BOSS窗口不在前台，已停止")
    mouse = WindowsMouse()
    if mouse.position() in (
        (0, 0),
        (user32.GetSystemMetrics(0) - 1, 0),
        (0, user32.GetSystemMetrics(1) - 1),
        (user32.GetSystemMetrics(0) - 1, user32.GetSystemMetrics(1) - 1),
    ):
        raise RuntimeError("鼠标位于屏幕角落，紧急停止")
    action = request["action"]
    if action in ("click", "scroll"):
        if list(mouse.position()) != request["expected_position"]:
            raise RuntimeError("工作进程检测到鼠标位置变化，未执行按钮或滚轮")
        if user32.GetForegroundWindow() != handle:
            raise RuntimeError("前台窗口变化，停止")
        if action == "click":
            mouse.click()
        else:
            ticks = request["ticks"]
            if not isinstance(ticks, int) or not 1 <= abs(ticks) <= 10:
                raise ValueError("滚动参数无效")
            mouse.scroll(ticks)
        actual = mouse.position()
    else:
        raise ValueError("未知鼠标操作")
    return {"ok": True, "position": list(actual), "event": mouse.last_event}


if __name__ == "__main__":
    if "--status" in sys.argv:
        state = Path(__file__).resolve().parents[1] / "artifacts" / "boss-desktop"
        try:
            service = json.loads((state / "mouse-service.json").read_text(encoding="utf-8"))
            with Client(("127.0.0.1", service["port"]), authkey=None) as connection:
                connection.send_bytes(
                    json.dumps(
                        {"action": "status", "auth": (state / "mouse-auth.key").read_bytes().hex()}
                    ).encode()
                )
                if not connection.poll(2):
                    raise TimeoutError
                result = json.loads(connection.recv_bytes(8192))
                if not result.get("ok"):
                    raise RuntimeError("Status failed")
                print("Mouse worker ready: PID=" + str(result["pid"]))
        except Exception:
            raise SystemExit(1)
        raise SystemExit(0)
    if "--serve" in sys.argv:
        root = Path(__file__).resolve().parents[1]
        state = root / "artifacts" / "boss-desktop"
        state.mkdir(parents=True, exist_ok=True)
        key_path = state / "mouse-auth.key"
        try:
            with key_path.open("xb") as stream:
                stream.write(os.urandom(32))
        except FileExistsError:
            pass
        with Listener(("127.0.0.1", 0), family="AF_INET", authkey=None) as listener:
            (state / "mouse-service.json").write_text(
                json.dumps(
                    {
                        "port": listener.address[1],
                        "pid": os.getpid(),
                    }
                ),
                encoding="utf-8",
            )
            while True:
                with listener.accept() as connection:
                    try:
                        if not connection.poll(3):
                            continue
                        request = json.loads(connection.recv_bytes(8192))
                        if not hmac.compare_digest(
                            request.pop("auth", ""), key_path.read_bytes().hex()
                        ):
                            raise RuntimeError("鼠标服务认证失败")
                        if request.get("action") == "status":
                            result = {"ok": True, "position": [0, 0], "pid": os.getpid()}
                        else:
                            result = execute(request)
                    except Exception as exc:
                        result = {
                            "ok": False,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                    try:
                        connection.send_bytes(json.dumps(result, ensure_ascii=True).encode())
                    except (OSError, EOFError):
                        pass  # An interrupted client must not terminate the service.
        raise SystemExit(0)
    try:
        result = execute(json.loads(sys.stdin.read(8192)))
    except Exception as exc:
        result = {"ok": False, "error_type": type(exc).__name__, "message": str(exc)}
    print(json.dumps(result, ensure_ascii=True))
