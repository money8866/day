# -*- coding: utf-8 -*-
"""
RIB 配置 - 所有参数集中于此

REVERSAL-IMPULSE-BASE-100 V1.0
长期下跌反转 → 第一波拉升 → 高位平台 → 突破 → 回踩 → 二波启动
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOLO_DIR = os.path.dirname(BASE_DIR)

# ── 数据路径 ──
CACHE_DB_PATH = os.path.join(r"D:\mystock\cache_daily", "rib_cache.db")
OUTPUT_DIR = os.path.join(SOLO_DIR, "output", "rib")


# ═════════════════════════════════════════════════════════
# 状态机状态
# ═════════════════════════════════════════════════════════
STATE_DOWNTREND = 'DOWNTREND'
STATE_REVERSAL_SETUP = 'REVERSAL_SETUP'
STATE_IMPULSE_START = 'IMPULSE_START'
STATE_IMPULSE_ACTIVE = 'IMPULSE_ACTIVE'
STATE_IMPULSE_PEAK = 'IMPULSE_PEAK'
STATE_POST_IMPULSE_BASE = 'POST_IMPULSE_BASE'
STATE_PRE_BREAKOUT = 'PRE_BREAKOUT'
STATE_SECOND_LEG_BREAKOUT = 'SECOND_LEG_BREAKOUT'
STATE_FIRST_PULLBACK = 'FIRST_PULLBACK'
STATE_PULLBACK_SUPPORT = 'PULLBACK_SUPPORT'
STATE_RE_ACCELERATION = 'RE_ACCELERATION'
STATE_PRIMARY_BUY = 'PRIMARY_BUY'
STATE_HOLD = 'HOLD'
STATE_EXIT = 'EXIT'
STATE_INVALIDATED = 'INVALIDATED'
STATE_FAILED_REVERSAL = 'FAILED_REVERSAL'
STATE_FAILED_BREAKOUT = 'FAILED_BREAKOUT'
STATE_FAILED_PULLBACK = 'FAILED_PULLBACK'

# 状态转移路径（严格禁止跳级）
VALID_TRANSITIONS = {
    STATE_DOWNTREND: [STATE_REVERSAL_SETUP, STATE_INVALIDATED],
    STATE_REVERSAL_SETUP: [STATE_IMPULSE_START, STATE_INVALIDATED],
    STATE_IMPULSE_START: [STATE_IMPULSE_ACTIVE, STATE_INVALIDATED],
    STATE_IMPULSE_ACTIVE: [STATE_IMPULSE_PEAK, STATE_INVALIDATED, STATE_FAILED_REVERSAL],
    STATE_IMPULSE_PEAK: [STATE_POST_IMPULSE_BASE, STATE_INVALIDATED, STATE_FAILED_REVERSAL],
    STATE_POST_IMPULSE_BASE: [STATE_PRE_BREAKOUT, STATE_INVALIDATED, STATE_FAILED_REVERSAL],
    STATE_PRE_BREAKOUT: [STATE_SECOND_LEG_BREAKOUT, STATE_INVALIDATED, STATE_FAILED_BREAKOUT],
    STATE_SECOND_LEG_BREAKOUT: [STATE_FIRST_PULLBACK, STATE_INVALIDATED, STATE_FAILED_BREAKOUT],
    STATE_FIRST_PULLBACK: [STATE_PULLBACK_SUPPORT, STATE_INVALIDATED, STATE_FAILED_PULLBACK],
    STATE_PULLBACK_SUPPORT: [STATE_RE_ACCELERATION, STATE_INVALIDATED, STATE_FAILED_PULLBACK],
    STATE_RE_ACCELERATION: [STATE_PRIMARY_BUY, STATE_INVALIDATED],
    STATE_PRIMARY_BUY: [STATE_HOLD, STATE_EXIT],
    STATE_HOLD: [STATE_EXIT],
}


RIB_CONFIG = {
    # ═══════════════════════════════════════════════════
    # 基础参数
    # ═══════════════════════════════════════════════════
    "min_bars": 130,                # 最少K线数（MA60 + 缓冲）
    "lookback_bars": 280,           # 最大回看窗口
    "max_impulse_search": 60,       # 第一波搜索窗口

    # ═══════════════════════════════════════════════════
    # 第一阶段：长期下跌识别
    # ═══════════════════════════════════════════════════
    "downtrend": {
        "min_days": 60,             # 最少下跌天数
        "max_days": 180,            # 最大下跌天数
        "ma60_slope_min": -0.15,    # MA60 年均斜率最小值（-15%）
        "ma20_below_ma60_ratio": 0.6,  # MA20 位于 MA60 下方的时间比例
        "higher_high_drop": 0.05,   # 每个高点下降幅度至少 5%
        "lower_low_drop": 0.03,     # 每个低点下降幅度至少 3%
        "max_highs_count": 3,       # 逐步降低的高点数至少
        "ma60_below_ratio": 0.5,    # 股价位于 MA60 下方的比例
        "volume_anomaly_factor": 1.5,  # 末期成交量异常倍数
        "score_min": 65,            # 进入反转识别的门槛
        "score_max": 100,
        # 分项分值权重
        "weight_60d_trend": 20,
        "weight_120d_trend": 15,
        "weight_ma20": 15,
        "weight_ma60": 15,
        "weight_higher_highs": 20,
        "weight_duration": 10,
        "weight_oversold": 5,
    },

    # ═══════════════════════════════════════════════════
    # 第二阶段：第一波反转（IMPULSE）
    # ═══════════════════════════════════════════════════
    "impulse": {
        # 幅度
        "min_return": 0.15,         # 最低 15%
        "optimal_return_low": 0.20,  # 优选下限 20%
        "optimal_return_high": 0.60, # 优选上限 60%
        "max_return": 1.0,          # 超过100% 视为异常
        "min_atr_ratio": 3.0,       # ImpulseATR 最低 3 倍 ATR
        # 时间
        "min_days": 3,              # 最少3日
        "optimal_days_low": 5,      # 最佳5日
        "optimal_days_high": 10,   # 最佳10日
        "max_days": 15,             # 最多15日
        "extreme_acceleration_days": 2,  # <=2日 标记为 EXTREME_ACCELERATION
        "gradual_days": 20,         # >=20日 降低评分
        # 成交量
        "min_volume_ratio": 1.2,    # 最低量比 1.2
        "optimal_volume_low": 1.5,  # 优选下限 1.5
        "optimal_volume_high": 2.5, # 优选上限 2.5
        # 突破确认
        "must_break_ma": True,     # 必须突破 MA20/MA60
        "must_break_trend": True,  # 必须突破下降趋势线
        "must_break_high": True,   # 必须突破阶段前高
        "score_min": 80,            # IMPULSE_SCORE 门槛
    },

    # ═══════════════════════════════════════════════════
    # 第三阶段：POST_IMPULSE_BASE
    # ═══════════════════════════════════════════════════
    "post_impulse_base": {
        "min_days": 5,              # 平台最少天数
        "optimal_days_low": 7,      # 最佳7日
        "optimal_days_high": 15,   # 最佳15日
        "max_days": 30,             # 最多30日
        # 回撤
        "pullback_optimal_low": 0.20,  # 回撤 20%
        "pullback_optimal_high": 0.40, # 回撤 40%
        "pullback_good_low": 0.15,    # 良好下限 15%
        "pullback_good_high": 0.50,   # 良好上限 50%
        "pullback_danger": 0.60,      # 危险 60%
        "pullback_extreme_danger": 0.70,  # 极危险 70%
        # 涨幅保留率
        "retain_excellent": 0.70,     # >=70% 优秀
        "retain_good": 0.60,          # 60~70% 良好
        "retain_pass": 0.50,          # 50~60% 合格
        "retain_bad": 0.40,           # <40% 通常 INVALID
        # 成交量
        "volume_shrink_ratio": 0.70,  # 平台量/第一波量 <=0.7
        # 质量门槛
        "quality_threshold": 40,      # 平台质量分低于该值视为弱势回调，不构成平台
        # MA 结构
        "ma20_slope_min": 0.0,        # MA20 开始向上
        "score_min": 80,              # POST_IMPULSE_BASE_SCORE 门槛
    },

    # ═══════════════════════════════════════════════════
    # 第四阶段：突破与第二波
    # ═══════════════════════════════════════════════════
    "breakout": {
        "atr_buffer": 0.3,          # Close > ImpulseHigh + 0.3*ATR
        "volume_ratio_min": 1.3,    # 突破量比 >=1.3
        "close_location_min": 0.75, # 收盘位置 >=0.75
        "max_upper_shadow": 0.3,    # 最大上影线比例
        "ma5_above_ma10": True,     # MA5 > MA10
        "ma20_slope_min": 0.0,      # MA20 不向下
        "confirm_window": 2,        # 簇确认窗口：首日站上后2日内补足量能/收盘确认
        # 禁止追高
        "max_breakout_distance_atr": 2.0,  # 距突破位 >2ATR 禁止
        "breakout_distance_penalty": 1.5,  # >1.5ATR 降分
        "score_min": 80,
    },

    # ═══════════════════════════════════════════════════
    # 第五阶段：第一次回踩
    # ═══════════════════════════════════════════════════
    "pullback": {
        "min_days": 1,
        "optimal_days_low": 2,
        "optimal_days_high": 3,
        "max_days": 5,
        # 量
        "volume_ratio_max": 0.80,   # 回踩量 <= 突破量的 80%
        "volume_ratio_optimal": 0.65,  # 最佳 <=65%
        # 深度
        "depth_optimal_low": 0.20,  # 20%
        "depth_optimal_high": 0.60, # 60%
        "depth_max": 0.80,          # 最大 80%
        # 不能跌回平台
        "must_stay_above_base": True,
        # TEST_AND_RECLAIM 加分
        "test_and_reclaim_bonus": 6,
    },

    # ═══════════════════════════════════════════════════
    # 第六阶段：二次启动
    # ═══════════════════════════════════════════════════
    "re_acceleration": {
        "volume_ratio_min": 1.1,    # 量比 >=1.1
        "close_location_min": 0.75,  # 收盘位置 >=0.75
        "max_distance_atr": 1.5,   # 距 ImpulseHigh 优选 <=1.5ATR
        "max_distance_atr_hard": 2.0,  # 距 ImpulseHigh 硬上限 2ATR（规范§21）
        "must_close_above_ma5": True,
        "must_close_above_prev_high": True,
        "must_ma5_above_ma10": True,
        "must_ma5_slope_up": True,
    },

    # ═══════════════════════════════════════════════════
    # 强制否决条件（任意一条触发 NO_TRADE）
    # ═══════════════════════════════════════════════════
    "veto": {
        "min_impulse_return": 0.15,       # 1. 第一波不足15%
        "min_impulse_volume_ratio": 1.2,   # 2. 无明显量能
        "require_trend_change": True,      # 3. 必须改变下降趋势
        "max_pullback_depth": 0.70,        # 4. 回撤超70%
        "forbid_volume_plunge": True,      # 5. 平台放量下跌
        "forbid_back_to_origin": True,     # 6. 跌回启动区
        "forbid_ma20_down": True,          # 7. MA20 明显向下
        "forbid_fake_breakout": True,      # 8. 假突破
        "forbid_quick_fall_back": True,    # 9. 快速跌回平台
        "forbid_pullback_volume": True,    # 10. 回踩放量
        "max_distance_atr": 2.0,           # 11. 距突破位>2ATR
        "min_risk_reward": 2.0,            # 12. 盈亏比<2
    },

    # ═══════════════════════════════════════════════════
    # 最终评分等级
    # ═══════════════════════════════════════════════════
    "grades": {
        "s_plus_plus": 90,    # S++++ 核心二波启动
        "s": 85,              # S 强二波机会
        "a_plus": 80,         # A+ 重点关注
        "a": 75,              # A 观察
        "b": 70,              # B 等待
        "no_trade": 70,       # <70 不交易
    },

    # ═══════════════════════════════════════════════════
    # 市场环境过滤
    # ═══════════════════════════════════════════════════
    "market_filter": {
        "bull": {
            "allow_states": ("PRIMARY_BUY",),
            "min_score": 70,
        },
        "normal": {
            "allow_states": ("PRIMARY_BUY",),
            "min_score": 75,
        },
        "recovery": {
            "allow_states": ("PRIMARY_BUY",),
            "min_score": 85,  # 只执行 S 级
        },
        "weak": {
            "allow_states": ("PRIMARY_BUY",),
            "min_score": 90,  # 只执行极强
        },
        "bear": {
            "allow_states": (),  # 关闭普通信号
            "min_score": 101,
        },
    },

    # ═══════════════════════════════════════════════════
    # 主题增强
    # ═══════════════════════════════════════════════════
    "theme": {
        "industry_up_bonus": 5,       # 行业增强 +5
        "leader_up_bonus": 3,        # 龙头同步 +3
        "theme_main_bonus": 3,       # 主升 +3
        "counter_industry_penalty": -5,  # 逆行业 -5
    },

    # ═══════════════════════════════════════════════════
    # 风险过滤
    # ═══════════════════════════════════════════════════
    "risk": {
        "min_list_days": 120,        # 上市天数
        "max_turnover_rate": 0.30,    # 异常换手率
        "min_market_cap": 10e8,     # 最小市值 10 亿
        "max_market_cap": 500e8,     # 最大市值 500 亿
        "forbid_st": True,            # 禁止 ST/*ST
        "forbid_suspended": True,    # 禁止停牌
    },

    # ═══════════════════════════════════════════════════
    # 盈亏比计算
    # ═══════════════════════════════════════════════════
    "rr": {
        "stop_loss_atr": 1.5,        # 止损位 = 买点 - 1.5*ATR
        "target1_atr": 3.0,          # 目标1 = 买点 + 3*ATR
        "target2_atr": 5.0,          # 目标2 = 买点 + 5*ATR
        "min_rr": 2.0,               # 最低盈亏比
        "position_size": 0.30,       # 建议仓位 30%
        "holding_days": 5,           # 预计持有 5 日
    },

    # ═══════════════════════════════════════════════════
    # 评分权重分配（最终100分）
    # ═══════════════════════════════════════════════════
    "weights": {
        "downtrend_bg": 10,          # ① 长期下跌背景：10分
        "impulse": 25,               # ② 第一波反转：25分
        "post_impulse_base": 30,     # ③ POST_IMPULSE_BASE：30分
        "second_breakout": 15,       # ④ 第二波突破：15分
        "first_pullback": 10,        # ⑤ 第一次回踩：10分
        "re_acceleration": 10,       # ⑥ 再启动：10分
    },

    # ═══════════════════════════════════════════════════
    # 输出
    # ═══════════════════════════════════════════════════
    "output": {
        "dir": OUTPUT_DIR,
        "format": "md",
    },
}
