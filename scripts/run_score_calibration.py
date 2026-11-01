"""Run deterministic positive, boundary, and safety calibration cases."""

import json
from copy import deepcopy

from app.matching import Criterion, Evaluation, assess, passes_threshold

PROFILE = {
    "facts": {"data_kind": "benchmark"},
    "skills": [
        {
            "canonical_name": "Python",
            "level": "proficient",
            "years": 3,
            "evidence": "使用Python开发后端服务",
        },
        {
            "canonical_name": "FastAPI",
            "level": "proficient",
            "years": 2,
            "evidence": "使用FastAPI开发REST API",
        },
        {
            "canonical_name": "PostgreSQL",
            "level": "proficient",
            "years": 2,
            "evidence": "使用PostgreSQL完成业务数据建模",
        },
        {
            "canonical_name": "RAG",
            "level": "proficient",
            "years": 1,
            "evidence": "使用RAG构建知识库问答",
        },
        {
            "canonical_name": "LangChain",
            "level": "basic",
            "years": 1,
            "evidence": "使用LangChain编排RAG流程",
        },
    ],
    "education": {"degree": "本科"},
}


def criterion(quote, index=None, *, importance="required", **values):
    return Criterion(
        quote=quote,
        category=values.pop("category", "technical"),
        importance=importance,
        status=values.pop("status", "matched"),
        skill_indices=[] if index is None else [index],
        **values,
    )


def positive_evaluation(*extra):
    return Evaluation(
        uncertain=False,
        requirements=[
            criterion("熟练使用Python开发", 0),
            criterion("熟悉FastAPI框架", 1),
            criterion("熟悉PostgreSQL数据库", 2),
            criterion("具备RAG项目经验", 3),
            criterion("熟悉LangChain", 4, importance="preferred"),
            criterion(
                "本科及以上",
                category="hard",
                profile_field="education.degree",
            ),
            *extra,
        ],
    )


def evaluate(evaluation, profile):
    text = "\n".join(item.quote for item in evaluation.requirements)
    return assess(evaluation, text, profile, {"threshold": 70, "comparison": "gte"})


def main():
    positive = evaluate(positive_evaluation(), PROFILE)

    simulation_profile = deepcopy(PROFILE)
    simulation_profile["facts"]["data_kind"] = "simulation"
    simulation = evaluate(positive_evaluation(), simulation_profile)

    hard_fail = evaluate(
        positive_evaluation(
            criterion(
                "必须为硕士",
                category="hard",
                status="unmatched",
                profile_field="education.degree",
            )
        ),
        PROFILE,
    )

    hard_unknown_profile = deepcopy(PROFILE)
    hard_unknown = evaluate(
        positive_evaluation(
            criterion(
                "要求2026届毕业生",
                category="hard",
                profile_field="education.graduation_year",
            )
        ),
        hard_unknown_profile,
    )

    cases = {
        "positive_100": {
            "actual": positive["eligible"],
            "expected": True,
            "score": positive["score"],
        },
        "simulation_never_eligible": {
            "actual": simulation["eligible"],
            "expected": False,
            "score": simulation["score"],
            "reasons": simulation["reasons"],
        },
        "hard_fail_blocks": {
            "actual": hard_fail["eligible"],
            "expected": False,
            "hard_conditions": hard_fail["hard_conditions"],
        },
        "hard_unknown_blocks": {
            "actual": hard_unknown["eligible"],
            "expected": False,
            "hard_conditions": hard_unknown["hard_conditions"],
        },
        "boundary_69_99_gte_70": {
            "actual": passes_threshold("69.99", "70", "gte"),
            "expected": False,
        },
        "boundary_70_gte_70": {
            "actual": passes_threshold("70", "70", "gte"),
            "expected": True,
        },
        "boundary_70_gt_70": {
            "actual": passes_threshold("70", "70", "gt"),
            "expected": False,
        },
        "boundary_70_01_gt_70": {
            "actual": passes_threshold("70.01", "70", "gt"),
            "expected": True,
        },
    }
    passed = all(case["actual"] == case["expected"] for case in cases.values())
    report = {"passed": passed, "cases": cases}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
