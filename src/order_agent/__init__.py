"""接单需求评估 Agent。

输入一段客户需求，输出五样东西：
类型判定、匹配度、澄清清单、三档报价、验收单草稿。

用法：
    from order_agent import analyze, Brief

    analysis = analyze(Brief(text="..."))
    print(analysis.verdict.kind.label)
"""

from .models import Analysis, Brief, Kind
from .pipeline import analyze

__version__ = "0.1.0"
__all__ = ["Analysis", "Brief", "Kind", "analyze"]
