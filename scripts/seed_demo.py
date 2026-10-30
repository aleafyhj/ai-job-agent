"""Explicitly create/reuse simulated resources for read-only desktop validation."""

import json

import sqlalchemy as sa
from fastapi import Response
from sqlalchemy.orm import Session

from app import models as m
from app.api.resources import create_policy, create_profile, policy_read, profile_read
from app.core.config import Settings
from app.database import make_engine
from app.schemas import PolicyWrite, ProfileWrite


def seed(engine):
    profile_body = ProfileWrite(
        name="模拟档案：AI开发联调（非真实求职资料）",
        experience_years=2,
        education={"degree": "本科", "major": "计算机科学与技术"},
        facts={"data_kind": "simulation", "purpose": "只读采集评分联调，禁止沟通和投递"},
        skills=[
            {"canonical_name": name, "level": "proficient", "years": 2, "evidence": evidence}
            for name, evidence in [
                ("Python", "模拟项目：使用Python开发服务，有数据结构与单元测试经验。"),
                ("FastAPI", "模拟项目：FastAPI REST API，参数验证、鉴权和异步接口。"),
                ("PostgreSQL", "模拟项目：SQLAlchemy建模、SQL查询和数据库迁移。"),
                ("LLM/RAG", "模拟项目：构建文档检索问答，分块、向量检索、提示词和效果评估。"),
                ("LangChain", "模拟项目：使用LangChain实现RAG及工具调用Agent。"),
                ("FAISS", "模拟项目：使用FAISS建立向量索引并检索。"),
                ("Docker", "模拟项目：使用Docker部署Python服务。"),
                ("API集成", "模拟项目：集成HTTP API，处理鉴权、超时和错误。"),
            ]
        ],
    )
    policy_body = PolicyWrite(name="模拟联调策略：只采集不沟通", auto_apply=False)
    with Session(engine) as session:
        root = (
            session.execute(sa.select(m.profiles).where(m.profiles.c.name == profile_body.name))
            .mappings()
            .one_or_none()
        )
        profile = profile_read(session, root) if root else None
        session.rollback()
        if profile is None:
            profile = create_profile(profile_body, session, Response())
        if profile.facts.get("data_kind") != "simulation":
            raise ValueError("同名档案不是模拟档案，拒绝复用")
        root = (
            session.execute(sa.select(m.policies).where(m.policies.c.name == policy_body.name))
            .mappings()
            .one_or_none()
        )
        policy = policy_read(session, root) if root else None
        session.rollback()
        if policy is None:
            policy = create_policy(policy_body, session, Response())
        if policy.auto_apply:
            raise ValueError("模拟策略被开启自动投递，拒绝复用")
        return {"profile_id": str(profile.id), "policy_id": str(policy.id), "simulation": True}


def main():
    settings = Settings()
    if settings.database_url is None:
        raise ValueError("请先配置数据库")
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        result = seed(engine)
        folder = settings.artifacts_dir / "boss-desktop"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "demo-resources.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
