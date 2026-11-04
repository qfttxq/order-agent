# order-agent 源码评审报告

评审日期：2026-11-03
代码版本：`95d085e`（v0.1.0）
评审范围：`src/order_agent/` 全部 2077 行、`tests/` 286 行、`eval/` 194 行
评审方法：全文通读 + 动态验证（每条问题都有可复现的实证输出，见文末附录）

---

## 一、总体判断

**工程质量在个人项目里属上乘，但它有一个方向性风险：报告看起来越专业，里面的假设性内容就越危险。**

具体说，这个工具的产物是「给客户看的报价单和验收单」。而它现在有 4 处会产出**读起来很确定、实际是猜的或错的**内容：客户没说过文档数，报告却写「按语料规模估算：50 份」；要求私有化部署，价格乘了 1.4 但工期一天没加。这类缺陷不会让程序崩，也不会让测试挂，但会在一场客户沟通里被当场问住。

这是它要解决的问题的反面——它本来是消除「凭感觉报价」的，现在自己引入了新的凭感觉。

除此之外，架构是干净的：单向数据流、零第三方依赖、业务逻辑与 IO 分离、`pipeline.py` 只有 17 行。核心设计判断（规则出结论、模型做第二意见）是对的，而且理由写清楚了。

| 维度 | 评价 |
|---|---|
| 架构分层 | 良。单向依赖，职责清晰，无明显上帝类 |
| 可解释性 | 优。每个结论都能追到原句，这是设计目标 |
| 健壮性 | 中。3 处输入/响应异常会直接崩栈 |
| 一致性 | 中。2 处口径不一致、1 处硬编码字符串判断 |
| 测试 | 中偏良。40 个测试 80% 行覆盖，但**恰好漏掉最该测的两个模块** |
| 文档 | 优。注释写「为什么」和「踩过的后果」，不是复述代码 |

---

## 二、项目画像

### 2.1 规模与结构

| 文件 | 行数 | 可执行行 | 职责 |
|---|---:|---:|---|
| `models.py` | 197 | 125 | 9 个 dataclass，纯数据定义 |
| `signals.py` | 233 | 117 | 正则抽信号，词表 + 数量/预算/否定处理 |
| `classify.py` | 254 | 151 | 加权计分判类型、打分匹配度、生成依据 |
| `clarify.py` | 326 | 215 | 双轨澄清清单（K 六组 / A 六组 / 拒单两组） |
| `quote.py` | 312 | 177 | 三套计价公式 + 私有化系数 + 预算预警 |
| `acceptance.py` | 163 | 55 | 三类验收单，全部可第三方判定 |
| `report.py` | 219 | 148 | Markdown 渲染，五段落 |
| `llm.py` | 165 | 81 | 可选的模型交叉验证 |
| `pipeline.py` | 30 | 17 | 串联五个阶段 |
| `cli.py` | 157 | 114 | `analyze` / `batch` / `verify` |
| `__init__.py` / `__main__.py` | 21 | 5 | 包导出与入口 |
| **合计** | **2077** | **1205** | |

依赖：`dependencies = []`，全部标准库（`re` / `json` / `argparse` / `urllib` / `dataclasses` / `pathlib`）。这一条在接单场景里是加分项——客户环境不需要装任何东西。

### 2.2 数据流

```
Brief ──> Signals ──> Verdict ──┬──> ClarifyGroup[]
                                ├──> Quote
                                └──> Acceptance
                                        │
                                   report.render()
```

`pipeline.analyze()` 是唯一的编排点，17 行，无副作用。这个切分是对的：评测脚本、将来的微信机器人、表单后端都能直接调 `analyze()`，不必经过 CLI。

### 2.3 复杂度分布

60 个函数，平均圈复杂度 4.5，平均 24 行。复杂度 > 10 的有 6 个：

| 函数 | 复杂度 | 行数 | 评价 |
|---|---:|---:|---|
| `signals.extract` | 20 | 41 | 三重循环 + 三个特例分支，最高 |
| `classify._fit` | 17 | 45 | 加减分规则堆叠，可表驱动化 |
| `clarify._assemble` | 13 | 59 | 过滤 + 上限替换 + 风险追加，三件事 |
| `classify._reasons` | 13 | 31 | 片段去重逻辑 |
| `classify.classify` | 11 | 63 | 拒单分支 + 兜底 + 混合判断 |
| `report._headline` | 11 | 28 | 按 kind/fit/预算分支 |

`extract` 和 `_fit` 是全项目最该重构的两个函数，但都还在可维护范围内（<25 且逻辑线性）。没有出现"一个函数干五件事"的情况。

---

## 三、做对了的五个设计

这几处值得保留，改动时别顺手拆掉。

### 3.1 规则出结论，模型只做第二意见

`classify.py` 顶部注释把理由写透了：模型不可复现、不可评测、挂了就不能用。

实证有效：样本 `b08`（"销售见客户前要准备资料，想让系统根据客户行业自动推材料"）规则判 A、模型判 K，`merge()` 标了 `conflict=True` 并把置信度从 0.55 降到 0.40，报告里追加一段「存在判定冲突，先跑一轮澄清」。这类需求靠关键词救不回来，模型补的正是语义缺口——但它只补位，不夺权。

这个分工在接单场景里是对的：客户问「为什么判 K」，你要能指着原句回答，而不是「模型说的」。

### 3.2 澄清清单是「真删除」，不是「标记为可不答」

`Question.gap` 指向一个信号名，命中就整条消失。这不是细节——`clarify.py` 的注释说得准确：客户已经说了「不同部门只能看自己组的资料」，你再问「能看的资料一样吗」，他一眼就知道你没读需求。

有测试守住（`test_known_permission_requirement_is_not_asked_again`），而且是测字符串出现与否，比测对象结构更贴近真实效果。

### 3.3 验收项全部可被第三方独立判定

`acceptance.py` 拒绝一切模糊词。A 类不写"准确率"（无限责任），写"连续执行 100 次，成功 ≥ 95 次"，并且**排除上游系统故障、账号失效、源数据缺失三种情况**——这三条排除比指标本身更重要，它把不可控因素从承诺里摘了出去。

### 3.4 评测口径诚实得少见

三批样本（开发 13 / 留出 9 / 盲测 10），报告里明确写「前两批 100% 不能当结论，规则是照着开发集调的」。只报盲测 9/10 和剩余一条的成因。

`eval/run_eval.py` 末尾还专门写了一段「这份指标的边界」，三条自我否定：样本是构造的、覆盖度不测客户答不答、价格区间是自定策略不是市场公允价。

大多数个人项目会写「准确率 100%」。这里写的是「盲测 90%，短板在这」——对客户反而更可信。

### 3.5 注释记录「为什么」和「踩过的后果」

抽查几处：

- `models.py:157` 报价倒挂防呆——"实际发生过的 bug"、"只有在某个档位的上限被单独写死时才会触发"
- `signals.py:76` Excel 不算业务系统——"会把数据处理单错判成 Agent 单"
- `classify.py:41` KB 保底加分——"不加这条的实测后果：报价从 3.8 万跳到 8 万，客户直接被吓走"
- `llm.py:8` 模型不能单独决策——列了三个具体问题，不是"最佳实践"

这种注释的价值在于：半年后改这段代码的人（包括自己）能判断"这条规则能不能动"。

---

## 四、问题清单

按影响分级。每条都给证据，全部可复现。

### A 级：会产生错误输出或崩溃（4 条）

#### A1　未提供文档数时，报告谎称「按语料规模估算：50 份」

**位置**：`quote.py:69`、`quote.py:70`、`quote.py:76-77`

```python
docs = signals.doc_count or 50          # ← 客户没说，静默取 50
basis = [f"按语料规模估算：{docs} {signals.doc_unit}"]
```

**实证**（输入："员工查制度不方便，想做个问答系统，让他们能直接问。"）：

```
doc_count = None
报价依据 = ['按语料规模估算：50 份', '50–200 份，标准档区间内']
标准版  = ¥18,000 – ¥35,000
```

**影响**：报告用陈述语气写出一个客户从未提供的数字，并据此给出 ¥18,000 起的报价。客户看到会说「我没说 50 份」，而这一步恰好发生在双方建立信任的早期。这是本报告认为最该先修的一条——它不是算错，是**把假设伪装成事实**。

**建议**：区分「已知」和「假设」两种 basis 文案。

```python
if signals.doc_count:
    basis = [f"按语料规模估算：{docs} {signals.doc_unit}"]
else:
    basis = ["客户未提供语料规模，暂按 50 份下限试算；实际报价需先确认总量"]
    # 同时把该档标注为"待确认"，或在报告里加一句显式提示
```

---

#### A2　GBK 编码的需求文件直接崩栈

**位置**：`cli.py:23`

```python
return Path(args.file).read_text(encoding="utf-8").strip()
```

**实证**（把一个 47 字节的 GBK 文本用 `-f` 传入）：

```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xce in position 0: invalid continuation byte
```

完整栈直接打到终端，退出码仍是 0（因为异常未被捕获且 `main` 未处理）。

**影响**：Windows 中文环境下，用记事本「另存为 ANSI」就是 GBK，这是**最高频的真实场景**——客户发来的需求文档几乎不可能是 UTF-8。这个工具的目标用户恰好是在 Windows 上收微信文件的人。

**建议**：按顺序尝试 `utf-8` → `gb18030`，失败给一句人话提示。

```python
raw = Path(args.file).read_bytes()
for enc in ("utf-8", "gb18030"):
    try:
        return raw.decode(enc).strip()
    except UnicodeDecodeError:
        continue
raise SystemExit(f"无法识别 {args.file} 的编码，请另存为 UTF-8 后重试")
```

---

#### A3　模型返回 `content: null` 时崩溃

**位置**：`llm.py:105` → `llm.py:118`

```python
content = body["choices"][0]["message"]["content"]   # 可能是 None
...
match = re.search(r"\{[^{}]*\}", content, re.S)      # None → TypeError
```

`review()` 的 `except` 只捕获 `URLError / KeyError / IndexError / JSONDecodeError / TimeoutError`，`TypeError` 不在其中，且 `_parse()` 调用在 `try` 块**外面**。

**实证**：

```
_parse(None, "m")  → 崩溃 TypeError: expected string or bytes-like object, got 'NoneType'
```

**影响**：模型返回 `tool_calls` 或内容被安全策略拦截时，`content` 为 `null`。这不是罕见情况——它会让整条 `analyze()` 挂掉，而 `llm.py` 的模块注释明确承诺「任何失败都返回 None，绝不抛出」。**实现与承诺不符**。

**建议**：把 `_parse()` 纳入 `try`，或在入口加一行类型防御。

```python
if not isinstance(content, str) or not content.strip():
    return None
```

---

#### A4　私有化部署只加价、不加工期

**位置**：`quote.py:50-54`

```python
if signals.has("onprem"):
    for tier in tiers:
        tier.price_low  = _round(tier.price_low  * ONPREM_FACTOR)
        tier.price_high = _round(tier.price_high * ONPREM_FACTOR)
    # tier.days 没有同步调整
```

**实证**：

| 档位 | 普通报价 | 私有化报价 | 普通工期 | 私有化工期 |
|---|---:|---:|---:|---:|
| 诊断版 | ¥3,500 | ¥5,000 | 3 | **3** |
| 标准版 | ¥38,000 – ¥48,000 | ¥53,000 – ¥67,000 | 15 | **15** |
| 生产版 | ¥60,000 – ¥180,000 | ¥84,000 – ¥252,000 | 45 | **45** |

**影响**：`ONPREM_FACTOR` 的注释自己写着"实测普遍比云端方案多 30%–60% 工时"，但代码只把工时折成了钱，没折成时间。结果是一份自相矛盾的报价单：**价格涨了 40%，工期不变**。客户按工期排资源，到期交付不了，这是直接的违约风险。

**建议**：同步 `tier.days = max(tier.days + 3, int(tier.days * 1.3))`，并在 basis 里说明工期也上调了。

---

### B 级：逻辑不一致与维护陷阱（7 条）

#### B1　澄清清单里两条问题语义重复，占掉三分之一名额

**位置**：`clarify.py:40-50`

```python
Question("一共有多少份？后续还会持续增加吗？", ..., gap="count"),
Question("后续还会持续增加吗？大概多久新增一批？", required=False),
```

前半句重复，且 `MAX_PER_GROUP = 3` 让语料组正好被三条占满，第四条（"同一份资料有没有多个版本"）永远进不来。

**实证**（未提供文档数时，语料组实际输出）：

```
1. 这批文档现在在哪儿、什么格式？有几个是扫描件或图片？
2. 一共有多少份？后续还会持续增加吗？      ← 重复点
3. 后续还会持续增加吗？大概多久新增一批？  ← 重复点
```

**影响**：`clarify.py` 的设计目标是"少问，每问都问在缺口上"。重复问反而违反了自己的原则，且挤掉了更有价值的那条（版本重复检测，直接影响去重成本）。

**建议**：把第 3 条改成只问增量节奏「后续大概多久新增一批？量级和现在差不多吗？」，去掉对 Q2 的重复；或把 `required=False` 那条降到第二组。

---

#### B2　`_fit` 用 `systems`，`classify` 用 `business_systems`

**位置**：`classify.py:218` vs `classify.py:129`

```python
# 计分（正确）
system_count = min(len(signals.business_systems), SYSTEM_CAP)

# 打分（不一致）
if len(signals.systems) >= 2:
    score += 1
```

**实证**：

```
systems = ['Excel', '多维表'] | business_systems = []
classify 用 len(business_systems) → 0
_fit 用 len(signals.systems)     → 2   ← 仍按"2 个系统"加分
```

**影响**：一条"整理 Excel 和多维表"的数据处理需求，匹配度会被多算 1 分，可能从"中"跳到"高"。`signals.py:76` 的注释专门说明了为什么要把 Excel 排除——但只在一个地方排除了。

**建议**：`_fit` 改为 `len(signals.business_systems) >= 2`。这是 typo 级别的问题，但后果是匹配度判断偏乐观，直接影响"要不要花时间谈这一单"。

---

#### B3　报告里的「到手参考」取的是最高档，不是主推档

**位置**：`report.py:143-147`

```python
main = max(quote.tiers, key=lambda tier: tier.price_high)   # 变量名叫 main，其实是"最贵的"
net = int(main.price_high * quote.net_ratio)
```

**实证**（A 类五系统需求）：

```
主推档 = 多系统 Agent
报告原文: > 到手参考：平台单扣 11.4%，最高档 ¥250,000 实收约 ¥221,500
```

而同一份报告推荐的起谈价是「多系统 Agent ¥80,000 起」。22 万这个数字和主推档的起报价无关，只是最高档的上限。

**影响**：报价单上出现一个和主推方案不搭的到手数字，客户会问"到底是 8 万还是 22 万"。`Quote.main` 字段的存在意义就是标明主推档，这里绕过了它。

**建议**：改用 `Quote.main` 定位（和 `_headline` 保持一致），并且用 `price_high` 时明确写"按上限估"。顺带把局部变量改名 `top` 或 `priciest`，避免与 `Quote.main` 概念冲突。

---

#### B4　全大写关键词恒不匹配，`haystack` 是死代码

**位置**：`signals.py:117-120`

```python
# 注释说：全大写的缩写（API/RAG）用原串匹配，其余用小写比较
haystack = sentence if kw.isupper() or kw.isascii() and kw.islower() else lowered
needle = kw if kw.isupper() else kw.lower()
if needle in haystack.lower():          # ← 无论如何都转小写
```

**实证**：

```
句子='需要对接他们的API'  kw=API  命中=False
句子='需要对接他们的api'  kw=api  命中=True
句子='做个RAG系统'       kw=RAG  命中=False
```

**影响**：功能上没坏，因为 `LEXICON` 里每个大写词都配了小写孪生（`"api", "API"`、`"rag", "RAG"`）。但这是**靠数据冗余掩盖逻辑错误**：任何人新增全大写关键词（如 `"SOP"`、`"LLM"`）都会静默失效，而且不会报错、不会有测试失败。注释还在告诉读者相反的结论——这类注释比没有注释更危险。

**建议**：删掉 `haystack` 三元，直接 `if kw.lower() in sentence.lower()`，注释改成"统一小写比较，LEXICON 里无需同时列大小写两种写法"。顺手把 `LEXICON` 里的大写孪生清掉。

---

#### B5　`Tier.__post_init__` 静默抬上限，让防呆测试恒真

**位置**：`models.py:156-161`

```python
if self.price_high < self.price_low:
    self.price_high = self.price_low    # 静默修正，无警告
```

**实证**：

```python
Tier(name="测试档", price_low=80000, price_high=60000, days=30)
→ 实际输出 ¥80,000   # 不报错
```

**影响**：这是防御性编程与快速失败的取舍，这里选错了边。原作者的本意（注释说明）是"报价单上出现倒挂是致命的"，但代价是：

1. 计算逻辑错了也不会暴露，报价单看起来正常
2. **`test_no_tier_has_inverted_range` 变成恒真断言**——`Tier` 构造完就已经自动修正，这个测试永远通过，实际上什么都没测到

一个防呆机制把对应的测试变成了摆设，这是负收益。

**建议**：改成 `raise ValueError`，测试才有意义；如果担心生产环境崩，改用 `warnings.warn` 并在报告里标注。修完要重跑 `test_no_tier_has_inverted_range`，确认它真的能抓到问题。

---

#### B6　用档位名称字符串做外键，改名即静默失效

**位置**：`quote.py:284-286`、`report.py:48-50`、`report.py:121`

```python
# quote.py
target = next((tier for tier in tiers if tier.name == main), None)
if target is None:
    return                                    # 找不到就静默跳过预算提示

# report.py
target = next((tier for tier in analysis.quote.tiers if tier.name == analysis.quote.main), None)
if target is None and analysis.quote.tiers:
    target = analysis.quote.tiers[0]          # 找不到就退回第一档（通常是引流档）

# report.py:121
days = f"{tier.days} 天" if tier.name != "按月维护" else "每月"
```

**影响**：`Quote.main` 是一个中文字符串，与 `Tier.name` 靠字面量匹配。改一处文案（比如"标准版"→"标准交付"）不会报错，但会导致：预算预警静默消失、首行结论退回用诊断版价格、周期显示错误。三处失效点都没有测试覆盖。

`report.py:121` 还硬编码了 `"按月维护"` 这个具体档位名来判断周期单位——这是把展示层的特殊规则写进了渲染逻辑，A 类/D 类/K 类各有一个"按月维护"档，改档位名就坏。

**建议**：`Tier` 加一个稳定的 `key` 字段（`"diag"` / `"standard"` / `"prod"` / `"monthly"`），`Quote.main` 存 key；按月档位用 `tier.period == "month"` 表达，别用名字判断。

---

#### B7　`llm.review()` 的 `signals` 参数从未使用

**位置**：`llm.py:77`

```python
def review(brief: Brief, signals: Signals) -> Opinion | None:
    ...
    payload = {... PROMPT.replace("{text}", brief.text) ...}   # signals 没出现
```

**影响**：`pipeline.py:19` 每次都在传，读者会以为信号被注入了 prompt（这是合理的增强方向——把已抽取的规模、系统清单喂给模型，结论会更稳）。实际上没有。属于"接口承诺了但没兑现"，容易误导后续维护者。

**建议**：要么用起来（`PROMPT` 加一段"已知事实：语料 300 份、涉及系统：企业微信、ERP"），要么删掉参数。

---

### C 级：结构性问题（4 条）

#### C1　`models` 与 `signals` 循环依赖

**证据**：

```
signals.py:17   from .models import Signals          ← 顶层
models.py:94    from .signals import DATA_TARGETS    ← 函数内
```

`Signals.business_systems` 是 `@property`，为了避开循环导入才把 import 塞进函数体。这样能跑，但每次访问属性都执行一次 import 语句（有缓存，开销可忽略），真正的问题是**依赖方向错了**：`models` 是最底层的数据定义，不该知道 `signals` 的词表。

**建议**：把 `DATA_TARGETS` 移到 `models.py`（它只是一个 `frozenset` 常量，与词表无关），或在 `pipeline` 里预先算好 `business_systems` 存进 `Signals` 字段。修完 `models` 变成零依赖，依赖图恢复单向。

#### C2　`Signals` 混杂了「原始信号」和「派生判断」

现在 `Signals` 里有三类东西：

- 原始命中：`hits`、`keywords`、`doc_count`、`systems`
- 派生冗余：`vague_markers`（= `hits["vague"]`）、`budget_in_text`（正则二次提取）
- 判断结果：`business_systems`（要查外部词表、要过滤）

结果是五个下游模块都读同一个对象，其中 `business_systems` 的语义依赖 `signals` 的词表定义。这让 `Signals` 变成了事实上的上帝对象——不是因为它大，而是因为它同时承载"抽取结果"和"业务判断"。

**建议**（不急，但值得记）：拆成 `RawSignals`（纯抽取，无判断）+ 在 `classify` 里算派生事实。好处是 `Signals` 可以脱离业务词表被单测。

#### C3　CLI 入口检查重复

`cli.py:42-43` 与 `cli.py:27-29` 做了同一个判断：

```python
# _cmd_analyze
if not args.text and not args.file and sys.stdin.isatty():
    raise SystemExit(__doc__)
# _read_text（随后必被调用）
if not sys.stdin.isatty():
    return sys.stdin.read().strip()
raise SystemExit("请提供需求文本：...")
```

两处错误提示还不一致（一个打 `__doc__`，一个打一行提示）。**建议**：删掉 `_cmd_analyze` 里的那段，统一交给 `_read_text`。

#### C4　`acceptance.build()` 对 `Kind.D` 的验收指标偏乐观

`_data` 里写「准确率 ≥ 98%」，但 D 类包含爬取、清洗、结构化，字段准确率 98% 在脏数据源上不一定可达。这里没有 `_note_budget_gap` 那种"条件收敛"机制。**建议**：加一句"若源数据缺失率高，准确率基准按抽样结果折算"，把不确定性写进条款——这和 A 类排除三种不可控因素的思路一致。

---

### D 级：测试与工程（3 条）

#### D1　覆盖率 80%，但漏掉的两个模块恰好最该测

用标准库 `trace` 跑 40 个测试，实测行覆盖：

| 文件 | 可执行行 | 覆盖 | 覆盖率 |
|---|---:|---:|---:|
| `acceptance.py` | 55 | 55 | 100% |
| `models.py` | 125 | 123 | 98% |
| `signals.py` | 117 | 113 | 97% |
| `clarify.py` | 215 | 200 | 93% |
| `quote.py` | 177 | 165 | 93% |
| `classify.py` | 151 | 139 | 92% |
| `pipeline.py` | 17 | 16 | 94% |
| `report.py` | 148 | 130 | 88% |
| **`llm.py`** | 81 | 24 | **30%** |
| **`cli.py`** | 114 | 0 | **0%** |
| 合计 | 1205 | 965 | 80% |

`cli.py` 的 0% 是真实的：`tests/` 里没有任何一处 `import cli`。之前只是我用命令行手动跑过冒烟测试，没有自动化。

**影响**：`llm.py` 是唯一有外部依赖的模块，`cli.py` 是用户唯一入口——**两个风险最高的模块测试最薄**。A2（GBK 崩溃）和 A3（`content=null` 崩溃）都在这里，且都能用 3 行测试抓到。

#### D2　关键分支明确无测试

未覆盖行里，有业务含义的：

| 位置 | 分支 | 为什么重要 |
|---|---|---|
| `quote.py:51-54` | **私有化加价** | 直接影响报价的乘法，`ONPREM_FACTOR` 无任何测试 |
| `quote.py:295-297` | 预算远高于上限 | 提醒"还有未识别需求"的唯一路径 |
| `report.py:211-213` | 判定冲突提示 | 双轨设计的核心产出，没有断言 |
| `report.py:45` | `Fit.LOW` 的结论话术 | "谨慎，倾向放弃"从未被渲染验证 |
| `classify.py:225-251` | `_fit` 多数减分项 | 匹配度判断的实际逻辑 |
| `clarify.py:291-297` | 模糊表述追问 | 风险组的主要来源 |

#### D3　无 CI、无 lint、无类型检查

`pyproject.toml` 只有 pytest 配置。没有 `ruff` / `mypy` / GitHub Actions。

考虑到这个仓库是对外展示的（签约要求"能代表代码质量的模块"），加一个 20 行的 workflow 性价比很高——它同时也是"这人做事有条理"的证据。而且 `mypy` 大概率能直接抓出 A3（`content` 为 `Optional[str]` 却传给 `re.search`）。

---

## 五、修复优先级

按「对客户沟通的影响 ÷ 改动成本」排序。

### 立刻（半天内，全部属于"报告说了假话"）

| 顺序 | 问题 | 改动量 |
|---|---|---|
| 1 | **A1** 谎称 50 份 | 4 行，改 basis 文案 |
| 2 | **A3** `content=null` 崩溃 | 2 行类型防御 |
| 3 | **A2** GBK 崩溃 | 8 行，加编码回退 |
| 4 | **B1** 问题语义重复 | 改 1 条文案 |

这四条加起来不到 20 行，但都是「在客户面前会被抓住」的类型。

### 本周（口径与工期）

| 顺序 | 问题 | 改动量 |
|---|---|---|
| 5 | **A4** 私有化不加工期 | 3 行 + 1 条测试 |
| 6 | **B2** `_fit` 口径不一致 | 1 行（typo 级） |
| 7 | **B3** 到手参考取错档 | 5 行 |
| 8 | **B5** 静默修正改 fail-fast | 3 行，但要重跑防呆测试 |

### 下周（消除隐患）

| 顺序 | 问题 | 说明 |
|---|---|---|
| 9 | **B6** 档位用 key 不用名字 | 涉及 3 处调用，改动中等，但能消掉三个静默失效点 |
| 10 | **B4** 清理死代码 + 错误注释 | 顺手把 LEXICON 去重 |
| 11 | **D1/D2** 补 `cli` 与 `llm` 测试 | 目标：两个模块都到 70% 以上 |
| 12 | **D3** 加 ruff + mypy + CI | 20 行 workflow |

### 有余力再做

**C1**（消除循环依赖）、**C2**（拆 `Signals`）、**C4**（D 类验收折算）。

C1 和 C2 是"让下次改动更安全"的投资，不影响当前输出正确性。在接单验证阶段，优先级应该低于 D1/D2——**先保证对外产出没错，再优化内部结构**。

---

## 六、一句话结论

这个项目现在的水准，足够拿去做面试演示：它有真实的评测数字、有诚实的短板说明、有能说清设计理由的注释。但**在把它当作对客户的报价依据之前，请先修 A1–A4**——那四条会让一份看起来很专业的报告，被一个有经验的客户在五分钟内问出破绽。

代码层面的最大优点不是架构，是**作者知道自己哪里不确定**（三批样本、边界声明、已知限制）。最大的风险也在这里：这些"不确定性"目前只写在评测报告里，没有传导到最终输出的报告上。A1 的修法本质上就是把这个传导补上——**该说"这是假设"的地方，别写成"这是估算结果"**。

---

## 七、修复记录（2026-11-04）

A1–A4 已全部修完，测试从 40 增至 62 个。

| 编号 | 修法 | 改动位置 |
|---|---|---|
| A1 | 区分「已知」与「假设」：`Quote` 新增 `provisional` 字段；未提供量时 basis 写「客户未提供语料规模，以下按 50 份下限试算」，includes 改为「约 50 份」；报告顶部加试算声明，首行结论改成「暂按…试算」而非「从…起谈」 | `models.py` `quote.py` `report.py` |
| A2 | `_read_file()` 按 utf-8 → gb18030 依次解码；按 NUL 字节拦截二进制文件（GB18030 几乎能解码任意字节，不设防线会把 docx 分析出离谱结论）；读不到文件给一句人话而不是堆栈 | `cli.py` |
| A3 | `_parse()` 入口加 `isinstance(content, str)` 与空串防御；`except` 补 `TypeError`；签名改 `str \| None` | `llm.py` |
| A4 | 新增 `ONPREM_DAYS_FACTOR = 1.3`，私有化时 `days = max(days + 3, round(days × 1.3))`；`Tier` 新增 `period` 字段，按月档不参与工期加成（顺带消掉 `report.py` 里 `if tier.name != "按月维护"` 这个硬编码） | `quote.py` `models.py` `report.py` |

**修复后的实际输出**（A4，同一需求含/不含私有化信号）：

| 档位 | 普通报价 | 私有化报价 | 普通工期 | 私有化工期 |
|---|---:|---:|---:|---:|
| 单流程 PoC | ¥6,000 – ¥15,000 | ¥8,000 – ¥21,000 | 14 | 18 |
| 单流程生产版 | ¥20,000 – ¥35,000 | ¥28,000 – ¥49,000 | 30 | 39 |
| 多系统 Agent | ¥80,000 – ¥250,000 | ¥112,000 – ¥350,000 | 60 | 78 |
| 按月维护 | ¥8,000 – ¥15,000 | ¥11,000 – ¥21,000 | 每月 | 每月 |

**验证方式**（避免新增测试恒真——B5 批评的正是这个）：

1. `pytest -q` → 62 passed
2. 把 `src/` 临时 `git stash` 回旧版本，同一批测试 → **14 failed**，逐个对应 A1–A4 与 `_read_file` 缺失。说明这些测试确实咬住了问题，不是恒真断言。
3. 重跑 `eval/run_eval.py --no-llm`，判定与报价结果**零变化**（样本均带明确规模），确认修复没有波及既有判定链路。

**一处更正**：A2 原文写「退出码仍是 0」，实测为 **1**。原因是我当时看的是管道后 `head` 的退出码，不是 python 进程的。结论（崩栈、无有效提示）不受影响，但描述不准确，在此更正。

**未修**：B1（澄清清单语义重复）、B2（`_fit` 口径不一致）、B3（到手参考取最高档）、B5（静默修正）、B7（`signals` 参数未使用）。这几条不产生错误输出，属本周/下周清单。

---

## 附录：核查方法

本报告所有结论均为动态验证，非静态推断。

| 验证项 | 方法 |
|---|---|
| 全量源码 | 2077 行逐文件通读（`models` / `signals` / `classify` / `clarify` / `quote` / `acceptance` / `llm` / `report` / `pipeline` / `cli`） |
| 行覆盖率 | 标准库 `trace.Trace(count=1)` + `pytest`，用 `code.co_lines()` 求可执行行基准（未安装 coverage，未改动环境） |
| 圈复杂度 | `ast` 遍历函数节点，统计 `If/For/While/ExceptHandler/With/BoolOp/IfExp/comprehension` |
| A1 | 调 `analyze()` 跑无文档数需求，打印 `quote.basis` |
| A2 | 写出 47 字节 GBK 文件，`analyze -f` 实际执行 |
| A3 | 直接调 `_parse(None, "m")` |
| A4 | 对比同一需求在含/不含 onprem 信号下的 price_text 与 days |
| B1 | 打印语料组实际输出的三条问题 |
| B2 | 构造 `Signals(systems=["Excel","多维表"])`，对比 `len(systems)` 与 `len(business_systems)` |
| B3 | 五系统 A 类需求，抓取报告中的"到手参考"行 |
| B4 | 逐 keyword 复现 `haystack`/`needle` 条件表达式，验证大写恒 False |
| B5 | 构造倒挂区间的 `Tier`，观察是否抛错 |
| 依赖分析 | `grep` 全部 import，确认仅标准库 + 包内模块 |

复现命令：

```bash
cd order-agent
export PYTHONPATH=src
python -m pytest -q                                    # 40 passed
python -m order_agent analyze "员工查制度不方便，想做个问答系统" --no-llm
python eval/run_eval.py --no-llm --samples requirements_blind.jsonl --out /tmp/x.md
```
