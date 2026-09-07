# -*- coding: utf-8 -*-
"""
IGE v1.2 配置
行业增长弹性计算参数（权重/阈值/报告期/路径）
v1.2 = v1.1 微调升级：仅新增 IGE_MOM / IGE_PERSISTENCE / IGE_EFFECTIVE /
      IGE_OPPORTUNITY_TYPE 及类型微调与分层排序，不改动五因子与基础评分。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SLI_CACHE_DIR = os.path.join(BASE_DIR, "..", "sli", "cache")   # 复用 SLI parquet 缓存
OUTPUT_DIR = os.path.join(BASE_DIR, "output")                    # IGE CSV 输出
for _d in (OUTPUT_DIR,):
    os.makedirs(_d, exist_ok=True)

# ── 运行参数 ──────────────────────────────────────────
SNAPSHOT_DATE = "20260901"       # 快照日（最新交易日）
SNAPSHOT_DATE = os.getenv("IGE_DATE", SNAPSHOT_DATE)

# ── 财务报告期（截至 2026-08，12 期覆盖约3年）────────────
# 与 sli.config.FINANCIAL_PERIODS 保持一致
FINANCIAL_PERIODS = [
    "20260630", "20260331", "20251231", "20250930",
    "20250630", "20250331", "20241231", "20240930",
    "20240630", "20240331", "20231231", "20230930",
]
# IGE 主窗口：最近 4 个报告期 (q0 最新) 及各自同比基期
# q0=2026H1, q1=2026Q1, q2=2025年报, q3=2025Q3（对应报告期同比增速序列）
Q_PAIRS = [
    ("20260630", "20250630"),
    ("20260331", "20250331"),
    ("20251231", "20241231"),
    ("20250930", "20240930"),
]

# ── 三级行业样本收缩阈值（N=样本数量门控）───────────────
SAMPLE_FULL = 15     # N>=15 只用三级
SAMPLE_MIX = 10      # 10<=N<15 → 70%三级+30%二级；N<10 → 40/40/20

# ── 五大核心因子权重 ──────────────────────────────────
W_DEMAND = 0.25
W_PROFIT_EL = 0.25
W_ACCEL = 0.20
W_SUPPLY = 0.15
W_MARKET = 0.15

# F1 需求增长：DemandScore = 50%DG + 30%DA + 20%Slope
W_DG, W_DA, W_SLOPE = 0.50, 0.30, 0.20
# F2 利润弹性：ProfitElasticityScore = 70%IPE(龙头中位数) + 30%IPE_BROAD(行业整体)
W_IPE, W_IPE_BROAD = 0.70, 0.30
# F3 景气加速度：RevenueAcceleration 35% + ProfitAcceleration 45% + MarginAcceleration 20%
W_RA, W_PA, W_MA = 0.35, 0.45, 0.20
# F5 行业股价弹性：20D/60D/120D 超额收益 + 盈利加速→超额收益 传导β（v1.1）
# 窗口超额收益(20/60/120D)部分合计占 F5 的 (1-W_MKT_BETA)，β 分位占 W_MKT_BETA；
# β 缺失时自动在窗口部分重归一化（weighted_blend fail-soft）。
W_XR20, W_XR60, W_XR120 = 0.30, 0.30, 0.40
W_MKT_BETA = 0.40
BETA_MIN_OBS = 6      # F5 传导β 回归最少行业成分样本（个股观测数）

# 龙头取数：每行业 Top 5~10 龙头（按 SLI_V2 得分优先，缺失则按营收规模）
LEADER_MIN = 5
LEADER_MAX = 10

# ── 行业生命周期修正 ──────────────────────────────────
LIFECYCLE_ADJ = {
    "EARLY_EXPANSION": 5,
    "ACCELERATION": 10,
    "MATURE_GROWTH": 0,
    "DECELERATION": -10,
    "CONTRACTION": -20,
}

# ── 个股相对行业弹性 SIA = 60% SIA_PROFIT + 40% SIA_PRICE
W_SIA_PROFIT, W_SIA_PRICE = 0.60, 0.40
W_IGE, W_SIA = 0.70, 0.30

# ── IGE 输出等级阈值（行业弹性等级，基于 IndustryElasticity）─
GRADE_BANDS = [
    (90, "EXTREME_ELASTICITY"),
    (80, "HIGH_ELASTICITY"),
    (70, "MEDIUM_HIGH_ELASTICITY"),
    (60, "NEUTRAL_ELASTICITY"),
    (50, "LOW_ELASTICITY"),
    (0, "VERY_LOW_ELASTICITY"),
]

# ROCKET / 交易门限
ROCKET_IGE_MIN = 75.0
ROCKET_BLOCK_IGE = 60.0

# ── Industry Elasticity State（v1.1）──────────────
# igemix 高但生命周期 DECELERATION/CONTRACTION → ELASTICITY_TRAP（禁止进 T120_ROCKET 核心池）
TRAP_IGE_MIX = 70.0

# 行业类型模板（v1.1）：仅决定解释文案与标签，不参与打分
# L1 名称 → 行业弹性类型（其余行业按增长/弹性分位数兜底，见 factors.industry_type_of）

# ── 数据清洗参数 ──────────────────────────────────────
WINSOR_LO = 1          # 极端值 winsorize 1%~99%
WINSOR_HI = 99
MIN_ELASTIC_REV_YOY = 5.0   # 利润弹性仅在 营收增速>=5% 时计算（防微小基数失真）
MAX_ELASTIC_ABS = 300.0     # 利润弹性原始值绝对值上限（超出视为异常先截断）

# 价格窗口（交易日）
LOOKBACK_WINDOWS = {"w20": 20, "w60": 60, "w120": 120}
PRICE_EXTRA_DAYS = 15        # 行情文件多加载缓冲
LIST_CUT_BACK = 120          # 剔除上市不足 120 个交易日的次新股

# F4 供需价格弹性：本期无产品价格数据源 → 用 毛利率水平 作为传导代理
SUPPLY_BASIS = "MARGIN_LEVEL_PROXY_NO_PRICE_DATA"

# ── IGE v1.2 增强变量（只新增，不改动五因子权重）──────────
# IGE_MOM：行业弹性动量（成分趋势动量口径，用户确认）。
# 每层级内对各趋势信号 winsorize+percentile 后加权：
#   营收增速 4 期斜率(dslope) 35% + 利润增速 4 期斜率(pslope) 30%
#   + 毛利率环比趋势(macc) 15% + 60D-20D 超额收益动能 20%
W_MOM_REV, W_MOM_PROF, W_MOM_MARGIN, W_MOM_MKT = 0.35, 0.30, 0.15, 0.20
# 逐级计算 mom_raw（0~100）→ L3/L2/L1 样本收缩混合 → 最后对 L3 行业横截面 rank 0~100
MOM_BANDS = [          # (下限, 标签) —— IGE_MOM 解读档
    (75, "STRONG_ACCEL"),
    (60, "MODERATE"),
    (40, "NEUTRAL"),
    (25, "WEAKENING"),
    (0, "DETERIORATING"),
]

# IGE_PERSISTENCE：行业增长持续性（权重 30/30/20/20，规格 §5）
W_PERS_REV, W_PERS_PROF, W_PERS_GM, W_PERS_XR = 0.30, 0.30, 0.20, 0.20
PERS_BANDS = [         # (下限, 标签) —— IGE_PERSISTENCE 解读档
    (75, "HIGH_PERSISTENCE"),
    (60, "MEDIUM_HIGH"),
    (45, "NORMAL"),
    (30, "LOW"),
    (0, "VERY_LOW"),
]

# IGE_EFFECTIVE = IGE_ADJ × ConfidenceMultiplier（仅交易决策用，不改 IGE_ADJ）
CONF_MULT = {"HIGH": 1.00, "MEDIUM": 0.90, "LOW": 0.80}

# 高弹性 / 加速 / 持续性 / 领先度的统一判定阈值
HIGH_IGE = 70.0         # 通用"高 IGE_ADJ"分界（ER20/T20 排序用）
MOM_STRONG = 60.0       # IGE_MOM ≥60 → 温和改善/加速
PERS_GOOD = 60.0        # IGE_PERSISTENCE ≥60 → 有持续性
LEADER_IGE = 80.0       # 类型判定用高 IGE 界
LEADER_SIA = 80.0       # 行业领跑 SIA 界（INDUSTRY_LEADER）
TOP_SIA = 90.0          # STOCK_LEADER 的个股碾压界
ONLY_SIA_LOW = 70.0     # INDUSTRY_ONLY：行业强但个股未跑赢行业（SIA<70）
LOW_IGE_BOUND = 50.0    # LOW_ELASTICITY：IGE_ADJ<50
# STRUCTURAL_ELASTICITY 微调（§6）：仅 TECHNOLOGY/GROWTH 可用
STRUCTURAL_ELASTICITY_IGE = 75.0
# CYCLICAL_HIGH_ELASTICITY（§6）：周期/价格驱动型即使 IGE_ADJ≥80，持续性不足仍须标注
CYCLICAL_HIGH_ELASTICITY_IGE = 80.0
CYCLICAL_HIGH_ELASTICITY_PERS = 50.0

# T120_ROCKET_CORE（§12 最严格行业门槛，§7 陷阱强制 FALSE）
CORE_IGE_EFFECTIVE = 75.0
CORE_MOM = 60.0
CORE_PERS = 60.0
CORE_SIA = 75.0
CORE_LIFECYCLE = ("EARLY_EXPANSION", "ACCELERATION")

# ── 排序分层（§13：不再简单按 IndustryElasticity DESC）─────
RANK_TIER = {          # 分层号越小优先级越高；0 = 不满足高 IGE 前提
    1: "IGE_HIGH+MOM_ACCEL+SIA_HIGH",
    2: "IGE_HIGH+PERS+SIA_HIGH",
    3: "IGE_HIGH+SIA_HIGH",
    4: "IGE_HIGH+SIA_LOW",
    5: "IGE_HIGH+DECELERATING",
}
RANK_HIGH_IGE = 70.0   # 高 IGE 分层前提
RANK_HIGH_MOM = 60.0
RANK_HIGH_PERS = 60.0
RANK_SIA_HIGH = 75.0
RANK_SIA_LOW = 50.0
