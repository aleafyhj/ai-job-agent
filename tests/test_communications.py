from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models as m
from app.communications import CommunicationBlocked, finish_communication, start_communication
from app.core.config import Settings
from app.database import make_session_factory
from app.main import create_app


def seed_eligible_analysis(engine):
    with Session(engine) as session, session.begin():
        profile_id = session.scalar(
            m.profiles.insert().values(name="真实档案").returning(m.profiles.c.id)
        )
        profile_version_id = session.scalar(
            m.profile_versions.insert()
            .values(
                profile_id=profile_id,
                version=1,
                skills=[{"canonical_name": "Python", "evidence": "Python项目"}],
                facts={"data_kind": "resume"},
            )
            .returning(m.profile_versions.c.id)
        )
        session.execute(
            m.profiles.update()
            .where(m.profiles.c.id == profile_id)
            .values(current_version_id=profile_version_id)
        )
        policy_id = session.scalar(
            m.policies.insert().values(name="沟通策略").returning(m.policies.c.id)
        )
        policy_version_id = session.scalar(
            m.policy_versions.insert()
            .values(
                policy_id=policy_id,
                version=1,
                threshold=70,
                comparison="gte",
                daily_limit=1,
                timezone="Asia/Shanghai",
                auto_apply=True,
                allowed_platforms=["boss"],
                blocked_companies=[],
            )
            .returning(m.policy_versions.c.id)
        )
        session.execute(
            m.policies.update()
            .where(m.policies.c.id == policy_id)
            .values(current_version_id=policy_version_id)
        )
        raw_artifact_id = session.scalar(
            m.artifacts.insert()
            .values(
                kind="job_screenshot",
                storage_key="seed-job.png",
                mime_type="image/png",
                sha256="a" * 64,
                size=1,
            )
            .returning(m.artifacts.c.id)
        )
        job_id = session.scalar(
            m.jobs.insert()
            .values(
                platform_key="boss",
                canonical_url="https://www.zhipin.com/web/geek/jobs",
                identity_key="communication-test",
                company_name="测试公司",
                title="Python开发",
            )
            .returning(m.jobs.c.id)
        )
        snapshot_id = session.scalar(
            m.job_snapshots.insert()
            .values(
                job_id=job_id,
                content_hash="b" * 64,
                raw_artifact_id=raw_artifact_id,
                normalized={"detail_text": "熟悉Python", "detail_complete": True},
                extractor_version="desktop-v2",
            )
            .returning(m.job_snapshots.c.id)
        )
        session.execute(
            m.jobs.update().where(m.jobs.c.id == job_id).values(latest_snapshot_id=snapshot_id)
        )
        analysis_id = session.scalar(
            m.analyses.insert()
            .values(
                job_snapshot_id=snapshot_id,
                profile_version_id=profile_version_id,
                policy_version_id=policy_version_id,
                status="ready",
                score=100,
                hard_status="pass",
                eligible=True,
                prompt_version="desktop-v6",
                model="qwen-test",
                input_hash="c" * 64,
            )
            .returning(m.analyses.c.id)
        )
    return profile_id, job_id, analysis_id


@pytest.mark.postgres
def test_communication_is_reserved_once_finalized_and_visible(database, tmp_path):
    engine, _ = database
    profile_id, job_id, analysis_id = seed_eligible_analysis(engine)
    settings = Settings(_env_file=None, api_token="x" * 40, artifacts_dir=tmp_path)

    pending = start_communication(engine, settings, analysis_id, b"before", (800, 100))
    assert pending["status"] == "pending"
    with pytest.raises(CommunicationBlocked, match="禁止重复点击"):
        start_communication(engine, settings, analysis_id, b"duplicate", (800, 100))

    confirmed = finish_communication(
        engine,
        settings,
        pending["id"],
        "confirmed",
        b"after",
        {"input": "executed", "confirmation": True},
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_at"] is not None
    with Session(engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(m.communications)) == 1
        artifacts = (
            session.execute(
                sa.select(m.artifacts).where(m.artifacts.c.kind == "communication_screenshot")
            )
            .mappings()
            .all()
        )
        assert len(artifacts) == 2
        assert {item["metadata"]["stage"] for item in artifacts} == {"before", "after"}

    app = create_app(settings)
    with TestClient(app) as client:
        app.state.db_engine = engine
        app.state.db_sessions = make_session_factory(engine)
        client.headers["Authorization"] = "Bearer " + "x" * 40
        detail = client.get(f"/api/v1/analyses/{analysis_id}")
        assert detail.status_code == 200
        assert detail.json()["communication"]["status"] == "confirmed"
        page = client.get("/api/v1/communications", params={"profile_id": profile_id})
        assert page.status_code == 200
        assert page.json()["items"][0]["job_id"] == str(job_id)
        assert client.get("/api/v1/communications", params={"status": "bad"}).status_code == 422

    assert UUID(str(confirmed["analysis_id"])) == analysis_id
