import json
from types import SimpleNamespace

import pytest

from app.boss_desktop import main, run
from app.desktop import Desktop, pixel
from app.vision import Card, Requirement, Screen, score


def screen():
    card = Card(title="开发", company="公司", city="上海", salary="10K", center=(200, 300))
    return Screen(
        blocked=False,
        uncertain=False,
        cards=[card],
        selected=card,
        detail_text="熟悉Python",
        detail_complete=True,
        hard_conditions="pass",
        requirements=[Requirement(quote="熟悉Python", importance="required", skill_index=0)],
    )


def test_coordinate_mapping_and_bounds():
    assert pixel((500, 250), (1920, 1080)) == (960, 270)
    with pytest.raises(ValueError):
        pixel((1001, 250), (1920, 1080))


def test_score_requires_complete_evidence():
    data = screen()
    assert score(data, [{"evidence": "项目经验"}]) == 100
    data.detail_complete = False
    assert score(data, [{"evidence": "项目经验"}]) is None
    data.detail_complete = True
    with pytest.raises(ValueError):
        score(data, [])
    data.requirements[0].quote = "不存在的原文"
    with pytest.raises(ValueError):
        score(data, [{"evidence": "项目经验"}])


def test_stale_image_never_clicks():
    desktop = Desktop.__new__(Desktop)
    desktop.check_window = lambda: None
    desktop.point = lambda x, y: None
    desktop.capture = lambda: (b"changed", (1000, 1000))
    with pytest.raises(RuntimeError, match="页面已变化"):
        desktop.click(100, 100, expected=b"old")


def test_readonly_loop_and_restart_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr("app.boss_desktop.time.sleep", lambda _: None)
    clicks = []
    desktop = SimpleNamespace(
        capture=lambda: (b"image", (1000, 1000)),
        click=lambda *a, **kw: clicks.append(a),
        scroll=lambda *a, **kw: None,
    )
    vision = SimpleNamespace(inspect=lambda *a: screen())
    profile = dict(
        id="p",
        version_id="p1",
        skills=[{"evidence": "项目经验"}],
        education={},
        experience_years=2,
        preferred_cities=[],
        remote_preference="any",
    )
    policy = dict(version_id="v1", threshold=70, comparison="gte", daily_limit=1)
    args = SimpleNamespace(
        pages=1,
        jobs=1,
        detail_scrolls=1,
        settle=1,
        ticks=3,
        communicate=False,
        right_scroll=[750, 800],
        left_scroll=[200, 500],
    )
    assert run(desktop, vision, profile, policy, tmp_path, args) == 1
    assert len(clicks) == 1
    records = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert next(iter(records.values()))["state"] == "analyzed"
    assert run(desktop, vision, profile, policy, tmp_path, args) == 0
    assert len(clicks) == 1


def test_blocked_page_stops_before_click(tmp_path):
    desktop = SimpleNamespace(capture=lambda: (b"image", (1000, 1000)))
    vision = SimpleNamespace(inspect=lambda *a: Screen())
    profile = dict(
        id="p",
        skills=[],
        education={},
        experience_years=0,
        preferred_cities=[],
        remote_preference="any",
    )
    args = SimpleNamespace(pages=1, jobs=1)
    with pytest.raises(RuntimeError, match="已暂停"):
        run(desktop, vision, profile, {}, tmp_path, args)


@pytest.mark.parametrize("action", ["screenshot", "click", "scroll"])
def test_manual_command_saves_images_and_reports_paths(action, tmp_path, monkeypatch, capsys):
    calls = []
    fake = SimpleNamespace(
        capture=lambda: (b"test-image", (1000, 1000)),
        click=lambda *a, **kw: calls.append("click"),
        scroll=lambda *a, **kw: calls.append("scroll"),
    )
    monkeypatch.setattr("app.boss_desktop.Desktop", lambda _: fake)
    monkeypatch.setattr(
        "app.boss_desktop.Settings", lambda: SimpleNamespace(artifacts_dir=tmp_path)
    )
    monkeypatch.setattr("app.boss_desktop.time.sleep", lambda _: None)
    monkeypatch.setattr("sys.argv", ["boss_desktop", action, "--x", "200", "--y", "300"])
    main()
    root = tmp_path / "boss-desktop"
    names = (
        ["screen.png"]
        if action == "screenshot"
        else [
            f"{action}-before.png",
            f"{action}-after.png",
        ]
    )
    output = capsys.readouterr().out
    for name in names:
        assert (root / name).read_bytes() == b"test-image"
        assert str((root / name).resolve()) in output
    assert calls == ([] if action == "screenshot" else [action])
    assert not (root / "desktop.lock").exists()
