"""信号抽取的测试。

这里测的都是「真实需求里踩过的坑」，不是构造出来的边界条件。
每一条对应一类会被判反的写法，注释里写了后果。
"""

from __future__ import annotations

import pytest

from order_agent import signals


def test_arabic_count_with_unit():
    result = signals.extract("有 300 份产品手册")
    assert result.doc_count == 300
    assert result.doc_unit == "份"


def test_wan_count_is_multiplied():
    result = signals.extract("大概 1.5 万份资料")
    assert result.doc_count == 15000


def test_chinese_quantity_is_parsed():
    """「两万条」不解析的话，报价会按默认 1000 条算，直接少报一个档。"""
    result = signals.extract("大概两万条工商信息")
    assert result.doc_count == 20000
    assert result.doc_unit == "条"


def test_small_count_is_ignored():
    """「3 个系统」不是语料量，不能当成文档数。"""
    result = signals.extract("要对接 3 个系统")
    assert result.doc_count is None


def test_negated_integration_is_not_integration():
    """「厂商不给接口」判断成「要求对接」的话，结论会完全反过来。"""
    result = signals.extract("老系统没有接口，只能人工在界面上敲")
    assert not result.has("integration")
    assert result.has("no_api")


def test_positive_integration_still_works():
    result = signals.extract("需要对接企业微信推送消息")
    assert result.has("integration")
    assert not result.has("no_api")


def test_system_dedup_keeps_longest():
    """「飞书」和「飞书文档」会被算成两个系统，导致报价多算一档。"""
    result = signals.extract("自动同步飞书文档的更新")
    assert "飞书文档" in result.systems
    assert "飞书" not in result.systems


def test_excel_is_not_business_system():
    """Excel 只是数据载体。算成业务系统会把数据处理单判成 Agent 单。"""
    result = signals.extract("整理成 Excel 给我们")
    assert "Excel" in result.systems
    assert result.business_systems == []


def test_budget_phrase_is_not_vague():
    """「预算大概 3 万」里的「大概」后面有数字，是给区间不是含糊。"""
    result = signals.extract("预算大概 3 万")
    assert not result.has("vague")


def test_real_vagueness_is_detected():
    result = signals.extract("需求大概就是这么个情况，你看着办")
    assert result.has("vague")


def test_budget_in_text():
    assert signals.extract("预算 2 万左右").budget_in_text == 20000


def test_keyword_recorded_for_snippet():
    """判定依据要能按关键词截片段，所以关键词必须被记下来。"""
    result = signals.extract("客服每次都要翻半天")
    assert result.keywords.get("qa_need")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("要个机器人自动点网页上的按钮", "ui_automation"),
        ("能不能用按键精灵", "ui_automation"),
        ("2 万条评论需要人工标注", "labeling"),
    ],
)
def test_reject_signals(text, expected):
    assert signals.extract(text).has(expected)
