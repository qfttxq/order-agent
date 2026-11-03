"""把各个阶段串起来。

单独一个文件而不是塞进 CLI，是为了让「一次分析」可被程序调用——
评测脚本要循环跑 12 条样本，将来接表单、接微信机器人也是调这个函数，
不需要经过命令行。
"""

from __future__ import annotations

from . import acceptance, clarify, classify, llm, quote, signals
from .models import Analysis, Brief


def analyze(brief: Brief, use_llm: bool = True) -> Analysis:
    extracted = signals.extract(brief.text)
    verdict = classify.classify(brief, extracted)

    if use_llm:
        verdict = llm.merge(verdict, llm.review(brief, extracted))
    else:
        verdict.llm_note = "已跳过模型校验（--no-llm），仅规则判定"

    return Analysis(
        brief=brief,
        signals=extracted,
        verdict=verdict,
        clarify=clarify.build(verdict.kind, extracted),
        quote=quote.build(verdict.kind, extracted, brief),
        acceptance=acceptance.build(verdict.kind, extracted),
    )
