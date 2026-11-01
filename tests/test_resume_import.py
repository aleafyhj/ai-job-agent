from io import BytesIO
from zipfile import ZipFile

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app import models as m
from app.core.config import Settings
from app.database import make_session_factory
from app.main import create_app
from app.resume_import import (
    ExtractedEducation,
    ExtractedSkill,
    ResumeExtraction,
    _validate_evidence,
    extract_resume_text,
    merge_profile,
    redact_contact_details,
)
from app.schemas import ProfileRead
from app.storage import MIME_TYPES

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


def docx_with_text(text):
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
            """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Override PartName="/word/document.xml"
          ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
          </Types>""",
        )
        archive.writestr(
            "word/document.xml",
            f"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>""",
        )
    return stream.getvalue()


def extraction(*, uncertain=False):
    return ResumeExtraction(
        skills=[
            ExtractedSkill(
                canonical_name="Go",
                level="proficient",
                evidence="使用Go开发微服务",
                years="2.00",
            )
        ],
        experience_years="3.00",
        experience_evidence="3年工作经验",
        education=ExtractedEducation(
            degree="本科", major="计算机科学", evidence=["本科 计算机科学"]
        ),
        uncertain=uncertain,
        warnings=["date_overlap"] if uncertain else [],
    )


def test_docx_text_and_contact_redaction(tmp_path):
    path = tmp_path / "resume.docx"
    path.write_bytes(docx_with_text("Python项目 test@example.com 13800138000 本科"))
    text, truncated = extract_resume_text(path, MIME_TYPES[".docx"])
    assert "Python项目" in text
    assert truncated is False
    safe = redact_contact_details(text)
    assert "test@example.com" not in safe
    assert "13800138000" not in safe
    assert "[REDACTED_EMAIL]" in safe and "[REDACTED_PHONE]" in safe


def test_merge_fills_missing_without_overwriting_verified_profile():
    current = ProfileRead(
        id="11111111-1111-1111-1111-111111111111",
        version=1,
        version_id="22222222-2222-2222-2222-222222222222",
        default_resume_id=None,
        created_at="2026-01-01T00:00:00Z",
        **PROFILE,
    )
    merged = merge_profile(current, extraction(), "fill_missing")
    assert [item.canonical_name for item in merged.skills] == ["Python", "Go"]
    assert merged.experience_years == 2
    assert merged.education == {"degree": "本科", "major": "计算机科学"}
    assert merged.preferred_cities == ["上海"]
    assert merged.facts == PROFILE["facts"]


def test_evidence_is_mapped_back_to_continuous_source_text():
    result = ResumeExtraction(
        skills=[
            ExtractedSkill(
                canonical_name="Python",
                level="proficient",
                evidence="使用python 开发接口",
            )
        ],
        education=ExtractedEducation(
            degree="本科", major="计算机科学", evidence=["本科 计算机科学"]
        ),
        uncertain=False,
    )
    source = "项目经历：使用Python\n开发接口\n教育经历：本科　计算机科学"
    _validate_evidence(result, source, False)
    assert result.skills[0].evidence == "使用Python\n开发接口"
    assert result.skills[0].evidence in source
    assert result.education.evidence == ["本科　计算机科学"]
    assert result.uncertain is True
    assert "evidence_repaired" in result.warnings


def test_rewritten_evidence_uses_skill_line_and_drops_unsupported_claims():
    result = ResumeExtraction(
        skills=[
            ExtractedSkill(
                canonical_name="FastAPI",
                level="proficient",
                evidence="熟练使用FastAPI构建多个高性能系统并负责完整架构",
            ),
            ExtractedSkill(
                canonical_name="Kubernetes",
                level="expert",
                evidence="精通Kubernetes集群治理",
            ),
        ],
        experience_years="3",
        experience_evidence="拥有三年后端研发经验",
        uncertain=False,
    )
    source = "项目：使用FastAPI开发内部管理接口\n工作经历：2023年至今"
    _validate_evidence(result, source, False)
    assert [item.canonical_name for item in result.skills] == ["FastAPI"]
    assert result.skills[0].evidence == "项目：使用FastAPI开发内部管理接口"
    assert result.skills[0].level == "basic"
    assert result.skills[0].years is None
    assert result.experience_years is None
    assert result.experience_evidence is None
    assert {"evidence_repaired", "insufficient_evidence", "experience_ambiguous"} <= set(
        result.warnings
    )


@pytest.mark.postgres
def test_resume_import_creates_profile_version_and_is_idempotent(api, monkeypatch):
    client, engine, _ = api
    client.app.state.settings.vision_api_key = SecretStr("test-model-key")
    monkeypatch.setattr("app.api.resources.import_resume_profile", lambda *args: extraction())
    profile = client.post("/api/v1/profiles", json=PROFILE).json()
    upload = client.post(
        f"/api/v1/profiles/{profile['id']}/resumes",
        files={
            "file": (
                "resume.docx",
                docx_with_text("使用Go开发微服务 3年工作经验 本科 计算机科学"),
                MIME_TYPES[".docx"],
            )
        },
    ).json()
    path = f"/api/v1/profiles/{profile['id']}/resumes/{upload['id']}/import"
    response = client.post(
        path,
        json={"mode": "fill_missing", "set_default_resume": True},
        headers={"If-Match": "1"},
    )
    assert response.status_code == 201
    result = response.json()
    assert response.headers["ETag"] == '"2"'
    assert result["status"] == "ready"
    assert result["profile"]["version"] == 2
    assert [item["canonical_name"] for item in result["profile"]["skills"]] == ["Python", "Go"]
    assert result["profile"]["default_resume_id"] == upload["id"]

    repeated = client.post(
        path,
        json={"mode": "fill_missing", "set_default_resume": True},
        headers={"If-Match": "2"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == result["id"]
    with Session(engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(m.resume_imports)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(m.profile_versions)) == 2
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(m.resume_imports.update().values(status="needs_review"))


@pytest.mark.postgres
def test_uncertain_resume_cannot_replace_profile(api, monkeypatch):
    client, engine, _ = api
    client.app.state.settings.vision_api_key = SecretStr("test-model-key")
    monkeypatch.setattr(
        "app.api.resources.import_resume_profile", lambda *args: extraction(uncertain=True)
    )
    profile = client.post("/api/v1/profiles", json=PROFILE).json()
    upload = client.post(
        f"/api/v1/profiles/{profile['id']}/resumes",
        files={
            "file": (
                "resume.docx",
                docx_with_text("使用Go开发微服务 3年工作经验 本科 计算机科学"),
                MIME_TYPES[".docx"],
            )
        },
    ).json()
    response = client.post(
        f"/api/v1/profiles/{profile['id']}/resumes/{upload['id']}/import",
        json={"mode": "replace_resume_fields"},
        headers={"If-Match": "1"},
    )
    assert response.status_code == 409
    with Session(engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(m.resume_imports)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(m.profile_versions)) == 1
