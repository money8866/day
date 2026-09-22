# -*- coding: utf-8 -*-
"""
SLI V1.0 配置
Subsector Leader Index —— A股细分行业龙头量化识别引擎
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 目录 ──────────────────────────────────────────────
DATA_DIR = os.path.join(BASE_DIR, "data")     # 原始数据快照
CACHE_DIR = os.path.join(BASE_DIR, "cache")    # parquet 缓存
OUTPUT_DIR = os.path.join(BASE_DIR, "output")  # CSV 输出
LOG_DIR = os.path.join(BASE_DIR, "logs")       # 日志
DB_DIR = os.path.join(BASE_DIR, "db")          # SQLite
DB_PATH = os.path.join(DB_DIR, "sli.db")
CONFIG_DIR = os.path.join(BASE_DIR, "config")  # V2 产业知识 JSON

for _d in (DATA_DIR, CACHE_DIR, OUTPUT_DIR, LOG_DIR, DB_DIR):
    os.makedirs(_d, exist_ok=True)

# ── Token 来源 ────────────────────────────────────────
ENV_CANDIDATES = [
    os.path.join(BASE_DIR, ".env"),
    os.path.join(BASE_DIR, "..", "config", ".env"),
    os.path.join(BASE_DIR, "..", "..", "config", ".env"),
    r"d:/mystock/config/.env",
]

# ── 数据源 ────────────────────────────────────────────
RATE_LIMIT_MS = 120          # 全局限流 120ms（约8次/秒，线程安全）
API_RETRY = 3                # 失败重试次数
API_RETRY_DELAY = 2.0        # 重试基础等待（秒）
CACHE_EXPIRE_HOURS = 6       # 行情类缓存过期（小时）
FIN_CACHE_EXPIRE_HOURS = 48  # 财务类缓存过期
CLS_CACHE_EXPIRE_HOURS = 72  # 行业分类/成分缓存过期

# 行情回看：为 T-120 处的 MA120 / RS120 留足窗口
LOOKBACK_TRADING_DAYS = 320

# ── 财务报告期（截至 2026-08，向前覆盖约3年） ──────────
FINANCIAL_PERIODS = [
    "20260630", "20260331", "20251231", "20250930",
    "20250630", "20250331", "20241231", "20240930",
    "20240630", "20240331", "20231231", "20230930",
]
# 主营构成仅需最近 2 期
MAINBZ_PERIODS = ["20251231", "20241231"]

# ── 生命周期快照点（交易日偏移） ───────────────────────
LIFECYCLE_OFFSETS = {"T": 0, "T20": 20, "T60": 60, "T120": 120}

# ══════════════════════════════════════════════════════
# SLI 权重
# SLI = Scale*30% + Profit*25% + Growth*10% + Purity*10%
#       + Moat*10% + Market*10% + Trend*5%
# 20260830 决策：Growth 20%→10%（利润增速是过去指标，短期下滑
# 不应否决产业龙头地位），Scale/Profit 各 +5%
# ══════════════════════════════════════════════════════
SLI_WEIGHTS = {
    "scale": 0.30,
    "profit": 0.25,
    "growth": 0.10,
    "purity": 0.10,
    "moat": 0.10,
    "market": 0.10,
    "trend": 0.05,
}

# 纯度置信度 → 权重折扣
PURITY_CONF_DISCOUNT = {"HIGH": 1.0, "MEDIUM": 0.85, "LOW": 0.50}

# ══════════════════════════════════════════════════════
# SLI V2 权重
# SLI_V2 = Industry*25% + Product*20% + ProfitQ*15% + Growth*15%
#          + ProductPurity*10% + Moat*5% + Market*5% + Trend*5%
# ══════════════════════════════════════════════════════
SLI_V2_WEIGHTS = {
    "industry": 0.25,
    "product": 0.20,
    "profit": 0.15,
    "growth": 0.15,
    "purity": 0.10,
    "moat": 0.05,
    "market": 0.05,
    "trend": 0.05,
}

# ProductPosition 权重（Capacity/市占率缺失时自动剔除并重归一化）
PRODUCT_POS_W = {
    "revenue": 0.40, "profit": 0.30, "capacity": 0.20, "mv": 0.10,
}
# ProductPurity 置信度 → 权重折扣
PRODUCT_CONF_DISCOUNT = {"HIGH": 1.0, "MEDIUM": 0.85, "LOW": 0.50}

# ProfitQuality 权重（V2：ROIC 升至第2权重）
PROFIT_Q_W = {
    "roe": 0.30, "roic": 0.25, "gm": 0.20, "nm": 0.15, "cfq": 0.10,
}

# ══════════════════════════════════════════════════════
# 子评分权重
# ══════════════════════════════════════════════════════
SCALE_W = {"revenue": 0.40, "mv": 0.30, "gross_profit": 0.20, "asset": 0.10}
PROFIT_W = {
    "roe": 0.30, "gross_margin": 0.25, "net_margin": 0.20,
    "roic": 0.15, "cashflow": 0.10,
}
GROWTH_W = {
    "rev_growth": 0.35, "profit_growth": 0.35,
    "roe_growth": 0.15, "margin_expansion": 0.15,
}
MOAT_W = {
    "gm_adv": 0.30, "roe_adv": 0.25, "nm_adv": 0.20,
    "cfq_adv": 0.15, "rd": 0.10,
}
MARKET_W = {"rs20": 0.35, "rs60": 0.35, "rs120": 0.20, "liq": 0.10}

# ══════════════════════════════════════════════════════
# 成长/加速加分
# ══════════════════════════════════════════════════════
ACCEL_BONUS_STRONG = 10.0   # 最近3期利润增速连续加速
ACCEL_BONUS_MILD = 5.0      # 最近2期利润增速加速
SUSTAINED_MOAT_BONUS = 5.0  # 连续3年盈利领先行业中位数
SUSTAINED_PROFIT_BONUS = 3.0

# ══════════════════════════════════════════════════════
# 龙头分类阈值
# ══════════════════════════════════════════════════════
CLS = {
    "absolute": {
        "sli": 85, "scale": 80, "profit": 70, "purity": 40,
        "rank_max": 2,          # 行业排名前2
    },
    "growth_leader": {"growth": 85, "sli": 75},
    "profit_leader": {"profit": 85},
    "challenger": {"sli": 75, "growth": 80, "profit": 80, "market": 80},
    "emerging": {"sli": 65, "growth": 85, "market": 80, "profit": 70},
    "acceleration": {"sli": 75, "growth": 80, "market": 75, "sli60_delta": 5.0},
    "next_leader": {"sli_lo": 65, "sli_hi": 85, "growth": 85, "market": 80, "profit": 70},
}

# LeaderGap 分级
LEADER_GAP_BANDS = [
    (12.0, "DOMINANT"),
    (8.0, "STRONG_LEADER"),
    (4.0, "CLOSE_CONTEST"),
    (-1.0, "FRAGMENTED"),
]

# 行业样本下限
LOW_SAMPLE_N = 5

# 与交易系统对接
TRADE_ALPHA_W = {
    "sli": 0.25, "er20": 0.20, "industry": 0.15, "growth_accel": 0.15,
    "rs": 0.10, "hvt": 0.10, "trend": 0.05,
}

# 特殊标签
SPECIAL_TAGS = ["LEADER_NO_TRADE", "LEADER_EARNINGS_TURN",
                "LEADER_BREAKOUT", "NEXT_LEADER", "NEXT_LEADER_V2"]

# ══ 池命名（项目内唯一，供下游策略统一引用）══════════════
# 中长线潜力池（全池，无 mid_long_score 额外门槛）：
#   产出 classify.next_leader_v2 → 面板布尔列 NEXT_LEADER_V2
#   读取 sli.reader.get_next_leaders()
# 与 V1 special_tags 的 NEXT_LEADER（SLI 65~85 的「下一代龙头」）区分开——
# 两者曾共用同名列，下游无法判断读到的是哪套口径。
POOL_MIDLONG_V2 = "NEXT_LEADER_V2"

# ══════════════════════════════════════════════════════
# SLI V2 龙头分类阈值
# ══════════════════════════════════════════════════════
CLS_V2 = {
    "absolute": {
        "sli": 85, "industry": 80, "product": 80, "purity": 40,
        "rank_max": 2,          # 细分赛道内排名前2
    },
    "product_leader": {"product": 90},   # 产品级绝对龙头
    "growth_leader": {"growth": 85, "sli": 75},
    "profit_leader": {"profit": 85},
    "challenger": {"sli": 75, "growth": 80, "profit": 80, "market": 80},
    "emerging": {"sli": 65, "growth": 85, "market": 80,
                 "sli60_delta": 5.0},
    "next_leader": {
        # 硬门槛仅3项（细分地位/成长/SLI），按行业景气档位阶梯化：
        # 高景气行业放宽细分龙头门槛，低景气行业收紧。
        # 原 market/sli60_delta 硬门槛已降级为打分项（见 score_w）——market_v2
        # 在 SLI_V2 里权重仅 5%，作为硬门槛却一票否决掉 2/3 候选，逻辑不自洽。
        "boom_tiers": {
            "高": {"sli": 58, "growth": 72, "product": 52},
            "中": {"sli": 65, "growth": 80, "product": 70},
            "低": {"sli": 72, "growth": 85, "product": 82},
        },
        # ind_boom 分档切点：行业横截面【分位】而非分数，保证三档各约占 1/3
        "boom_cuts": (0.3333, 0.6667),
        # ind_boom 权重：行业主力成长 / 行业主力利润加速 / 价格相对强度
        # 三个分项均按「市值加权」度量行业主力，避免尾部小市值公司稀释景气判定
        "boom_w": {"growth": 0.50, "accel": 0.30, "rs": 0.20},
        # 动量门槛（替代原 85 分数上界）：要求 SLI_V2 当期相对 60 日前未明显
        # 回落。原上界「SLI_V2>85 即剔除」把产业地位最强、盈利质量最高的一批
        # （紫金矿业/卫星化学/石药创新等）系统性剔除，与「中长线有潜力上涨」
        # 目标方向相反——真正该问的是「还在不在变强」，而非「分数是否太高」。
        "sli_drop_tol": 2.0,
        # 打分项权重（合计 1.0）：mid_long_score 用于池内排序与入选阈值
        "score_w": {
            "sli": 0.20,          # 产业地位（中长线根基，避免唯便宜/唯成长）
            "market": 0.15,       # 赛道内相对强度
            "sli60_delta": 0.15,  # SLI_V2 60日改善幅度
            "confirm": 0.15,      # 增长确认项数（利润加速/产品增长/份额提升）
            "valuation": 0.20,    # 三级行业内 pe_ttm 分位（越便宜越高分）
            "size": 0.15,         # 市值规模（流动性 / 抗经营波动）
        },
        "size_full_mv": 100.0,  # 市值达标线（亿元）：达到即满分，不奖励巨型股
        # mid_long_score 入选阈值。经 sli.backtest 20230630~20260921（40 期点）
        # 校准：池相对全市场等权超额 20日+0.3% / 60日+2.0% / 120日+3.6% /
        # 250日+5.3%（t=2.0），相对沪深300 250日+17.5%（t=4.7），超额随持有期
        # 拉长而扩大；阈值 55~82 的差异在噪声内，70 在 250 日口径最优。
        "score_min": 70.0,
        "confirm_min": 1,     # 增长确认项数下限（纯0项说明成长故事无支撑）
        "val_neutral": 50.0,  # 估值分位不可用（亏损/缺失）时的中性分
    },
    "super_leader": {"sli": 85, "growth": 70, "ind_rs60": 0.0,
                     "accel": 0.0},   # 高龙头质量 × 高行业景气 × 利润加速
}

# Dominance：40%SLI差距 + 30%产品规模差距 + 20%盈利差距 + 10%市占率差距
DOMINANCE_W = {"sli": 0.40, "product": 0.30, "profit": 0.20, "share": 0.10}
DOMINANCE_BANDS = [
    (20.0, "DOMINANT"),
    (10.0, "STRONG_LEADER"),
    (5.0, "COMPETITIVE"),
    (-1.0, "FRAGMENTED"),
]

# ChallengerScore：30%SLI增长 + 25%Growth + 20%ProductPosition增长
#                  + 15%Profit增长 + 10%RelativeStrength
CHALLENGER_W = {"sli_growth": 0.30, "growth": 0.25, "product_growth": 0.20,
                "profit_growth": 0.15, "rs": 0.10}
CHALLENGER_THRESHOLD = 80.0

# 回测配置
BACKTEST_PERIODS = [
    "20260630", "20260331", "20251231", "20250930",
    "20250630", "20250331", "20241231", "20240930",
    "20240630", "20240331", "20231231", "20230930",
    "20230630", "20230331", "20221231", "20220930",
    "20220630", "20220331", "20211231", "20210930",
]
BACKTEST_YEARS = [2023, 2024, 2025, 2026]
BACKTEST_HORIZONS = [20, 60, 120, 250]
BACKTEST_BENCH = {"hs300": "000300.SH", "csi1000": "000852.SH"}
# 中长线潜力池入选阈值校准网格（score_min 候选值）
BACKTEST_NL_SCORE_GRID = [55, 60, 65, 70, 72, 75, 78, 80, 82]

# 市场范围
INCLUDE_BJ = False   # 龙头池剔除北交所（下游交易系统不交易北交所）
FILTER_ST = True     # 剔除 ST/*ST

# ── 10个测试行业（人工合理性验证） ─────────────────────
TEST_INDUSTRIES = [
    "钛白粉", "锂电池", "机器人", "光伏设备", "医疗器械",
    "印制电路板", "白酒", "工程机械整机", "航空发动机",
    "服务器",
]
