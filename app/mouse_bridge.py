"""Delegate only mouse input to a verified independent interpreter."""

import json
from multiprocessing.connection import Client
from pathlib import Path


class BridgeMouse:
    def __init__(self, interpreter, title, state_dir=None):
        self.interpreter = str(interpreter)
        self.title = title
        self.last_event = None
        self.expected_position = None
        self.state_dir = (
            Path(state_dir)
            if state_dir is not None
            else Path(__file__).resolve().parents[1] / "artifacts" / "boss-desktop"
        )

    def call(self, action, **values):
        key_path = self.state_dir / "mouse-auth.key"
        try:
            service = json.loads(
                (key_path.parent / "mouse-service.json").read_text(encoding="utf-8")
            )
            with Client(
                ("127.0.0.1", service["port"]), family="AF_INET", authkey=None
            ) as connection:
                connection.send_bytes(
                    json.dumps(
                        {
                            "action": action,
                            "title": self.title,
                            "auth": key_path.read_bytes().hex(),
                            **values,
                        }
                    ).encode()
                )
                if not connection.poll(12):
                    raise TimeoutError
                data = json.loads(connection.recv_bytes(8192))
        except Exception as exc:
            raise RuntimeError(
                "鼠标服务未启动或请求结果未知（"
                + type(exc).__name__
                + "）；未自动重试。请先运行 scripts/start-mouse.ps1"
            ) from None
        self.last_event = data.get("event")
        if not data.get("ok"):
            raise RuntimeError(data.get("message", "独立鼠标进程失败，未重试"))
        return tuple(data["position"])

    def move(self, x, y):
        position = self.call("move", x=x, y=y)
        self.expected_position = position
        return position

    def click(self):
        self.call("click", expected_position=self.expected_position)

    def scroll(self, ticks):
        self.call("scroll", ticks=ticks, expected_position=self.expected_position)
