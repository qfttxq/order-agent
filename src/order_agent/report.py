"""把分析结果渲染成一份能直接用的 Markdown。

排版上有两条硬要求：

一、给客户看的部分要能整段复制。所以澄清清单和报价用引用块圈出来，
   复制过去就是一个完整的微信消息，不用再整理格式。
二、「为什么问这个」放在每个问题下面。客户看到解释会比看到问题更愿意回答，
   这直接决定澄清环节能不能拿到信息。
"""

from __future__ import annotations

from .models import Analysis, Fit, Kind


def render(analysis: Analysis) -> str:
    lines: list[str] = []
    verdict = analysis.verdict

    lines.append(f"# 需求评估：{verdict.kind.label}")
    lines.append("")
    lines.append(f"> {_headline(analysis)}")
    lines.append("")

    lines.extend(_verdict_section(analysis))
    lines.extend(_clarify_section(analysis))
    lines.extend(_quote_section(analysis))
    lines.extend(_acceptance_section(analysis))
    lines.extend(_notes_section(analysis))

    return "\n".join(lines).rstrip() + "\n"


def _headline(analysis: Analysis) -> str:
    """一句话给动作，不是给描述。

    用户拿到这份报告第一眼要知道的是「下一步做什么」，
    而不是「这是什么类型」——类型在标题里已经有了。
    """
    verdict = analysis.verdict
    if verdict.kind is Kind.X:
        return "**建议不接。** 这类活的性质与开发能力不匹配，硬接会拖垮工时且无法承诺效果。"

    if verdict.fit is Fit.LOW:
        return "**谨慎，倾向放弃。** 信息或预算不足以支撑一次正常交付，建议只做诊断或直接婉拒。"

    if verdict.fit is Fit.HIGH:
        target = next((tier for tier in analysis.quote.tiers if tier.name == analysis.quote.main), None)
        if target is None and analysis.quote.tiers:
            target = analysis.quote.tiers[0]
        budget = analysis.brief.budget or analysis.signals.budget_in_text
        if target and budget and budget < target.price_low:
            return (
                f"**值得谈，但先谈预算。** 客户预算 ¥{budget:,} 低于「{target.name}」下限 "
                f"¥{target.price_low:,}，先缩范围或分期，别直接降价。"
            )
        if target:
            return f"**建议接，直接约沟通。** 报价从「{target.name}」档起谈（{target.price_text}）。"
        return "**建议接。** 需求信息较完整，可以直接进入沟通。"

    return "**可以接，但先做一轮澄清。** 把范围压死再谈价，不要现在给确定数字。"


def _verdict_section(analysis: Analysis) -> list[str]:
    verdict = analysis.verdict
    lines = ["## 一、判定", ""]
    lines.append("| 项目 | 结果 |")
    lines.append("|---|---|")
    lines.append(f"| 类型 | {verdict.kind.label} |")
    lines.append(f"| 置信度 | {verdict.confidence:.0%} |")
    lines.append(f"| 匹配度 | {verdict.fit.label} |")
    lines.append("")

    if verdict.reasons:
        lines.append("**判定依据**（引号内为客户原话）")
        lines.append("")
        for reason in verdict.reasons:
            lines.append(f"- {reason}")
        lines.append("")

    if verdict.fit_reasons:
        lines.append("**风险点**")
        lines.append("")
        for risk in verdict.fit_reasons:
            lines.append(f"- {risk}")
        lines.append("")

    return lines


def _clarify_section(analysis: Analysis) -> list[str]:
    if not analysis.clarify:
        return []

    total = sum(len(group.questions) for group in analysis.clarify)
    lines = [f"## 二、问客户的问题（{total} 条）", ""]
    lines.append("> 以下可直接复制发给客户。已经说过的内容不重复问，只问缺失的关键项。")
    lines.append("")

    for group in analysis.clarify:
        lines.append(f"### {group.name}")
        lines.append("")
        for index, question in enumerate(group.questions, 1):
            mark = "" if question.required else "（可不答）"
            lines.append(f"{index}. {question.text}{mark}")
            lines.append(f"   <sub>为什么问：{question.why}</sub>")
        lines.append("")

    return lines


def _quote_section(analysis: Analysis) -> list[str]:
    quote = analysis.quote
    if not quote.tiers:
        return ["## 三、报价", "", "不建议报价。", ""]

    lines = ["## 三、报价建议", ""]
    lines.append("| 档位 | 报价 | 周期 |")
    lines.append("|---|---|---|")
    for tier in quote.tiers:
        days = f"{tier.days} 天" if tier.name != "按月维护" else "每月"
        lines.append(f"| {tier.name} | {tier.price_text} | {days} |")
    lines.append("")

    for tier in quote.tiers:
        lines.append(f"**{tier.name}** — 含：{'；'.join(tier.includes)}")
        lines.append("")
        lines.append(f"不含：{'；'.join(tier.excludes)}")
        lines.append("")

    lines.append("**报价依据**")
    lines.append("")
    for item in quote.basis:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("**付款方式**")
    lines.append("")
    for item in quote.payment:
        lines.append(f"- {item}")
    lines.append("")

    main = max(quote.tiers, key=lambda tier: tier.price_high)
    net = int(main.price_high * quote.net_ratio)
    lines.append(
        f"> 到手参考：平台单扣 11.4%（服务费 5% + 税费 6.36%），"
        f"最高档 ¥{main.price_high:,} 实收约 ¥{net:,}；私单不扣，但需自行承担协议与收据。"
    )
    lines.append("")

    return lines


def _acceptance_section(analysis: Analysis) -> list[str]:
    acceptance = analysis.acceptance
    if analysis.verdict.kind is Kind.X:
        return [
            "## 四、验收单",
            "",
            "不适用——建议不接的需求不生成验收单。",
            "",
            "如果客户能改成「导出文件 → 处理后回传」这类不依赖界面的路径，"
            "按新的需求描述重新跑一次，再决定是否报价。",
            "",
        ]
    if not acceptance.items:
        return []

    lines = ["## 四、验收单草稿", ""]
    lines.append("> 每一条都能由一个第三方独立判断通过与否。模糊表述一律不写进验收单。")
    lines.append("")
    lines.append("| 验收项 | 判定标准 |")
    lines.append("|---|---|")
    for name, standard in acceptance.items:
        lines.append(f"| {name} | {standard} |")
    lines.append("")

    lines.append("**不包含**")
    lines.append("")
    for item in acceptance.excluded:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("**交付物**")
    lines.append("")
    for item in acceptance.handover:
        lines.append(f"- {item}")
    lines.append("")

    return lines


def _notes_section(analysis: Analysis) -> list[str]:
    lines = ["## 五、附注", ""]
    lines.append(f"- 渠道：{analysis.brief.channel}")

    if analysis.brief.budget:
        lines.append(f"- 客户自述预算：¥{analysis.brief.budget:,}")

    if analysis.signals.systems:
        lines.append(f"- 提到的系统：{'、'.join(analysis.signals.systems)}")

    if analysis.signals.doc_count:
        lines.append(
            f"- 数据规模：约 {analysis.signals.doc_count:,} {analysis.signals.doc_unit}"
        )

    lines.append(f"- 类型判定：{analysis.verdict.llm_note}")

    if analysis.verdict.conflict:
        lines.append("")
        lines.append(
            "> **存在判定冲突。** 规则和模型给出了不同的类型。"
            "这不是要你二选一——它通常说明这段需求本身是混合型的，"
            "或者关键信息没写清。先跑一轮澄清，再决定按哪一类报价。"
        )

    lines.append("")
    return lines
