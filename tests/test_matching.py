import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from app.matching import Criterion, Evaluation, assess
from app.vision import Vision

PROFILE = {
    "skills": [
        {
            "canonical_name": "Python",
            "level": "proficient",
            "years": "2",
            "evidence": "Python后端项目",
        },
        {"canonical_name": "Docker", "level": "basic", "years": "1", "evidence": "Docker部署项目"},
    ],
    "education": {"degree": "本科"},
}
POLICY = {"threshold": 70, "comparison": "gte"}


def requirement(quote, **kwargs):
    return Criterion(
        quote=quote,
        category=kwargs.pop("category", "technical"),
        importance=kwargs.pop("importance", "required"),
        status=kwargs.pop("status", "matched"),
        **kwargs,
    )


def evaluate(*items, text=None, policy=None, profile=None):
    return assess(
        Evaluation(uncertain=False, requirements=list(items)),
        text or "\n".join(x.quote for x in items),
        profile or PROFILE,
        policy or POLICY,
    )


def test_only_technical_requirements_enter_denominator():
    result = evaluate(
        requirement("熟悉Python", skill_indices=[0]),
        requirement("本科", category="hard", profile_field="education.degree"),
        requirement("沟通能力", category="soft"),
        requirement("五险一金", category="other"),
    )
    assert result["score"] == 100
    assert result["weights"]["total"] == 3
    assert result["eligible"] is True


def test_misclassified_degree_is_excluded_and_requires_review():
    result = evaluate(requirement("Python", skill_indices=[0]), requirement("本科"))
    assert result["score"] == 100
    assert result["weights"]["total"] == 3
    assert result["needs_review"]
    assert not result["eligible"]
    assert "category_conflict" in result["reasons"]


def test_missing_evidence_is_unknown_not_unmatched():
    result = evaluate(
        requirement("Python", skill_indices=[0]), requirement("Java", status="unmatched")
    )
    assert result["score"] == 50
    assert result["score_upper_bound"] == 100
    assert result["requirements"][1]["status"] == "unknown"
    assert result["needs_review"]


@pytest.mark.parametrize("field,value", [("min_level", "expert"), ("min_years", 3)])
def test_explicit_insufficient_level_or_years_is_unmatched(field, value):
    result = evaluate(requirement("Python", skill_indices=[0], **{field: value}))
    assert result["score"] == 0
    assert result["score_upper_bound"] == 0
    assert result["requirements"][0]["status"] == "unmatched"


def test_missing_hard_fact_blocks_even_full_technical_score():
    result = evaluate(
        requirement("Python", skill_indices=[0]),
        requirement("2026届毕业生", category="hard", profile_field="education.graduation_year"),
    )
    assert result["score"] == 100
    assert result["hard_conditions"] == "unknown"
    assert not result["eligible"]


def test_invalid_evidence_quote_and_indices_cannot_pass():
    result = evaluate(requirement("Python", skill_indices=[99]))
    assert not result["eligible"]
    assert "invalid_skill_reference" in result["reasons"]
    result = evaluate(requirement("Python", skill_indices=[0]), text="只要求Java")
    assert result["score"] is None
    assert "quote_not_in_source" in result["reasons"]
    with pytest.raises(ValidationError):
        requirement("Python", skill_indices=[True])


def test_unsplit_and_requirements_and_multiple_roles_require_review():
    result = evaluate(requirement("Python和Java", skill_indices=[0]))
    assert "unsplit_technical_requirement" in result["reasons"]
    assert not result["eligible"]
    result = evaluate(
        requirement("Python", skill_indices=[0]), text="岗位一：Python\n岗位二：视频制作"
    )
    assert "multiple_roles_require_selection" in result["reasons"]
    assert result["score"] is None


def test_or_clause_accepts_a_supported_option():
    result = evaluate(requirement("Python、Java任一语言", skill_indices=[0]))
    assert result["score"] == 100
    assert result["eligible"]


def test_duplicate_quotes_are_safely_deduplicated():
    item = requirement("Python", skill_indices=[0])
    result = evaluate(item, item)
    assert result["weights"]["total"] == 3
    assert result["eligible"]


@pytest.mark.parametrize("comparison,eligible", [("gte", True), ("gt", False)])
def test_exact_seventy_with_known_unmatched_skills(comparison, eligible):
    profile = {
        "skills": PROFILE["skills"]
        + [{"canonical_name": "SQL", "level": "proficient", "years": 2, "evidence": "SQL项目"}]
    }
    result = evaluate(
        requirement("Python", skill_indices=[0]),
        requirement("SQL", skill_indices=[2]),
        requirement("精通Docker", skill_indices=[1], min_level="expert"),
        requirement("Docker", skill_indices=[1], importance="preferred"),
        profile=profile,
        policy={"threshold": 70, "comparison": comparison},
    )
    assert result["score"] == 70
    assert result["eligible"] is eligible


def test_full_text_evaluation_uses_text_only_request(monkeypatch):
    requests = []
    payload = Evaluation(uncertain=False, requirements=[requirement("Python", skill_indices=[0])])

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, *a):
            return json.dumps(
                {"choices": [{"message": {"content": payload.model_dump_json()}}]}
            ).encode()

    def call(request, **kwargs):
        requests.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr("app.vision.urlopen", call)
    vision = Vision(
        SimpleNamespace(
            vision_base_url="https://example.test/v1",
            vision_model="test",
            vision_api_key=SecretStr("test"),
        )
    )
    assert vision.evaluate_text("Python", PROFILE) == payload
    content = requests[0]["messages"][1]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"


def test_rag_reference_cannot_claim_finetuning_without_evidence():
    profile = {
        "skills": [
            {
                "canonical_name": "RAG",
                "evidence": "使用RAG构建知识库",
                "level": "proficient",
                "years": 2,
            }
        ]
    }
    result = evaluate(requirement("熟悉大模型微调", skill_indices=[0]), profile=profile)
    assert result["requirements"][0]["status"] == "unknown"
    assert "unsupported_skill_concept" in result["reasons"]
    assert not result["eligible"]


def test_language_evidence_cannot_cover_algorithms_and_coding_standards():
    quote = "熟悉Python，熟练掌握数据结构和常用算法设计，具备良好的编码规范"
    result = evaluate(requirement(quote, skill_indices=[0]))
    assert result["requirements"][0]["status"] == "unknown"
    assert "unsupported_skill_concept" in result["reasons"]
    assert "unsplit_technical_requirement" in result["reasons"]


def test_wrong_canonical_skill_reference_is_rejected_even_when_evidence_mentions_name():
    profile = {
        "skills": [
            {
                "canonical_name": "Python",
                "level": "proficient",
                "years": 2,
                "evidence": "Python后端项目",
            },
            {
                "canonical_name": "Spring",
                "level": "basic",
                "years": None,
                "evidence": "熟悉Spring、Git等工具",
            },
        ]
    }
    result = evaluate(requirement("使用Python和Git开发", skill_indices=[0, 1]), profile=profile)
    assert result["requirements"][0]["status"] == "unknown"
    assert "unsupported_skill_concept" in result["reasons"]


def test_leading_skill_tags_are_visible_but_excluded_from_score():
    text = "职位描述\nC/C++ Java Python\n学历要求：\n本科\n专业要求：\n熟悉Python开发"
    result = evaluate(
        requirement("C/C++ Java Python", skill_indices=[0]),
        requirement("熟悉Python开发", skill_indices=[0]),
        text=text,
    )
    assert result["requirements"][0]["scoring_exclusion"] == "header_skill_tags"
    assert result["requirements"][0]["weight"] == 0
    assert result["weights"]["total"] == 3
    assert result["score"] == 100


def test_pre_section_mission_is_not_mistaken_for_keyword_tags():
    quote = "将工程知识转化为Agent、RAG和自动化工具"
    text = f"岗位使命：\n{quote}\n岗位职责：\n1. 交付原型"
    result = evaluate(requirement(quote), text=text)
    assert result["requirements"][0]["scoring_exclusion"] is None
    assert result["weights"]["total"] == 3


def test_formatting_only_quote_change_is_grounded_to_source():
    source = "熟悉 Python、Git 及 API 开发"
    result = evaluate(requirement("熟悉Python、Git及API开发", skill_indices=[0]), text=source)
    assert result["requirements"][0]["quote"] == source
    assert "quote_not_in_source" not in result["reasons"]


def test_non_scored_context_accepts_no_importance_and_stays_zero_weight():
    context = Criterion.model_validate(
        {"quote": "五险一金", "category": "other", "importance": "none", "status": "unknown"}
    )
    result = evaluate(requirement("Python", skill_indices=[0]), context)
    assert result["score"] == 100
    assert result["requirements"][1]["weight"] == 0
