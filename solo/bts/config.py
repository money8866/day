# -*- coding: utf-8 -*-
"""
BTS（Breakout Trend Start）V1.0 配置
平台突破后趋势启动选股模块 -- 全部参数集中于此，禁止散落硬编码
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOLO_DIR = os.path.dirname(BASE_DIR)

# 数据源路径
CACHE_DB_PATH = os.path.join(r"D:\mystock\cache_daily", "stock_data.db")
STOCK_BASIC_CSV = os.path.join(r"D:\mystock\cache_daily", "stock_basic.csv")
TDX_PATH = r"C:\new_tdx"
REPORT_DIR = os.path.join(SOLO_DIR, "output", "bts")

# ═════════════════════════════════════════════
# Stage A：震荡平台 Base
# ═════════════════════════════════════════════
BTS_CONFIG = {
    # -- 平台窗口 --
    "base_window_min": 20,          # 平台最短交易日
    "base_window_max": 60,          # 平台最长交易日
    "max_base_range": 0.30,         # 平台振幅上限 (30%)
    "good_base_range": 0.20,        # 优质平台振幅 (20%)
    "base_max_drawdown": 0.18,      # 平台内部从高点最大回撤上限（过滤单边暴跌）
    "base_slope_penalty": -0.05,    # 平台整体斜率明显向下时的扣分

    # -- 压力位识别（Resistance Cluster）--
    "resistance_atol_pct": 0.015,   # 触及价与压力位的容差（相对值 1.5%）
    "resistance_min_touches": 2,    # 最少有效触及次数
    "resistance_good_touches": 3,   # 优质触及次数

    # -- 突破判定 --
    "breakout_threshold": 0.01,     # 收盘突破幅度下限（resistance * 1.01）
    "breakout_volume_ratio": 1.30,  # 突破日量 / 平台均量 下限
    "breakout_ideal_vr": (1.5, 3.0),  # 理想突破量比区间
    "burst_volume_ratio": 5.0,      # 爆量陷阱阈值
    "candle_min_pos": 0.60,         # 突破K线收盘位置下限（(close-low)/(high-low)）
    "candle_strong_pos": 0.75,      # 强突破收盘位置
    "breakout_max_pct": 0.08,       # 单日突破幅度超过 8% 开始惩罚
    "upper_shadow_limit": 0.40,     # 突破日长上影过滤（上影/振幅 > 40% 扣分）

    # -- 突破后确认窗口 --
    "post_breakout_days": 5,        # 突破后观察窗口（Day0~Day5）
    "post_breakout_floor": 0.98,    # 突破后最低价不得低于 resistance*0.98
    "failure_close_below": 0.995,   # 突破后收盘跌回平台上沿*0.995 以内 → 假突破

    # -- 信号日创新高硬门槛（V1.6）--
    "new_high_window": 120,         # 信号日高点需站上过去 N 个交易日新高（过滤未突破前高的弱突破）

    # -- MA5 趋势引擎 --
    "ma5_up_min_days": 2,           # MA5 连续向上最少天数
    "ma5_up_good_days": 3,          # MA5 连续向上优先天数
    "max_ma5_distance": 0.10,       # close/MA5-1 上限（10%）
    "no_buy_ma5_distance": 0.15,    # 超过 15% 原则上 NO BUY
    "ideal_ma5_distance": (0.0, 0.05),  # 理想贴线区间
    "ma5_track_days": 5,            # MA5 承接统计窗口

    # -- 量能持续性 --
    "vol_ma_window": (5, 10, 20),
    "min_volume_persistence": 3,    # 最近5日 volume>VOL_MA20 的最少天数
    "v1_min": 1.2,                  # 当日量/VOL_MA20 下限
    "v2_min": 1.1,                  # VOL_MA5/VOL_MA20 下限
    "up_down_vol_ideal": 1.2,       # 上涨日量/下跌日量 理想下限
    "up_down_vol_good": 1.5,

    # -- 回踩质量 --
    "pullback_depth_ideal": 0.08,   # 回踩深度理想上限 8%
    "pullback_depth_good": 0.05,    # 优秀 5%

    # -- 评分门槛 --
    "min_bts_score": 70,            # 正式买入池门槛
    "grade_s": 85,
    "grade_a": 78,
    "grade_b": 70,
    "grade_c": 60,

    # -- 行业/板块共振因子（V1.1 新增）--
    "sector_heat_max": 6.0,         # 行业共振最高加分（Entry）
    "sector_heat_per_sig": 1.0,     # 每1只同行业当日信号 +1（封顶 heat_max）
    "sector_heat_min_sig": 3,       # 同行业当日信号 >=3 才开始加分

    # -- 主线板块识别（V1.8 新增）--
    # 用户只做"强势高确定性主线板块"。判定=行业信号集中度：当日信号数最多的 TOP 行业即主线。
    # 应用=加分优先：主线板块内信号额外加 mainline_premium（并入附加分统一压缩，防 Entry 饱和）。
    "mainline": {
        "enabled": True,
        "top_n": 2,                 # 信号数最多的前 N 个行业为主线板块
        "min_sig": 5,               # 主线板块至少需 5 只当日信号（防小行业噪声）
        "premium": 4.0,             # 主线板块内信号 Entry 额外加分
    },

    # -- 突破后第1日确认加分（V1.1 新增；V1.2 加大）--
    "day1_premium": 5.0,            # 突破后第1日且未跌回平台/量能不衰：Entry +5

    # -- 买入池强过滤（V1.2 新增；V1.7 扩展为 持续确认买点）--
    "buy_pool_day1_only": True,     # 买入池只收 Day1(突破后第1日且量能不衰) 且 S/A/B 的信号
    # V1.7：突破后稳步向上+量能充沛 → 非 Day1 也可进买入池（捕获 20260811 博济这类 Day2 持续确认）
    "sustained_buy": {
        "enabled": True,            # 开启持续确认买点
        "min_days_after": 2,        # 突破后第 N 日起（含）
        "max_days_after": 5,        # 突破后第 N 日止（含，不超过 post_breakout_days）
        "min_vol_persist_ratio": 0.6,  # 突破日至今放量天数比例下限
        "min_vol_ratio": 1.0,       # 当日量比下限
        "min_v2": 1.0,              # 量能中枢抬升下限
        "min_trend_eff": 0.0,       # 突破后日均涨幅下限（稳步向上）
        "max_dist_ma5": 0.10,       # 距 MA5 上限（防止已追高）
        "buy_point": "BUY-C",       # 持续确认买点类型
    },

    # -- 市值因子（V1.3 新增，基本面）--
    # 回测实测（买入池/Day1且S/A/B，20日均）：<30亿 +4.43% / 30-50 +2.83% / 50-80 +2.74%
    # 80-120 +2.54% / 120-200 +2.54% / 200-300 +3.89% / 300-600 +3.53% / 600-1500 +2.41% / >1500 +1.79%
    # 规律：小市值(<30亿)显著占优、超大盘(>1500亿)最弱；300亿附近有小反弹峰
    # 注意：上限仅 3 分（否则与 Day1/行业共振叠加后 Entry 大量封顶 100，排序失真）
    "mv_max": 3.0,                  # 市值加分上限（保守，避免 Entry 饱和）
    "mv_edges": [(30, 3.0), (50, 2.5), (80, 2.0), (200, 1.5), (300, 2.0), (600, 2.0),
                 (1500, 1.0), (float('inf'), 0.5)],  # (上界, 得分)，单位亿元

    # -- 附加分压缩（V1.4 新增）--
    # 三因子满分修复后 BTS 高分股普遍存在（>=85 占比高），Day1+行业+市值附加分叠加易致 Entry 饱和 100、
    # 排序失去区分度。高分股已足够优秀，附加分边际意义小，按 BTS 分档压缩权重：(BTS下限, 权重)
    "extra_compress": [(90, 0.3), (80, 0.6)],

    # -- 市场环境权重 --
    "market_weight": {"strong": 1.10, "neutral": 1.00, "weak": 0.75, "bear": 0.75},

    # -- 数据要求 --
    "min_bars": 120,                # 最少K线数（60日平台 + 缓冲）
    "lookback_bars": 260,           # 单次评估回看的最大K线数

    # -- 门槛开关（GATE：三项硬条件全过才进买入池）--
    "gate_breakout_confirmed": True,
    "gate_ma5_trend": True,
    "gate_volume_persistence": True,
}

# 评分权重（合计 100）
SCORE_WEIGHTS = {
    "base_quality": 15,
    "breakout": 20,
    "ma5_trend": 20,
    "volume_persistence": 20,
    "volume_price": 10,
    "pullback": 10,
    "extension": 5,
}

SIGNAL_CN = {
    "BREAKOUT_NOW": "突破确认",
    "TREND_START": "趋势启动",
    "PULLBACK_BUY": "回踩买点",
    "TREND_EXTENDED": "过度扩张",
    "FAILED_BREAKOUT": "突破失败",
    "NO_SIGNAL": "无信号",
}

STATUS_CN = {
    "NEW": "新进",
    "CONTINUE": "延续",
    "UPGRADE": "升级",
    "DOWNGRADE": "降级",
}

GRADE_STARS = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
