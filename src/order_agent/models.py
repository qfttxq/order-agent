"""数据结构定义。

设计原则：每个阶段只用一个 dataclass 传递结果，不做「一层套一层的 dict」。
理由很实际——这个工具要输出给客户看，任何字段在传给模板时拼错，都会变成
一份有错别字的报价单。用 dataclass 可以让拼错在运行前就报错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Kind(str, Enum):
    """需求类型。X 是「建议不接」，不是错误类型。"""

    K = "K"  # 知识库 / RAG
    A = "A"  # Agent / 工作流自动化
    D = "D"  # 数据处理（谨慎接）
    X = "X"  # 建议不接

    @property
    def label(self) -> str:
        return {
            "K": "知识库 / RAG",
            "A": "Agent / 工作流自动化",
            "D": "数据处理",
            "X": "建议不接",
        }[self.value]


class Fit(str, Enum):
    """匹配度。分三档而不是十分制，因为十分制是假精度。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def label(self) -> str:
        return {"high": "高，值得优先投入", "medium": "中，可接但要控制范围", "low": "低，建议放弃"}[self.value]


@dataclass(frozen=True)
class Brief:
    """客户发来的一段需求。

    budget 和 deadline 是可选的结构化输入：现实里客户经常只在口头上说，
    落成文字时反而漏掉，所以允许后续手动补录。
    """

    text: str
    budget: int | None = None
    deadline_days: int | None = None
    channel: str = "未知渠道"

    @property
    def length(self) -> int:
        return len(self.text)


@dataclass
class Signals:
    """从需求文本里抽出来的客观特征。

    这里刻意只放「能指出来源」的东西：每个信号都保留命中的原文片段，
    因为报价和判定结论都要能向客户解释「我是根据你这句判断的」。
    """

    hits: dict[str, list[str]] = field(default_factory=dict)
    keywords: dict[str, list[str]] = field(default_factory=dict)

    doc_count: int | None = None
    doc_unit: str = "份"
    systems: list[str] = field(default_factory=list)
    vague_markers: list[str] = field(default_factory=list)
    budget_in_text: int | None = None

    def add(self, name: str, fragment: str, keyword: str = "") -> None:
        self.hits.setdefault(name, [])
        if fragment not in self.hits[name]:
            self.hits[name].append(fragment)
        if keyword:
            self.keywords.setdefault(name, [])
            if keyword not in self.keywords[name]:
                self.keywords[name].append(keyword)

    def has(self, name: str) -> bool:
        return bool(self.hits.get(name))

    @property
    def business_systems(self) -> list[str]:
        """需要真正对接的系统。剔除 Excel 这类只是数据载体的工具。"""
        from .signals import DATA_TARGETS

        return [name for name in self.systems if name not in DATA_TARGETS]

    def first(self, name: str) -> str:
        return self.hits.get(name, [""])[0]

    @property
    def names(self) -> list[str]:
        return sorted(self.hits)


@dataclass
class Verdict:
    """类型判定结果。

    confidence 不是「模型有多自信」，而是规则命中强度归一化后的值，
    所以它可解释：把命中项列出来，人就能复核。
    """

    kind: Kind
    confidence: float
    reasons: list[str] = field(default_factory=list)
    fit: Fit = Fit.MEDIUM
    fit_reasons: list[str] = field(default_factory=list)
    llm_kind: Kind | None = None
    llm_note: str = ""
    conflict: bool = False


@dataclass
class Question:
    """澄清清单里的一条。

    gap 是这个类里最重要的一个字段：它指向一个信号名，只要该信号已经命中，
    这条问题就整个跳过。这和「标为可不答」是两回事——客户已经说了权限怎么分，
    再问一句「能看的资料一样吗」，示范效果是负的。
    """

    text: str
    why: str
    required: bool = True
    gap: str = ""


@dataclass
class ClarifyGroup:
    name: str
    questions: list[Question] = field(default_factory=list)


@dataclass
class Tier:
    """报价的一档。"""

    name: str
    price_low: int
    price_high: int
    days: int
    includes: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 防呆：报价区间倒挂（¥80,000 – ¥60,000）是实际发生过的 bug，
        # 而且只有在某个档位的上限被单独写死时才会触发，测试很难覆盖全。
        # 与其相信每处计算都对，不如在这里兜住——报价单上出现倒挂区间是致命的。
        if self.price_high < self.price_low:
            self.price_high = self.price_low

    @property
    def price_text(self) -> str:
        if self.price_low == self.price_high:
            return f"¥{self.price_low:,}"
        return f"¥{self.price_low:,} – ¥{self.price_high:,}"


@dataclass
class Quote:
    tiers: list[Tier] = field(default_factory=list)
    basis: list[str] = field(default_factory=list)
    payment: list[str] = field(default_factory=list)
    net_ratio: float = 0.886
    # 主推档的名称。诊断版和 PoC 是引流/试水档，不能拿来当「预算够不够」的基准，
    # 所以单独标出来，避免「客户预算 3 万 → 提示低于 ¥3,500 诊断版」这种荒谬判断。
    main: str = ""


@dataclass
class Acceptance:
    items: list[tuple[str, str]] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    handover: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    """一次完整分析的产出。CLI 只负责渲染它。"""

    brief: Brief
    signals: Signals
    verdict: Verdict
    clarify: list[ClarifyGroup] = field(default_factory=list)
    quote: Quote = field(default_factory=Quote)
    acceptance: Acceptance = field(default_factory=Acceptance)
