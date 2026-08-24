# -*- coding: utf-8 -*-
"""
PRB（Platform-Reacceleration Breakout）V1.0 配置
A股3-5日短线「平台->有效突破->首次健康回踩->再启动」高胜率买点识别引擎
全部参数集中于此，禁止散落硬编码
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOLO_DIR = os.path.dirname(BASE_DIR)

# 数据源路径（复用 uDC 统一日线缓存）
CACHE_DB_PATH = os.path.join(r"D:\mystock\cache_daily", "stock_data.db")
STOCK_BASIC_CSV = os.path.join(r"D:\mystock\cache_daily", "stock_basic.csv")
TDX_PATH = r"C:\new_tdx"
REPORT_DIR = os.path.join(SOLO_DIR, "output", "prb")

# ═════════════════════════════════════════════
# 全局参数
# ═════════════════════════════════════════════
PRB_CONFIG = {
    # -- 数据要求 --
    "min_bars": 160,                    # 最少K线数（120日平台回看 + 40日缓冲）
    "lookback_bars": 300,               # 单次评估回看的最大K线数

    # ═══ 阶段一：平台识别（第二/三节）═══
    # -- 平台时间 --
    "platform_min_days": 10,            # 平台最短交易日
    "platform_ideal_days": (15, 40),    # 最佳平台长度区间
    "platform_max_days": 60,            # 平台最长交易日（超过降权重）
    "platform_very_long": 80,           # 超长平台（进一步降权）

    # -- 平台价格区间 --
    "platform_range_excellent": 0.15,   # 优秀平台振幅 (≤15%)
    "platform_range_qualified": 0.22,   # 合格平台振幅 (≤22%)
    "platform_range_wide": 0.30,        # 宽平台 (≤30%)
    "platform_range_max": 0.30,         # >30% 不认定为高质量平台（硬上限）
    "platform_range_atr_max": 8.0,      # PlatformRange/ATR20 上限（ATR自适应）

    # -- 平台波动收敛（前半段 vs 后半段）--
    "convergence_ratios": {             # 收敛判定：后半段/前半段 ≤ 阈值
        "atr": 0.90,                    # ATR
        "true_range": 0.90,             # 真实波幅
        "amplitude": 0.90,              # 日振幅均值
        "close_std": 0.90,              # 收盘价标准差
    },
    "ma20_dev_max": 0.06,               # 平台后段 MA20 偏离上限

    # -- 平台高点重复测试 --
    "resistance_atol_atr": 1.0,         # 高点误差容差（ATR 数，≤1.0~1.5）
    "resistance_min_tests": 2,          # 上沿最少测试次数（硬门槛）
    "resistance_good_tests": 3,         # 优质测试次数

    # -- 平台底部承接 --
    "support_atol_atr": 1.0,            # 低点误差容差（ATR 数）
    "support_min_tests": 2,             # 下沿最少承接次数（硬门槛）

    # -- 平台成交量收缩 --
    "platform_vol_shrink": 0.80,        # 后半段量/前半段量 优选取值上限
    "platform_vol_hard": 1.0,           # 后半段量/前半段量 硬上限（不能放大量阴跌）

    # -- 平台均线结构 --
    "platform_ma20_slope_min": -0.02,   # MA20 不明显向下（-2%）
    "platform_ma60_slope_min": -0.03,   # MA60 不明显向下（-3%）

    # -- 平台质量门槛（第三节）--
    "platform_score_gate": 75.0,        # PLATFORM_SCORE >= 75 才进入突破识别
    "platform_score_a_plus": 85.0,      # A+ 级平台
    "platform_score_a": 75.0,           # A 级平台
    "platform_score_b": 65.0,           # B 级平台

    # -- PLATFORM_SCORE 权重（100分制，第三节）--
    # 时间10 + 收敛15 + 高点测试15 + 承接10 + 量缩15 + MA20结构10 + MA60结构5 + 涨跌平衡10 + ATR压缩10
    "platform_weights": {
        "duration": 10.0,
        "convergence": 15.0,
        "resistance_tests": 15.0,
        "support_tests": 10.0,
        "volume_shrink": 15.0,
        "ma20_structure": 10.0,
        "ma60_structure": 5.0,
        "balance": 10.0,
        "atr_compression": 10.0,
    },

    # ═══ 阶段二：突破识别（第四/五节）═══
    # -- 突破价格条件 --
    "breakout_atr_buffer": 0.3,         # Close > BreakoutLevel + 0.3×ATR20（硬条件）
    "breakout_atr_buffer_ideal": 0.5,   # 优选 Close > BreakoutLevel + 0.5×ATR20

    # -- 突破幅度 --
    "breakout_pct_min": 0.01,           # BreakoutPct >= 1.0%（硬条件）
    "breakout_pct_ideal": 0.015,        # 优选 >=1.5%
    "breakout_pct_strong": 0.02,        # 极强突破 >=2%
    "breakout_pct_overheat": 0.07,      # 单日 +7% 以上警惕短线高潮
    "breakout_pct_climax": 0.10,        # +10% 以上（追高风险）

    # -- 突破量能 --
    "breakout_vr_min": 1.30,            # VolumeRatio >= 1.30（硬条件）
    "breakout_vr_ideal": (1.50, 2.50),  # 理想量比区间
    "breakout_vr_insufficient": 1.10,   # <1.10 = 量能不足假突破
    "breakout_vr_exhaust": 6.0,         # 爆量衰竭警惕线

    # -- 突破收盘位置 --
    "candle_pos_min": 0.75,             # CloseLocation >= 0.75（硬条件）
    "candle_pos_ideal": 0.85,           # 优选 >= 0.85
    "upper_shadow_max": 0.30,           # 上影线/振幅 上限（K线质量）
    "upper_shadow_strict": 0.15,        # 严格上影上限（K线质量优）

    # -- 突破后确认（1~3日）--
    "post_breakout_window": 3,          # 突破后观察窗口（交易日）
    "post_confirm_keep_ratio": 0.5,     # 观察窗内收盘维持 BreakoutLevel 之上的比例
    "post_confirm_floor": 0.99,         # 突破后收盘不得低于 BreakoutLevel*0.99
    "breakout_failed_close_days": 2,    # 连续2日收盘回平台 -> BREAKOUT FAILED（硬禁令）

    # -- 突破评分门槛（第五节）--
    "breakout_score_gate": 75.0,        # BREAKOUT_SCORE >= 75 才进入回踩识别
    "breakout_score_strong": 85.0,      # 强突破
    "breakout_score_valid": 75.0,       # 有效突破
    "breakout_score_weak": 65.0,        # 弱突破

    # -- BREAKOUT_SCORE 权重（100分制，第五节）--
    # 幅度15 + 量能20 + 收盘位置15 + K线质量15 + 持续性20 + 平台质量10 + 共振5
    "breakout_weights": {
        "amplitude": 15.0,
        "volume": 20.0,
        "close_location": 15.0,
        "candle_quality": 15.0,
        "persistence": 20.0,
        "platform_quality": 10.0,
        "resonance": 5.0,
    },

    # ═══ 阶段三：首次健康回踩（第六~八节）═══
    # -- 回踩时间 --
    "pullback_days_ideal": (1, 5),      # 优选 1~5 日
    "pullback_days_best": (2, 3),       # 最佳 2~3 日
    "pullback_days_max": 7,             # 超过7日降评分
    "pullback_days_hard": 10,           # 超过10日形态走坏

    "first_pullback_only": True,        # 只做第一次回踩（硬规则）
    "max_pullback_cycles": 1,           # 最多突破回踩循环次数

    # -- 回踩幅度（斐波那契口径：PullbackDepth = (BH-PL)/(BH-BL)）--
    "pullback_depth_ideal": (0.20, 0.60),  # 优选 20%~60%
    "pullback_depth_shallow": 0.15,     # <15% 过浅（未形成有效换手）
    "pullback_depth_deep": 0.80,        # >80% 过深（风险显著增加）
    "pullback_depth_hard": 1.0,         # =100% 完全跌回突破起点（结构破坏）

    # -- 关键位承接 --
    "pullback_floor_atr": 0.8,          # PullbackLow >= BreakoutLevel - 0.8×ATR20（硬条件）
    "pullback_close_below_days": 2,     # 连续2日收盘回平台 -> BREAKOUT FAILURE（硬禁令）

    # -- 回踩缩量（核心）--
    "pullback_vol_ratio_ideal": 0.65,   # PullbackVolume/BreakoutVolume <= 0.65（优选）
    "pullback_vol_ratio_ok": 0.80,      # <= 0.80（合格）
    "pullback_vol_expand_hard": 1.0,    # 回踩期量 > 突破量 = 抛压未衰减（严禁买入，硬禁令）

    # -- 回踩K线结构 --
    "lower_shadow_min": 0.35,           # 承接K线下影线/振幅 >= 35%
    "down_candle_shrink": 0.6,          # 阴线实体逐渐缩小判定阈值
    "pullback_daily_loss_max": 0.06,    # 单日回踩跌幅 >6% 惩罚（接近跌停）

    # -- 回踩结束判定（第八节：至少2项）--
    "pullback_end_min_evidence": 2,     # 至少满足2项止跌证据
    "pullback_end_evidence": (
        "consecutive_shrink",           # 连续缩量
        "lower_shadow",                 # 下影线明显
        "no_new_low",                   # 当日低点不再创新低
        "close_above_ma5",              # 收盘重新站上MA5
        "close_above_prev_high",        # 收盘站上前一日高点
        "volume_recover",               # 量能开始恢复
        "turn_positive",                # 当日涨幅转正
        "break_prev_high",              # 突破前一交易日高点
    ),

    # ═══ 阶段四：重新转强（第九/十节）═══
    # -- 价格转强 --
    "reaccel_close_above_ma5": True,    # Close > MA5（硬条件）
    "reaccel_close_above_prev_high": True,  # Close > 前一交易日 High（硬条件）
    "reaccel_ma5_above_ma10": True,     # MA5 > MA10（硬条件）
    "reaccel_ma10_slope_min": 0.0,      # MA10 斜率 >= 0（硬条件）
    "reaccel_vol_ratio_min": 1.10,      # TurnStrengthVolume >= 1.10（硬条件，温和放量）
    "reaccel_vol_ratio_max": 3.0,       # 转强量比上限（异常爆量警惕）
    "reaccel_candle_pos_min": 0.70,     # CloseLocation >= 0.70（硬条件）
    "reaccel_candle_pos_ideal": 0.80,   # 优选 >= 0.80
    "reaccel_max_price_ext": 2.0,       # 当前价 <= BreakoutLevel + 2×ATR（盈亏比，硬禁令）

    # -- RE-ACCELERATION 100分制权重（第十五节：转强5+MA5/10 4+放量4+破回踩高点4+分时3 = 20）
    # 按 5 倍折算为 100 制，使"再启动"在最终 100 分模型里真实贡献 20 分档
    "reaccel_weights": {
        "close_strength": 25.0,         # 5/20 -> 25/100
        "ma_structure": 20.0,           # 4/20 -> 20/100
        "volume_recovery": 20.0,        # 4/20 -> 20/100
        "break_pullback_high": 20.0,    # 4/20 -> 20/100
        "intraperiod": 15.0,            # 3/20 -> 15/100（分时强度，日线级用收盘位近似）
    },

    # ═══ 最终评分（第十五节：100分模型）═══
    "final_weights": {
        "platform": 30.0,               # 平台 30分
        "breakout": 25.0,               # 突破 25分
        "pullback": 25.0,               # 回踩 25分
        "reaccel": 20.0,                # 再启动 20分
    },

    # -- 评级门槛（第十五节）--
    "grade_s": 90.0,                    # ≥90 S级 PRIMARY BUY
    "grade_strong_buy": 85.0,           # 85~89 强买点
    "grade_a": 78.0,                    # 78~84 A级观察/轻仓
    "grade_wait": 70.0,                 # 70~77 等待确认
    # <70 不交易

    # -- S级 PRIMARY BUY 硬条件（第十节）--
    "primary_buy_rules": {
        "platform_score_min": 80.0,
        "breakout_score_min": 80.0,
        "first_pullback": True,
        "pullback_vol_ratio_max": 0.75,
        "pullback_floor_atr": 0.8,
        "close_above_ma5": True,
        "close_above_prev_high": True,
        "ma5_above_ma10": True,
        "vol_ratio_min": 1.10,
        "candle_pos_min": 0.75,
    },

    # -- A/B级买点（第十一节）--
    "early_buy_rules": {
        # A级 EARLY BUY：回踩关键位标准+明显缩量+强承接，但重新转强未完全确认 -> 轻仓试探 20%~30%
        "pullback_depth_min": 0.20,       # 回踩深度标准（>=20% 有效换手）
        "pullback_vol_ratio_max": 0.80,   # 明显缩量
        "floor_ok_required": True,        # 关键位承接
        "evidence_min": 2,                # 止跌证据>=2
        "platform_score_min": 75.0,
        "breakout_score_min": 75.0,
    },
    "confirmed_buy_rules": {
        # B级 CONFIRMED BUY：二次突破 Close > PullbackHigh 且量比>1.2 -> 确认型追涨
        "vol_ratio_min": 1.2,
        "break_pullback_high": True,
        "platform_score_min": 75.0,
        "breakout_score_min": 75.0,
    },

    # -- 第二次上涨检查（第一节：已发生第二次大幅上涨的只观察）--
    "second_wave_max_gain": 0.15,         # 回踩低点至今涨幅 >15% 视为第二波已走完

    # ═══ 行业/主题共振（第十四节）═══
    "theme_strength": {
        "enabled": True,
        "industry_5d_win_min": 0.50,    # 所属行业近5日上涨家数占比 >50%
        "industry_ret5_median_gap": 0.0,  # 行业近5日涨幅 > 市场中位数
        "premium_max": 5.0,             # 行业共振最高加分
        "premium_per_stock": 0.5,       # 同行业当日信号数加分系数
        "premium_per_stock_max": 3.0,   # 同行业信号数加分上限
    },

    # -- 市场过滤器（第十三节）--
    "market_filter": {
        "strong": {"allow_grades": ["S", "STRONG", "A", "B"], "strong_gate": (0.0, 0.0)},
        "bull": {"allow_grades": ["S", "STRONG", "A", "B"], "strong_gate": (0.0, 0.0)},
        "neutral": {"allow_grades": ["S", "STRONG", "A"], "strong_gate": (0.0, 0.0)},
        "weak": {"allow_grades": ["S"], "strong_gate": (88.0, 88.0)},   # 仅极强平台+突破
        "bear": {"allow_grades": [], "strong_gate": (88.0, 88.0)},      # 关闭普通突破策略
    },

    # ═══ 状态机（第十六节）═══
    "state_machine": {
        "initialized": "PLATFORM_BUILDING",
        "states": [
            "PLATFORM_BUILDING",        # 平台构建中
            "PLATFORM_CONFIRMED",       # 平台确认（score>=75）
            "NEAR_BREAKOUT",            # 接近突破
            "BREAKOUT_PENDING",         # 突破待确认
            "BREAKOUT_CONFIRMED",       # 突破确认
            "FIRST_PULLBACK",           # 首次回踩中
            "PULLBACK_SUPPORT",         # 关键位承接
            "RE_ACCELERATION",          # 重新转强
            "PRIMARY_BUY",              # ★ 主买点
            "HOLD",                     # 持有
            "EXIT",                     # 离场
            "INVALIDATED",              # 结构破坏
            "BREAKOUT_FAILED",          # 突破失败
            "PULLBACK_FAILED",          # 回踩失败
        ],
    },

    # ═══ 严禁买入（第十二节）═══
    "forbidden": {
        "false_breakout": True,         # 假突破（收盘跌回平台）
        "pullback_volume_expand": True,  # 回踩放量
        "pullback_too_deep": True,      # 回踩过深（跌破 BL-1.0ATR）
        "platform_too_wide": True,      # 平台过宽 >30%
        "platform_too_short": True,     # 平台时间过短 <10日
        "breakout_climax": True,        # 突破日爆涨 >8~10%
        "too_far_from_breakout": True,  # 当前价 > BL + 2×ATR
    },

    # ═══ 输出格式（第十七节）═══
    # 每只股票输出全部四段字段 + 状态 + 交易结论
    "output_fields": [
        "ts_code", "name", "industry",
        "platform_start", "platform_end", "platform_high", "platform_low",
        "platform_range", "platform_days", "platform_tests", "platform_score",
        "breakout_date", "breakout_price", "breakout_pct", "breakout_vr", "breakout_candle_pos",
        "breakout_score", "pullback_start", "pullback_low", "pullback_depth", "pullback_days",
        "pullback_vol_ratio", "pullback_below_bl", "pullback_score",
        "reaccel_date", "reaccel_price", "reaccel_vol_ratio",
        "final_score", "grade", "state", "action",
    ],
}

# 状态中文
STATE_CN = {
    "PLATFORM_BUILDING": "平台构建中",
    "PLATFORM_CONFIRMED": "平台确认",
    "NEAR_BREAKOUT": "接近突破",
    "BREAKOUT_PENDING": "突破待确认",
    "BREAKOUT_CONFIRMED": "突破确认",
    "FIRST_PULLBACK": "首次回踩中",
    "PULLBACK_SUPPORT": "关键位承接",
    "PULLBACK_SUPPORT_VIOLATED": "关键位失守",
    "RE_ACCELERATION": "重新转强",
    "PRIMARY_BUY": "主买点",
    "HOLD": "持有",
    "EXIT": "离场",
    "INVALIDATED": "结构破坏",
    "BREAKOUT_FAILED": "突破失败",
    "PAUSED": "暂停",
    "WAIT_BREAKOUT": "等突破",
    "WAIT_PULLBACK": "等回踩",
    "WAIT_REACCELERATION": "等再启动",
    "NO_TRADE": "不交易",
    "EARLY_BUY": "轻仓试探",
    "CONFIRMED_BUY": "确认型追涨",
    "PULLBACK_FAILED": "回踩失败",
}

# 交易结论（第十七节）
ACTION_CN = {
    "WAIT_PLATFORM": "等平台",
    "WAIT_BREAKOUT": "等突破",
    "WAIT_PULLBACK": "等回踩",
    "WAIT_REACCELERATION": "等再启动",
    "EARLY_BUY": "轻仓试探",
    "PRIMARY_BUY": "★主买点",
    "CONFIRMED_BUY": "确认型追涨",
    "NO_TRADE": "不交易",
    "BREAKOUT_FAILED": "突破失败",
    "PULLBACK_FAILED": "回踩失败",
}
