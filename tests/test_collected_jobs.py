from types import SimpleNamespace
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models as m
from app.collected_jobs import persist_analysis
from app.core.config import Settings
from app.database import make_session_factory
from app.main import create_app
from app.vision import Card


@pytest.mark.postgres
def test_visual_collection_dedup_queries_and_stale_analysis(database, tmp_path):
    engine, _ = database
    settings = Settings(_env_file=None, api_token="x" * 40, artifacts_dir=tmp_path)
    skills = [{"canonical_name": "Python", "level": "proficient", "evidence": "项目经验"}]
    with Session(engine) as session, session.begin():
        profile_id = session.scalar(
            m.profiles.insert().values(name="Test").returning(m.profiles.c.id)
        )
        version_id = session.scalar(
            m.profile_versions.insert()
            .values(
                profile_id=profile_id,
                version=1,
                skills=skills,
            )
            .returning(m.profile_versions.c.id)
        )
        session.execute(
            m.profiles.update()
            .where(m.profiles.c.id == profile_id)
            .values(
                current_version_id=version_id,
            )
        )
        policy_id = session.scalar(
            m.policies.insert().values(name="Test").returning(m.policies.c.id)
        )
        policy_version = session.scalar(
            m.policy_versions.insert()
            .values(
                policy_id=policy_id,
                version=1,
                allowed_platforms=["boss"],
            )
            .returning(m.policy_versions.c.id)
        )
        session.execute(
            m.policies.update()
            .where(m.policies.c.id == policy_id)
            .values(
                current_version_id=policy_version,
            )
        )
    profile = dict(id=str(profile_id), version_id=str(version_id), skills=skills)
    policy = dict(id=str(policy_id), version_id=str(policy_version), threshold=70, comparison="gte")
    card = Card(title="Python开发", company="Test", city="上海", salary="10K", center=(200, 300))
    detail = SimpleNamespace(detail_text="熟悉Python", detail_complete=True, hard_conditions="pass")
    record = {
        "score": 75,
        "requirements": [{"quote": "熟悉Python", "skill_index": 0}],
        "decision": {"score": 75, "threshold": 70, "eligible": True, "reasons": []},
    }
    image = tmp_path / "test.png"
    image.write_bytes(b"test screenshot")
    first = persist_analysis(
        engine, settings, profile, policy, "visual-id", card, detail, record, image
    )
    second = persist_analysis(
        engine, settings, profile, policy, "visual-id", card, detail, record, image
    )
    assert first == second
    with Session(engine) as session:
        for table in [m.jobs, m.job_snapshots, m.analyses, m.artifacts]:
            assert session.scalar(sa.select(sa.func.count()).select_from(table)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(m.applications)) == 0
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.db_engine = engine
        app.state.db_sessions = make_session_factory(engine)
        assert client.get("/api/v1/jobs").status_code == 401
        client.headers["Authorization"] = "Bearer " + "x" * 40
        jobs = client.get("/api/v1/jobs").json()
        assert len(jobs["items"]) == 1
        job = client.get("/api/v1/jobs/" + first["job_id"]).json()
        assert job["snapshot"]["normalized"]["source_url_kind"] == "search_page"
        assert (
            client.get(
                "/api/v1/artifacts/" + job["snapshot"]["raw_artifact_id"] + "/content"
            ).content
            == image.read_bytes()
        )
        response = client.get("/api/v1/analyses/" + first["analysis_id"]).json()
        assert response["eligible"] is True
        assert response["match_evidence"]["decision"] == record["decision"]
        assert response["prompt_version"] == "desktop-v4"
        analysis_page = client.get("/api/v1/analyses", params={"profile_id": profile_id}).json()
        assert len(analysis_page["items"]) == 1
        assert analysis_page["items"][0]["job_title"] == card.title
        assert analysis_page["items"][0]["profile_name"] == "Test"
        assert client.get("/api/v1/analyses", params={"status": "invalid"}).status_code == 422
        review_record = {
            **record,
            "decision": {
                "score": 75,
                "eligible": False,
                "needs_review": True,
                "reasons": ["category_conflict"],
            },
        }
        review = persist_analysis(
            engine,
            settings,
            profile,
            policy,
            "needs-review-job",
            card,
            detail,
            review_record,
            image,
        )
        review_response = client.get("/api/v1/analyses/" + review["analysis_id"]).json()
        assert review_response["status"] == "needs_review"
        assert review_response["eligible"] is False
        assert "category_conflict" in review_response["uncertainties"]
        with Session(engine) as session, session.begin():
            session.execute(
                m.analyses.update()
                .where(m.analyses.c.id == UUID(first["analysis_id"]))
                .values(prompt_version="desktop-v3")
            )
        old = client.get("/api/v1/analyses/" + first["analysis_id"]).json()
        assert old["is_current"] is False
        assert old["eligible"] is False
        with Session(engine) as session, session.begin():
            version = session.scalar(
                m.profile_versions.insert()
                .values(
                    profile_id=profile_id,
                    version=2,
                    skills=skills,
                )
                .returning(m.profile_versions.c.id)
            )
            session.execute(
                m.profiles.update()
                .where(m.profiles.c.id == profile_id)
                .values(
                    current_version_id=version,
                )
            )
        response = client.get("/api/v1/analyses/" + first["analysis_id"]).json()
        assert response["is_current"] is False
        assert response["eligible"] is False

    detail.detail_text = "熟悉Python，另有待确认要求"
    detail.detail_complete = False
    record["score"] = None
    latest = persist_analysis(
        engine, settings, profile, policy, "visual-id", card, detail, record, image
    )
    assert latest["job_id"] == first["job_id"]
    assert latest["snapshot_id"] != first["snapshot_id"]
    with Session(engine) as session:
        row = (
            session.execute(
                sa.select(m.analyses).where(m.analyses.c.id == UUID(latest["analysis_id"]))
            )
            .mappings()
            .one()
        )
        assert row["status"] == "needs_review"
        assert row["eligible"] is False
