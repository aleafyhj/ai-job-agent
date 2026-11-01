"""Typed full-text evaluation and conservative, reproducible scoring."""

import re
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONCEPT_PATTERNS = [
    r"python",
    r"fastapi",
    r"java(?!script)",
    r"c\+\+",
    r"rag",
    r"微调|fine.?tun",
    r"工具调用|function.?call",
    r"docker",
    r"postgresql",
    r"langchain",
    r"数据结构",
    r"算法",
    r"编码规范",
    r"多线程",
    r"性能调优",
    r"tcp/?ip|网络协议|数据包",
    r"操作系统.*内核|内核驱动",
]
EVIDENCE_CONCEPT_PATTERNS = [
    r"微调|fine.?tun",
    r"工具调用|function.?call",
    r"数据结构",
    r"算法",
    r"编码规范",
    r"多线程",
    r"性能调优",
    r"tcp/?ip|网络协议|数据包",
    r"操作系统.*内核|内核驱动",
]
DIRECT_SKILL_PATTERNS = {
    "Python": r"(?<![a-z0-9])python(?![a-z0-9])",
    "Git": r"(?<![a-z0-9])git(?![a-z0-9])",
    "C++": r"c\+\+",
    "Java": r"java(?!script)",
    "Spring": r"(?<![a-z0-9])spring(?![a-z0-9])",
    "Flask": r"(?<![a-z0-9])flask(?![a-z0-9])",
    "MySQL": r"(?<![a-z0-9])mysql(?![a-z0-9])",
    "RAG": r"(?<![a-z0-9])rag(?![a-z0-9])",
    "LangChain": r"(?<![a-z0-9])langchain(?![a-z0-9])",
    "Dify": r"(?<![a-z0-9])dify(?![a-z0-9])",
    "Docker": r"(?<![a-z0-9])docker(?![a-z0-9])",
    "PostgreSQL": r"(?<![a-z0-9])postgresql(?![a-z0-9])",
    "FastAPI": r"(?<![a-z0-9])fastapi(?![a-z0-9])",
}


def _canonical(value: str, *, positions: bool = False):
    output = []
    source_positions = []
    for index, char in enumerate(value):
        for normalized in unicodedata.normalize("NFKC", char).casefold():
            if not normalized.isspace():
                output.append(normalized)
                source_positions.append(index)
    result = "".join(output)
    return (result, source_positions) if positions else result


def _ground_quote(quote, text):
    if quote in text:
        return quote
    needle = _canonical(quote)
    haystack, positions = _canonical(text, positions=True)
    if not needle or (offset := haystack.find(needle)) < 0:
        return None
    return text[positions[offset] : positions[offset + len(needle) - 1] + 1]


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
    first_section = re.search(r"岗位职责|任职要求|职位要求|学历要求|专业要求", text)
    for item in evaluation.requirements:
        quote = _ground_quote(item.quote, text)
        if quote is None:
            issues.append("quote_not_in_source")
            continue
        key = _canonical(quote)
        if key in seen:
            continue
        seen.add(key)
        category = item.category
        if (
            re.search(r"学历|本科|大专|硕士|博士|毕业生|毕业年份", quote)
            and category == "technical"
        ):
            category = "hard"
            issues.append("category_conflict")
        if category == "technical" and re.search(r"薪资|月薪|年薪|五险|公积金|年终奖", quote):
            category = "other"
            issues.append("category_conflict")
        if category == "technical":
            count = sum(bool(re.search(pattern, quote, re.I)) for pattern in CONCEPT_PATTERNS)
            is_or = bool(re.search(r"其中之一|至少一种|任一|或|择一", quote))
            if (
                count > 1
                and re.search(r"和|及|、|，|,|并|与", quote)
                and (not is_or or bool(re.search(r"数据结构|算法|编码规范", quote, re.I)))
            ):
                issues.append("unsplit_technical_requirement")
        if (
            re.search(
                r"沟通能力|团队协作|责任心|执行力|逻辑思维|审美能力|(?<!机器)(?<!深度)学习能力",
                quote,
            )
            and category == "technical"
        ):
            category = "soft"
            issues.append("category_conflict")
        status = item.status
        refs = []
        quote_position = text.find(quote)
        line_start = text.rfind("\n", 0, quote_position) + 1
        line_end = text.find("\n", quote_position)
        source_line = text[line_start : line_end if line_end >= 0 else len(text)]
        direct_tag_count = sum(
            bool(re.search(pattern, quote, re.I)) for pattern in DIRECT_SKILL_PATTERNS.values()
        )
        looks_like_tag = bool(
            "\t" in source_line
            or (
                (len(quote) <= 15 or direct_tag_count >= 2)
                and any(re.search(pattern, quote, re.I) for pattern in CONCEPT_PATTERNS)
                and not re.search(r"熟悉|掌握|负责|使用|开发|具备|经验|能力|参与|完成|搭建", quote)
            )
        )
        header_tag = bool(
            category == "technical"
            and first_section is not None
            and 0 <= quote_position < first_section.start()
            and looks_like_tag
        )
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
            else:
                evidence = "\n".join(
                    str(s.get("canonical_name", "")) + " " + s.get("evidence", "") for s in refs
                )
                support = [
                    bool(re.search(pattern, evidence, re.I))
                    for pattern in EVIDENCE_CONCEPT_PATTERNS
                    if re.search(pattern, quote, re.I)
                ]
                mentioned = [
                    name
                    for name, pattern in DIRECT_SKILL_PATTERNS.items()
                    if re.search(pattern, quote, re.I)
                ]
                referenced_names = "\n".join(str(skill.get("canonical_name", "")) for skill in refs)
                named_support = [
                    bool(re.search(DIRECT_SKILL_PATTERNS[name], referenced_names, re.I))
                    for name in mentioned
                ]
                supported = (not support or all(support)) and (
                    not named_support or (any(named_support) if is_or else all(named_support))
                )
                if not supported:
                    status = "unknown"
                    issues.append("unsupported_skill_concept")
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
        elif category == "hard":
            value = profile
            for part in (item.profile_field or "").split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if value is None or value == "" or value == [] or value == {}:
                status = "unknown"
        weight = (
            (3 if item.importance == "required" else 1)
            if category == "technical" and not header_tag
            else 0
        )
        items.append(
            {
                **item.model_dump(),
                "quote": quote,
                "category": category,
                "status": status,
                "weight": weight,
                "matched": status == "matched",
                "skill_evidence": refs,
                "scoring_exclusion": "header_skill_tags" if header_tag else None,
            }
        )
    technical = [
        item
        for item in items
        if item["category"] == "technical" and item["scoring_exclusion"] is None
    ]
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
