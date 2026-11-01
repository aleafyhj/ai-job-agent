"""Persist one safe, non-retriable BOSS immediate-communication attempt."""

import hashlib
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app import models as m
from app.vision import ANALYSIS_VERSION


class CommunicationBlocked(ValueError):
    pass


def _store_screenshot(session, settings, image: bytes, label: str):
    root = settings.artifacts_dir.resolve()
    folder = root / "boss-desktop" / "communications"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid4().hex}-{label}.png"
    path.write_bytes(image)
    storage_key = path.relative_to(root).as_posix()
    artifact_id = session.scalar(
        m.artifacts.insert()
        .values(
            kind="communication_screenshot",
            storage_key=storage_key,
            mime_type="image/png",
            sha256=hashlib.sha256(image).hexdigest(),
            size=len(image),
            metadata={"stage": label, "platform": "boss"},
        )
        .returning(m.artifacts.c.id)
    )
    return artifact_id, path


def start_communication(engine, settings, analysis_id: str | UUID, before_image: bytes, coordinate):
    """Reserve one communication before the irreversible mouse click."""
    written: Path | None = None
    try:
        with Session(engine) as session, session.begin():
            analysis = (
                session.execute(
                    sa.select(m.analyses)
                    .where(m.analyses.c.id == UUID(str(analysis_id)))
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            snapshot = (
                session.execute(
                    sa.select(m.job_snapshots).where(
                        m.job_snapshots.c.id == analysis["job_snapshot_id"]
                    )
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
            profile_version = (
                session.execute(
                    sa.select(m.profile_versions).where(
                        m.profile_versions.c.id == analysis["profile_version_id"]
                    )
                )
                .mappings()
                .one()
            )
            profile = (
                session.execute(
                    sa.select(m.profiles)
                    .where(m.profiles.c.id == profile_version["profile_id"])
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            policy_version = (
                session.execute(
                    sa.select(m.policy_versions).where(
                        m.policy_versions.c.id == analysis["policy_version_id"]
                    )
                )
                .mappings()
                .one()
            )
            policy = (
                session.execute(
                    sa.select(m.policies).where(m.policies.c.id == policy_version["policy_id"])
                )
                .mappings()
                .one()
            )
            existing = session.scalar(
                sa.select(m.communications.c.id).where(
                    sa.or_(
                        m.communications.c.analysis_id == analysis["id"],
                        sa.and_(
                            m.communications.c.profile_id == profile["id"],
                            m.communications.c.job_id == job["id"],
                        ),
                    )
                )
            )
            if existing is not None:
                raise CommunicationBlocked("该档案与职位已有沟通记录，禁止重复点击")
            current = (
                analysis["prompt_version"] == ANALYSIS_VERSION
                and profile["current_version_id"] == profile_version["id"]
                and policy["current_version_id"] == policy_version["id"]
                and job["latest_snapshot_id"] == snapshot["id"]
            )
            if not (
                current
                and analysis["status"] == "ready"
                and analysis["eligible"]
                and analysis["hard_status"] == "pass"
            ):
                raise CommunicationBlocked("分析已失效、需复核或未达到沟通条件")
            if profile_version["facts"].get("data_kind") == "simulation":
                raise CommunicationBlocked("模拟档案禁止沟通")
            if not (
                policy_version["auto_apply"]
                and "boss" in policy_version["allowed_platforms"]
                and job["status"] == "active"
                and job["company_name"] not in policy_version["blocked_companies"]
            ):
                raise CommunicationBlocked("策略或职位状态不允许沟通")
            timezone = policy_version["timezone"]
            local_date = sa.func.timezone(timezone, sa.func.clock_timestamp()).cast(sa.Date)
            used = session.scalar(
                sa.select(sa.func.count())
                .select_from(m.communications)
                .where(
                    m.communications.c.profile_id == profile["id"],
                    m.communications.c.local_date == local_date,
                    m.communications.c.policy_timezone == timezone,
                )
            )
            if used >= policy_version["daily_limit"]:
                raise CommunicationBlocked("本次沟通次数达到策略限额")
            # Validate the configured IANA timezone before persisting it.
            ZoneInfo(timezone)
            artifact_id, written = _store_screenshot(session, settings, before_image, "before")
            row = (
                session.execute(
                    m.communications.insert()
                    .values(
                        profile_id=profile["id"],
                        job_id=job["id"],
                        analysis_id=analysis["id"],
                        status="pending",
                        local_date=local_date,
                        policy_timezone=timezone,
                        before_artifact_id=artifact_id,
                        button_coordinate={"x": int(coordinate[0]), "y": int(coordinate[1])},
                        result={"input": "not_yet_executed"},
                    )
                    .returning(m.communications)
                )
                .mappings()
                .one()
            )
            return dict(row)
    except Exception:
        if written is not None:
            written.unlink(missing_ok=True)
        raise


def finish_communication(
    engine,
    settings,
    communication_id: str | UUID,
    status: str,
    after_image: bytes,
    result: dict,
):
    """Finalize a pending click exactly once; unknown outcomes remain non-retriable."""
    if status not in {"confirmed", "unknown"}:
        raise ValueError("无效沟通结果")
    written: Path | None = None
    try:
        with Session(engine) as session, session.begin():
            row = (
                session.execute(
                    sa.select(m.communications)
                    .where(m.communications.c.id == UUID(str(communication_id)))
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if row["status"] != "pending":
                if row["status"] == status:
                    return dict(row)
                raise CommunicationBlocked("沟通结果已经确定，禁止覆盖")
            artifact_id, written = _store_screenshot(session, settings, after_image, "after")
            values = {
                "status": status,
                "after_artifact_id": artifact_id,
                "result": result,
                "confirmed_at": sa.func.now() if status == "confirmed" else None,
                "updated_at": sa.func.now(),
            }
            updated = (
                session.execute(
                    m.communications.update()
                    .where(m.communications.c.id == row["id"])
                    .values(**values)
                    .returning(m.communications)
                )
                .mappings()
                .one()
            )
            return dict(updated)
    except Exception:
        if written is not None:
            written.unlink(missing_ok=True)
        raise
