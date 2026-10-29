"""Database-only atomic operations. Call inside ``with session.begin():``.

These are building blocks, not a worker or an automatic application service.
"""

from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import models as m


class ApplicationBlocked(ValueError):
    pass


def enqueue_application(
    session: Session, *, analysis_id: UUID, resume_id: UUID, platform_account_id: UUID
) -> UUID:
    """Validate frozen evidence, reserve quota and enqueue once in one transaction.

    Caller owns commit/rollback. Never perform a browser operation in this transaction.
    """
    analysis = (
        session.execute(sa.select(m.analyses).where(m.analyses.c.id == analysis_id))
        .mappings()
        .one()
    )
    version = (
        session.execute(
            sa.select(m.profile_versions).where(
                m.profile_versions.c.id == analysis["profile_version_id"]
            )
        )
        .mappings()
        .one()
    )
    # All enqueue operations for this candidate serialize, including different accounts.
    profile = (
        session.execute(
            sa.select(m.profiles).where(m.profiles.c.id == version["profile_id"]).with_for_update()
        )
        .mappings()
        .one()
    )
    snapshot = (
        session.execute(
            sa.select(m.job_snapshots).where(m.job_snapshots.c.id == analysis["job_snapshot_id"])
        )
        .mappings()
        .one()
    )
    job = (
        session.execute(
            sa.select(m.jobs).where(m.jobs.c.id == snapshot["job_id"]).with_for_update()
        )
        .mappings()
        .one()
    )
    existing = session.scalar(
        sa.select(m.applications.c.id).where(
            m.applications.c.profile_id == profile["id"], m.applications.c.job_id == job["id"]
        )
    )
    if existing is not None:
        return existing
    policy = (
        session.execute(
            sa.select(m.policy_versions).where(
                m.policy_versions.c.id == analysis["policy_version_id"]
            )
        )
        .mappings()
        .one()
    )
    policy_root = (
        session.execute(
            sa.select(m.policies).where(m.policies.c.id == policy["policy_id"]).with_for_update()
        )
        .mappings()
        .one()
    )
    account = (
        session.execute(
            sa.select(m.platform_accounts)
            .where(m.platform_accounts.c.id == platform_account_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )
    resume = (
        session.execute(sa.select(m.resumes).where(m.resumes.c.id == resume_id)).mappings().one()
    )
    score = analysis["score"]
    passes = score is not None and (
        score >= policy["threshold"]
        if policy["comparison"] == "gte"
        else score > policy["threshold"]
    )
    if not (
        analysis["status"] == "ready"
        and analysis["eligible"]
        and analysis["hard_status"] == "pass"
        and passes
    ):
        raise ApplicationBlocked("analysis_not_eligible")
    if (
        profile["current_version_id"] != version["id"]
        or job["latest_snapshot_id"] != snapshot["id"]
        or policy_root["current_version_id"] != policy["id"]
    ):
        raise ApplicationBlocked("stale_analysis")
    if resume["profile_id"] != profile["id"]:
        raise ApplicationBlocked("resume_owner_mismatch")
    if not (
        account["enabled"]
        and account["session_status"] == "valid"
        and account["platform_key"] == job["platform_key"]
        and job["status"] == "active"
    ):
        raise ApplicationBlocked("platform_unavailable")
    if not policy["auto_apply"]:
        raise ApplicationBlocked("auto_apply_disabled")
    if (
        job["platform_key"] not in policy["allowed_platforms"]
        or job["company_name"] in policy["blocked_companies"]
    ):
        raise ApplicationBlocked("policy_blocked")

    now = session.scalar(sa.select(sa.func.clock_timestamp()))
    zone = ZoneInfo(policy["timezone"])
    local_date = now.astimezone(zone).date()
    start = datetime.combine(local_date, time.min, zone)
    end = datetime.combine(local_date + timedelta(days=1), time.min, zone)
    # Count across all accounts, using real instants rather than timezone-string groups.
    used = session.scalar(
        sa.select(sa.func.count())
        .select_from(
            m.quota_reservations.join(
                m.applications, m.quota_reservations.c.application_id == m.applications.c.id
            )
        )
        .where(
            m.applications.c.profile_id == profile["id"],
            m.quota_reservations.c.state.in_(["reserved", "consumed"]),
            m.quota_reservations.c.created_at >= start,
            m.quota_reservations.c.created_at < end,
        )
    )
    if used >= policy["daily_limit"]:
        raise ApplicationBlocked("daily_limit_reached")
    application_id = session.scalar(
        m.applications.insert()
        .values(
            profile_id=profile["id"],
            job_id=job["id"],
            platform_account_id=platform_account_id,
            analysis_id=analysis_id,
            resume_id=resume_id,
        )
        .returning(m.applications.c.id)
    )
    quota_id = session.scalar(
        insert(m.daily_quotas)
        .values(
            profile_id=profile["id"],
            platform_account_id=platform_account_id,
            local_date=local_date,
            policy_timezone=policy["timezone"],
            reserved=1,
        )
        .on_conflict_do_update(
            constraint="uq_quota_day",
            set_={"reserved": m.daily_quotas.c.reserved + 1, "updated_at": sa.func.now()},
        )
        .returning(m.daily_quotas.c.id)
    )
    session.execute(
        m.quota_reservations.insert().values(
            application_id=application_id,
            quota_id=quota_id,
            created_at=now,
        )
    )
    session.execute(
        m.tasks.insert().values(
            type="submit_application",
            application_id=application_id,
            payload={"application_id": str(application_id)},
            max_attempts=1,
        )
    )
    return application_id


def claim_task(session: Session, owner: str, *, lease_seconds: int = 60):
    """Claim only queued tasks; expired submitting work is deliberately not reclaimed."""
    if not owner or lease_seconds <= 0:
        raise ValueError("owner and positive lease_seconds are required")
    now = sa.func.clock_timestamp()
    task_id = session.scalar(
        sa.select(m.tasks.c.id)
        .where(
            m.tasks.c.status.in_(["queued", "retry_wait"]),
            m.tasks.c.available_at <= now,
            m.tasks.c.attempt_count < m.tasks.c.max_attempts,
        )
        .order_by(m.tasks.c.priority.desc(), m.tasks.c.available_at, m.tasks.c.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if task_id is None:
        return None
    return (
        session.execute(
            m.tasks.update()
            .where(m.tasks.c.id == task_id)
            .values(
                status="running",
                lease_owner=owner,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                attempt_count=m.tasks.c.attempt_count + 1,
            )
            .returning(m.tasks)
        )
        .mappings()
        .one()
    )


def heartbeat_task(
    session: Session, task_id: UUID, owner: str, attempt: int, *, lease_seconds: int = 60
) -> bool:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    now = sa.func.clock_timestamp()
    result = session.execute(
        m.tasks.update()
        .where(
            m.tasks.c.id == task_id,
            m.tasks.c.status == "running",
            m.tasks.c.lease_owner == owner,
            m.tasks.c.attempt_count == attempt,
            m.tasks.c.lease_expires_at > now,
        )
        .values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=lease_seconds))
    )
    return result.rowcount == 1
