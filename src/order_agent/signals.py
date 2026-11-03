"""从一段需求文本里抽客观信号。

为什么先抽信号、而不是直接把需求丢给模型？

因为报价必须可解释。客户问「为什么这个要 2 万」，答案不能是「模型说的」，
而应该是「你提到 300 份扫描件、要对接企业微信、要权限分级，这三项各占一部分」。
信号层就是这个解释的来源：每个信号都保留命中的原句，后面判定和报价都从它推导。

另一个原因是可评测：规则路径完全确定，同一份输入跑一百次结果一样。
模型路径做不到这一点，所以它只作为交叉验证，不单独决定结论。
"""

from __future__ import annotations

import re

from .models import Signals

# 同义组：名称 -> 关键词。用组而不是单个词，是因为真实需求里说法太散。
# 每个关键词都用子串匹配，中文不做分词——分词的边界错误反而会漏掉「知识库」这种整词。
LEXICON: dict[str, tuple[str, ...]] = {
    # —— K 类（知识库 / 检索 / 问答）——
    "kb": ("知识库", "知识管理", "问答系统", "智能问答", "faq", "FAQ", "检索", "rag", "RAG", "语义搜索"),
    "documents": (
        "文档", "资料", "手册", "pdf", "PDF", "word", "Word", "制度", "档案", "合同",
        "培训材料", "讲义", "产品说明", "操作规范", "录音", "会议记录", "工单记录",
        "聊天记录", "对话记录", "咨询记录", "客户咨询",
    ),
    "qa_need": (
        "能回答", "答不上来", "查不到", "找不到", "问它", "搜不到", "找资料",
        "每次都要翻", "翻半天", "问不到", "常见问题", "自动回答", "自动回复", "回复客户",
    ),
    "accuracy": ("准确", "不能瞎编", "不能乱编", "幻觉", "有依据", "给出处", "可溯源"),
    # —— A 类（Agent / 工作流）——
    "workflow": (
        "流程", "自动化", "自动", "定时", "每天", "每周", "批量", "一键", "流水线",
        "审批", "派单", "跟单", "催收", "对账", "排班", "发货",
    ),
    "integration": ("对接", "接口", "api", "API", "打通", "同步", "推送", "回调", "webhook"),
    "trigger": ("触发", "当有", "一旦", "收到", "只要", "实时"),
    "manual_pain": (
        "手动", "人工", "复制粘贴", "一个个", "重复", "粘贴", "录入到", "导来导去",
        "靠人盯", "全靠人", "加班",
    ),
    # —— D 类（数据处理）——
    "data_ops": (
        "爬取", "采集", "抓取", "清洗", "去重", "整理成", "整理到", "批量录入", "数据整理",
        "做成表格", "做成 excel", "做成Excel", "工商信息", "企业信息", "名录", "批量下", "爬下来",
    ),
    # —— 拒单信号 ——
    "ui_automation": (
        "模拟点击", "按键精灵", "rpa", "RPA", "自动点击", "自动点", "点按钮",
        "网页上的按钮", "界面操作", "模拟操作", "控件", "屏幕识别", "界面上", "手工录",
    ),
    "labeling": ("数据标注", "人工标注", "打标签", "标注平台"),
    # —— 风险 / 缺口信号 ——
    "vague": ("大概", "类似", "看情况", "差不多", "反正", "比较简单", "你看着办", "随便", "再说"),
    "urgency": ("尽快", "很急", "马上", "这周就要", "明天要", "急用"),
    "scope_creep": ("顺便", "另外", "还要", "再加", "最好也", "能不能也"),
    "no_budget": ("预算还没定", "预算不多", "先看看", "价格好说", "没多少钱", "便宜点"),
    "permission": ("权限", "分级", "谁能看", "保密", "脱敏", "不同部门"),
    "ui_need": ("界面", "页面", "后台", "看板", "前端", "小程序", "app", "APP"),
    "onprem": ("私有化", "内网", "本地部署", "不能上外网", "信创", "国产化"),
}

# 已知的业务系统名。抽出来是为了让报价里「对接 N 个系统」有具体清单，
# 而不是笼统写「含系统对接」——客户看到自己系统的名字会明显更信任。
KNOWN_SYSTEMS: tuple[str, ...] = (
    "企业微信", "钉钉", "飞书", "企微", "微信公众号", "小程序", "金蝶", "用友", "SAP",
    "salesforce", "Salesforce", "CRM", "ERP", "OA", "MES", "WMS", "POS", "抖店",
    "淘宝", "京东", "拼多多", "有赞", "微盟", "Shopify", "亚马逊", "Excel", "多维表",
    "飞书文档", "腾讯文档", "谷歌表格", "MySQL", "SQL Server", "PostgreSQL",
)

# 只作为数据载体出现的工具。Excel 出现在需求里说明「数据长什么样」，
# 不说明「要调谁的接口」——把它算成一个要对接的系统，会把数据处理单错判成 Agent 单。
DATA_TARGETS = frozenset({"Excel", "多维表", "飞书文档", "腾讯文档", "谷歌表格"})

# 中文数量词。只覆盖需求描述里真会出现的量级，不做通用中文数字解析。
_CN_NUM = {
    "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 否定词。「对方系统没有接口」里的「接口」是「没有」的宾语，
# 意思是「无法对接」，而不是「要求对接」——按正向关键词处理会把结论判反。
_NEGATIONS = ("没有", "没", "无", "不", "未")

_SENT_SPLIT = re.compile(r"[。\n\r；;！!？?]+")

_COUNT_PATTERNS = (
    # 300 份文档 / 2000 条录音 / 1.5 万份
    re.compile(r"(\d+(?:\.\d+)?)\s*(万|千|k|K|w|W)?\s*(?:多)?\s*(份|个|篇|页|条|本|套|张|单|G|GB|gb)"),
    # 文档 300 份
    re.compile(r"(?:文档|资料|文件|录音|视频|图片|合同|工单|订单)\s*(\d+(?:\.\d+)?)\s*(万|千)?\s*(份|个|篇|页|条|本|张|单)"),
)


def _parse_count(number: str, unit: str | None) -> int:
    value = float(number)
    if unit in ("万", "w", "W"):
        value *= 10000
    elif unit in ("千", "k", "K"):
        value *= 1000
    return int(value)


def extract(text: str) -> Signals:
    """扫一遍文本，填出 Signals。"""
    signals = Signals()
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]

    for sentence in sentences:
        lowered = sentence.lower()
        for name, keywords in LEXICON.items():
            for kw in keywords:
                # 全大写的缩写（API/RAG）用原串匹配，其余用小写比较，避免中文被误改
                haystack = sentence if kw.isupper() or kw.isascii() and kw.islower() else lowered
                needle = kw if kw.isupper() else kw.lower()
                if needle in haystack.lower():
                    # 「预算大概 3 万」里的「大概」后面紧跟数字，是给区间不是含糊，
                    # 不该被当成模糊表述去追问例子——那样问客户会觉得很蠢。
                    if name == "vague" and _quantified_after(sentence, kw):
                        continue
                    # 「没有接口」是被否定的对接诉求，反过来是模拟点击的前兆
                    if name == "integration" and _negated(sentence, kw):
                        signals.add("no_api", sentence, kw)
                        continue
                    signals.add(name, sentence, kw)
                    break

    # 系统名单独处理：按名字长度降序匹配，避免「飞书文档」和「飞书」被算成两个系统
    for system in sorted(KNOWN_SYSTEMS, key=len, reverse=True):
        if system.lower() not in text.lower():
            continue
        if any(system in existing or existing in system for existing in signals.systems):
            continue
        signals.systems.append(system)
    signals.systems.sort(key=lambda name: text.lower().find(name.lower()))

    signals.doc_count, signals.doc_unit = _extract_doc_count(text)
    signals.vague_markers = signals.hits.get("vague", [])

    budget = _extract_budget(text)
    if budget is not None:
        signals.budget_in_text = budget

    return signals


def _extract_doc_count(text: str) -> tuple[int | None, str]:
    """抽语料规模，返回 (数量, 单位)。

    单位要带出来，因为「300 份文档」和「300 条录音」在报告里必须说对——
    写错单位会直接被客户看出来没认真读需求。
    """
    found: list[tuple[int, str]] = []
    for pattern in _COUNT_PATTERNS:
        for match in pattern.finditer(text):
            number, unit = match.group(1), match.group(2)
            value = _parse_count(number, unit)
            # 过滤掉「3 个系统」「2 个人」这种非语料量：只有量级 >= 10 才算语料
            if value >= 10:
                inner_unit = match.group(3) if match.lastindex and match.lastindex >= 3 else "份"
                found.append((value, inner_unit))

    # 「两万条」「三千份」这类中文数量级，只在没有阿拉伯数字时兜底。
    # 不做通用中文数字解析（「一万五千」这类不处理），只覆盖需求里真会出现的写法。
    if not found:
        cn = re.search(r"([一两二三四五六七八九十])\s*(万|千|百)\s*(?:多)?\s*(份|个|篇|页|条|本|张|单)", text)
        if cn:
            scale = {"万": 10000, "千": 1000, "百": 100}[cn.group(2)]
            found.append((_CN_NUM[cn.group(1)] * scale, cn.group(3)))

    if not found:
        return None, "份"

    value, unit = max(found, key=lambda item: item[0])
    return value, _unit_label(unit)


def _unit_label(unit: str) -> str:
    """把量词映射到报告里读得通的写法。

    注意保留「单」：A 类需求里的「300 多单」是订单量，如果统一写成「份」，
    报告会变成「语料规模：约 300 份」，客户一看就知道你没读懂他的生意。
    """
    if unit in ("G", "GB", "gb"):
        return "GB"
    if unit in ("页", "篇", "单", "条", "个"):
        return unit
    if unit in ("本", "套", "张"):
        return "份"
    return "份"


def _negated(sentence: str, keyword: str, window: int = 3) -> bool:
    """关键词前面一小段里是否出现否定词。

    「没有接口」和「要求对接接口」用的是同一个词，意思正好相反。
    不做这层判断，规则会把「对方没接口，只能手工录」判成「要做系统对接」，
    结论直接反了——而且反得很有说服力，因为报告里会引用这句话当依据。
    """
    index = sentence.find(keyword)
    if index <= 0:
        return False
    prefix = sentence[max(0, index - window) : index]
    return any(negation in prefix for negation in _NEGATIONS)


def _quantified_after(sentence: str, keyword: str, window: int = 10) -> bool:
    """关键词后面一小段里是否出现了数字。

    用来区分「大概 3 万」（给区间，具体）和「大概就是这样」（含糊，要追问）。
    """
    index = sentence.find(keyword)
    if index < 0:
        return False
    tail = sentence[index + len(keyword) : index + len(keyword) + window]
    return bool(re.search(r"\d", tail))


def _extract_budget(text: str) -> int | None:
    """抽客户自己说的预算。这是报价最重要的锚点，比任何估算都准。"""
    patterns = (
        re.compile(r"预算\s*(?:大概|约|在)?\s*(\d+(?:\.\d+)?)\s*(万|千|w|W|k|K)?"),
        re.compile(r"(\d+(?:\.\d+)?)\s*(万|千|w|W|k|K)?\s*(?:元)?\s*(?:以内|左右|以下|封顶)"),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _parse_count(match.group(1), match.group(2) if match.lastindex and match.lastindex >= 2 else None)
    return None
