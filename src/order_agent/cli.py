"""命令行入口。

三条命令对应三种用法：
- analyze  每来一个需求就跑一次，出报告
- batch    一次跑一批，用于评测
- verify   检查模型接口是否可用（不给 Key 也能用规则路径）
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import llm, pipeline, report
from .models import Brief, Signals


def _read_text(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8").strip()
    if args.text:
        return " ".join(args.text).strip()
    # 支持管道：cat req.txt | order-agent analyze
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    raise SystemExit("请提供需求文本：order-agent analyze \"需求内容\" 或 -f req.txt")


def _build_brief(args: argparse.Namespace) -> Brief:
    return Brief(
        text=_read_text(args),
        budget=args.budget,
        deadline_days=args.deadline,
        channel=args.channel,
    )


def _cmd_analyze(args: argparse.Namespace) -> int:
    if not args.text and not args.file and sys.stdin.isatty():
        raise SystemExit(__doc__)

    brief = _build_brief(args)
    analysis = pipeline.analyze(brief, use_llm=not args.no_llm)

    if args.json:
        output = json.dumps(_to_dict(analysis), ensure_ascii=False, indent=2)
    else:
        output = report.render(analysis)

    if args.save:
        path = Path(args.save)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
        print(f"已保存：{path}", file=sys.stderr)

    if args.quiet and args.save:
        return 0
    print(output)
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    """批量跑，输出一行的摘要。用于评测样本集。"""
    path = Path(args.file)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    for row in rows:
        brief = Brief(text=row["text"], budget=row.get("budget"), channel=row.get("id", "样本"))
        analysis = pipeline.analyze(brief, use_llm=not args.no_llm)
        results.append(
            {
                "id": row.get("id"),
                "expected": row.get("expect"),
                "predicted": analysis.verdict.kind.value,
                "confidence": analysis.verdict.confidence,
                "fit": analysis.verdict.fit.value,
                "conflict": analysis.verdict.conflict,
                "questions": sum(len(g.questions) for g in analysis.clarify),
                "top_price": max((t.price_high for t in analysis.quote.tiers), default=0),
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    print(f"模型接口：{'已配置' if llm.available() else '未配置（规则路径可用，模型校验将跳过）'}")
    if not llm.available():
        return 0
    brief = Brief(text="我们有 200 份产品手册，客服每次都要翻半天，想做个能直接问的。")
    opinion = llm.review(brief, Signals())
    if opinion is None:
        print("调用失败：请检查 API Key 与网络")
        return 1
    print(f"连通正常：{opinion.model} 判定 {opinion.kind.value}，理由「{opinion.reason}」")
    return 0


def _to_dict(analysis) -> dict:
    return {
        "brief": asdict(analysis.brief),
        "signals": {
            "doc_count": analysis.signals.doc_count,
            "systems": analysis.signals.systems,
            "vague_markers": analysis.signals.vague_markers,
            "hits": {name: fragments[:2] for name, fragments in analysis.signals.hits.items()},
        },
        "verdict": asdict(analysis.verdict),
        "clarify": [
            {
                "group": group.name,
                "questions": [asdict(question) for question in group.questions],
            }
            for group in analysis.clarify
        ],
        "quote": {
            "basis": analysis.quote.basis,
            "payment": analysis.quote.payment,
            "tiers": [asdict(tier) for tier in analysis.quote.tiers],
        },
        "acceptance": {
            "items": analysis.acceptance.items,
            "excluded": analysis.acceptance.excluded,
            "handover": analysis.acceptance.handover,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="order-agent", description="接单需求评估 Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="分析一段需求")
    analyze.add_argument("text", nargs="*", help="需求文本")
    analyze.add_argument("-f", "--file", help="从文件读取需求")
    analyze.add_argument("--budget", type=int, help="客户预算（元）")
    analyze.add_argument("--deadline", type=int, help="期望工期（天）")
    analyze.add_argument("--channel", default="程序员客栈", help="需求来源渠道")
    analyze.add_argument("--json", action="store_true", help="输出结构化 JSON")
    analyze.add_argument("--no-llm", action="store_true", help="跳过模型校验，只跑规则")
    analyze.add_argument("--save", help="保存到文件")
    analyze.add_argument("-q", "--quiet", action="store_true", help="配合 --save 时不打印")
    analyze.set_defaults(func=_cmd_analyze)

    batch = sub.add_parser("batch", help="批量分析样本集")
    batch.add_argument("-f", "--file", required=True, help="样本 JSONL 文件")
    batch.add_argument("--no-llm", action="store_true")
    batch.set_defaults(func=_cmd_batch)

    verify = sub.add_parser("verify", help="检查模型接口")
    verify.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)
