"""生成澄清清单。

这里最容易被做成「一张 20 条的问卷」，但那样是错的。

客户发来一段需求，如果你回他二十个问题，他不会答，只会觉得你不靠谱。
真正有效的是「少问，但每一问都问在缺口上」：他已经说过的不再问，
他没说但会直接决定报价的，必须问出来。

所以每条问题带一个 gap 字段，指向一个信号名。该信号已命中，这条问题就整个消失——
不是标成「可不答」，是不出现。差别很实在：客户说了「不同部门只能看自己组的」，
你还问「能看的资料一样吗」，他一眼就知道你没读完他的需求。

每条问题还带 why，用途有三个：面试演示时能讲「为什么问这个」、
客户看到解释更愿意回答、将来整理成 FAQ 可以直接复用。
"""

from __future__ import annotations

from .models import ClarifyGroup, Kind, Question, Signals

# 每组最多几条。超过 3 条客户会开始跳着看，反而漏掉关键的。
MAX_PER_GROUP = 3


def build(kind: Kind, signals: Signals) -> list[ClarifyGroup]:
    if kind is Kind.X:
        return _for_reject()
    if kind is Kind.A:
        return _for_agent(signals)
    return _for_kb(signals)


def _for_kb(signals: Signals) -> list[ClarifyGroup]:
    corpus = [
        Question(
            "这批文档现在在哪儿、什么格式？有几个是扫描件或图片？",
            "扫描件要走 OCR，成本和准确率都和电子版差一个量级，必须先分类",
            required=True,
        ),
        Question(
            "一共有多少份？后续还会持续增加吗？",
            "总量决定切分和嵌入成本，增量决定要不要做自动入库",
            required=True,
            gap="count",
        ),
        Question(
            "后续还会持续增加吗？大概多久新增一批？",
            "增量决定要不要做自动入库和增量更新",
            required=False,
        ),
        Question(
            "同一份资料有没有多个版本、或者内容重复的情况？",
            "重复片段会让检索总是返回近似内容，必须在入库前先去重",
            required=False,
        ),
    ]

    scenario = [
        Question(
            "谁会用它、大概多少人？他们平时是为了找什么来问？",
            "使用人数决定要不要做权限和并发，使用场景决定评测题怎么出",
            required=True,
        ),
        Question(
            "能不能给我三个他们真实问过的问题？",
            "真实问题才是评测集，编出来的问题会掩盖真实短板",
            required=True,
        ),
    ]

    effect = [
        Question(
            "答不上来的时候，你希望它怎么表现——直接说不知道，还是给个相近的？",
            "这是产品决策不是技术细节，直接决定拒答阈值怎么设",
            required=True,
        ),
        Question(
            "答案需不需要标注出处（第几页、哪份文件）？",
            "要出处就得保留页码级元数据，切分策略和成本都会变",
            required=False,
            gap="accuracy",
        ),
    ]

    permission = [
        Question(
            "不同部门/角色能看的资料一样吗？",
            "要做权限隔离，检索时必须先过滤再排序，工作量差别很大",
            required=True,
            gap="permission",
        ),
        Question(
            "有没有不能上云、必须留在内网的资料？",
            "决定能不能用云端模型，可能直接改变技术方案和成本",
            required=False,
            gap="onprem",
        ),
    ]

    delivery = [
        Question(
            "最终交付形态是什么：网页、嵌在微信/企微里，还是要接进现有系统？",
            "界面和集成方式的工时差异比模型本身大得多",
            required=True,
        ),
        Question(
            "怎么算验收通过？有没有可量化的标准？",
            "没有验收标准就没有结项时点，这是私下收款最大的风险",
            required=True,
        ),
    ]

    return _assemble(
        [
            ("语料情况", corpus),
            ("使用场景", scenario),
            ("效果预期", effect),
            ("权限与合规", permission),
            ("交付与验收", delivery),
        ],
        signals,
    )


def _for_agent(signals: Signals) -> list[ClarifyGroup]:
    scope = [
        Question(
            "请把这个流程从头到尾走一遍：谁发起、经过哪几步、到哪一步算结束？",
            "A 类的范围就是流程条数，流程说不清就没法定价，也没法验收",
            required=True,
        ),
        Question(
            "现在这套流程每天/每月跑多少次？每次大概花多少人工时间？",
            "频次和人工时长决定这单对客户值多少钱，也决定你该报多少",
            required=True,
        ),
        Question(
            "有没有例外情况——比如数据不全、审批被驳回时，现在是怎么处理的？",
            "例外分支通常占总工作量的一半，客户往往想不起来说",
            required=False,
        ),
    ]

    system = [
        Question(
            "要读写哪些系统？这些系统有没有开放 API 或者能导出数据？",
            "没有 API 就只能退化成模拟点击或人工中转，性质和报价都变了",
            required=True,
        ),
        Question(
            "系统是自建的还是采购的？如果是采购的，能不能找厂商开接口？",
            "采购系统开接口要走厂商流程，可能拖一个月",
            required=False,
            gap="systems",
        ),
    ]

    authority = [
        Question(
            "Agent 能不能直接执行写操作（下单、改状态、发消息），还是只能给建议由人确认？",
            "写权限越大，出错代价越大，责任划分必须在合同里写清",
            required=True,
        ),
        Question(
            "操作日志需要留多久？出问题时要能查到哪一步吗？",
            "可追溯是生产级和 Demo 的分界线，也是你和低价竞争者的差别",
            required=False,
        ),
    ]

    failure = [
        Question(
            "如果 Agent 判断错了、造成了实际损失，责任怎么算？希望怎么处理？",
            "A 类最高频的争议点，开工前不谈清楚，验收时一定扯皮",
            required=True,
        ),
        Question(
            "出错时需要人接管吗？接管的人希望看到什么信息？",
            "人工接管界面是必做项还是可选项，差一到两周工作量",
            required=False,
        ),
    ]

    effect = [
        Question(
            "怎么算这个 Agent 做成了？是「跑通一次」，还是「连续两周不出错」？",
            "效果定义不同，验收周期差好几倍，必须此刻定下来",
            required=True,
        ),
        Question(
            "能接受的成功率是多少？（比如 100 单里允许几单要人工介入）",
            "任何 A 类都有失败率，承诺 100% 等于无限责任",
            required=True,
        ),
    ]

    compliance = [
        Question(
            "流程里会碰到客户个人信息、支付或合同数据吗？",
            "涉及个人信息就要考虑脱敏和留存，可能影响方案选型",
            required=False,
        ),
        Question(
            "数据能不能离开你们内网？",
            "决定能否用云端模型，直接改变成本和可行性",
            required=False,
            gap="onprem",
        ),
    ]

    return _assemble(
        [
            ("流程边界", scope),
            ("系统与接口", system),
            ("授权范围", authority),
            ("异常与责任", failure),
            ("效果定义", effect),
            ("数据合规", compliance),
        ],
        signals,
    )


def _for_reject() -> list[ClarifyGroup]:
    """拒单场景也要给东西：先确认判断没错，再给替代方案。"""
    return [
        ClarifyGroup(
            name="确认是否真的要拒",
            questions=[
                Question(
                    "这个流程必须靠「模拟点击界面」完成吗？对方系统能不能开放接口？",
                    "能拿到接口的话，这类活就从「不接」变成「可接」，值得再确认一次",
                    required=True,
                ),
                Question(
                    "如果必须模拟点击，界面改版后需要有人跟着改，这个维护谁负责？",
                    "模拟点击的成本主要在维护，不在开发，客户通常没算这笔",
                    required=True,
                ),
            ],
        ),
        ClarifyGroup(
            name="可提供的替代方案",
            questions=[
                Question(
                    "能不能改成「导出一份表格 → 处理后回传」？",
                    "脱离界面依赖，稳定性和报价都可控，是最常见的替代路径",
                    required=False,
                ),
            ],
        ),
    ]


def _filled(gap: str, signals: Signals) -> bool:
    """这个缺口是否已经被需求文本填上了。

    "count" 是特例：语料规模不是命中类信号，而是 Signals 上的一个独立字段，
    但它同样是「客户已经说过了」的信息，不能重复问。
    """
    if not gap:
        return False
    if gap == "count":
        return signals.doc_count is not None
    return signals.has(gap)


def _assemble(groups: list[tuple[str, list[Question]]], signals: Signals) -> list[ClarifyGroup]:
    """按缺口过滤问题，并追加由风险信号触发的问题。

    保留原顺序，只在超出上限时把非必答项换出去。
    这一点很重要：如果按「必答优先」重排，「文件是什么格式」会因为已经知道份数
    而被排到最后，但它其实是语料组里最该问的一条。
    """
    result: list[ClarifyGroup] = []
    for name, questions in groups:
        alive = [q for q in questions if not _filled(q.gap, signals)]
        picked = list(alive[:MAX_PER_GROUP])
        for candidate in alive[MAX_PER_GROUP:]:
            if not candidate.required:
                continue
            for index in range(len(picked) - 1, -1, -1):
                if not picked[index].required:
                    picked[index] = candidate
                    break
        if picked:
            result.append(ClarifyGroup(name=name, questions=picked))

    risk: list[Question] = []
    if signals.vague_markers:
        risk.append(
            Question(
                "需求里有些表述比较笼统（比如「"
                + signals.vague_markers[0][:18]
                + "…」），能不能给两三个具体例子？",
                "具体例子能把模糊需求压缩成一个可验收的范围",
                required=True,
            )
        )
    if signals.has("scope_creep"):
        risk.append(
            Question(
                "你提到的这些里，哪一件是最要紧、必须先上线的？",
                "扩范围表述必须先排序，否则一期永远做不完",
                required=True,
            )
        )
    if signals.budget_in_text is None:
        risk.append(
            Question(
                "这个项目的预算区间大概是多少？",
                "预算决定方案档位——同一件事，5 千和 5 万的做法完全不同",
                required=True,
            )
        )
    if signals.has("urgency"):
        risk.append(
            Question(
                "期望什么时候上线？这个时间点是硬要求还是希望？",
                "排期是接单的前提，接不住要提前说，不能签了再说做不完",
            )
        )

    if risk:
        result.append(ClarifyGroup(name="风险与边界", questions=risk[:MAX_PER_GROUP]))
    return result
