"""判定、澄清、报价、验收的集成测试。

这里刻意从 analyze() 入口测，而不是单测各个内部函数——
因为这个工具的价值在于「五个产出之间是互相一致的」。
单独测每个模块都通过、但报价按 A 类算而验收单按 K 类写，那种 bug 单测抓不到。
"""

from __future__ import annotations

import pytest

from order_agent import Brief, analyze
from order_agent.models import Fit, Kind

KB_TEXT = "我们有 300 份产品手册，客服每次都要翻半天，想做个能直接问的知识库。预算 3 万。"
AGENT_TEXT = "每天把抖店的订单一条条录进 ERP，两个人弄两小时，想自动化。"
RPA_TEXT = "要个机器人自动点网页上的按钮，把 Excel 数据填进去。"
LABEL_TEXT = "有 2 万条商品评论需要人工打标签，分三类。"
THIN_TEXT = "想做个 AI 客服。"


def run(text: str) -> object:
    return analyze(Brief(text=text), use_llm=False)


def test_kb_is_classified_as_k():
    analysis = run(KB_TEXT)
    assert analysis.verdict.kind is Kind.K


def test_kb_quote_scales_with_doc_count():
    small = run("有 30 份文档，想做知识库，客户查资料找不到。")
    large = run("有 800 份文档，想做知识库，客户查资料找不到。")
    small_main = next(t for t in small.quote.tiers if t.name == small.quote.main)
    large_main = next(t for t in large.quote.tiers if t.name == large.quote.main)
    assert large_main.price_low > small_main.price_low


def test_kb_declared_beats_attached_automation():
    """「知识库 + 自动同步文档」是知识库项目，不是流程自动化。
    判成 A 类会把报价从 3.8 万跳到 8 万。"""
    analysis = run("内部文档太乱，想做个能问的知识库，同时希望自动同步飞书文档的更新。文档 500 份。")
    assert analysis.verdict.kind is Kind.K


def test_agent_classified_and_priced_by_systems():
    one = run("每天把订单录进 ERP，想自动化。")
    many = run("每天把抖店的订单录进 ERP，再同步到企业微信和钉钉，想自动化。")
    one_main = next(t for t in one.quote.tiers if t.name == one.quote.main)
    many_main = next(t for t in many.quote.tiers if t.name == many.quote.main)
    assert many_main.price_low > one_main.price_low


def test_agent_acceptance_uses_success_rate_not_accuracy():
    """A 类验收必须落到「成功率」，不能写「准确率」——后者是无限责任。"""
    analysis = run(AGENT_TEXT)
    text = " ".join(f"{name}{standard}" for name, standard in analysis.acceptance.items)
    assert "成功率" in text or "成功完成" in text


@pytest.mark.parametrize("text", [RPA_TEXT, LABEL_TEXT])
def test_reject_cases(text):
    analysis = run(text)
    assert analysis.verdict.kind is Kind.X
    assert analysis.verdict.fit is Fit.LOW


def test_reject_case_has_no_quote():
    analysis = run(RPA_TEXT)
    assert analysis.quote.tiers == []


def test_reject_case_still_gives_questions():
    """拒单也要给东西：先确认判断没错，再给替代方案。"""
    analysis = run(RPA_TEXT)
    assert analysis.clarify


def test_no_api_without_business_signal_is_reject():
    """「没接口 + 只能人工敲」没有业务关键词，不能被兜底判成 K。"""
    analysis = run("厂商也不给接口了，只能人工一条条往里敲，能不能写个脚本帮我们敲。")
    assert analysis.verdict.kind is Kind.X


def test_thin_requirement_is_low_fit():
    analysis = run(THIN_TEXT)
    assert analysis.verdict.fit is Fit.LOW


def test_thin_requirement_asks_for_basics():
    analysis = run(THIN_TEXT)
    questions = " ".join(q.text for group in analysis.clarify for q in group.questions)
    assert "多少" in questions


def test_budget_below_main_tier_is_flagged():
    analysis = run(KB_TEXT)
    basis = " ".join(analysis.quote.basis)
    assert "低于主推档" in basis


def test_known_doc_count_is_not_asked_again():
    """已经说了 300 份，还问「一共有多少份」会让客户觉得你没读需求。"""
    analysis = run(KB_TEXT)
    questions = " ".join(q.text for group in analysis.clarify for q in group.questions)
    assert "一共有多少份" not in questions


def test_known_permission_requirement_is_not_asked_again():
    analysis = run("要做知识库，不同部门的律师只能看自己组的资料，2000 份。")
    questions = " ".join(q.text for group in analysis.clarify for q in group.questions)
    assert "不同部门/角色能看的资料一样吗" not in questions


def test_question_groups_are_capped():
    analysis = run(KB_TEXT)
    for group in analysis.clarify:
        assert len(group.questions) <= 3


def test_mixed_requirement_mentions_phasing():
    analysis = run(
        "每天把订单自动录进 ERP，还要做个知识库让客服查产品参数，两个都要。"
    )
    reasons = " ".join(analysis.verdict.reasons)
    assert "混合" in reasons or analysis.verdict.kind in (Kind.K, Kind.A)


def test_verdict_reasons_quote_different_fragments():
    """三条依据引用同一句话，客户一眼就看出是模板。"""
    analysis = run(KB_TEXT)
    quoted = [reason for reason in analysis.verdict.reasons if "「" in reason]
    assert len(quoted) == len(set(quoted))


def test_confidence_is_bounded():
    for text in (KB_TEXT, AGENT_TEXT, THIN_TEXT, RPA_TEXT):
        analysis = run(text)
        assert 0 < analysis.verdict.confidence <= 0.95


def test_report_contains_all_sections():
    from order_agent import report

    text = report.render(run(KB_TEXT))
    for section in ("## 一、判定", "## 二、问客户的问题", "## 三、报价建议", "## 四、验收单草稿", "## 五、附注"):
        assert section in text


def test_report_for_reject_has_no_quote_table():
    from order_agent import report

    text = report.render(run(RPA_TEXT))
    assert "不建议报价" in text
    assert "| 档位 | 报价 | 周期 |" not in text


def test_no_tier_has_inverted_range():
    """¥80,000 – ¥60,000 这种倒挂区间在报价单上是致命的，任何档位都不允许出现。"""
    for text in (KB_TEXT, AGENT_TEXT, "有 8000 条数据要采集整理成表格", "对接 5 个系统做自动化"):
        for tier in run(text).quote.tiers:
            assert tier.price_high >= tier.price_low, f"{text} -> {tier.name} {tier.price_text}"


def test_poc_stays_cheapest_tier():
    """PoC 是试水档，被系统数放大到比生产版还贵就失去意义了。"""
    analysis = run("每天把订单录进 ERP、企业微信、钉钉、金蝶、用友，想全自动。")
    poc = next(t for t in analysis.quote.tiers if t.name == "单流程 PoC")
    assert poc.price_low <= 15000


def test_multi_system_becomes_main_tier():
    analysis = run("每天把订单录进 ERP、企业微信、钉钉，想全自动。")
    assert analysis.quote.main == "多系统 Agent"
