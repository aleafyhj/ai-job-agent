"""Replay stored job text against a profile without controlling the desktop."""

import argparse
import json
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app import models as m
from app.api.resources import get_row, policy_read, profile_read
from app.core.config import Settings
from app.database import make_engine
from app.matching import assess
from app.vision import ANALYSIS_VERSION, Vision


def main():
    parser = argparse.ArgumentParser(description="离线回放已采集正文，仅调用模型，不修改业务分析")
    parser.add_argument("--profile-id", type=UUID, required=True)
    parser.add_argument("--policy-id", type=UUID, required=True)
    parser.add_argument("--limit", type=int, choices=range(1, 11), default=3)
    args = parser.parse_args()
    settings = Settings()
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        with Session(engine) as session:
            profile = profile_read(
                session, get_row(session, m.profiles, args.profile_id)
            ).model_dump(mode="json")
            policy = policy_read(session, get_row(session, m.policies, args.policy_id)).model_dump(
                mode="json"
            )
    finally:
        engine.dispose()
    root = settings.artifacts_dir / "boss-desktop"
    history = json.loads((root / "history.json").read_text(encoding="utf-8"))
    evidence = {
        key: profile[key]
        for key in (
            "skills",
            "education",
            "experience_years",
            "preferred_cities",
            "remote_preference",
        )
    }
    folder = root / "replays" / uuid4().hex
    folder.mkdir(parents=True)
    report = {
        "version": ANALYSIS_VERSION,
        "simulation": profile.get("facts", {}).get("data_kind") == "simulation",
        "status": "running",
        "jobs": [],
    }
    vision = Vision(settings)
    try:
        records = [
            value
            for key, value in history.items()
            if key.startswith(str(args.profile_id) + ":") and value.get("detail_complete")
        ]
        for record in records[: args.limit]:
            text = record["detail_text"]
            evaluation = vision.evaluate_text(text, evidence)
            decision = assess(evaluation, text, profile, policy)
            if report["simulation"]:
                decision["eligible"] = False
                decision["reasons"].append("simulation_profile")
            row = {
                "card": record["card"],
                "previous_score": record["score"],
                "evaluation": evaluation.model_dump(),
                "decision": decision,
            }
            report["jobs"].append(row)
            print(
                record["card"]["title"],
                "旧分数:",
                record["score"],
                "新分数:",
                decision["score"],
                "需复核:",
                decision["needs_review"],
                flush=True,
            )
        report["status"] = "completed"
    except Exception as exc:
        report.update(status="stopped", error_type=type(exc).__name__)
        raise
    finally:
        (folder / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("回放报告:", (folder / "report.json").resolve())


if __name__ == "__main__":
    main()
