import json

import pytest

from app.mouse_bridge import BridgeMouse


@pytest.fixture
def service(tmp_path):
    (tmp_path / "mouse-auth.key").write_bytes(b"test-key")
    (tmp_path / "mouse-service.json").write_text('{"port": 12345}', encoding="utf-8")
    return tmp_path


def fake_client(monkeypatch, *, response=None, ready=True):
    requests = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_bytes(self, value):
            requests.append(json.loads(value))

        def poll(self, timeout):
            assert timeout == 12
            return ready

        def recv_bytes(self, maximum):
            assert maximum == 8192
            return json.dumps(
                response or {"ok": True, "position": [553, 716], "event": {"backend": "Python3.9"}}
            ).encode()

    def connect(address, **kwargs):
        assert address == ("127.0.0.1", 12345)
        assert kwargs == {"family": "AF_INET", "authkey": None}
        return Connection()

    monkeypatch.setattr("app.mouse_bridge.Client", connect)
    return requests


def test_bridge_authenticates_and_checks_position(monkeypatch, service):
    requests = fake_client(monkeypatch)
    bridge = BridgeMouse("working-python.exe", "BOSS直聘", state_dir=service)
    assert bridge.move(553, 716) == (553, 716)
    bridge.click()
    bridge.scroll(-3)
    assert requests[0] == {
        "action": "move",
        "title": "BOSS直聘",
        "auth": b"test-key".hex(),
        "x": 553,
        "y": 716,
    }
    assert requests[1]["expected_position"] == [553, 716]
    assert requests[2]["ticks"] == -3
    assert bridge.last_event == {"backend": "Python3.9"}


def test_bridge_never_retries_unknown_result(monkeypatch, service):
    requests = fake_client(monkeypatch, ready=False)
    with pytest.raises(RuntimeError, match="未自动重试"):
        BridgeMouse("working-python.exe", "BOSS直聘", state_dir=service).move(553, 716)
    assert len(requests) == 1


def test_bridge_preserves_worker_rejection(monkeypatch, service):
    requests = fake_client(monkeypatch, response={"ok": False, "message": "窗口变化，停止"})
    bridge = BridgeMouse("working-python.exe", "BOSS直聘", state_dir=service)
    with pytest.raises(RuntimeError, match="窗口变化，停止"):
        bridge.move(553, 716)
    assert bridge.expected_position is None
    assert len(requests) == 1


def test_missing_service_does_not_launch_or_send(monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        pytest.fail("Missing state must fail before connecting")

    monkeypatch.setattr("app.mouse_bridge.Client", unexpected)
    with pytest.raises(RuntimeError, match="start-mouse.ps1"):
        BridgeMouse("working-python.exe", "BOSS直聘", state_dir=tmp_path).move(553, 716)
