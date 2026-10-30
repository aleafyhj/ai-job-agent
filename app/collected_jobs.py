"""Persist visual observations without pretending that a search URL is a job URL."""

import hashlib
import json
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import models as m


def persist_analysis(engine, settings, profile, policy, key, card, detail, record, image_path):
    normalized = {
        "source_kind": "desktop_screenshot",
        "identity_strength": "visual_composite",
        "city": card.city,
        "salary_text": card.salary,
        "detail_text": detail.detail_text,
        "detail_complete": detail.detail_complete,
        "source_url_kind": "search_page",
    }
    content_hash = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    with Session(engine) as session, session.begin():
        job_id = session.scalar(
            insert(m.jobs)
            .values(
                platform_key="boss",
                identity_key="desktop:" + key,
                canonical_url="https://www.zhipin.com/web/geek/jobs",
                company_name=card.company,
                title=card.title,
            )
            .on_conflict_do_update(
                constraint="uq_jobs_platform_key_identity_key",
                set_={"last_seen_at": sa.func.now(), "updated_at": sa.func.now()},
            )
            .returning(m.jobs.c.id)
        )
        # The upsert locks this logical job until commit, serializing snapshot creation.
        snapshot_id = session.scalar(
            sa.select(m.job_snapshots.c.id).where(
                m.job_snapshots.c.job_id == job_id,
                m.job_snapshots.c.content_hash == content_hash,
            )
        )
        if snapshot_id is None:
            image = image_path.read_bytes()
            artifact_id = session.scalar(
                insert(m.artifacts)
                .values(
                    kind="job_screenshot",
                    storage_key=image_path.resolve()
                    .relative_to(settings.artifacts_dir.resolve())
                    .as_posix(),
                    mime_type="image/png",
                    sha256=hashlib.sha256(image).hexdigest(),
                    size=len(image),
                )
                .on_conflict_do_nothing(constraint="uq_artifacts_storage_key")
                .returning(m.artifacts.c.id)
            )
            if artifact_id is None:
                existing = (
                    session.execute(
                        sa.select(m.artifacts).where(
                            m.artifacts.c.storage_key
                            == image_path.resolve()
                            .relative_to(settings.artifacts_dir.resolve())
                            .as_posix()
                        )
                    )
                    .mappings()
                    .one()
                )
                if (
                    existing["kind"] != "job_screenshot"
                    or existing["sha256"] != hashlib.sha256(image).hexdigest()
                ):
                    raise ValueError("截图文件与已有证据记录不一致")
                artifact_id = existing["id"]
            snapshot_id = session.scalar(
                m.job_snapshots.insert()
                .values(
                    job_id=job_id,
                    content_hash=content_hash,
                    raw_artifact_id=artifact_id,
                    normalized=normalized,
                    extractor_version="desktop-v2",
                )
                .returning(m.job_snapshots.c.id)
            )
        session.execute(
            m.jobs.update()
            .where(m.jobs.c.id == job_id)
            .values(
                latest_snapshot_id=snapshot_id,
            )
        )
        complete = record["score"] is not None and detail.detail_complete
        passes = complete and (
            record["score"] >= float(policy["threshold"])
            if policy["comparison"] == "gte"
            else record["score"] > float(policy["threshold"])
        )
        profile_current = session.scalar(
            sa.select(m.profiles.c.current_version_id).where(m.profiles.c.id == UUID(profile["id"]))
        )
        policy_current = session.scalar(
            sa.select(m.policies.c.current_version_id).where(m.policies.c.id == UUID(policy["id"]))
        )
        stale = (
            str(profile_current) != profile["version_id"]
            or str(policy_current) != policy["version_id"]
        )
        needs_review = not complete or detail.hard_conditions == "unknown" or stale
        values = dict(
            job_snapshot_id=snapshot_id,
            profile_version_id=UUID(profile["version_id"]),
            policy_version_id=UUID(policy["version_id"]),
            status="needs_review" if needs_review else "ready",
            score=record["score"],
            hard_status=detail.hard_conditions,
            eligible=bool(passes and not needs_review and detail.hard_conditions == "pass"),
            requirements=record["requirements"],
            match_evidence={"skills": profile["skills"]},
            uncertainties=["incomplete_or_unknown_or_stale"] if needs_review else [],
            prompt_version="desktop-v2",
            model=settings.vision_model,
            input_hash=hashlib.sha256(
                (content_hash + profile["version_id"] + policy["version_id"]).encode()
            ).hexdigest(),
        )
        analysis_id = session.scalar(
            insert(m.analyses)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_analysis_versions")
            .returning(m.analyses.c.id)
        )
        if analysis_id is None:
            analysis_id = session.scalar(
                sa.select(m.analyses.c.id).where(
                    m.analyses.c.job_snapshot_id == snapshot_id,
                    m.analyses.c.profile_version_id == UUID(profile["version_id"]),
                    m.analyses.c.policy_version_id == UUID(policy["version_id"]),
                    m.analyses.c.prompt_version == "desktop-v2",
                    m.analyses.c.model == settings.vision_model,
                )
            )
        return {
            "job_id": str(job_id),
            "snapshot_id": str(snapshot_id),
            "analysis_id": str(analysis_id),
        }
