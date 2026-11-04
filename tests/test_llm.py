"""模型交叉验证的解析与合并。

`llm.py` 是唯一有外部依赖的模块，却是覆盖率最低的（30%）。
这里只测不联网的部分——真正需要守住的不是「调用成功」，而是「调用失败时不崩」。
"""

from __future__ import annotations

from order_agent import llm
from order_agent.models import Brief, Kind, Signals, Verdict


def test_null_content_returns_none():
    """模型走 tool_calls、或被安全策略拦截时，content 是 null。

    模块开头承诺「任何失败都返回 None，绝不抛出」，这条测试守住它。
    之前 null 会一路带到 re.search 里崩 TypeError，把整条 analyze() 带挂。
    """
    assert llm._parse(None, "m") is None


def test_blank_content_returns_none():
    assert llm._parse("", "m") is None
    assert llm._parse("   \n ", "m") is None


def test_valid_json_is_parsed():
    opinion = llm._parse('{"kind":"K","confidence":80,"reason":"文档问答"}', "m")
    assert opinion is not None
    assert opinion.kind is Kind.K
    assert opinion.confidence == 80
    assert opinion.model == "m"


def test_code_fence_still_parsed():
    """模型常把 JSON 包在 ``` 里，取第一个花括号块即可。"""
    opinion = llm._parse('```json\n{"kind":"A","confidence":70}\n```', "m")
    assert opinion is not None and opinion.kind is Kind.A


def test_unknown_kind_is_discarded():
    """返回 K/A/D/X 之外的东西说明它没按格式来，那它的判断也不可信。"""
    assert llm._parse('{"kind":"Z","confidence":90}', "m") is None


def test_no_free_text_fallback():
    assert llm._parse("我觉得这是知识库项目", "m") is None


def test_review_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    assert llm.review(Brief(text="随便一段需求"), Signals()) is None


def test_merge_without_opinion_keeps_rule_verdict():
    verdict = Verdict(kind=Kind.K, confidence=0.6)
    merged = llm.merge(verdict, None)
    assert merged.kind is Kind.K
    assert merged.llm_kind is None
    assert "仅规则判定" in merged.llm_note


def test_merge_conflict_lowers_confidence_and_flags():
    verdict = Verdict(kind=Kind.K, confidence=0.6)
    opinion = llm.Opinion(kind=Kind.A, confidence=80, reason="流程自动化", model="m")
    merged = llm.merge(verdict, opinion)

    assert merged.kind is Kind.K  # 有争议时保留能指出依据的那一方
    assert merged.conflict is True
    assert merged.confidence < 0.6
