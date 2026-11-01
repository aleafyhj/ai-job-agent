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
from app.matching import Criterion, Evaluation
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


@pytest.mark.postgres
def test_reanalyze_saved_job_and_append_human_review(database, tmp_path, monkeypatch):
    engine, _ = database
    settings = Settings(
        _env_file=None,
        api_token="x" * 40,
        artifacts_dir=tmp_path,
        vision_api_key="secret",
        vision_base_url="https://example.invalid/v1",
        vision_model="qwen-test",
    )
    skills = [
        {
            "canonical_name": "Python",
            "level": "proficient",
            "evidence": "使用Python开发后端服务",
            "years": "2.00",
        }
    ]
    with Session(engine) as session, session.begin():
        profile_id = session.scalar(
            m.profiles.insert().values(name="真实档案").returning(m.profiles.c.id)
        )
        version_id = session.scalar(
            m.profile_versions.insert()
            .values(profile_id=profile_id, version=1, skills=skills)
            .returning(m.profile_versions.c.id)
        )
        session.execute(
            m.profiles.update()
            .where(m.profiles.c.id == profile_id)
            .values(current_version_id=version_id)
        )
        policy_id = session.scalar(
            m.policies.insert().values(name="70分策略").returning(m.policies.c.id)
        )
        policy_version_id = session.scalar(
            m.policy_versions.insert()
            .values(
                policy_id=policy_id,
                version=1,
                threshold=70,
                comparison="gte",
                allowed_platforms=["boss"],
            )
            .returning(m.policy_versions.c.id)
        )
        session.execute(
            m.policies.update()
            .where(m.policies.c.id == policy_id)
            .values(current_version_id=policy_version_id)
        )
        artifact_id = session.scalar(
            m.artifacts.insert()
            .values(
                kind="job_screenshot",
                storage_key="job.png",
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
                identity_key="desktop:reanalyze",
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
                raw_artifact_id=artifact_id,
                normalized={"detail_text": "熟悉Python", "detail_complete": True},
                extractor_version="desktop-v2",
            )
            .returning(m.job_snapshots.c.id)
        )
        session.execute(
            m.jobs.update().where(m.jobs.c.id == job_id).values(latest_snapshot_id=snapshot_id)
        )

    monkeypatch.setattr(
        "app.api.jobs.Vision.evaluate_text",
        lambda self, text, profile: Evaluation(
            uncertain=False,
            multiple_roles=False,
            requirements=[
                Criterion(
                    quote="熟悉Python",
                    category="technical",
                    importance="required",
                    status="matched",
                    skill_indices=[0],
                )
            ],
        ),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.db_engine = engine
        app.state.db_sessions = make_session_factory(engine)
        client.headers["Authorization"] = "Bearer " + "x" * 40
        payload = {"profile_id": str(profile_id), "policy_id": str(policy_id)}
        first = client.post(f"/api/v1/jobs/{job_id}/analyses", json=payload)
        assert first.status_code == 201
        analysis = first.json()
        assert analysis["score"] == "100.000000"
        assert analysis["eligible"] is True
        assert analysis["latest_review"] is None
        assert client.post(f"/api/v1/jobs/{job_id}/analyses", json=payload).status_code == 200

        invalid_review = client.post(
            f"/api/v1/analyses/{analysis['id']}/reviews",
            json={
                "decision": "approved",
                "requirement_reviews": [
                    {"quote": "不存在的要求", "decision": "confirmed", "notes": ""}
                ],
            },
        )
        assert invalid_review.status_code == 422

        review = client.post(
            f"/api/v1/analyses/{analysis['id']}/reviews",
            json={
                "decision": "approved",
                "notes": "证据与原文一致",
                "requirement_reviews": [
                    {
                        "quote": "熟悉Python",
                        "decision": "confirmed",
                        "notes": "技能证据充分",
                    }
                ],
            },
        )
        assert review.status_code == 201
        refreshed = client.get(f"/api/v1/analyses/{analysis['id']}").json()
        assert refreshed["latest_review"]["decision"] == "approved"
        assert refreshed["latest_review"]["requirement_reviews"][0]["decision"] == "confirmed"
    with Session(engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(m.analyses)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(m.analysis_reviews)) == 1
