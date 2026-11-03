"""类型判定与匹配度打分。

判定逻辑刻意做成「加权计分 + 阈值」，而不是让模型直接说一个类型。

原因：模型对类型判断的稳定性远不如想象。同一段需求换个说法、或者提示词里
多一句「请仔细分析」，结论就可能从 K 漂到 A。而类型判错在这里的代价很具体——
K 类按文档量报价、A 类按流程条数报价，判错就是报错价，报高了丢单、报低了亏工时。

所以这里的分工是：规则给结论，模型给第二意见，冲突时人工看一眼。
模型永远不单独决定类型。
"""

from __future__ import annotations

from .models import Brief, Fit, Kind, Signals, Verdict

# 权重。数值不是拍脑袋：3 分的是「这类需求的定义性特征」，
# 1 分的是「可能只是顺口提到」。比如「文档」出现在 A 类需求里很常见
# （要做流程，附了几份 SOP），但它不足以说明这是知识库需求。
WEIGHTS: dict[str, tuple[str, int]] = {
    # 信号名 -> (归属类型, 权重)
    "kb": ("K", 3),
    "documents": ("K", 2),
    "qa_need": ("K", 3),
    "accuracy": ("K", 1),
    "workflow": ("A", 3),
    "manual_pain": ("A", 2),
    "integration": ("A", 3),
    "trigger": ("A", 1),
    "data_ops": ("D", 4),
}

# 一份需求里提到多少种业务系统，按「系统数 × 该权重」加分，上限 3 个。
# 超过 3 个不再加分，因为对接难度已经进入另一个量级，由报价层单独处理。
SYSTEM_WEIGHT = 2
SYSTEM_CAP = 3

# 客户明确说出「知识库 / 问答系统」时，K 保底加的分。
# 理由：「知识库」这个词本身宣告了项目性质，里面出现的「自动同步文档」「对接企微」
# 都是这个项目的附属功能，不是独立的流程自动化需求。
# 不加这条的实测后果：「知识库 + 自动同步飞书文档」被判成 A 类，
# 报价从 3.8 万跳到 8 万，客户直接被吓走。
KB_DECLARED_BONUS = 3

# 判定门槛：主类型必须达到这个分，否则归为「信息不足」，按最低档诊断单处理。
MIN_SCORE = 3

# 次类型分数达到主类型的这个比例，视为混合需求，需要提醒分期。
MIXED_RATIO = 0.6

# 报价下限。低于这个数的单，沟通成本会吃掉全部利润。
PRICE_FLOOR = 3000


def classify(brief: Brief, signals: Signals) -> Verdict:
    """给出类型、置信度、理由和匹配度。"""
    scores = _score(signals)

    # 拒单信号优先于一切：RPA / 模拟点击类的活，工时会翻倍且稳定性无法承诺
    if signals.has("ui_automation"):
        return Verdict(
            kind=Kind.X,
            confidence=0.95,
            reasons=[f"命中拒单信号：「{signals.first('ui_automation')}」"],
            fit=Fit.LOW,
            fit_reasons=["模拟点击 / 界面自动化类，稳定性依赖对方系统版本，无法承诺效果"],
        )

    # 纯标注需求才是拒单；如果是「知识库 + 需要标注」，那是 K 类附带的计价项
    if signals.has("labeling") and not (signals.has("kb") or signals.has("documents")):
        return Verdict(
            kind=Kind.X,
            confidence=0.9,
            reasons=[f"命中拒单信号：「{signals.first('labeling')}」"],
            fit=Fit.LOW,
            fit_reasons=["纯人力标注活，与开发能力不匹配，且工时不可控"],
        )

    if not scores or max(scores.values()) < MIN_SCORE:
        # 兜底不能一律判 K。「没接口 + 只能人工在系统里敲」这类需求几乎没有业务关键词，
        # 但它们根本不是知识库需求——判成 K 会给出一份完全无关的报价单。
        if signals.has("no_api"):
            return Verdict(
                kind=Kind.X,
                confidence=0.6,
                reasons=[f"对方系统无接口：「{signals.first('no_api')}」"],
                fit=Fit.LOW,
                fit_reasons=[
                    "没有人可以对接的接口，也没有描述出可导出的数据路径，"
                    "只能走界面操作；先确认能否改用导出文件/数据库直连"
                ],
            )
        return Verdict(
            kind=Kind.K,
            confidence=0.2,
            reasons=["需求信息量不足，无法判断类型，按最低档诊断单处理"],
            fit=Fit.LOW,
            fit_reasons=["缺少可判断的具体信息，先做一轮需求澄清再报价"],
        )

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_kind, top_score = ranked[0]
    kind = Kind(top_kind)

    reasons = _reasons(signals, top_kind)
    confidence = _confidence(ranked)

    second_kind, second_score = (ranked[1] if len(ranked) > 1 else (None, 0))
    if second_kind and second_score and top_score and second_score / top_score >= MIXED_RATIO:
        reasons.append(
            f"同时存在 {Kind(second_kind).label} 的明显特征，属于混合需求："
            f"建议一期只做 {Kind(top_kind).label} 部分，二期再谈另一部分"
        )

    fit, fit_reasons = _fit(brief, signals, kind)

    return Verdict(kind=kind, confidence=confidence, reasons=reasons, fit=fit, fit_reasons=fit_reasons)


def _score(signals: Signals) -> dict[str, int]:
    scores: dict[str, int] = {}
    for name, (kind, weight) in WEIGHTS.items():
        if signals.has(name):
            scores[kind] = scores.get(kind, 0) + weight

    if signals.has("kb"):
        scores["K"] = scores.get("K", 0) + KB_DECLARED_BONUS

    system_count = min(len(signals.business_systems), SYSTEM_CAP)
    if system_count:
        scores["A"] = scores.get("A", 0) + system_count * SYSTEM_WEIGHT

    return scores


def _reasons(signals: Signals, top_kind: str) -> list[str]:
    """把命中项翻译成人话，每条都带原句片段，方便直接回给客户。

    这里要处理一个很影响观感的问题：一句需求往往同时命中好几个信号，
    如果每条理由都引用整句，报告里会出现三条一模一样的话，客户一眼就看出是模板。

    解法是按关键字就地截取片段——「有现成文档语料：「…300 份产品手册…」」
    和「有问答场景：「…想做个能直接问的…」」来自同一句，但读起来是两个独立依据。
    """
    used: set[str] = set()
    picked: list[tuple[str, str]] = []

    for name, (kind, _weight) in WEIGHTS.items():
        if kind != top_kind or not signals.has(name):
            continue
        fragments = signals.hits.get(name, [])
        keywords = signals.keywords.get(name, [])
        candidates = [
            _snippet(fragment, keyword)
            for fragment in fragments[:2]
            for keyword in (keywords or [""])
        ]
        chosen = next((c for c in candidates if c not in used), candidates[0] if candidates else "")
        if chosen:
            used.add(chosen)
        picked.append((name, chosen))

    if top_kind == "A" and signals.business_systems:
        picked.append(("systems", "提到系统：" + "、".join(signals.business_systems[:5])))

    return [f"{_SIGNAL_LABEL.get(name, name)}：「{fragment}」" for name, fragment in picked]


def _snippet(sentence: str, keyword: str, width: int = 16) -> str:
    """按关键字截取片段，两端补省略号。没有关键字时截取开头。"""
    if not keyword:
        return sentence[: width * 2] + ("…" if len(sentence) > width * 2 else "")

    index = sentence.find(keyword)
    if index < 0:
        return sentence[: width * 2] + ("…" if len(sentence) > width * 2 else "")

    center = index + len(keyword) // 2
    start = max(0, center - width)
    end = min(len(sentence), center + width)
    text = sentence[start:end]
    return ("…" if start > 0 else "") + text + ("…" if end < len(sentence) else "")


_SIGNAL_LABEL = {
    "kb": "知识库/检索诉求",
    "documents": "有现成文档语料",
    "qa_need": "有明确的问答/查资料场景",
    "accuracy": "关注答案准确性",
    "workflow": "有流程自动化诉求",
    "manual_pain": "描述了重复人工劳动",
    "integration": "要求系统对接",
    "trigger": "描述了触发条件",
    "data_ops": "属于数据采集/清洗类",
}


def _confidence(ranked: list[tuple[str, int]]) -> float:
    """主力类型占总分的比例，再压到 0.35–0.95。

    不做成「模型置信度」那种数字，是因为它必须能用一句话解释：
    「K 的分数占比 78%，所以判 K」。这样人工复核时能直接看出该不该推翻。
    """
    top = ranked[0][1]
    total = sum(score for _kind, score in ranked) or 1
    ratio = top / total
    return round(min(0.95, max(0.35, ratio)), 2)


def _fit(brief: Brief, signals: Signals, kind: Kind) -> tuple[Fit, list[str]]:
    """匹配度：这一单值不值得投入时间去谈。"""
    risks: list[str] = []
    score = 2

    # 加分项：信息具体，说明客户想过
    if signals.doc_count:
        score += 1
    if len(signals.systems) >= 2:
        score += 1
    if signals.budget_in_text and signals.budget_in_text >= PRICE_FLOOR * 3:
        score += 1

    # 减分项
    if len(signals.vague_markers) >= 3:
        score -= 2
        risks.append(f"需求描述含 {len(signals.vague_markers)} 处模糊表述，变更加工风险高")
    if brief.length < 60:
        score -= 1
        risks.append("需求描述过短，关键信息缺失，先做澄清再谈价")
    if signals.has("scope_creep"):
        score -= 1
        risks.append("出现「顺便/还要」等扩范围表述，必须在一期范围里明确写死")
    if signals.has("urgency"):
        risks.append("客户提到时间紧，需评估自己的排期是否接得住")
    if signals.has("no_api"):
        risks.append(
            "客户明确说对方系统没有接口：先确认能否改用导出文件/数据库直连，"
            "否则只能退化为界面操作，性质和报价都要重新评估"
        )
    if signals.has("onprem"):
        risks.append("要求私有化/内网部署，需额外确认硬件与运维责任")

    if signals.budget_in_text is not None and signals.budget_in_text < PRICE_FLOOR:
        score = 0
        risks.append(
            f"客户预算 ¥{signals.budget_in_text:,} 低于接单下限 ¥{PRICE_FLOOR:,}，"
            f"沟通成本会吃掉利润"
        )

    if kind is Kind.X:
        return Fit.LOW, risks or ["类型不匹配"]

    fit = Fit.HIGH if score >= 4 else Fit.MEDIUM if score >= 2 else Fit.LOW
    return fit, risks
