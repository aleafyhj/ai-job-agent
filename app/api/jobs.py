"""Read the jobs and evidence collected by the desktop runner."""

from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException

from app import models as m
from app.api.dependencies import DB, require_token
from app.api.resources import Cursor, Limit, get_row, page_query

router = APIRouter(dependencies=[Depends(require_token)], tags=["jobs"])


def job_read(session, row):
    result = dict(row)
    result["snapshot"] = None
    if row["latest_snapshot_id"]:
        snapshot = get_row(session, m.job_snapshots, row["latest_snapshot_id"])
        result["snapshot"] = dict(snapshot)
    return result


@router.get("/jobs")
def list_jobs(
    session: DB,
    cursor: Cursor = None,
    limit: Limit = 20,
    platform_key: str = "boss",
    status: str | None = None,
):
    statement = sa.select(m.jobs).where(m.jobs.c.platform_key == platform_key)
    if status is not None:
        if status not in {"active", "closed", "unavailable"}:
            raise HTTPException(422, "无效职位状态")
        statement = statement.where(m.jobs.c.status == status)
    rows, next_cursor = page_query(
        session, m.jobs, statement, cursor, limit, f"jobs:{platform_key}:{status}"
    )
    return {"items": [job_read(session, row) for row in rows], "next_cursor": next_cursor}


@router.get("/jobs/{job_id}")
def read_job(job_id: UUID, session: DB):
    return job_read(session, get_row(session, m.jobs, job_id))


@router.get("/analyses/{analysis_id}")
def read_analysis(analysis_id: UUID, session: DB):
    row = get_row(session, m.analyses, analysis_id)
    result = dict(row)
    result["score"] = str(row["score"]) if row["score"] is not None else None
    snapshot = get_row(session, m.job_snapshots, row["job_snapshot_id"])
    job = get_row(session, m.jobs, snapshot["job_id"])
    version = get_row(session, m.profile_versions, row["profile_version_id"])
    profile = get_row(session, m.profiles, version["profile_id"])
    policy_version = get_row(session, m.policy_versions, row["policy_version_id"])
    policy = get_row(session, m.policies, policy_version["policy_id"])
    result["is_current"] = (
        profile["current_version_id"] == version["id"]
        and policy["current_version_id"] == policy_version["id"]
        and job["latest_snapshot_id"] == snapshot["id"]
    )
    result["eligible"] = row["eligible"] and result["is_current"]
    result["job_id"] = job["id"]
    return result
