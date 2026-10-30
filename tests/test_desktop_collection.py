import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from app.desktop_collection import collect_detail
from app.vision import Box, Card, Requirement, Screen, match_report


def detail(text, *, start=False, complete=False, title="Python开发"):
    return Screen(
        blocked=False,
        uncertain=False,
        selected=Card(
            title=title, company="测试公司", city="上海", salary="10K", center=(200, 300)
        ),
        detail_text=text,
        detail_start_visible=start,
        detail_complete=complete,
        detail_scroll=Box(center=(750, 800)),
    )


def collect(tmp_path, monkeypatch, frames, evaluate=None):
    monkeypatch.setattr("app.desktop_collection.time.sleep", lambda _: None)
    stream = BytesIO()
    Image.new("RGB", (100, 100), "white").save(stream, format="PNG")
    scrolls = []
    phases = []
    iterator = iter(frames)

    def inspect(image, evidence, phase):
        phases.append(phase)
        if phase == "evaluate_all_text":
            assert evidence["complete_detail_text"] == "岗位职责：开发API\n任职要求：熟悉Python"
            return evaluate
        return next(iterator)

    desktop = SimpleNamespace(
        capture=lambda: (stream.getvalue(), (100, 100)),
        scroll=lambda *point, **kw: scrolls.append((point, kw["ticks"])),
    )
    args = SimpleNamespace(detail_scrolls=3, right_scroll=None, settle=1, ticks=3)
    result = collect_detail(
        desktop,
        SimpleNamespace(inspect=inspect),
        detail("x").selected,
        {"skills": []},
        tmp_path,
        args,
        evaluate=evaluate is not None,
    )
    return result, scrolls, phases


def test_collect_reads_top_then_bottom_and_keeps_all_evidence(tmp_path, monkeypatch):
    (result, _, folder), scrolls, phases = collect(
        tmp_path,
        monkeypatch,
        [
            detail("当前在正文中间"),
            detail("岗位职责：开发API", start=True),
            detail("任职要求：熟悉Python", complete=True),
        ],
    )
    assert result.detail_complete
    assert result.hard_conditions == "unknown"
    assert result.detail_text == "岗位职责：开发API\n任职要求：熟悉Python"
    assert scrolls == [((75, 80), 10), ((75, 80), -3)]
    assert phases == ["detail"] * 3
    report = json.loads((folder / "collection.json").read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    for observation in report["observations"]:
        assert (tmp_path / observation / "screen.png").is_file()
    assert (folder / "detail.txt").read_text(encoding="utf-8") == result.detail_text


def test_scroll_without_progress_never_implies_complete(tmp_path, monkeypatch):
    (result, _, folder), scrolls, _ = collect(
        tmp_path,
        monkeypatch,
        [
            detail("岗位职责：开发API", start=True),
            detail("岗位职责：开发API", start=True),
        ],
    )
    assert not result.detail_complete
    assert len(scrolls) == 1
    report = json.loads((folder / "collection.json").read_text(encoding="utf-8"))
    assert report["status"] == "incomplete"
    assert report["stop_reason"] == "no_scroll_progress"


def test_changed_job_stops_collection_and_saves_failure(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="身份不匹配"):
        collect(tmp_path, monkeypatch, [detail("正文", title="其他职位")])
    report = json.loads(
        next((tmp_path / "collections").rglob("collection.json")).read_text(encoding="utf-8")
    )
    assert report["status"] == "failed"
    assert report["detail_complete"] is False


def test_short_complete_job_does_not_scroll(tmp_path, monkeypatch):
    (result, _, _), scrolls, _ = collect(
        tmp_path,
        monkeypatch,
        [
            detail("任职要求：熟悉Python", start=True, complete=True),
        ],
    )
    assert result.detail_complete
    assert not scrolls


def test_model_top_flag_cannot_hide_missing_opening_paragraphs(tmp_path, monkeypatch):
    (result, _, _), scrolls, _ = collect(
        tmp_path,
        monkeypatch,
        [
            detail("4、对接API。任职要求：熟悉Python", start=True, complete=True),
            detail("岗位职责：开发API", start=True),
            detail("任职要求：熟悉Python", complete=True),
        ],
    )
    assert result.detail_text.startswith("岗位职责：开发API")
    assert scrolls[0][1] == 10


def test_evaluation_uses_collected_text_instead_of_only_last_image(tmp_path, monkeypatch):
    evaluated = detail("仅末页", complete=True)
    evaluated.hard_conditions = "pass"
    evaluated.requirements = [Requirement(quote="熟悉Python", importance="required", skill_index=0)]
    (result, _, _), _, phases = collect(
        tmp_path,
        monkeypatch,
        [
            detail("岗位职责：开发API", start=True),
            detail("任职要求：熟悉Python", complete=True),
        ],
        evaluated,
    )
    assert result.detail_text == "岗位职责：开发API\n任职要求：熟悉Python"
    assert phases[-1] == "evaluate_all_text"
    assert result.hard_conditions == "pass"


@pytest.mark.parametrize("comparison,eligible", [("gte", True), ("gt", False)])
def test_seventy_boundary_and_explanation(comparison, eligible):
    data = detail("Python、FastAPI、PostgreSQL、Docker", start=True, complete=True)
    data.hard_conditions = "pass"
    data.requirements = [
        Requirement(quote=name, importance=importance, skill_index=index)
        for name, importance, index in [
            ("Python", "required", 0),
            ("FastAPI", "required", 1),
            ("PostgreSQL", "required", None),
            ("Docker", "preferred", 2),
        ]
    ]
    skills = [
        {"canonical_name": name, "evidence": "实际项目"} for name in ("Python", "FastAPI", "Docker")
    ]
    policy = {"threshold": 70, "comparison": comparison}
    report = match_report(data, skills, policy)
    assert report["score"] == 70
    assert report["eligible"] is eligible
    assert report["requirements"][2]["matched"] is False
    assert report["requirements"][3]["weight"] == 1
    data.hard_conditions = "unknown"
    assert match_report(data, skills, policy)["eligible"] is False
    data.detail_complete = False
    assert match_report(data, skills, policy)["score"] is None


def test_boolean_is_not_a_skill_reference():
    with pytest.raises(ValidationError):
        Requirement(quote="Python", importance="required", skill_index=False)
