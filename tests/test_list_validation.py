import json
from types import SimpleNamespace

from pydantic import SecretStr

from app.vision import Vision


def test_clipped_invalid_card_does_not_abort_other_cards_or_confirm_end(monkeypatch):
    valid = dict(title="Python开发", company="公司", city="上海", salary="10K", center=[200, 300])
    payload = dict(
        blocked=False,
        uncertain=False,
        cards=[valid, {**valid, "salary": ""}],
        selected={**valid, "salary": ""},
        list_end_visible=True,
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, *args):
            return json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}]}).encode()

    monkeypatch.setattr("app.vision.urlopen", lambda *a, **kw: Response())
    vision = Vision(
        SimpleNamespace(
            vision_base_url="https://example.test/v1",
            vision_model="test",
            vision_api_key=SecretStr("test-key"),
        )
    )
    result = vision.inspect(b"not-transmitted", {}, "list")
    assert len(result.cards) == 1
    assert result.discarded_cards == 1
    assert result.selected is None
    assert result.list_end_visible is False
