"""Candidate profiles, immutable resume versions and application policies."""

import base64
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from app import models as m
from app.api.dependencies import DB, require_token
from app.schemas import (
    DefaultResumeWrite,
    Page,
    PolicyRead,
    PolicyWrite,
    ProfileRead,
    ProfileWrite,
    ResumeRead,
)
from app.storage import resolve_artifact, save_resume

router = APIRouter(dependencies=[Depends(require_token)])
Version = Annotated[str, Header(alias="If-Match", pattern=r'^(?:[1-9][0-9]*|"[1-9][0-9]*")$')]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=512)]


def get_row(session, table, resource_id, *, lock=False):
    statement = sa.select(table).where(table.c.id == resource_id)
    if lock:
        statement = statement.with_for_update()
    row = session.execute(statement).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "资源不存在")
    return row


def profile_read(session, root):
    version = get_row(session, m.profile_versions, root["current_version_id"])
    return ProfileRead(
        id=root["id"],
        name=root["name"],
        version=version["version"],
        version_id=version["id"],
        default_resume_id=root["default_resume_id"],
        created_at=root["created_at"],
        skills=version["skills"],
        experience_years=version["experience_years"],
        education=version["education"],
        facts=version["facts"],
        **version["preferences"],
    )


def profile_values(body):
    data = body.model_dump(mode="json")
    return {
        "skills": data["skills"],
        "experience_years": body.experience_years,
        "education": data["education"],
        "facts": data["facts"],
        "preferences": {key: data[key] for key in ("preferred_cities", "remote_preference")},
    }


def policy_read(session, root):
    version = get_row(session, m.policy_versions, root["current_version_id"])
    fields = {key: version[key] for key in PolicyWrite.model_fields if key != "name"}
    return PolicyRead(
        id=root["id"],
        name=root["name"],
        version=version["version"],
        version_id=version["id"],
        created_at=root["created_at"],
        **fields,
    )


def check_version(current, expected):
    if str(current) != expected.strip('"'):
        raise HTTPException(409, "版本已变化，请重新读取后修改")


def etag(response, version):
    response.headers["ETag"] = f'"{version}"'


def page_query(session, table, statement, cursor, limit, scope):
    if cursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            stamp, resource_id = datetime.fromisoformat(data["at"]), UUID(data["id"])
            if data["scope"] != scope or stamp.tzinfo is None:
                raise ValueError
        except Exception:
            raise HTTPException(422, "无效的分页 cursor") from None
        statement = statement.where(
            sa.tuple_(table.c.created_at, table.c.id) < (stamp, resource_id)
        )
    rows = (
        session.execute(
            statement.order_by(table.c.created_at.desc(), table.c.id.desc()).limit(limit + 1)
        )
        .mappings()
        .all()
    )
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "scope": scope,
                    "at": last["created_at"].isoformat(),
                    "id": str(last["id"]),
                }
            ).encode()
        ).decode()
    return items, next_cursor


@router.post("/profiles", response_model=ProfileRead, status_code=201, tags=["profiles"])
def create_profile(body: ProfileWrite, session: DB, response: Response):
    with session.begin():
        root_id = session.scalar(
            m.profiles.insert().values(name=body.name).returning(m.profiles.c.id)
        )
        version_id = session.scalar(
            m.profile_versions.insert()
            .values(profile_id=root_id, version=1, **profile_values(body))
            .returning(m.profile_versions.c.id)
        )
        session.execute(
            m.profiles.update()
            .where(m.profiles.c.id == root_id)
            .values(
                current_version_id=version_id,
            )
        )
        result = profile_read(session, get_row(session, m.profiles, root_id))
    etag(response, result.version)
    return result


@router.get("/profiles", response_model=Page[ProfileRead], tags=["profiles"])
def list_profiles(session: DB, cursor: Cursor = None, limit: Limit = 20):
    rows, next_cursor = page_query(
        session, m.profiles, sa.select(m.profiles), cursor, limit, "profiles"
    )
    return Page(items=[profile_read(session, row) for row in rows], next_cursor=next_cursor)


@router.get("/profiles/{profile_id}", response_model=ProfileRead, tags=["profiles"])
def read_profile(profile_id: UUID, session: DB, response: Response):
    result = profile_read(session, get_row(session, m.profiles, profile_id))
    etag(response, result.version)
    return result


@router.put("/profiles/{profile_id}", response_model=ProfileRead, tags=["profiles"])
def update_profile(
    profile_id: UUID, body: ProfileWrite, version: Version, session: DB, response: Response
):
    with session.begin():
        root = get_row(session, m.profiles, profile_id, lock=True)
        old = get_row(session, m.profile_versions, root["current_version_id"])
        check_version(old["version"], version)
        new_id = session.scalar(
            m.profile_versions.insert()
            .values(profile_id=profile_id, version=old["version"] + 1, **profile_values(body))
            .returning(m.profile_versions.c.id)
        )
        session.execute(
            m.profiles.update()
            .where(m.profiles.c.id == profile_id)
            .values(
                name=body.name,
                current_version_id=new_id,
            )
        )
        result = profile_read(session, get_row(session, m.profiles, profile_id))
    etag(response, result.version)
    return result


def resume_read(row):
    return ResumeRead(
        **{key: row[key] for key in ResumeRead.model_fields if key != "filename"},
        filename=row["original_filename"],
    )


@router.post(
    "/profiles/{profile_id}/resumes", response_model=ResumeRead, status_code=201, tags=["resumes"]
)
def upload_resume(profile_id: UUID, file: UploadFile, session: DB, request: Request):
    stored = None
    try:
        with session.begin():
            # Serialize resume version numbers with profile updates/default changes.
            get_row(session, m.profiles, profile_id, lock=True)
            stored = save_resume(file, request.app.state.settings.artifacts_dir)
            artifact_id = session.scalar(
                m.artifacts.insert()
                .values(
                    kind="resume",
                    storage_key=stored["storage_key"],
                    mime_type=stored["mime_type"],
                    sha256=stored["sha256"],
                    size=stored["size"],
                )
                .returning(m.artifacts.c.id)
            )
            number = (
                session.scalar(
                    sa.select(sa.func.coalesce(sa.func.max(m.resumes.c.version), 0)).where(
                        m.resumes.c.profile_id == profile_id
                    )
                )
                + 1
            )
            row = (
                session.execute(
                    m.resumes.insert()
                    .values(
                        profile_id=profile_id,
                        version=number,
                        artifact_id=artifact_id,
                        sha256=stored["sha256"],
                        original_filename=stored["filename"],
                        mime_type=stored["mime_type"],
                        size=stored["size"],
                    )
                    .returning(m.resumes)
                )
                .mappings()
                .one()
            )
            result = resume_read(row)
        return result
    except Exception:
        if stored is not None:
            stored["path"].unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


@router.get("/profiles/{profile_id}/resumes", response_model=Page[ResumeRead], tags=["resumes"])
def list_resumes(profile_id: UUID, session: DB, cursor: Cursor = None, limit: Limit = 20):
    get_row(session, m.profiles, profile_id)
    rows, next_cursor = page_query(
        session,
        m.resumes,
        sa.select(m.resumes).where(m.resumes.c.profile_id == profile_id),
        cursor,
        limit,
        f"resumes:{profile_id}",
    )
    return Page(items=[resume_read(row) for row in rows], next_cursor=next_cursor)


@router.put("/profiles/{profile_id}/default-resume", response_model=ProfileRead, tags=["resumes"])
def set_default_resume(profile_id: UUID, body: DefaultResumeWrite, session: DB):
    with session.begin():
        get_row(session, m.profiles, profile_id, lock=True)
        resume = get_row(session, m.resumes, body.resume_id)
        if resume["profile_id"] != profile_id:
            raise HTTPException(409, "简历不属于该档案")
        session.execute(
            m.profiles.update()
            .where(m.profiles.c.id == profile_id)
            .values(
                default_resume_id=body.resume_id,
            )
        )
        return profile_read(session, get_row(session, m.profiles, profile_id))


@router.get("/artifacts/{artifact_id}/content", tags=["resumes"])
def download_artifact(artifact_id: UUID, session: DB, request: Request):
    artifact = get_row(session, m.artifacts, artifact_id)
    # Only resume downloads are exposed in this phase.
    resume = (
        session.execute(sa.select(m.resumes).where(m.resumes.c.artifact_id == artifact_id))
        .mappings()
        .one_or_none()
    )
    if artifact["kind"] != "resume" or resume is None:
        raise HTTPException(404, "文件不存在")
    path = resolve_artifact(request.app.state.settings.artifacts_dir, artifact["storage_key"])
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(
        path,
        media_type=artifact["mime_type"],
        filename=resume["original_filename"],
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/policies", response_model=PolicyRead, status_code=201, tags=["policies"])
def create_policy(body: PolicyWrite, session: DB, response: Response):
    with session.begin():
        root_id = session.scalar(
            m.policies.insert().values(name=body.name).returning(m.policies.c.id)
        )
        version_id = session.scalar(
            m.policy_versions.insert()
            .values(policy_id=root_id, version=1, **body.model_dump(exclude={"name"}))
            .returning(m.policy_versions.c.id)
        )
        session.execute(
            m.policies.update()
            .where(m.policies.c.id == root_id)
            .values(
                current_version_id=version_id,
            )
        )
        result = policy_read(session, get_row(session, m.policies, root_id))
    etag(response, result.version)
    return result


@router.get("/policies", response_model=Page[PolicyRead], tags=["policies"])
def list_policies(session: DB, cursor: Cursor = None, limit: Limit = 20):
    rows, next_cursor = page_query(
        session, m.policies, sa.select(m.policies), cursor, limit, "policies"
    )
    return Page(items=[policy_read(session, row) for row in rows], next_cursor=next_cursor)


@router.get("/policies/{policy_id}", response_model=PolicyRead, tags=["policies"])
def read_policy(policy_id: UUID, session: DB, response: Response):
    result = policy_read(session, get_row(session, m.policies, policy_id))
    etag(response, result.version)
    return result


@router.put("/policies/{policy_id}", response_model=PolicyRead, tags=["policies"])
def update_policy(
    policy_id: UUID, body: PolicyWrite, version: Version, session: DB, response: Response
):
    with session.begin():
        root = get_row(session, m.policies, policy_id, lock=True)
        old = get_row(session, m.policy_versions, root["current_version_id"])
        check_version(old["version"], version)
        new_id = session.scalar(
            m.policy_versions.insert()
            .values(
                policy_id=policy_id, version=old["version"] + 1, **body.model_dump(exclude={"name"})
            )
            .returning(m.policy_versions.c.id)
        )
        session.execute(
            m.policies.update()
            .where(m.policies.c.id == policy_id)
            .values(
                name=body.name,
                current_version_id=new_id,
            )
        )
        result = policy_read(session, get_row(session, m.policies, policy_id))
    etag(response, result.version)
    return result
