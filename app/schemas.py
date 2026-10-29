"""Request and response contracts for candidate resources."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Text = Annotated[str, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Skill(InputModel):
    canonical_name: Text
    level: Literal["basic", "proficient", "expert"]
    evidence: str = Field(min_length=1, max_length=10000)
    years: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)


class ProfileWrite(InputModel):
    name: Text
    skills: list[Skill] = Field(default_factory=list, max_length=200)
    experience_years: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    education: dict[str, str | None] = Field(default_factory=dict, max_length=30)
    preferred_cities: list[Text] = Field(default_factory=list, max_length=30)
    remote_preference: Literal["any", "required", "onsite"] = "any"
    facts: dict[str, str | None] = Field(default_factory=dict, max_length=100)

    @field_validator("skills")
    @classmethod
    def unique_skills(cls, value):
        names = [skill.canonical_name.casefold() for skill in value]
        if len(names) != len(set(names)):
            raise ValueError("技能名称不能重复")
        return value


class ProfileRead(ProfileWrite):
    id: UUID
    version: int
    version_id: UUID
    default_resume_id: UUID | None
    created_at: datetime


class PolicyWrite(InputModel):
    name: Text
    threshold: Decimal = Field(default=Decimal("70"), ge=0, le=100, decimal_places=6)
    comparison: Literal["gte", "gt"] = "gte"
    daily_limit: int = Field(default=20, ge=1, le=100, strict=True)
    timezone: str = Field(default="Asia/Shanghai", max_length=100)
    auto_apply: bool = False
    blocked_companies: list[Text] = Field(default_factory=list, max_length=500)
    allowed_platforms: list[Literal["boss"]] = Field(default_factory=lambda: ["boss"], max_length=1)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("无效的 IANA 时区") from None
        return value


class PolicyRead(PolicyWrite):
    id: UUID
    version: int
    version_id: UUID
    created_at: datetime


class ResumeRead(BaseModel):
    id: UUID
    profile_id: UUID
    version: int
    artifact_id: UUID
    filename: str
    mime_type: str
    size: int
    sha256: str
    created_at: datetime


class DefaultResumeWrite(InputModel):
    resume_id: UUID


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None
