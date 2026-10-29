from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app import models as m
from app.core.config import Settings
from app.database import make_session_factory
from app.main import create_app
from app.storage import MIME_TYPES, resolve_artifact

TOKEN = "test-access-token-" + "x" * 32
PROFILE = {
    "name": "测试候选人",
    "skills": [
        {
            "canonical_name": "Python",
            "level": "proficient",
            "evidence": "独立完成 FastAPI 项目",
            "years": "2.00",
        }
    ],
    "experience_years": "2.00",
    "preferred_cities": ["上海"],
    "facts": {"email": "test@example.invalid"},
}


@pytest.fixture
def api(database, tmp_path):
    engine, _ = database
    app = create_app(
        Settings(_env_file=None, database_url=None, api_token=TOKEN, artifacts_dir=tmp_path)
    )
    with TestClient(app) as client:
        app.state.db_engine = engine
        app.state.db_sessions = make_session_factory(engine)
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        yield client, engine, tmp_path


def pdf():
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    return stream.getvalue()


def docx():
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(
            "_rels/.rels",
            """<Relationships
          xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Target="word/document.xml"
          Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>
          </Relationships>""",
        )
        archive.writestr(
            "[Content_Types].xml",
            """<Types
          xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Override PartName="/word/document.xml"
          ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
          </Types>""",
        )
        archive.writestr(
            "word/document.xml",
            """<w:document
          xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body><w:p/></w:body></w:document>""",
        )
    return stream.getvalue()


@pytest.mark.postgres
def test_authentication_and_profile_version_concurrency(api):
    client, engine, _ = api
    assert (
        client.post(
            "/api/v1/profiles", json=PROFILE, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    response = client.post("/api/v1/profiles", json=PROFILE)
    assert response.status_code == 201
    assert response.headers["ETag"] == '"1"'
    original = response.json()
    path = f"/api/v1/profiles/{original['id']}"
    assert client.get(path).json()["skills"][0]["canonical_name"] == "Python"
    assert client.put(path, json=PROFILE).status_code == 422

    def update(_):
        return client.put(
            path, json=dict(PROFILE, experience_years="3.00"), headers={"If-Match": '"1"'}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, range(2))) == [200, 409]
    assert client.get(path).json()["version"] == 2
    with Session(engine) as session:
        versions = (
            session.execute(sa.select(m.profile_versions).order_by(m.profile_versions.c.version))
            .mappings()
            .all()
        )
        assert len(versions) == 2
        assert versions[0]["experience_years"] == 2
        assert versions[1]["experience_years"] == 3


@pytest.mark.postgres
def test_profile_and_policy_pagination_and_validation(api):
    client, _, _ = api
    for index in range(3):
        assert (
            client.post("/api/v1/profiles", json={"name": f"Candidate {index}"}).status_code == 201
        )
    first = client.get("/api/v1/profiles", params={"limit": 2}).json()
    second = client.get("/api/v1/profiles", params={"cursor": first["next_cursor"]}).json()
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    assert len({item["id"] for item in first["items"] + second["items"]}) == 3
    assert client.get("/api/v1/profiles", params={"cursor": "bad"}).status_code == 422
    assert client.get("/api/v1/profiles", params={"limit": 101}).status_code == 422
    assert client.get(f"/api/v1/profiles/{uuid4()}").status_code == 404
    duplicate = dict(PROFILE, skills=PROFILE["skills"] * 2)
    assert client.post("/api/v1/profiles", json=duplicate).status_code == 422
    assert client.post("/api/v1/profiles", json={"name": "   "}).status_code == 422
    for patch in [
        {"timezone": "Invalid/Zone"},
        {"threshold": 101},
        {"daily_limit": 0},
        {"allowed_platforms": ["unknown"]},
        {"unexpected": 1},
    ]:
        assert client.post("/api/v1/policies", json={"name": "Test", **patch}).status_code == 422


@pytest.mark.postgres
def test_policy_versions_and_defaults(api):
    client, engine, _ = api
    created = client.post("/api/v1/policies", json={"name": "BOSS默认策略"})
    assert created.status_code == 201
    policy = created.json()
    assert Decimal(policy["threshold"]) == 70
    assert policy["allowed_platforms"] == ["boss"]
    assert policy["auto_apply"] is False
    path = f"/api/v1/policies/{policy['id']}"
    updated = client.put(
        path,
        json={"name": "严格模式", "comparison": "gt", "daily_limit": 10},
        headers={"If-Match": '"1"'},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.headers["ETag"] == '"2"'
    assert client.get(path).json()["comparison"] == "gt"
    assert client.put(path, json={"name": "覆盖"}, headers={"If-Match": "1"}).status_code == 409
    assert len(client.get("/api/v1/policies").json()["items"]) == 1
    with Session(engine) as session:
        old = (
            session.execute(sa.select(m.policy_versions).where(m.policy_versions.c.version == 1))
            .mappings()
            .one()
        )
        assert old["comparison"] == "gte"


@pytest.mark.postgres
def test_pdf_docx_upload_version_download_and_default_ownership(api):
    client, _, root = api
    profile = client.post("/api/v1/profiles", json=PROFILE).json()
    path = f"/api/v1/profiles/{profile['id']}/resumes"
    pdf_data = pdf()
    one = client.post(path, files={"file": ("../../resume.pdf", pdf_data, MIME_TYPES[".pdf"])})
    two = client.post(path, files={"file": ("简历.docx", docx(), MIME_TYPES[".docx"])})
    assert one.status_code == two.status_code == 201
    assert [one.json()["version"], two.json()["version"]] == [1, 2]
    assert one.json()["filename"] == "resume.pdf"
    assert "storage_key" not in one.json()
    assert len(list(root.rglob("*.pdf"))) == 1
    content_path = f"/api/v1/artifacts/{one.json()['artifact_id']}/content"
    downloaded = client.get(content_path)
    assert downloaded.content == pdf_data
    assert downloaded.headers["Cache-Control"] == "no-store"
    assert client.get(content_path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    first = client.get(path, params={"limit": 1}).json()
    second = client.get(path, params={"cursor": first["next_cursor"]}).json()
    assert [first["items"][0]["version"], second["items"][0]["version"]] == [2, 1]
    default_path = f"/api/v1/profiles/{profile['id']}/default-resume"
    assert (
        client.put(default_path, json={"resume_id": one.json()["id"]}).json()["default_resume_id"]
        == one.json()["id"]
    )
    other = client.post("/api/v1/profiles", json={"name": "Other"}).json()
    assert (
        client.put(
            f"/api/v1/profiles/{other['id']}/default-resume", json={"resume_id": one.json()["id"]}
        ).status_code
        == 409
    )
    assert (
        client.get(
            f"/api/v1/profiles/{other['id']}/resumes", params={"cursor": first["next_cursor"]}
        ).status_code
        == 422
    )
    stored_file = next(root.rglob("*.pdf"))
    stored_file.unlink()
    assert client.get(content_path).status_code == 404


@pytest.mark.postgres
def test_invalid_uploads_do_not_create_files_or_records(api):
    client, engine, root = api
    profile = client.post("/api/v1/profiles", json=PROFILE).json()
    path = f"/api/v1/profiles/{profile['id']}/resumes"
    for name, data, mime, status in [
        ("fake.pdf", b"%PDF-fake", MIME_TYPES[".pdf"], 422),
        ("fake.docx", b"PK fake", MIME_TYPES[".docx"], 422),
        ("resume.pdf", pdf(), "text/plain", 422),
        ("empty.pdf", b"", MIME_TYPES[".pdf"], 422),
        ("large.pdf", b"x" * (10 * 1024 * 1024 + 1), MIME_TYPES[".pdf"], 413),
    ]:
        assert client.post(path, files={"file": (name, data, mime)}).status_code == status
    assert (
        client.post(
            path,
            content=b"x" * (12 * 1024 * 1024 + 1),
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 413
    )
    with Session(engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(m.resumes)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(m.artifacts)) == 0
    assert list(root.rglob("*.pdf")) == []


@pytest.mark.postgres
def test_failed_database_write_cleans_uploaded_file(api, monkeypatch):
    client, engine, root = api
    profile = client.post("/api/v1/profiles", json=PROFILE).json()

    def fail(_):
        raise RuntimeError("simulate failed transaction")

    monkeypatch.setattr("app.api.resources.resume_read", fail)
    response = client.post(
        f"/api/v1/profiles/{profile['id']}/resumes",
        files={"file": ("resume.pdf", pdf(), MIME_TYPES[".pdf"])},
    )
    assert response.status_code == 500
    assert list(root.rglob("*.pdf")) == []
    with Session(engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(m.resumes)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(m.artifacts)) == 0


def test_missing_token_configuration_does_not_expose_resources():
    with TestClient(create_app(Settings(_env_file=None, api_token=None))) as client:
        assert client.get("/api/v1/profiles").status_code == 503
        assert client.get("/api/v1/health").status_code == 200


def test_artifact_path_cannot_escape_root(tmp_path):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        resolve_artifact(tmp_path, "../outside.pdf")


@pytest.mark.postgres
def test_storage_readiness_reports_failure(api):
    client, _, root = api
    assert client.get("/api/v1/ready").json()["storage"] == "ok"
    invalid_root = root / "not-a-directory"
    invalid_root.write_text("file", encoding="utf-8")
    client.app.state.settings.artifacts_dir = invalid_root
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json()["storage"] == "unavailable"
