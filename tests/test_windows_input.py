import ctypes
from types import SimpleNamespace

import pytest

from app.windows_input import Input, MouseInput, WindowsMouse, absolute_point


@pytest.mark.parametrize(
    "point,bounds",
    [
        ((553, 716), (0, 0, 2560, 1600)),
        ((0, 0), (0, 0, 2560, 1600)),
        ((2559, 1599), (0, 0, 2560, 1600)),
        ((-150, 100), (-1920, 0, 4480, 1600)),
    ],
)
def test_absolute_coordinates_roundtrip_to_physical_pixel(point, bounds):
    nx, ny = absolute_point(*point, bounds)
    left, top, width, height = bounds
    assert (left + nx * width // 65536, top + ny * height // 65536) == point


def test_invalid_target_rejected():
    with pytest.raises(ValueError):
        absolute_point(2560, 10, (0, 0, 2560, 1600))


def test_native_abi_and_rejected_events_do_not_retry():
    assert ctypes.sizeof(MouseInput) == (32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24)
    assert ctypes.sizeof(Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    calls = []
    mouse = WindowsMouse.__new__(WindowsMouse)
    mouse.user32 = SimpleNamespace(SendInput=lambda *args: calls.append(args[0]) or 0)
    with pytest.raises(RuntimeError, match="0/2"):
        mouse.click()
    assert calls == [2]
    assert mouse.last_event["accepted"] == 0


def test_native_move_and_scroll_event_shapes():
    packets = []
    mouse = WindowsMouse.__new__(WindowsMouse)

    def send(count, events, size):
        packets.extend(
            (event.mi.dx, event.mi.dy, event.mi.mouseData, event.mi.dwFlags) for event in events
        )
        assert size == ctypes.sizeof(Input)
        return count

    mouse.user32 = SimpleNamespace(
        SendInput=send,
        GetSystemMetrics=lambda index: {
            76: 0,
            77: 0,
            78: 2560,
            79: 1600,
        }[index],
    )
    mouse.position = lambda: (553, 716)
    assert mouse.move(553, 716) == (553, 716)
    assert packets[0][3] == 0xC001
    mouse.scroll(-3)
    assert packets[1][2] == (-360 & 0xFFFFFFFF)
    assert packets[1][3] == 0x0800
