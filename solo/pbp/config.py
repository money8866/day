# -*- coding: utf-8 -*-
"""
PBP（Platform -> Breakout -> Pullback -> Re-Acceleration）V1.0 配置
A股3-5日短线「平台->有效突破->首次健康回踩->再启动」高胜率买点识别引擎
全部参数集中于此，禁止散落硬编码
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOLO_DIR = os.path.dirname(BASE_DIR)

# 数据源路径（复用项目已有缓存）
CACHE_DB_PATH = os.path.join(r"D:\mystock\cache_daily", "stock_data.db")
STOCK_BASIC_CSV = os.path.join(r"D:\mystock\cache_daily", "stock_basic.csv")
TDX_PATH = r"C:\new_tdx"
REPORT_DIR = os.path.join(SOLO_DIR, "output", "pbp")

# ═══════════════════════════════════════════════════════
# 状态机状态（第十六节）
# ═══════════════════════════════════════════════════════
STATE_PLATFORM_BUILDING = 'PLATFORM_BUILDING'
STATE_PLATFORM_CONFIRMED = 'PLATFORM_CONFIRMED'
STATE_NEAR_BREAKOUT = 'NEAR_BREAKOUT'
STATE_BREAKOUT_PENDING = 'BREAKOUT_PENDING'
STATE_BREAKOUT_CONFIRMED = 'BREAKOUT_CONFIRMED'
STATE_FIRST_PULLBACK = 'FIRST_PULLBACK'
STATE_PULLBACK_SUPPORT = 'PULLBACK_SUPPORT'
STATE_RE_ACCELERATION = 'RE_ACCELERATION'
STATE_PRIMARY_BUY = 'PRIMARY_BUY'
STATE_HOLD = 'HOLD'
STATE_EXIT = 'EXIT'
STATE_INVALIDATED = 'INVALIDATED'
STATE_BREAKOUT_FAILED = 'BREAKOUT_FAILED'
STATE_PULLBACK_FAILED = 'PULLBACK_FAILED'

# 交易结论（第十七节，封闭选项集）
ACTION_WAIT_PLATFORM = 'WAIT_PLATFORM'
ACTION_WAIT_BREAKOUT = 'WAIT_BREAKOUT'
ACTION_WAIT_PULLBACK = 'WAIT_PULLBACK'
ACTION_WAIT_REACCELERATION = 'WAIT_REACCELERATION'
ACTION_EARLY_BUY = 'EARLY_BUY'
ACTION_PRIMARY_BUY = 'PRIMARY_BUY'
ACTION_CONFIRMED_BUY = 'CONFIRMED_BUY'
ACTION_NO_TRADE = 'NO_TRADE'
ACTION_BREAKOUT_FAILED = 'BREAKOUT_FAILED'
ACTION_PULLBACK_FAILED = 'PULLBACK_FAILED'

PBP_CONFIG = {
    # ── 数据要求 ──
    "min_bars": 130,                # 最少K线数（60日平台 + MA60缓冲）
    "lookback_bars": 280,           # 单次评估回看最大K线数
    "breakout_search_days": 15,     # 从T日往回搜索突破日的最大天数

    # ═════════════════════════════════════════════
    # 第一阶段：平台识别（第二节）
    # ═════════════════════════════════════════════
    "platform_days_min": 10,        # 平台最少交易日
    "platform_days_best": (15, 40), # 最佳区间
    "platform_days_max": 60,        # 超过60日降低权重
    "platform_range_good": 0.15,    # 优秀：≤15%
    "platform_range_ok": 0.22,      # 合格：15%~22%
    "platform_range_wide": 0.30,    # 宽平台：22%~30%；>30% 不认定
    "resistance_cluster_atr": 1.2,  # 高点聚类容差（ATR倍数）
    "resistance_test_min": 2,       # 上沿最少测试次数
    "resistance_test_good": 3,      # 上沿优选测试次数
    "support_test_min": 2,          # 下沿最少承接次数

    # ── 平台评分门槛（第三节）──
    "platform_score_min": 75,       # 进入突破识别的门槛
    "platform_score_a_plus": 85,
    "platform_score_a": 75,
    "platform_score_b": 65,

    # ═════════════════════════════════════════════
    # 第二阶段：突破识别（第四节）
    # ═════════════════════════════════════════════
    "breakout_atr_buffer": 0.3,     # Close > BreakoutLevel + 0.3*ATR20
    "breakout_atr_buffer_good": 0.5,
    "breakout_pct_min": 0.010,      # 突破幅度 ≥1.0%
    "breakout_pct_good": 0.015,     # 优选 ≥1.5%
    "breakout_pct_strong": 0.02,    # 极强 ≥2%
    "breakout_pct_excessive": 0.07, # >7% 警惕短线高潮
    "breakout_pct_ban": 0.08,       # ≥8% 爆涨过度，不作 PRIMARY BUY
    "breakout_vol_ratio_min": 1.30, # VolumeRatio ≥1.30
    "breakout_vol_ratio_ideal": (1.50, 2.50),
    "breakout_vol_ratio_weak": 1.10,# <1.10 量能不足
    "breakout_close_loc_min": 0.75, # 收盘位置 ≥0.75
    "breakout_close_loc_good": 0.85,
    "breakout_confirm_days": 3,     # 突破后观察1~3日
    "breakout_fail_close_below": 1, # 单日收盘跌破突破位：重扣分
    "breakout_fail_close_below_run": 2,  # 连续2日收盘回平台 -> FAILED
    "post_breakout_max_atr": 2.0,   # 当前价 > BreakoutLevel + 2*ATR 禁追高

    # ── 突破评分门槛（第五节）──
    "breakout_score_min": 75,       # 进入回踩识别的门槛（且平台≥75）
    "breakout_score_strong": 85,

    # ═════════════════════════════════════════════
    # 第三阶段：首次健康回踩（第六、七节）
    # ═════════════════════════════════════════════
    "pullback_days_ideal": (1, 5),  # 优选1~5日
    "pullback_days_best": (2, 3),   # 最佳2~3日
    "pullback_days_max": 7,         # 超过7日降分
    "pullback_depth_ideal": (0.20, 0.60),  # 优选20%~60%
    "pullback_depth_too_shallow": 0.15,    # <15% 换手不足
    "pullback_depth_too_deep": 0.80,       # >80% 风险显著
    "pullback_level_atr_tolerance": 0.8,   # PullbackLow ≥ BreakoutLevel - 0.8*ATR
    "pullback_level_atr_ban": 1.0,         # 跌破 - 1.0*ATR：严禁买入
    "pullback_vol_ratio_good": 0.65,       # 回踩量/突破量 ≤0.65
    "pullback_vol_ratio_ok": 0.80,         # 合格 ≤0.80
    "pullback_vol_ban": 1.00,              # 回踩日量 > 突破日量：大幅扣分
    "pullback_high_vol_ban": 0.90,         # 回踩单日量 > 0.9*突破量且当日下跌：严重抛压

    # ── 回踩结束证据（第八节）：至少满足2项 ──
    "pullback_end_evidence_min": 2,

    # ═════════════════════════════════════════════
    # 第五阶段：重新转强（第九节）
    # ═════════════════════════════════════════════
    "reacc_vol_ratio_min": 1.10,    # 转强日量比 ≥1.10
    "reacc_close_loc_min": 0.70,    # 转强日收盘位置 ≥0.70
    "reacc_close_loc_good": 0.80,

    # ── S级 PRIMARY BUY 硬性条件（第十节）──
    "s_platform_score_min": 80,
    "s_breakout_score_min": 80,
    "s_pullback_vol_ratio_max": 0.75,
    "s_close_loc_min": 0.75,

    # ── B级 CONFIRMED BUY（第十一节）──
    "confirmed_vol_ratio_min": 1.2, # 二次突破量比 >1.2

    # ═════════════════════════════════════════════
    # 最终100分模型（第十五节）
    # ═════════════════════════════════════════════
    "final_grade_s": 90,            # ≥90 S级 PRIMARY BUY
    "final_grade_strong": 85,       # 85~89 强买点
    "final_grade_a": 78,            # 78~84 A级观察/轻仓
    "final_grade_wait": 70,         # 70~77 等待确认；<70 不交易

    # ═════════════════════════════════════════════
    # 市场过滤器（第十三节）
    # ═════════════════════════════════════════════
    "market_filter": {
        "bull":  {"allow": ("PRIMARY_BUY", "EARLY_BUY", "CONFIRMED_BUY"), "min_final": 70},
        "neutral": {"allow": ("PRIMARY_BUY", "EARLY_BUY"), "min_final": 78},
        "weak":  {"allow": ("PRIMARY_BUY",), "min_final": 90},
        "bear":  {"allow": (), "min_final": 101},
    },
    # weak/bear 下"极强行业龙头、极强突破"豁免线
    "extreme_final": 90,
    "extreme_theme_rank": 0.10,     # 行业排名前10%

    # ── 行业/主题共振（第十四节，scanner 层计算后传入）──
    "theme_lookback_days": 5,
    "theme_up_ratio_min": 0.50,     # 行业上涨家数占比 >50%

    # ── 输出 ──
    "min_list_days": 120,           # 上市不足120日剔除（次新股波动异常）
}
