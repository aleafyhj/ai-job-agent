"""Read, re-evaluate and review jobs collected by the desktop runner."""

import hashlib
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.dialects.postgresql import insert

from app import models as m
from app.api.dependencies import DB, require_token
from app.api.resources import Cursor, Limit, get_row, page_query, policy_read, profile_read
from app.matching import assess
from app.schemas import AnalysisReviewWrite, AnalysisRunWrite
from app.vision import ANALYSIS_VERSION, Vision

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


def analysis_read(session, row):
    result = dict(row)
    result["score"] = str(row["score"]) if row["score"] is not None else None
    snapshot = get_row(session, m.job_snapshots, row["job_snapshot_id"])
    job = get_row(session, m.jobs, snapshot["job_id"])
    version = get_row(session, m.profile_versions, row["profile_version_id"])
    profile = get_row(session, m.profiles, version["profile_id"])
    policy_version = get_row(session, m.policy_versions, row["policy_version_id"])
    policy = get_row(session, m.policies, policy_version["policy_id"])
    result["is_current"] = (
        row["prompt_version"] == ANALYSIS_VERSION
        and profile["current_version_id"] == version["id"]
        and policy["current_version_id"] == policy_version["id"]
        and job["latest_snapshot_id"] == snapshot["id"]
    )
    result["eligible"] = row["eligible"] and result["is_current"]
    result["job_id"] = job["id"]
    result["job_title"] = job["title"]
    result["company_name"] = job["company_name"]
    result["profile_id"] = profile["id"]
    result["profile_name"] = profile["name"]
    result["policy_id"] = policy["id"]
    result["policy_name"] = policy["name"]
    review = (
        session.execute(
            sa.select(m.analysis_reviews)
            .where(m.analysis_reviews.c.analysis_id == row["id"])
            .order_by(m.analysis_reviews.c.created_at.desc(), m.analysis_reviews.c.id.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    result["latest_review"] = dict(review) if review is not None else None
    return result


@router.get("/analyses")
def list_analyses(
    session: DB,
    cursor: Cursor = None,
    limit: Limit = 20,
    profile_id: UUID | None = None,
    status: str | None = None,
):
    statement = sa.select(m.analyses)
    if profile_id is not None:
        statement = statement.join(
            m.profile_versions,
            m.profile_versions.c.id == m.analyses.c.profile_version_id,
        ).where(m.profile_versions.c.profile_id == profile_id)
    if status is not None:
        if status not in {"queued", "running", "ready", "needs_review", "failed"}:
            raise HTTPException(422, "无效分析状态")
        statement = statement.where(m.analyses.c.status == status)
    scope = f"analyses:{profile_id}:{status}"
    rows, next_cursor = page_query(session, m.analyses, statement, cursor, limit, scope)
    return {"items": [analysis_read(session, row) for row in rows], "next_cursor": next_cursor}


@router.get("/analyses/{analysis_id}")
def read_analysis(analysis_id: UUID, session: DB):
    return analysis_read(session, get_row(session, m.analyses, analysis_id))


def _current_analysis(session, snapshot_id, profile_version_id, policy_version_id, model):
    return (
        session.execute(
            sa.select(m.analyses).where(
                m.analyses.c.job_snapshot_id == snapshot_id,
                m.analyses.c.profile_version_id == profile_version_id,
                m.analyses.c.policy_version_id == policy_version_id,
                m.analyses.c.prompt_version == ANALYSIS_VERSION,
                m.analyses.c.model == model,
            )
        )
        .mappings()
        .one_or_none()
    )


@router.post("/jobs/{job_id}/analyses", status_code=201)
def analyze_saved_job(
    job_id: UUID,
    body: AnalysisRunWrite,
    session: DB,
    request: Request,
    response: Response,
):
    """Evaluate one saved snapshot with the current profile and policy versions."""
    settings = request.app.state.settings
    if settings.vision_api_key is None:
        raise HTTPException(503, "请先配置千问 API Key")
    with session.begin():
        job = get_row(session, m.jobs, job_id)
        if job["latest_snapshot_id"] is None:
            raise HTTPException(409, "职位没有可分析的采集快照")
        snapshot = get_row(session, m.job_snapshots, job["latest_snapshot_id"])
        profile = profile_read(session, get_row(session, m.profiles, body.profile_id))
        policy = policy_read(session, get_row(session, m.policies, body.policy_id))
        existing = _current_analysis(
            session,
            snapshot["id"],
            profile.version_id,
            policy.version_id,
            settings.vision_model,
        )
    if existing is not None:
        response.status_code = 200
        return analysis_read(session, existing)

    normalized = snapshot["normalized"] or {}
    text = str(normalized.get("detail_text") or "").strip()
    if not text:
        raise HTTPException(409, "职位快照没有可分析的正文")
    evidence = profile.model_dump(
        mode="json",
        include={
            "skills",
            "education",
            "experience_years",
            "preferred_cities",
            "remote_preference",
        },
    )
    try:
        evaluation = Vision(settings).evaluate_text(text, evidence)
    except RuntimeError:
        raise HTTPException(502, "职位 AI 分析失败，请稍后重试") from None
    profile_data = profile.model_dump(mode="json")
    policy_data = policy.model_dump(mode="json")
    decision = assess(evaluation, text, profile_data, policy_data)
    detail_complete = bool(normalized.get("detail_complete"))
    reasons = list(decision["reasons"])
    if not detail_complete:
        reasons.append("incomplete_job_detail")
    reasons = list(dict.fromkeys(reasons))
    needs_review = bool(reasons)
    score = decision["score"]
    eligible = bool(
        detail_complete
        and not needs_review
        and decision["eligible"]
        and profile.facts.get("data_kind") != "simulation"
    )
    content_hash = snapshot["content_hash"]
    input_hash = hashlib.sha256(
        (
            content_hash + str(profile.version_id) + str(policy.version_id) + ANALYSIS_VERSION
        ).encode()
    ).hexdigest()
    values = {
        "job_snapshot_id": snapshot["id"],
        "profile_version_id": profile.version_id,
        "policy_version_id": policy.version_id,
        "status": "needs_review" if needs_review else "ready",
        "score": score,
        "hard_status": decision["hard_conditions"],
        "eligible": eligible,
        "requirements": [item.model_dump(mode="json") for item in evaluation.requirements],
        "match_evidence": {
            "skills": profile_data["skills"],
            "decision": {**decision, "reasons": reasons, "eligible": eligible},
            "profile_data_kind": profile.facts.get("data_kind", "unspecified"),
        },
        "uncertainties": reasons,
        "prompt_version": ANALYSIS_VERSION,
        "model": settings.vision_model,
        "input_hash": input_hash,
    }
    with session.begin():
        analysis_id = session.scalar(
            insert(m.analyses)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_analysis_versions")
            .returning(m.analyses.c.id)
        )
        if analysis_id is None:
            row = _current_analysis(
                session,
                snapshot["id"],
                profile.version_id,
                policy.version_id,
                settings.vision_model,
            )
            response.status_code = 200
        else:
            row = get_row(session, m.analyses, analysis_id)
    return analysis_read(session, row)


@router.post("/analyses/{analysis_id}/reviews", status_code=201)
def review_analysis(analysis_id: UUID, body: AnalysisReviewWrite, session: DB):
    """Append a human conclusion without changing the immutable model output."""
    with session.begin():
        analysis = get_row(session, m.analyses, analysis_id)
        requirements = (
            (analysis["match_evidence"] or {}).get("decision", {}).get("requirements", [])
        )
        valid_quotes = {item.get("quote") for item in requirements if item.get("quote")}
        submitted_quotes = [item.quote for item in body.requirement_reviews]
        if len(submitted_quotes) != len(set(submitted_quotes)) or not set(
            submitted_quotes
        ).issubset(valid_quotes):
            raise HTTPException(422, "逐项复核必须对应本次分析中的唯一职位要求")
        row = (
            session.execute(
                m.analysis_reviews.insert()
                .values(
                    analysis_id=analysis_id,
                    decision=body.decision,
                    reviewer="local_user",
                    notes=body.notes or None,
                    requirement_reviews=[
                        item.model_dump(mode="json") for item in body.requirement_reviews
                    ],
                )
                .returning(m.analysis_reviews)
            )
            .mappings()
            .one()
        )
    return dict(row)
