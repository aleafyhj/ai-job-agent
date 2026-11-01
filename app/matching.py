"""Typed full-text evaluation and conservative, reproducible scoring."""

import re
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: str = Field(min_length=1)
    category: Literal["technical", "hard", "soft", "other"]
    importance: Literal["required", "preferred"]
    status: Literal["matched", "unmatched", "unknown"]
    skill_indices: list[Annotated[int, Field(ge=0, strict=True)]] = Field(
        default_factory=list, max_length=20
    )
    profile_field: (
        Literal[
            "education.degree",
            "education.major",
            "education.graduation_year",
            "experience_years",
            "preferred_cities",
            "remote_preference",
        ]
        | None
    ) = None
    min_level: Literal["basic", "proficient", "expert"] | None = None
    min_years: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def non_scored_importance(cls, value):
        if isinstance(value, dict) and value.get("category") in {"soft", "other"}:
            # Importance has no scoring/gating meaning for these categories.
            return {**value, "importance": "preferred"}
        return value


class Evaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uncertain: bool = True
    multiple_roles: bool = False
    requirements: list[Criterion] = Field(max_length=150)


def assess(evaluation, text, profile, policy):
    issues = []
    items = []
    seen = set()
    levels = {"basic": 0, "proficient": 1, "expert": 2}
    skills = profile["skills"]
    multiple = (
        evaluation.multiple_roles
        or len(set(re.findall(r"岗位[一二三四五六七八九十]+[：:、]", text))) > 1
    )
    if multiple:
        issues.append("multiple_roles_require_selection")
    if evaluation.uncertain:
        issues.append("uncertain_extraction")
    for item in evaluation.requirements:
        if item.quote not in text:
            issues.append("quote_not_in_source")
            continue
        key = "".join(unicodedata.normalize("NFKC", item.quote).split()).casefold()
        if key in seen:
            issues.append("duplicate_requirement")
            continue
        seen.add(key)
        category = item.category
        if (
            re.search(r"学历|本科|大专|硕士|博士|毕业生|毕业年份", item.quote)
            and category == "technical"
        ):
            category = "hard"
            issues.append("category_conflict")
        if category == "technical" and re.search(r"薪资|月薪|年薪|五险|公积金|年终奖", item.quote):
            category = "other"
            issues.append("category_conflict")
        if category == "technical":
            concepts = [
                r"python",
                r"fastapi",
                r"java(?!script)",
                r"rag",
                r"微调|fine.?tun",
                r"工具调用|function.?call",
                r"docker",
                r"postgresql",
                r"langchain",
            ]
            count = sum(bool(re.search(pattern, item.quote, re.I)) for pattern in concepts)
            if (
                count > 1
                and re.search(r"和|及|、|，|,|并|与", item.quote)
                and not re.search(r"其中之一|至少一种|任一|或|择一", item.quote)
            ):
                issues.append("unsplit_technical_requirement")
        if (
            re.search(
                r"沟通能力|团队协作|责任心|执行力|逻辑思维|审美能力|(?<!机器)(?<!深度)学习能力",
                item.quote,
            )
            and category == "technical"
        ):
            category = "soft"
            issues.append("category_conflict")
        status = item.status
        refs = []
        if category == "technical":
            invalid = len(set(item.skill_indices)) != len(item.skill_indices)
            for index in item.skill_indices:
                if isinstance(index, bool) or index < 0 or index >= len(skills):
                    invalid = True
                    continue
                skill = skills[index]
                if not skill.get("evidence", "").strip():
                    invalid = True
                refs.append({"index": index, **skill})
            if invalid:
                issues.append("invalid_skill_reference")
                status = "unknown"
            elif not refs:
                status = "unknown"  # Omission from a resume is not a proven inability.
            elif item.min_level and any(s.get("level") not in levels for s in refs):
                status = "unknown"
            elif item.min_level and any(
                levels.get(s.get("level"), -1) < levels[item.min_level] for s in refs
            ):
                status = "unmatched"
            elif item.min_years is not None:
                if any(s.get("years") is None for s in refs):
                    status = "unknown"
                elif any(float(s["years"]) < item.min_years for s in refs):
                    status = "unmatched"
            if status == "matched" and refs:
                evidence = "\n".join(
                    str(s.get("canonical_name", "")) + " " + s.get("evidence", "") for s in refs
                )
                support = [
                    bool(re.search(pattern, evidence, re.I))
                    for pattern in concepts
                    if re.search(pattern, item.quote, re.I)
                ]
                is_or = bool(re.search(r"其中之一|至少一种|任一|或|择一", item.quote))
                if support and not (any(support) if is_or else all(support)):
                    status = "unknown"
                    issues.append("unsupported_skill_concept")
        elif category == "hard":
            value = profile
            for part in (item.profile_field or "").split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if value is None or value == "" or value == [] or value == {}:
                status = "unknown"
        weight = (3 if item.importance == "required" else 1) if category == "technical" else 0
        items.append(
            {
                **item.model_dump(),
                "category": category,
                "status": status,
                "weight": weight,
                "matched": status == "matched",
                "skill_evidence": refs,
            }
        )
    technical = [item for item in items if item["category"] == "technical"]
    total = sum(item["weight"] for item in technical)
    covered = sum(item["weight"] for item in technical if item["status"] == "matched")
    unknown = sum(item["weight"] for item in technical if item["status"] == "unknown")
    # Different advertised roles must not be collapsed into one match ratio.
    score = 100 * covered / total if total and not multiple else None
    upper = 100 * (covered + unknown) / total if total and not multiple else None
    hard = [
        item["status"]
        for item in items
        if item["category"] == "hard" and item["importance"] == "required"
    ]
    hard_status = "fail" if "unmatched" in hard else "unknown" if "unknown" in hard else "pass"
    if not total:
        issues.append("no_technical_requirements")
    if any(item["status"] == "unknown" and item["importance"] == "required" for item in technical):
        issues.append("required_skill_evidence_unknown")
    if hard_status == "unknown":
        issues.append("hard_conditions_unknown")
    comparison = policy["comparison"]
    if comparison not in {"gte", "gt"}:
        raise ValueError("未知匹配阈值比较方式")
    threshold = float(policy["threshold"])
    passes = score is not None and (
        score >= threshold if comparison == "gte" else score > threshold
    )
    reasons = list(dict.fromkeys(issues))
    if score is not None and not passes:
        reasons.append("below_threshold")
    if hard_status == "fail":
        reasons.append("hard_conditions_fail")
    return {
        "score": score,
        "score_upper_bound": upper,
        "threshold": threshold,
        "comparison": comparison,
        "hard_conditions": hard_status,
        "needs_review": bool(issues),
        "eligible": passes and not issues and hard_status == "pass",
        "reasons": reasons,
        "requirements": items,
        "weights": {"total": total, "covered": covered, "unknown": unknown},
    }
