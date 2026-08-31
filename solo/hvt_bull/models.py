# -*- coding: utf-8 -*-
"""HVT-BULL 事件结构与状态机定义"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


HVT_STATES = [
    'NORMAL', 'HVT_DETECTED', 'HVT_STRONG', 'WATCH', 'LOCKING', 'LOCKED',
    'BREAKOUT_READY', 'PRIMARY_BUY', 'T20_ROCKET_WATCH', 'CONFIRMED',
    'FAILED', 'DISTRIBUTION', 'EXIT', 'EVENT_SPIKE',
]


@dataclass
class HvtEvent:
    """单次历史天量换手事件（T0）"""
    ts_code: str
    name: str = ''
    t0_date: str = ''
    t0_index: int = -1

    # HVT 判定
    hvt_grade: str = ''             # A / B / C
    hvt_rank_250: int = 0           # T0换手在250日内的排名（1=最高）
    hvt_rank_anchor: int = 0        # T0换手自anchor_date以来的排名（1=最高；0=锚点口径未启用）
    turnover_pct_120: float = 0.0   # 120日分位(%)
    turnover_ratio_20: float = 0.0  # 当日换手/20日均换手
    amount_ratio_20: float = 0.0
    t0_turnover: float = 0.0
    t0_amount: float = 0.0

    # 天量日价格强度
    t0_pct_chg: float = 0.0
    t0_close_pos: float = 0.0       # (Close-Low)/(High-Low)
    t0_body: float = 0.0            # (Close-Open)/(High-Low)
    t0_close: float = 0.0
    t0_high: float = 0.0
    t0_low: float = 0.0
    t0_mid: float = 0.0

    # 前期趋势
    ma20: float = 0.0
    ma60: float = 0.0
    ma120: float = 0.0
    ma20_slope: float = 0.0
    ma60_slope: float = 0.0
    platform_breakout: bool = False

    # 前期涨幅结构
    r5: float = 0.0
    r10: float = 0.0
    r20: float = 0.0
    r60: float = 0.0
    r120: float = 0.0
    r250: float = 0.0
    dist_high_120: float = 0.0      # 距120日高点(%，正=在其下)

    # 天量后跟踪（每日刷新）
    days_after: int = 0
    vol_5d_ratio: float = 1.0       # T+1~T+5均量 / T0量
    vol_3d_ratio: float = 1.0
    post_min_low: float = 0.0
    post_max_drawdown: float = 0.0  # (T0_Close-最低Close)/T0_Close (%)
    normalized_drawdown: float = 0.0
    locked_chip: bool = False
    strong_locked_chip: bool = False

    # 二次突破
    breakout_date: str = ''
    breakout_turnover_ratio: float = 0.0
    breakout_close_pos: float = 0.0
    false_breakout: bool = False
    breakout_pct_above_t0_high: float = 0.0   # 突破收盘高于T0_High的幅度(%)
    t0_to_breakout_days: int = 0              # T0到突破日的交易日数
    signal_tier: str = ''                     # 归因信号分层：T1/T2/T3/空

    # 右侧持有跟踪（T+35~T+right_track_days，主升捕获，V3.3）
    right_tail_max_close: float = 0.0    # 右侧窗口内最高收盘
    right_tail_max_date: str = ''        # 主升高点日期
    right_tail_dd_from_peak: float = 0.0 # 当前距主升高点回撤(%)
    right_tail_ma10: float = 0.0         # 当前 MA10
    right_tail_hold: bool = False        # 持有中（仍站上 MA10）
    right_tail_exit: bool = False        # 止盈信号（回撤>15% 且 跌破MA10）

    # 突破回踩结构（二次突破后缩量承接，V3.4）
    pb_shrink_ratio: float = 0.0         # 回踩均量/突破日量（<0.8 缩量）
    pb_low_close: float = 0.0            # 回踩最低价
    pb_low_date: str = ''                # 回踩低点日期
    pb_low_vs_t0high: float = 0.0        # 低点相对 T0_High(%)
    pb_cur_vs_t0high: float = 0.0        # 当前收盘相对 T0_High(%)
    pb_cur_vs_break: float = 0.0         # 当前收盘相对突破收盘(%)
    pb_verdict: str = 'NA'               # GOOD/NEAR/POOR/NA

    # V3.0 双评分系统
    entry_score: float = 0.0                  # 入场时点评分（现在是否适合买）
    expansion_score: float = 0.0              # T20扩张潜力评分（右尾捕获核心）
    entry_subs: Dict[str, float] = field(default_factory=dict)
    exp_subs: Dict[str, float] = field(default_factory=dict)
    close_pos_grade: str = ''                 # 收盘位置分级 A+/A/B/C/D
    volume_grade: str = ''                    # 放量分级 A+/A/B/C/D
    hard_veto: List[str] = field(default_factory=list)   # 硬否决原因
    rs20: float = 0.0                         # 20日相对强度百分位（全市场截面）
    rs10: float = 0.0
    rs5: float = 0.0
    rs_accel: float = 0.0                     # RS加速度 = RS5 - RS20
    tail_score: float = 0.0                   # T20右尾综合分（含历史概率校准）
    tail_calibrated: bool = False             # 概率是否来自充足历史样本

    # V3.1 Future Expansion 增强层（只读叠加，不改V3.0状态，§一/§十五）
    fe_score: float = 0.0                     # 未来扩张空间总分 0~100
    fe10: float = 0.0                         # 未来10日延续能力
    fe20: float = 0.0                         # 未来20日确认能力
    fe60: float = 0.0                         # 未来60日中期扩张
    fe120: float = 0.0                        # 未来120日右尾潜力
    lifecycle: str = ''                       # EARLY~EXTREME_EXTENDED（§三）
    trend_gain: float = 0.0                   # MajorBase→现价涨幅(%)
    base_price: float = 0.0                   # MajorBase价
    base_date: str = ''                       # MajorBase日期
    base_method: str = ''                     # MajorBase识别方式
    continuation_score: float = 0.0           # 趋势延续能力 0~100（§五）
    extension_risk: float = 0.0               # 扩张风险 0~100（§六）
    expansion_type: str = ''                  # §十七五类标签
    fe_mode: str = ''                         # EXTENDED/EXTREME_TREND_MODE（§四）
    fe_parts: Dict[str, float] = field(default_factory=dict)
    why_space: List[str] = field(default_factory=list)   # 为什么还有空间（§十九）
    why_risk: List[str] = field(default_factory=list)    # 为什么可能没有空间

    # 状态与评分
    state: str = 'HVT_DETECTED'
    score: float = 0.0
    grade: str = ''
    similarity_score: float = 0.0
    fundamental_grade: str = ''
    fundamental_score: float = 0.0
    sector_strength: float = 0.0
    sector_name: str = ''
    money_quality_score: float = 50.0
    subs: Dict[str, float] = field(default_factory=dict)
    wait_reasons: List[str] = field(default_factory=list)

    # 交易计划
    entry: float = 0.0
    stop_loss: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    atr14: float = 0.0

    # ---- V3.5 Trade Execution 增量层（只读叠加，可整体关闭；关闭时 daily 组装 JSON 前会过滤以下字段） ----
    buyability: float = 0.0                                    # 交易位置质量 0~100（非股票质量分）
    buyability_parts: Dict[str, float] = field(default_factory=dict)
    execution_score: float = 0.0                               # 执行分 0~100
    execution_state: str = ''                                  # READY_BUY/BREAKOUT_WAIT/PULLBACK_BUY/WAIT_CONFIRM/NO_CHASE/SKIP
    next_day_action: str = ''                                  # BUY/BUY_ON_CONFIRM/WAIT/WAIT_PULLBACK/NO_CHASE/SKIP
    entry_trigger: float = 0.0                                 # 触发价（现有平台/突破规则导出）
    buy_zone_low: float = 0.0
    buy_zone_high: float = 0.0
    invalidation: float = 0.0                                  # 失效价（止损）
    no_chase_level: float = 0.0                                # 追高上限 = 突破位 + N×ATR
    position_size: str = ''                                    # 目标仓位（受现有仓位模块约束）
    initial_position: str = ''                                 # 首次建仓 = 目标 × initial_ratio
    primary_horizon: str = ''                                  # T20/T60/T120
    stock_type: str = ''                                       # NEW_TREND/RE_ACCELERATION/EXTENDED_CONTINUATION/LATE_STAGE
    confirmation_level: str = ''                               # 执行前需要的确认条件
    execution_reason: str = ''
    why_not_buy: List[str] = field(default_factory=list)       # FE高但不买的原因（§31）
    open_playbook: List[str] = field(default_factory=list)     # 次日四种开盘情况预案（§17）
    intraday_available: bool = False                           # 是否有分钟数据（False 时不得伪造 VWAP 确认）

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


def state_rank(state: str) -> int:
    order = ['NORMAL', 'HVT_DETECTED', 'HVT_STRONG', 'WATCH', 'LOCKING', 'LOCKED',
             'BREAKOUT_READY', 'PRIMARY_BUY', 'T20_ROCKET_WATCH', 'CONFIRMED',
             'FAILED', 'DISTRIBUTION', 'EXIT', 'EVENT_SPIKE']
    return order.index(state) if state in order else 0
