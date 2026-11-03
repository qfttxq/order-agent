"""模型交叉验证（可选增强）。

这个模块的存在方式很关键：它是**可选的第二意见，不是主判决**。

理由来自这个工具本身的使用场景。如果让模型直接输出「这是 K 类，报价 2 万」，
会有三个具体问题：

1. 不可复现——同一段需求今天判 K、明天判 A，你没法向客户解释
2. 不可评测——测出来的准确率每次都不同，指标没有意义
3. 不可兜底——模型挂了、余额没了、网络断了，工具就完全不能用了

所以流程是：规则先出结论 → 模型独立判一次 → 两者不一致时标注冲突，
交给人看一眼。冲突本身是有价值的信号，往往说明这段需求确实是混合型，
或者写得太含糊。

没有 API Key 时这个模块静默返回 None，整条链路照常工作。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .models import Brief, Kind, Signals, Verdict

DEFAULT_ENDPOINT = "https://api.siliconflow.cn/v1/chat/completions"
DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"
TIMEOUT = 30

PROMPT = """你是软件外包的需求分类助手。只做一件事：判断这段需求属于哪一类。

类别定义：
- K：知识库 / 文档问答 / 检索增强。核心是「让一堆资料变得能问」。
- A：Agent / 流程自动化。核心是「把一串人工步骤变成自动执行」。
- D：数据处理。核心是「把数据搬过来、洗一遍、整理成表」。
- X：不建议接。核心是「靠模拟点击界面完成」，或「纯人力标注」。

判断规则：
1. 看客户真正的痛点，不要看关键词。提到"文档"不等于 K 类，可能只是附了几份 SOP。
2. 如果两类都明显，选**预算主要花在哪**的那一类。
3. 拿不准时选 X 以外的、最接近的一类，不要自创类别。

参考例子：
需求："我们有 300 份产品手册，客服每次都要翻半天，想做个能直接问的。" → K
需求："每天把抖店的订单导出来，一条条录进 ERP，两个人做两小时，想自动。" → A
需求："要个机器人自动点网页上的按钮，把数据填进去。" → X

只输出 JSON，不要任何解释文字：
{"kind": "K|A|D|X", "confidence": 0到100, "reason": "20字以内的判断依据"}

需求原文：
---
{text}
---"""


@dataclass
class Opinion:
    kind: Kind
    confidence: int
    reason: str
    model: str


def available() -> bool:
    return bool(_api_key())


def _api_key() -> str:
    return os.environ.get("SILICONFLOW_API_KEY", "").strip()


def review(brief: Brief, signals: Signals) -> Opinion | None:
    """让模型独立判一次。任何失败都返回 None，绝不抛出。"""
    key = _api_key()
    if not key:
        return None

    model = os.environ.get("ORDER_AGENT_MODEL", DEFAULT_MODEL)
    endpoint = os.environ.get("ORDER_AGENT_ENDPOINT", DEFAULT_ENDPOINT)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT.replace("{text}", brief.text)}],
        "temperature": 0,
        "max_tokens": 200,
    }

    try:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError):
        return None

    return _parse(content, model)


def _parse(content: str, model: str) -> Opinion | None:
    """严格解析。

    这里不做「猜一个最接近的」这种容错——如果模型返回了 K/A/D/X 之外的东西，
    说明它没按格式来，那它的判断也不可信，直接丢弃比勉强采用更安全。
    """
    match = re.search(r"\{[^{}]*\}", content, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    raw_kind = str(data.get("kind", "")).strip().upper()[:1]
    if raw_kind not in {"K", "A", "D", "X"}:
        return None

    try:
        confidence = int(float(data.get("confidence", 50)))
    except (TypeError, ValueError):
        confidence = 50

    return Opinion(
        kind=Kind(raw_kind),
        confidence=max(0, min(100, confidence)),
        reason=str(data.get("reason", "")).strip()[:60],
        model=model,
    )


def merge(verdict: Verdict, opinion: Opinion | None) -> Verdict:
    """把模型意见并进判定结果。

    不一致时**保留规则结论**，只标注冲突。原因：规则结论能指出依据哪几句话，
    而模型给的理由常常是「客户提到了文档」这种含糊说法。在有争议时，
    能拿出依据的那一方更适合作为对外结论。
    """
    if opinion is None:
        verdict.llm_note = "未启用模型校验（无 API Key 或调用失败），仅规则判定"
        return verdict

    verdict.llm_kind = opinion.kind
    if opinion.kind == verdict.kind:
        verdict.llm_note = f"模型判定一致（{opinion.kind.value}，{opinion.confidence}%）：{opinion.reason}"
        return verdict

    verdict.conflict = True
    verdict.confidence = round(max(0.3, verdict.confidence - 0.15), 2)
    verdict.llm_note = (
        f"模型判定为 {opinion.kind.value}（{opinion.confidence}%）：{opinion.reason}。"
        f"与规则结论不一致，建议人工复核后再报价"
    )
    return verdict
