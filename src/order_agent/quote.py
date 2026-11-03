"""三档报价。

两个刻意的设计：

一、只报区间，不报单价。
同一个需求，客户给的三份资料里有两份是扫描件还是一份都没有，成本能差一倍。
报一个精确数字，等于在信息不全时承担了全部不确定性。

二、K 类按文档量算，A 类按流程和系统对接数算，两套公式不能混用。
K 类的成本主体是语料处理和评测，可以随文档量线性外推；
A 类的成本主体是流程理解和异常处理，跟系统数量强相关、跟「工作量」弱相关。
用同一套公式报这两类单，必然有一类报错。
"""

from __future__ import annotations

from .models import Brief, Kind, Quote, Signals, Tier

# 平台单的到手比例：5% 服务费 + 6.36% 个税代缴 = 11.4% 被扣掉。
# 私单没有这部分，所以报告里两个数字都给，让使用者自己选渠道。
PLATFORM_NET_RATIO = 0.886

PAYMENT_TERMS = [
    "签约预付 30%，PoC / 阶段交付后 30%，验收通过后付 40%",
    "诊断版一次付清",
    "个人账户收款，双方签署书面服务协议并出具收款收据",
    "变更需求需书面确认，按增减量调整预算与周期",
]

# 私有化部署不是「加一点配置」，涉及硬件适配、内网模型、离线依赖，
# 实测普遍比云端方案多 30%–60% 工时。
ONPREM_FACTOR = 1.4


def build(kind: Kind, signals: Signals, brief: Brief) -> Quote:
    if kind is Kind.X:
        return Quote(
            tiers=[],
            basis=["类型不匹配，不建议报价"],
            payment=[],
        )

    if kind is Kind.A:
        tiers, basis, main = _agent_tiers(signals)
    elif kind is Kind.D:
        tiers, basis, main = _data_tiers(signals)
    else:
        tiers, basis, main = _kb_tiers(signals)

    if signals.has("onprem"):
        for tier in tiers:
            tier.price_low = _round(tier.price_low * ONPREM_FACTOR)
            tier.price_high = _round(tier.price_high * ONPREM_FACTOR)
        basis.append(f"要求私有化/内网部署，工时按 ×{ONPREM_FACTOR} 上调")

    if signals.has("urgency"):
        basis.append("客户时间要求紧，如需插队需另行确认排期与加急费用")

    # 客户预算优先用显式传入的，其次用文本里自己说的——现实里客户常在开头提一句预算，
    # 后面就再也不提了，这条信息比任何估算都准
    budget = brief.budget or signals.budget_in_text
    if budget and tiers:
        _note_budget_gap(budget, tiers, main, basis)

    return Quote(tiers=tiers, basis=basis, payment=PAYMENT_TERMS, net_ratio=PLATFORM_NET_RATIO, main=main)


def _kb_tiers(signals: Signals) -> tuple[list[Tier], list[str], str]:
    docs = signals.doc_count or 50
    basis = [f"按语料规模估算：{docs} {signals.doc_unit}"]

    if docs < 50:
        standard = (18000, 18000)
        basis.append("50 份以内，按标准档下限计")
    elif docs <= 200:
        standard = (18000, 35000)
        basis.append("50–200 份，标准档区间内")
    else:
        low = 35000 + (docs - 200) // 100 * 3000
        # 区间宽度至少留 25%：报价只报一个点，等于把「扫描件多、重复多、格式杂」
        # 这些还没确认的风险全揽到自己身上
        standard = (low, max(low + 3000, _round(low * 1.25)))
        basis.append(
            f"超过 200 份，按每 100 份 ¥3,000 递增：{docs} 份对应 ¥{low:,} 起"
        )

    if signals.has("permission"):
        basis.append("含权限隔离，检索需先过滤后排序，工作量计入生产档")

    tiers = [
        Tier(
            name="诊断版",
            price_low=3500,
            price_high=3500,
            days=3,
            includes=[
                "现有语料抽样体检（无标点、重复、扫描件占比）",
                "20 题评测集与基线召回率、延迟实测",
                "一页结论：问题在哪、需要多少工作量、值不值得做",
            ],
            excludes=["不含系统搭建", "不含文档清洗"],
        ),
        Tier(
            name="标准版",
            price_low=standard[0],
            price_high=standard[1],
            days=max(7, docs // 20),
            includes=[
                f"{docs} 份文档解析、清洗、切分与入库",
                "向量化 + 重排调优，交付召回率与忠实性实测",
                "30 题评测集与交付报告",
                "一次线上答疑与调优",
            ],
            excludes=[
                "不含权限隔离与后台管理界面",
                f"超出的文档按 ¥{_per_doc(docs):,}/份计",
                "不含第三方系统对接",
            ],
        ),
        Tier(
            name="生产版",
            price_low=60000,
            price_high=180000,
            days=45,
            includes=[
                "标准版全部内容",
                "权限隔离（按部门/角色过滤）",
                "管理后台：文档增删、状态监控、问答日志",
                "对接企业微信/钉钉/现有系统",
                "三个月内的问题修复",
            ],
            excludes=["不含客户侧服务器与模型采购费用", "不含持续运营与语料日常维护"],
        ),
    ]
    return tiers, basis, "标准版"


def _agent_tiers(signals: Signals) -> tuple[list[Tier], list[str], str]:
    """A 类四档。

    档位之间是**互斥的定位**，不是同一条公式的倍数关系：
    PoC 是试水，生产版是主力，多系统是另一个量级，按月在最后兜底。
    早期版本让所有档位都乘同一个系统数系数，结果三个系统时
    「单流程生产版」被算成 8 万起、上限却写成 6 万——区间直接倒挂。
    """
    systems = len(signals.business_systems) or 1
    basis = [f"按系统对接数量估算：{systems} 个系统"]

    if systems <= 1:
        prod = (20000, 35000)
        monthly = (8000, 15000)
        main = "单流程生产版"
        basis.append("单系统单流程，按单流程生产版下限计")
    elif systems == 2:
        prod = (35000, 60000)
        monthly = (8000, 15000)
        main = "单流程生产版"
        basis.append("两个系统对接，需处理双向数据一致与失败重试")
    else:
        prod = (35000, 60000)
        monthly = (15000, 25000)
        main = "多系统 Agent"
        basis.append(
            f"{systems} 个系统，属多系统 Agent，需做统一状态机、跨系统补偿与可观测"
        )

    tiers = [
        Tier(
            name="单流程 PoC",
            price_low=6000,
            price_high=15000,
            days=14,
            includes=[
                "一条主流程端到端跑通",
                "10 条真实样本测试，给出成功率",
                "一页结论：能不能做、卡在哪、生产化还要多少",
            ],
            excludes=[
                "不接生产系统，用测试数据或只读权限",
                "不含异常处理与人工接管界面",
                "不含部署与运维",
            ],
        ),
        Tier(
            name="单流程生产版",
            price_low=prod[0],
            price_high=prod[1],
            days=30,
            includes=[
                "主流程 + 异常分支的完整实现",
                "失败重试、幂等、断点续跑",
                "人工接管入口与操作日志",
                "连续 100 次执行的稳定性验收",
            ],
            excludes=[
                "不含上游系统改造（需对方配合的部分由客户协调）",
                "不含新增流程，超出按每条 ¥8,000–20,000 另计",
            ],
        ),
        Tier(
            name="多系统 Agent",
            price_low=80000,
            price_high=250000,
            days=60,
            includes=[
                "多流程编排与统一状态管理",
                "跨系统数据一致性与补偿机制",
                "可观测：执行链路追踪、成功率看板、告警",
                "权限分级与审计日志",
            ],
            excludes=["不含客户侧系统接口开发", "不含长期运维"],
        ),
        Tier(
            name="按月维护",
            price_low=monthly[0],
            price_high=monthly[1],
            days=30,
            includes=[
                "每月固定巡检与问题修复",
                "上游系统变更后的适配",
                "新增 1–2 条轻量流程",
                "月度运行报告（成功率、人工介入率、节省工时）",
            ],
            excludes=["不含重写或架构级改造"],
        ),
    ]
    return tiers, basis, main


def _data_tiers(signals: Signals) -> tuple[list[Tier], list[str], str]:
    records = signals.doc_count or 1000
    basis = [f"按处理量估算：{records} 条记录"]

    if records <= 5000:
        main = (8000, 15000)
    elif records <= 50000:
        main = (15000, 35000)
    else:
        main = (35000, 35000 + records // 50000 * 8000)

    tiers = [
        Tier(
            name="脚本交付",
            price_low=main[0],
            price_high=main[1],
            days=max(7, records // 10000 + 7),
            includes=[
                f"{records} 条数据的采集/清洗/结构化",
                "可重复运行的脚本与使用说明",
                "抽样 100 条做准确率校验并出具报告",
            ],
            excludes=["不含长期定时运行", "不含数据源变更后的维护", "不含人工补录"],
        ),
        Tier(
            name="含定时运行",
            price_low=_round(main[0] * 1.8),
            price_high=_round(main[1] * 2.2),
            days=max(14, records // 10000 + 14),
            includes=["脚本交付全部内容", "定时任务部署与失败告警", "一个月内的问题修复"],
            excludes=["不含数据源结构变更后的重构"],
        ),
        Tier(
            name="按月维护",
            price_low=4000,
            price_high=8000,
            days=30,
            includes=["每月运行保障与修复", "数据源小范围适配", "月度数据质量报告"],
            excludes=["不含新增数据源"],
        ),
    ]
    return tiers, basis, "脚本交付"


def _note_budget_gap(budget: int, tiers: list[Tier], main: str, basis: list[str]) -> None:
    """预算和报价对不上时，把话说在明面上。

    基准取「主推档」而不是「最低档」：诊断版 ¥3,500 是引流品，
    拿它当参照会出现「客户预算 3 万，提示低于最低档」这种明显不合理的判断。
    真正该比的是标准版 / 生产版——客户预算够不上主力档，才是需要预警的事。

    现实里这一步最容易犯错：客户说预算 3 万，你直接报 5 万，沟通就断了。
    正确做法是说清「你这个预算能买到什么」，把选择权交回去。
    """
    target = next((tier for tier in tiers if tier.name == main), None)
    if target is None:
        return

    if budget < target.price_low:
        gap = target.price_low - budget
        basis.append(
            f"客户预算 ¥{budget:,} 低于主推档「{main}」下限 ¥{target.price_low:,}，"
            f"差 ¥{gap:,}。三个可选做法：① 缩范围（减少文档量或砍掉对接）"
            f"② 先做诊断版，用实测数据支撑后续报价 ③ 分两期付款。不要直接降价接全部需求"
        )
    elif budget > target.price_high * 1.5:
        basis.append(
            f"客户预算 ¥{budget:,} 明显高于主推档上限 ¥{target.price_high:,}，"
            f"说明大概率还有没识别出来的需求，先问清再报价，不要按预算上限开口"
        )


def _per_doc(docs: int) -> int:
    if docs <= 50:
        return 120
    if docs <= 200:
        return 90
    return 60


def _round(value: float) -> int:
    """取整到千位。报价单上出现 ¥18,347 会显得很业余。"""
    return int(round(value / 1000) * 1000)
