# theme3.json 配置 + theme3_constituents_v2.py 分析报告

**分析日期**: 2026-10-27
**分析对象**: `solo/theme3.json`（235KB）、`solo/theme3_constituents_v2.py`（35KB）
**数据基准**: `cache_backbone_tushare/theme3_constituents_20261027.json`（最新一次运行产出）

---

## 一、总体结论（TL;DR）

1. **配置体系设计是完整的**：74 个子主题、14 个一级目录，每个主题都有语义（core_semantic）、产业链角色权重（industry_roles）、业务 DNA（business_dna_tags）、负向压流（negative_pressure_tags）、行业软约束（industry_soft_constraints）等 V2 字段，形成"语义 + 业务 + 行业"三元匹配框架。
2. **有三个必须处理的问题**：
   - **P0 数据一致性**：`THEME_FLAT_MAP` 与 `CATEGORIES` 已漂移（70 vs 74 个主题），且生成链已断裂（`theme_v2_meta_part*.py` 只剩 bak0615 备份，`generate_theme3.py` 在当前目录直接 ImportError）；
   - **P0 匹配质量**：DNA Gate 的 4 字符门槛导致"要么过紧、要么失效"——最少的主题只有 2 只股票（新能源汽车材料链），最多的 381 只（充换电与能源补给），相差 190 倍；
   - **P1 噪声污染**：2 字符英文 token（AR/MR）和行业大词（电子/半导体）泄漏进概念匹配，产出中出现"CAR-T细胞疗法"进入消费电子主题、"电子后视镜/电子纸"进入存储芯片主题等明显错误。

---

## 二、theme3.json 配置分析

### 2.1 结构与规模

```
theme3.json
├── CATEGORIES            ← 实际生效的配置（theme3_constituents_v2.py 只读这里）
│   ├── AI             10 个子主题
│   ├── 人形机器人       6
│   ├── 低空经济         4
│   ├── 军工（国防军工）  4
│   ├── 半导体           7
│   ├── 商业航天         3
│   ├── 新能源           6
│   ├── 新能源汽车       5
│   ├── 消费             6
│   ├── 物理AI           5
│   ├── 生物医药         7
│   ├── 资源             5
│   ├── 化工             1
│   └── 金融             5
│   合计 74 个子主题
├── THEME_FLAT_MAP       ← 扁平副本，已漂移（70 个主题）
├── version: V2
└── _generated_from: "theme2.json + theme_v2_meta_part1~4"
```

每个子主题的标准字段（所有 74 个主题字段齐全，无缺漏）：

| 字段 | 作用 |
|---|---|
| `core_semantic[]` | 主题语义描述（3 条） |
| `industry_roles{}` | 产业链角色 → 权重（和≈1.0） |
| `business_dna_tags[]` | 业务 DNA 标签（匹配核心依据） |
| `weak_positive_tags[]` | 弱正向标签 |
| `negative_pressure_tags{}` | 负向压流标签 → 负分 |
| `industry_soft_constraints{}` | 行业软约束 → 权重 |
| `stock_role_mapping{}` | 龙头/中军/补涨的静态定义 |
| `matching_strategy{}` | 全部统一为 `semantic_business_hybrid`，权重 0.55/0.30/0.15 |

### 2.2 设计亮点

- **三元匹配框架清晰**：embedding（语义）/ business（业务 DNA）/ industry（行业）三路加权，`matching_strategy` 74/74 全部显式配置，没有默认值兜底的遗漏。
- **负向压流设计**：每个主题都配了 3-4 个负向标签（如 AI应用 压"游戏/短剧视频/芯片制造/PCB"），用于 IRS 评分扣分和主营排他。
- **主题拆分粒度合理**：AI 目录从早期的大"AI算力链"拆成了算力芯片/光模块与CPO/数据中心网络/散热/高速铜连接 5 个细分主题，符合实际产业链结构。

### 2.3 问题

#### P0-1：THEME_FLAT_MAP 与 CATEGORIES 双向漂移

对比结果（脚本实测）：

| 差异类型 | 明细 |
|---|---|
| 仅 CATEGORIES 有（FLAT_MAP 缺失） | AI算力芯片、光模块与CPO、数据中心网络、数据中心散热、高速铜连接（5 个新拆分主题） |
| 仅 THEME_FLAT_MAP 有（CATEGORIES 已删） | AI算力链（旧合并主题） |
| 共有主题字段不一致 | 低空基础设施、低空数据与控制、低空运营服务、低空飞行器制造（4 个的 `industry_soft_constraints` 不同，如 CATEGORIES 版含"机场"、FLAT_MAP 版是"机场Ⅱ"且权重分布不同）；消费电子（`weak_positive_tags` 不同） |

**根因**：theme3.json 生成后，CATEGORIES 被手工编辑过（AI 拆分、低空/消费电子微调），但 THEME_FLAT_MAP 没有同步重生成。

**影响面**：
- `theme3_constituents_v2.py` 的 `load_theme3_flat()` 优先读 `CATEGORIES`，**不受影响**（这也是目前产出正确的原因）；
- 但读 `THEME_FLAT_MAP` 的脚本（`theme_purity_engine_v4.py`、`theme_trade_selector_v3.6.py`、bak0615 下的若干检查脚本）拿到的是**过期配置**——AI 主题 taxonomy 完全不同、低空 4 主题行业约束失真。

#### P0-2：生成链已断裂，"重跑修复"这条路是堵的

- `generate_theme3.py` 从 `theme2.json` + `theme_v2_meta_part1~4.py` 重新生成 theme3.json，但这 4 个 meta 文件**在当前 solo 目录不存在**（只剩 `bak0615/theme_v2_meta_part*.py`），直接重跑会 **ImportError**。
- 更麻烦的是三份文件的 AI 主题命名互不相同：

| 文件 | AI 目录主题名 |
|---|---|
| theme2.json（现存） | AI芯片、CPO光模块、高速连接、AI存储芯片、AI服务器、数据中心瓶颈硬件链、AI应用、AI模型与AI Agent、AI终端、AI文化娱乐 |
| theme3.json CATEGORIES（现行） | AI应用、AI文化娱乐、AI模型与AI Agent、AI算力芯片、光模块与CPO、数据中心网络、数据中心散热、高速铜连接、数据中心瓶颈硬件链、AI终端 |
| theme3.json THEME_FLAT_MAP（陈旧） | 含已废弃的"AI算力链" |

即使用 bak0615 的 meta 文件勉强重跑，生成的也会是第三套命名。**结论：theme3.json 现在没有可靠的可再生生成路径，CATEGORIES 就是唯一事实源，今后只能程序化同步 FLAT_MAP，不能整文件重生成。**

#### P1-3：配置层面的三个先天不足（会被桥接层放大，见 3.5）

1. **business_dna_tags 大量 2-3 字短词**：锂矿(2)、碳酸锂(3)、电解液(3)、充电桩(3)、船舶(2)、折叠屏(3)、苹果链(3)、摄像头(3)、隔膜(2)、铜箔(2)、HBM(3)、MCU(3)、SoC(3)、GPU(3)、MR/AR/VR(2) —— 这些词恰恰是主题最核心的业务标签，却全部撞上 DNA Gate 的 4 字符门槛（详见 3.3）。
2. **industry_soft_constraints 用了行业大词**：半导体、电子、电力、通用设备、消费电子等。作为 Industry Gate 的候选池来源，一个"半导体"就是 300+ 只股票。
3. **粒度错配**：DNA 标签是技术细目（DRAM/NAND/CoWoS/托卡马克），东财概念板块是粗概念（存储芯片/先进封装/核能核电），"DRAM"这类标签永远映射不到"存储芯片"板块。

---

## 三、theme3_constituents_v2.py 分析

### 3.1 流程架构（9 步）

```
theme3.json (74主题)
  │
  ▼ Step1  load_theme3_flat()          扁平化，挂 _top_category
  │
  ▼ Step2  tts.get_dc_members()        东财板块成分股（Tushare dc_index/dc_member，SQLite缓存）
  │
  ▼ Step3  build_v2_bridge_factory()   ★ V2字段 → 旧字段桥接（核心创新层）
  │         ├─ industry  ← industry_soft_constraints + industry_roles 的键
  │         ├─ concept   ← 全部 token（含行业词，此处有泄漏）
  │         ├─ keywords  ← dna + weak + semantic（截断25个）
  │         ├─ exclude_keywords ← negative_pressure_tags 的键
  │         ├─ dna_concept_required ← DNA Gate 白名单（4~15字符 DNA 标签映射板块）
  │         └─ MANUAL_CONCEPT 手工补强（9个主题硬编码在代码里）
  │
  ▼ Step4  tts.get_stock_basic()
  │
  ▼ Step5  tts.match_theme_stocks()    ★ 匹配漏斗（在 theme_trend_sentiment_score.py）
  │         Phase1  Industry Gate（方式A~G：东财行业板块/申万/stock_basic/概念兜底/同花顺）
  │         Phase1.5 DNA Gate（dna_concept_required 非空时强制过滤）
  │         Phase1.6 主营排他词过滤
  │         Phase2  IRS 四维评分 + 分层（<50 排除）
  │         Phase3  龙头/核心公司强制纳入
  │         Phase4  多主题去重 _disambiguate_multi_theme
  │
  ▼ Step6  compute_kline_metrics()     100日K线：连板/5-10-20-60日涨跌/MA斜率/趋势分(0-100)
  ▼ Step7  compute_market_cap_metrics() daily_basic：总市值/流通市值/换手率
  ▼ Step8  classify_role()             龙头(连板)/中军(成交+趋势)/补涨 + combined_score
  ▼ Step9  导出 theme3_constituents_{日期}.json/.csv
```

### 3.2 桥接层设计评价

`build_v2_bridge_factory` 是整个方案最巧妙的部分：theme3.json 不保存任何东财板块名，全部在运行时基于 `dc_df` 动态映射，板块改名不影响配置。同时用 `INDUSTRY_NOISE`/`CONCEPT_NOISE` 对大行业词降权（÷4），用 `MANUAL_CONCEPT` 给 9 个难映射主题手工兜底。

但这个层有 4 个代码级问题：

1. **概念匹配泄漏行业词**（L140-144）：注释写"concept 用 business/weak/semantic"，实际 `concept_tokens` 用的是 `all_tokens`（含 `ind_soft + ind_roles`）。实测后果：存储芯片主题的 concept 列表里出现 **电子后视镜、电子纸概念、电子身份证**（行业约束里的"电子"2 字符子串命中的）。
2. **英文短 token 误匹配**（L85-129）：`_match_boards` 的 `min_len=2` 对英文缩写太宽。实测后果：消费电子主题的 concept 列表里出现 **CAR-T细胞疗法**（DNA 标签"AR"是"cAR-T"的子串）。
3. **MANUAL_CONCEPT 硬编码**：9 个主题的补强板块名写在代码里，新增主题（如 AI 拆分出的 5 个新主题中只有 AI算力芯片/光模块与CPO/数据中心网络/数据中心散热/高速铜连接 有部分覆盖）容易漏配，且只按 `theme_name` 精确匹配。应移到 theme3.json 配置字段（如 `manual_concept_boards`）。
4. **docstring 过期**：文件头写"13 个一级目录 / 共 70 个子主题"，实际是 **14 / 74**。

### 3.3 DNA Gate：本版本最大的设计缺陷

`dna_concept_required` 的生成条件（L181-188）：

```python
dna_concept_required = _match_boards(
    {_norm(t) for t in tag_tokens if 4 <= len(_norm(t)) <= 15},  # ← 4字符门槛
    CON_NORM, CONCEPT_NOISE, topn=10
)
```

**设计意图是对的**（semantic_business_hybrid 模式下，股票必须命中至少一个业务 DNA 对应的东财概念板块，防止行业溢出），但 4 字符门槛 + 板块名粒度错配，使它走向两个极端：

**极端 A：DNA Gate 名存实亡 → 行业溢出**

| 主题 | DNA 门槛筛后剩余可映射板块 | 结果 |
|---|---|---|
| 充换电与能源补给 | **空**（充电桩=3字符被跳过，只剩"充电模块"且无对应板块） | **381 只**（通用设备/电力/电力设备整条行业板块灌入） |
| 存储芯片 | **空**（DRAM/NAND/Nor Flash/存储控制器都映射不到"存储芯片"板块） | **323 只** |
| 必选消费红利链 | **空**（食品饮料是行业不是概念） | **232 只** |
| 创新医药主线 | 空或极少 | **215 只** |
| 低空飞行器制造 | 飞行汽车(eVTOL)、无人机（低空经济主题反而映射成功） | **175 只**（军工电子/汽车零部件/机械设备/通用设备全进） |

**极端 B：DNA Gate 过紧 → 误杀核心成分**

| 主题 | DNA 白名单 | 结果 | 误杀情况 |
|---|---|---|---|
| 新能源汽车材料链 | 仅 `PVDF概念` | **2 只** | 锂矿/碳酸锂/正极材料全在"能源金属"行业闸门内，但都没有 PVDF 概念 → 被 DNA Gate 全部误杀 |
| 低空运营服务 | 仅 `无人机` | **3 只** | 电力巡检/农林植保/飞行培训/通航运营（均 4 字符但无对应板块）全灭 |
| 脑机接口 | 仅 `脑机接口` | **5 只** | 板块本身股票就少，尚无大碍 |
| 新能源汽车智能化 | 仅 `激光雷达` | **6 只** | 智驾芯片/座舱芯片/域控制器标签映射失败 |
| 船舶制造 | 仅 `海工装备` | **8 只** | "船舶"2 字符被跳过 |
| 消费电子 | 仅 AI手机、被动元件概念 | **10 只** | 折叠屏(3)/苹果链(3)/摄像头(3)全被跳过 |

同一套代码，最大主题 381 只、最小主题 2 只，**相差 190 倍**——这不是主题真实宽度差异，是门槛把 DNA 标签的"字符长度分布"投影成了成分股数量。

### 3.4 动态角色识别（Step 6-8）评价

- `compute_kline_metrics`：100 日窗口、batch 500，指标齐全（连板/多周期涨跌/MA 斜率/站上均线/趋势分归一 0-100），实现稳健，有 `pct_chg` 缺失时用 close 推算的兜底。
- `classify_role` 规则清晰，但**龙头门槛偏低**：`近10日≥2次涨停 + 5日涨幅>5%` 即判龙头，且**没有主题内限额**——热点主题内可能出现十几个"龙头"，稀释了"龙头"的信息量。建议按 `trend_score`/`combined_score` 主题内取 Top 3-5。
- `combined_score = static×2×0.4 + trend×0.4 + amt_norm×0.2`：量纲统一（static 0-50 → ×2 归一），合理。
- 健壮性缺口：K 线/市值拉取失败时静默返回 `{}`，所有股票会落入默认"补涨 + 趋势分 0"，输出照常生成，**无失败告警**。

### 3.5 10-27 运行结果总览

```
74 个主题 / 6016 条主题-股票记录 / 去重 3551 只股票
（平均每只股票属于 1.7 个主题，主题间仍有重叠）
K 线指标覆盖 3516 只，市值指标 3514 只（35 只无 K 线数据的股票 trend_score=0）
```

数量分布（只）：

| 区间 | 主题 |
|---|---|
| <10 | 新能源汽车材料链(2)、低空运营服务(3)、脑机接口(5)、新能源汽车智能化(6)、船舶制造(8)、消费电子(10) |
| 10-60 | 数据中心网络(14)、保险(15)、合成生物(15)、家电家装(13)、低空基础设施(6)… |
| 60-160 | 半导体/机器人/风电等主体区间（健康） |
| >200 | 充换电(381)、存储芯片(323)、晶圆代工与IDM(245)、必选消费红利链(232)、创新医药主线(215)、低空飞行器制造(175) |

---

## 四、修复建议（按优先级）

### P0 — 数据一致性（半小时）

1. **写一个 `sync_flat_map.py`**：从 theme3.json 的 CATEGORIES 程序化重建 THEME_FLAT_MAP（`new_flat_map[theme_name] = cfg`），保证两份永远一致；把 `generate_theme3.py` 归档或改造为"只同步 FLAT_MAP"模式，**禁止再从 theme2.json 整文件重生成**。
2. **修正 `theme3_constituents_v2.py` 文件头 docstring**：13→14 个目录、70→74 个主题。

### P0 — 匹配质量（核心）

3. **DNA Gate 三处放宽 + 降级**：
   - 字符门槛 4 → 3（覆盖充电桩/折叠屏/苹果链/电解液等高频 3 字标签）；
   - 英文缩写（纯 [A-Za-z0-9] 且 ≤4 字符）单独处理：要求全词匹配或词边界正则，杜绝 AR→CAR-T 式误匹配；
   - **DNA 标签映射不到任何板块时降级为关键词匹配**（在股票东财概念名/主营文本里做子串搜索），而不是让整个 Gate 失效——这是修复"381 只溢出"和"2 只误杀"的关键一招。
4. **concept token 剔除行业词**：`concept_tokens` 只用 `tag_tokens + weak_tokens + sem_tokens`（与注释对齐），行业词只进 industry 匹配。可直接消灭"CAR-T/电子后视镜/电子纸"类污染。
5. **大行业词降为软证据**：对 INDUSTRY_NOISE 命中的行业板块（半导体/电子/电力/消费电子/通用设备…），仅当股票同时有 ≥1 个概念/关键词重叠时才入候选；纯行业归属不再直接进池。预计存储芯片 323→~100、充换电 381→~80。
6. **给每个主题加成分股软上限**（如 IRS 分数截断在主题内 P90，或硬上限 250 只），防止个别主题霸占输出体积。

### P1 — 可维护性

7. **MANUAL_CONCEPT 移入配置**：theme3.json 每个主题加 `manual_concept_boards[]` 字段，桥接层读取；补强 AI 拆分后的 5 个新主题（数据中心网络/散热/高速铜连接目前依赖手工表）。
8. **龙头限额**：`classify_role` 后按 combined_score 主题内取 Top N（建议 3-5）标记龙头，其余降级中军/补涨。
9. **失败告警**：K 线/市值指标覆盖率 <95% 时在输出 JSON 里记 `warnings` 字段并打印，避免静默降级。

### P2 — 性能（可选）

10. **K 线增量缓存**：3551 只 × 100 日每天全量重拉，是每次运行最重的一段；按 `ts_code+日期` 做 SQLite 增量缓存后可节省分钟级耗时。

---

## 五、关键事实速查

- theme3.json：14 个一级目录 / 74 个子主题 / 全部 V2 字段齐全 / 匹配策略 100% 统一为 `semantic_business_hybrid (0.55/0.30/0.15)`
- THEME_FLAT_MAP：70 个主题，与 CATEGORIES 有 5 个主题名级差异 + 5 个字段级差异（已漂移，勿直接消费）
- `theme_v2_meta_part1~4.py` 仅存于 `bak0615/`，`generate_theme3.py` 在当前目录无法运行
- theme2.json 现存版本（Jun 14）的 AI 命名与 theme3 两处结构均不同，**不是**当前 theme3 的可靠上游
- 最新产出：`theme3_constituents_20261027.json`，74 主题 / 6016 条 / 去重 3551 只；下游被 `theme3_heat.py`、`theme3_opportunity.py` 消费生成 `report_daily/theme3_*_20261027.json`
- 最大/最小主题：充换电与能源补给 381 只 vs 新能源汽车材料链 2 只（相差 190 倍，根因均为 DNA Gate）

---

## 五、修复实施记录（2026-10-27 晚）

### 已落地
1. **sync_flat_map.py（新增）**：以 CATEGORIES 为唯一事实源重建 THEME_FLAT_MAP（70→74），
   备份 `theme3.json.bak_20261027_223755`；MANUAL_CONCEPT 从代码迁移到 theme3.json
   顶层键 `MANUAL_CONCEPT_BOARDS`（现 11 个主题，含新增低空 2 条）。
2. **theme3_constituents_v2.py**：
   - ASCII token 词边界匹配（`_sub_hit`/`_ascii_hit`）：AR 不再命中 cAR-T细胞疗法，PVD 不再命中 PVDF概念
   - concept token 屏蔽纯行业词 `CONCEPT_TOKEN_BLOCKLIST`（电子/医药/军工等 9 词），"电子后视镜/电子纸"不再混入存储芯片
   - DNA Gate 重构（分级白名单）：DNA 标签映射（中文/ASCII 均≥2字+词边界保护）→ 映射为空才合并 weak/semantic/主题名 → 并入手工板块（过宽泛过滤）→ 仍空则退化为 concept_list；
     成员数 ≥400 的板块不配做 Gate 证据（`GATE_MAX_BOARD_MEMBERS`）
   - 桥接函数签名 `build_v2_bridge_factory(dc_df, manual_concept=None)`，手工表优先读配置
3. **theme_trend_sentiment_score.py**：
   - Phase 1.5 GateLoose 防误杀改为**部分回退**：仅保留与 DNA 白名单或主题 concept 有概念重叠的候选，不再全量回退
   - Phase 4 去重豁免：股票概念板块与主题 DNA 白名单**精确相等**时不参与"只留最佳主题"删除（兆易创新不再被"传感器"抢走，允许多主题归属）

### 修复效果（10-27 数据实测，静态匹配层）
| 类型 | 主题 | 修复前 | 修复后 |
|---|---|---|---|
| 溢出收敛 | 充换电与能源补给 | 381 | 127 |
| 溢出收敛 | 存储芯片 | 323 | 178（兆易创新/澜起/江波龙/佰维全部保留） |
| 溢出收敛 | 灵巧手 | 161 | 22 |
| 溢出收敛 | 伺服电机与运动控制 | 146 | 8 |
| 溢出收敛 | 先进封装 | 160 | 64 |
| 溢出收敛 | 核聚变 | 143 | 93 |
| 误杀缓解 | 新能源汽车材料链 | 2 | 14（天齐锂业/华友钴业回归） |
| 误杀缓解 | 新能源汽车智能化 | 6 | 18（德赛西威/保隆科技回归） |
| 误杀缓解 | 消费电子 | 10 | 54（立讯精密/长盈精密回归） |
| 误杀缓解 | 脑机接口 | 5 | 16（gate=['脑机接口'] 小而准） |
| 误杀缓解 | 低空运营服务 | 3 | 77（gate 补入 低空经济/通用航空） |

### 遗留事项（后续调优）
- AI 家族主题（AI应用 47→175、数据中心网络 14→172、数据中心散热 53→174）扩容较大：
  其概念板块（算力概念/云计算/数字经济）本身宽泛，属 theme3.json 配置层调优问题，
  可考虑给这些主题的 DNA 标签补更具体的板块锚点（交换机/液冷/IDC 等）
- 芯片设计（136→7）、半导体设备（154→12）偏窄，若需放宽可在 MANUAL_CONCEPT_BOARDS 补锚点
- 北京君正/英维克/联创光电在修复前就不在任何主题中（存量缺口），需在 theme3.json
  相应主题补 core_companies 才能强制纳入
