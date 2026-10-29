"""Real PostgreSQL integration tests; each test owns a disposable UUID-named schema."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models as m
from app.core.config import Settings
from app.main import create_app
from app.transactions import ApplicationBlocked, claim_task, enqueue_application, heartbeat_task

pytestmark = pytest.mark.postgres


def add(session, table, **values):
    return session.scalar(table.insert().values(**values).returning(table.c.id))


def seed(engine, *, daily_limit=20):
    with Session(engine) as session, session.begin():
        profile = add(session, m.profiles, name="Test candidate")
        version = add(session, m.profile_versions, profile_id=profile, version=1)
        session.execute(
            m.profiles.update()
            .where(m.profiles.c.id == profile)
            .values(
                current_version_id=version,
            )
        )
        policy = add(session, m.policies, name="Test policy")
        policy_version = add(
            session,
            m.policy_versions,
            policy_id=policy,
            version=1,
            auto_apply=True,
            allowed_platforms=["fixture"],
            daily_limit=daily_limit,
        )
        session.execute(
            m.policies.update()
            .where(m.policies.c.id == policy)
            .values(
                current_version_id=policy_version,
            )
        )
        artifact = add(
            session,
            m.artifacts,
            kind="resume",
            storage_key=str(uuid4()),
            mime_type="application/pdf",
            sha256="a" * 64,
            size=10,
        )
        resume = add(
            session,
            m.resumes,
            profile_id=profile,
            version=1,
            artifact_id=artifact,
            sha256="a" * 64,
            original_filename="test.pdf",
            mime_type="application/pdf",
            size=10,
        )
        account = add(
            session,
            m.platform_accounts,
            platform_key="fixture",
            label="Test",
            session_status="valid",
        )
        job, analysis = add_job(session, version, policy_version, artifact)
    return dict(
        profile=profile,
        version=version,
        policy=policy,
        policy_version=policy_version,
        artifact=artifact,
        resume=resume,
        account=account,
        job=job,
        analysis=analysis,
    )


def add_job(session, version, policy_version, artifact):
    job = add(
        session,
        m.jobs,
        platform_key="fixture",
        canonical_url="https://example.invalid/job",
        identity_key=str(uuid4()),
        company_name="Test company",
        title="Python engineer",
    )
    snapshot = add(
        session,
        m.job_snapshots,
        job_id=job,
        content_hash="b" * 64,
        raw_artifact_id=artifact,
        extractor_version="v1",
    )
    session.execute(m.jobs.update().where(m.jobs.c.id == job).values(latest_snapshot_id=snapshot))
    analysis = add(
        session,
        m.analyses,
        job_snapshot_id=snapshot,
        profile_version_id=version,
        policy_version_id=policy_version,
        status="ready",
        score=75,
        hard_status="pass",
        eligible=True,
        prompt_version="v1",
        model="fixture",
        input_hash="c" * 64,
    )
    return job, analysis


def enqueue(session, ids):
    return enqueue_application(
        session,
        analysis_id=ids["analysis"],
        resume_id=ids["resume"],
        platform_account_id=ids["account"],
    )


def count(session, table):
    return session.scalar(sa.select(sa.func.count()).select_from(table))


def test_migration_roundtrip_and_metadata(database):
    engine, config = database
    with engine.begin() as connection:
        assert compare_metadata(MigrationContext.configure(connection), m.metadata) == []
        assert len(sa.inspect(connection).get_table_names()) == 23  # 22 + alembic_version
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert sa.inspect(connection).get_table_names() == ["alembic_version"]
        command.upgrade(config, "head")
        assert compare_metadata(MigrationContext.configure(connection), m.metadata) == []


def test_version_ownership_and_uniqueness(database):
    engine, _ = database
    ids = seed(engine)
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        add(session, m.profile_versions, profile_id=ids["profile"], version=1)
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        other = add(session, m.profiles, name="Other")
        session.execute(
            m.profiles.update()
            .where(m.profiles.c.id == other)
            .values(
                current_version_id=ids["version"],
            )
        )


@pytest.mark.parametrize(
    "table_name", ["profile_versions", "policy_versions", "resumes", "job_snapshots"]
)
def test_versions_are_immutable(database, table_name):
    engine, _ = database
    seed(engine)
    table = m.metadata.tables[table_name]
    for operation in [table.update().values(updated_at=sa.func.now()), table.delete()]:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(operation)


def test_application_deduplication_and_atomic_rollback(database):
    engine, _ = database
    ids = seed(engine)
    with pytest.raises(RuntimeError), Session(engine) as session, session.begin():
        enqueue(session, ids)
        raise RuntimeError("simulate transaction failure")
    with Session(engine) as session:
        for table in [m.applications, m.quota_reservations, m.daily_quotas, m.tasks]:
            assert count(session, table) == 0

    def run():
        with Session(engine) as session, session.begin():
            return enqueue(session, ids)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results[0] == results[1]
    with Session(engine) as session:
        for table in [m.applications, m.quota_reservations, m.tasks]:
            assert count(session, table) == 1
        assert session.scalar(sa.select(m.daily_quotas.c.reserved)) == 1


def test_daily_limit_across_accounts_under_concurrency(database):
    engine, _ = database
    first = seed(engine, daily_limit=1)
    with Session(engine) as session, session.begin():
        job, analysis = add_job(
            session, first["version"], first["policy_version"], first["artifact"]
        )
        account = add(
            session,
            m.platform_accounts,
            platform_key="fixture",
            label="Second account",
            session_status="valid",
        )
    second = dict(first, job=job, analysis=analysis, account=account)

    def run(ids):
        try:
            with Session(engine) as session, session.begin():
                enqueue(session, ids)
            return "queued"
        except ApplicationBlocked as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, [first, second])) == ["daily_limit_reached", "queued"]
    with Session(engine) as session:
        assert count(session, m.applications) == 1
        assert count(session, m.tasks) == 1


def test_stale_analysis_is_not_enqueued(database):
    engine, _ = database
    ids = seed(engine)
    with Session(engine) as session, session.begin():
        version = add(session, m.profile_versions, profile_id=ids["profile"], version=2)
        session.execute(
            m.profiles.update()
            .where(m.profiles.c.id == ids["profile"])
            .values(
                current_version_id=version,
            )
        )
    with pytest.raises(ApplicationBlocked, match="stale_analysis"):
        with Session(engine) as session, session.begin():
            enqueue(session, ids)
    with Session(engine) as session:
        assert count(session, m.applications) == 0


def test_database_rejects_mismatched_application_links(database):
    engine, _ = database
    ids = seed(engine)
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        wrong_job, _ = add_job(session, ids["version"], ids["policy_version"], ids["artifact"])
        add(
            session,
            m.applications,
            profile_id=ids["profile"],
            job_id=wrong_job,
            analysis_id=ids["analysis"],
            resume_id=ids["resume"],
            platform_account_id=ids["account"],
        )


def test_task_claim_skips_locked_and_rejects_stale_heartbeat(database):
    engine, _ = database
    with Session(engine) as session, session.begin():
        for _ in range(2):
            add(session, m.tasks, type="read_job")
    with Session(engine) as first, first.begin():
        claimed = claim_task(first, "worker-1")
        with Session(engine) as second, second.begin():
            other = claim_task(second, "worker-2")
            assert other["id"] != claimed["id"]
    with Session(engine) as session, session.begin():
        assert heartbeat_task(session, claimed["id"], "worker-1", 1)
        assert not heartbeat_task(session, claimed["id"], "wrong-owner", 1)
        assert not heartbeat_task(session, claimed["id"], "worker-1", 2)
        session.execute(
            m.tasks.update()
            .where(m.tasks.c.id == claimed["id"])
            .values(
                lease_expires_at=sa.func.now() - timedelta(seconds=1),
            )
        )
        assert not heartbeat_task(session, claimed["id"], "worker-1", 1)
        assert claim_task(session, "worker-3") is None


def test_readiness_with_and_without_schema(database):
    engine, config = database
    app = create_app(Settings(_env_file=None, database_url=None))
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/ready").status_code == 503
        app.state.db_engine = engine
        assert client.get("/api/v1/ready").json() == {
            "status": "ok",
            "database": "ok",
            "storage": "ok",
        }
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "base")
        assert client.get("/api/v1/ready").status_code == 503
