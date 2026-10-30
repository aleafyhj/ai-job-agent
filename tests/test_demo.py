from types import SimpleNamespace
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app import models as m
from app.api.resources import get_row, policy_read, profile_read
from app.boss_desktop import run
from app.collected_jobs import persist_analysis
from app.core.config import Settings
from app.vision import Card
from scripts.seed_demo import seed


def test_simulated_profile_cannot_communicate_even_if_flag_is_passed(tmp_path):
    with pytest.raises(ValueError, match="模拟档案"):
        run(
            None,
            None,
            {"facts": {"data_kind": "simulation"}},
            {},
            tmp_path,
            SimpleNamespace(communicate=True),
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.postgres
def test_demo_reuse_and_simulated_analysis_never_becomes_eligible(database, tmp_path):
    engine, _ = database
    resources = seed(engine)
    assert seed(engine) == resources
    with Session(engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(m.profiles)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(m.policies)) == 1
        profile = profile_read(
            session, get_row(session, m.profiles, UUID(resources["profile_id"]))
        ).model_dump(mode="json")
        policy = policy_read(
            session, get_row(session, m.policies, UUID(resources["policy_id"]))
        ).model_dump(mode="json")
    assert profile["facts"]["data_kind"] == "simulation"
    assert policy["auto_apply"] is False
    image = tmp_path / "test.png"
    image.write_bytes(b"test-image")
    ids = persist_analysis(
        engine,
        Settings(_env_file=None, artifacts_dir=tmp_path),
        profile,
        policy,
        "demo-job",
        Card(title="Python开发", company="测试公司", city="上海", salary="10K", center=(200, 300)),
        SimpleNamespace(detail_text="熟悉Python", detail_complete=True, hard_conditions="pass"),
        {"score": 100, "requirements": []},
        image,
    )
    with Session(engine) as session:
        analysis = get_row(session, m.analyses, UUID(ids["analysis_id"]))
        assert analysis["score"] == 100
        assert analysis["eligible"] is False
        assert analysis["match_evidence"]["profile_data_kind"] == "simulation"
        assert session.scalar(sa.select(sa.func.count()).select_from(m.applications)) == 0
