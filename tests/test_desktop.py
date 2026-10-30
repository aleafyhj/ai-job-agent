import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.boss_desktop import main, run
from app.desktop import Desktop, pixel, verify_target_image
from app.desktop_report import ground_card, observe, verify_selection
from app.vision import Card, Requirement, Screen, TitleTarget, score


def screen():
    card = Card(title="开发", company="公司", city="上海", salary="10K", center=(200, 300))
    return Screen(
        blocked=False,
        uncertain=False,
        cards=[card],
        selected=card,
        detail_text="熟悉Python",
        detail_complete=True,
        detail_start_visible=True,
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
        capture=lambda: (png_bytes(), (1000, 1000)),
        click=lambda *a, **kw: clicks.append(a),
        scroll=lambda *a, **kw: None,
    )
    vision = fake_vision()
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
    desktop = SimpleNamespace(capture=lambda: (png_bytes(), (1000, 1000)))
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


def png_bytes():
    stream = BytesIO()
    Image.new("RGB", (1000, 1000), "white").save(stream, format="PNG")
    return stream.getvalue()


def test_observation_saves_coordinates_and_failure_report(tmp_path):
    result, size, folder = observe(fake_vision(), png_bytes(), {}, "list", tmp_path)
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    assert size == (1000, 1000)
    assert report["cards"][0]["pixel"] == [200, 300]
    assert (folder / "targets.png").is_file()
    assert (folder / "screen.png").read_bytes() == png_bytes()
    verify_selection(result.cards[0], result)
    result.selected = result.selected.model_copy(update={"company": "Other"})
    with pytest.raises(RuntimeError, match="身份不匹配"):
        verify_selection(result.cards[0], result)

    def fail(*_):
        raise RuntimeError("secret provider diagnostics")

    with pytest.raises(RuntimeError):
        observe(SimpleNamespace(inspect=fail), png_bytes(), {}, "detail", tmp_path)
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.rglob("report.json")]
    failed = next(item for item in reports if item["status"] == "failed")
    assert failed["error_type"] == "RuntimeError"
    assert "secret" not in json.dumps(failed)


def test_offline_inspect_never_constructs_desktop(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    source.write_bytes(png_bytes())
    monkeypatch.setattr(
        "app.boss_desktop.Desktop", lambda _: pytest.fail("offline must not control desktop")
    )
    monkeypatch.setattr(
        "app.boss_desktop.Settings", lambda: SimpleNamespace(artifacts_dir=tmp_path)
    )
    monkeypatch.setattr("app.boss_desktop.Vision", lambda _: fake_vision())
    monkeypatch.setattr("sys.argv", ["boss_desktop", "inspect", "--image", str(source)])
    main()
    assert len(list(tmp_path.rglob("report.json"))) == 1
    assert not (tmp_path / "boss-desktop" / "desktop.lock").exists()


@pytest.mark.parametrize("index,expected_clicks", [(1, 1), (2, 0)])
def test_click_card_uses_fresh_model_coordinates(index, expected_clicks, tmp_path, monkeypatch):
    clicks = []
    desktop = SimpleNamespace(
        capture=lambda: (png_bytes(), (1000, 1000)),
        click=lambda *a, **kw: clicks.append((a, kw)),
    )
    monkeypatch.setattr("app.boss_desktop.Desktop", lambda _: desktop)
    monkeypatch.setattr(
        "app.boss_desktop.Settings", lambda: SimpleNamespace(artifacts_dir=tmp_path)
    )
    monkeypatch.setattr("app.boss_desktop.Vision", lambda _: fake_vision())
    monkeypatch.setattr("app.boss_desktop.time.sleep", lambda _: None)
    monkeypatch.setattr("sys.argv", ["boss_desktop", "click-card", "--card-index", str(index)])
    if expected_clicks:
        main()
        assert clicks[0][0] == (200, 300)
        assert clicks[0][1]["expected"] == png_bytes()
    else:
        with pytest.raises(SystemExit):
            main()
    assert len(clicks) == expected_clicks


def fake_vision():
    return SimpleNamespace(
        inspect=lambda *a: screen(),
        ground_title=lambda image, card: TitleTarget(
            title=card.title, center=(500, 500), uncertain=False
        ),
    )


def test_local_title_mapping_and_conservative_identity(tmp_path):
    original = screen()
    card = original.cards[0]
    point = ground_card(fake_vision(), png_bytes(), card, original.cards, tmp_path)
    assert point == (200, 300)
    assert (tmp_path / "title-crop.png").is_file()
    report = json.loads((tmp_path / "title-target.json").read_text(encoding="utf-8"))
    assert report["pixel"] == [200, 300]
    card.city = "深圳-龙岗区"
    original.selected = card.model_copy(update={"city": "深圳·龙岗区"})
    verify_selection(card, original)
    original.selected = card.model_copy(update={"city": "深圳"})
    verify_selection(card, original)
    original.selected = card.model_copy(update={"city": "深圳·南山区"})
    with pytest.raises(RuntimeError, match="city"):
        verify_selection(card, original)
    original.selected = card.model_copy(update={"title": "其他职位"})
    with pytest.raises(RuntimeError, match="title"):
        verify_selection(card, original)
    bad = SimpleNamespace(
        ground_title=lambda *a: TitleTarget(
            title=card.title,
            center=(500, 500),
            uncertain=True,
        )
    )
    with pytest.raises(RuntimeError, match="未执行点击"):
        ground_card(bad, png_bytes(), card, original.cards, tmp_path)


@pytest.mark.parametrize(
    "change,mode,should_click",
    [
        ("outside", True, True),
        ("outside", False, False),
        ("target", True, False),
        ("window", True, False),
    ],
)
def test_card_guard_ignores_only_unrelated_changes(change, mode, should_click, tmp_path):
    before = png_bytes()
    with Image.open(BytesIO(before)) as source:
        source = source.convert("RGB")
        source.putpixel((500, 500) if change == "target" else (10, 10), (0, 0, 0))
        stream = BytesIO()
        source.save(stream, format="PNG")
        current = stream.getvalue()
    old_signature = (1, 0, 0, 1000, 1000)
    new_signature = (1, 100, 0, 1100, 1000) if change == "window" else old_signature
    desktop = Desktop.__new__(Desktop)
    desktop._last_frame = (hashlib.sha256(before).digest(), old_signature)
    desktop.check_window = lambda: 1
    desktop.window_signature = lambda: new_signature
    desktop.point = lambda x, y: None
    clicks = []
    desktop.gui = SimpleNamespace(
        moveTo=lambda *a, **kw: None, position=lambda: (500, 500), click=lambda: clicks.append(1)
    )

    def capture():
        desktop._last_frame = (hashlib.sha256(current).digest(), new_signature)
        return current, (1000, 1000)

    desktop.capture = capture
    if should_click:
        desktop.click(500, 500, expected=before, target_only=mode, diagnostics=tmp_path)
        assert clicks == [1]
    else:
        with pytest.raises(RuntimeError):
            desktop.click(500, 500, expected=before, target_only=mode, diagnostics=tmp_path)
        assert clicks == []
        assert (tmp_path / "guard-current.png").read_bytes() == current


def test_target_guard_rejects_screen_resize():
    stream = BytesIO()
    Image.new("RGB", (800, 800)).save(stream, format="PNG")
    with pytest.raises(RuntimeError, match="屏幕尺寸"):
        verify_target_image(png_bytes(), stream.getvalue(), 500, 500)
