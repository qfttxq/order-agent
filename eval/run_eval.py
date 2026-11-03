"""评测脚本：跑一遍样本集，算三个指标。

为什么要专门写这个脚本、而不是「我自己看了几条觉得还行」？

因为「觉得还行」没法交付。客户问「你凭什么说你做的 Agent 能用」，
答案必须是一组可复现的数字，而不是感觉。这个脚本就是那组数字的来源——
任何人 clone 下来跑一条命令，都会得到同样的结果（规则路径下完全确定）。

三个指标的定义：
- 类型判定准确率：判对 K/A/D/X 的比例。判错的代价最直接——K 按文档量报价，
  A 按流程条数报价，判错就是报错价。
- 必问覆盖度：专家认为「不问就没法定价」的问题里，清单覆盖了多少。
  这个指标比准确率更能反映实用性：类型判对但没问到关键项，报价照样是错的。
- 报价落位：主推档的起报价是否落在合理区间内。只检查起报点，
  因为区间上限本来就该随澄清结果浮动。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from order_agent import Brief, analyze  # noqa: E402


def load_samples(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(samples: list[dict], use_llm: bool) -> list[dict]:
    results = []
    for sample in samples:
        analysis = analyze(Brief(text=sample["text"], channel=sample["id"]), use_llm=use_llm)
        questions = " ".join(q.text for group in analysis.clarify for q in group.questions)

        hit = [keyword for keyword in sample.get("must_ask", []) if keyword in questions]
        missed = [keyword for keyword in sample.get("must_ask", []) if keyword not in questions]

        main_tier = next(
            (tier for tier in analysis.quote.tiers if tier.name == analysis.quote.main),
            analysis.quote.tiers[0] if analysis.quote.tiers else None,
        )
        low, high = sample.get("price_range", [0, 0])
        price_ok = None
        if main_tier and high > 0:
            price_ok = low <= main_tier.price_low <= high

        results.append(
            {
                "id": sample["id"],
                "expect": sample["expect"],
                "predicted": analysis.verdict.kind.value,
                "correct": sample["expect"] == analysis.verdict.kind.value,
                "confidence": analysis.verdict.confidence,
                "fit": analysis.verdict.fit.value,
                "conflict": analysis.verdict.conflict,
                "llm": analysis.verdict.llm_kind.value if analysis.verdict.llm_kind else "",
                "questions": sum(len(group.questions) for group in analysis.clarify),
                "must_total": len(sample.get("must_ask", [])),
                "must_hit": len(hit),
                "missed": missed,
                "main_tier": main_tier.name if main_tier else "",
                "main_price_low": main_tier.price_low if main_tier else 0,
                "price_ok": price_ok,
            }
        )
    return results


def summarize(results: list[dict]) -> dict:
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    must_total = sum(r["must_total"] for r in results)
    must_hit = sum(r["must_hit"] for r in results)
    priced = [r for r in results if r["price_ok"] is not None]
    price_ok = sum(1 for r in priced if r["price_ok"])

    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 0,
        "must_total": must_total,
        "must_hit": must_hit,
        "coverage": must_hit / must_total if must_total else 0,
        "price_total": len(priced),
        "price_ok": price_ok,
        "price_rate": price_ok / len(priced) if priced else 0,
        "avg_questions": sum(r["questions"] for r in results) / total if total else 0,
        "conflicts": sum(1 for r in results if r["conflict"]),
    }


def render(results: list[dict], stats: dict, use_llm: bool, sample_file: str = "") -> str:
    lines = ["# 评测报告：接单需求评估 Agent", ""]
    lines.append(f"样本集：`samples/{sample_file or 'requirements.jsonl'}`，共 {stats['total']} 条构造样本。")
    lines.append(f"模型路径：{'启用（规则 + 模型交叉验证）' if use_llm else '未启用（纯规则）'}")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| 指标 | 结果 |")
    lines.append("|---|---|")
    lines.append(f"| 类型判定准确率 | {stats['correct']}/{stats['total']}（{stats['accuracy']:.0%}） |")
    lines.append(f"| 必问项覆盖度 | {stats['must_hit']}/{stats['must_total']}（{stats['coverage']:.0%}） |")
    lines.append(
        f"| 起报价落位 | {stats['price_ok']}/{stats['price_total']}（{stats['price_rate']:.0%}） |"
    )
    lines.append(f"| 平均问题条数 | {stats['avg_questions']:.1f} |")
    lines.append(f"| 判定冲突 | {stats['conflicts']} |")
    lines.append("")

    lines.append("## 逐条结果")
    lines.append("")
    lines.append("| # | 期望 | 判定 | 结果 | 匹配度 | 问题数 | 必问命中 | 主推档 | 起报 | 落位 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        mark = "通过" if r["correct"] else "**不符**"
        hit = f"{r['must_hit']}/{r['must_total']}" if r["must_total"] else "—"
        price = f"¥{r['main_price_low']:,}" if r["main_price_low"] else "不报"
        place = "—" if r["price_ok"] is None else ("OK" if r["price_ok"] else "**偏离**")
        lines.append(
            f"| {r['id']} | {r['expect']} | {r['predicted']} | {mark} | {r['fit']} | "
            f"{r['questions']} | {hit} | {r['main_tier'] or '—'} | {price} | {place} |"
        )
    lines.append("")

    missed = [(r["id"], r["missed"]) for r in results if r["missed"]]
    if missed:
        lines.append("## 漏掉的必问项")
        lines.append("")
        for sample_id, items in missed:
            lines.append(f"- `{sample_id}`：{'、'.join(items)}")
        lines.append("")

    wrong = [r for r in results if not r["correct"]]
    if wrong:
        lines.append("## 判定不符的样本")
        lines.append("")
        for r in wrong:
            lines.append(f"- `{r['id']}`：期望 {r['expect']}，判成 {r['predicted']}（置信度 {r['confidence']:.0%}）")
        lines.append("")

    lines.append("## 这份指标的边界")
    lines.append("")
    lines.append("三条必须说清楚，否则这些数字会被误读：")
    lines.append("")
    lines.append(
        "1. **样本是构造的，不是平台真实订单。** 写法参照平台需求的真实风格"
        "（口语、省略、带错别字），但内容由我编写。所以上面的准确率说明"
        "「在这个分布上表现如何」，不等于「在真实派单上 100% 准」。"
    )
    lines.append(
        "2. **必问覆盖度只测「问没问到」，不测「客户答不答」。** 真实场景里"
        "客户可能跳过一半问题，那部分损失这个指标反映不出来。"
    )
    lines.append(
        "3. **起报价落位区间是我按自己的定价策略划的**，不是市场公允价。"
        "它验证的是「公式有没有算错」，不是「这个价格客户会不会接受」。"
    )
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    use_llm = "--no-llm" not in sys.argv
    sample_file = "requirements.jsonl"
    out_name = "eval-report.md"
    for index, arg in enumerate(sys.argv):
        if arg == "--samples" and index + 1 < len(sys.argv):
            sample_file = sys.argv[index + 1]
        if arg == "--out" and index + 1 < len(sys.argv):
            out_name = sys.argv[index + 1]

    sample_path = ROOT / "samples" / sample_file
    samples = load_samples(sample_path)
    results = run(samples, use_llm=use_llm)
    stats = summarize(results)
    report = render(results, stats, use_llm, sample_file)

    out = ROOT / "reports" / out_name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    print(report)
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
