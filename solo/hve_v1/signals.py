# -*- coding: utf-8 -*-
"""HVE V1 信号常量与映射（§26 无综合评分 / §28 reason 机器可读 / §47 四态输出）

禁止任何 0~100 综合分：本模块只做 状态 → signal_type / signal 的确定性映射。
"""

# ---- §47 日报四态（signal_type） ----
HVE_BULL = 'HVE_BULL'
HVE_2ND = 'HVE_2ND'
HVE_WATCH = 'HVE_WATCH'
HVE_FAIL = 'HVE_FAIL'
NO_TRADE = 'NO_TRADE'

# ---- §25 状态机状态（state，必须可追踪） ----
S_NORMAL = 'NORMAL'
S_HVE_EVENT = 'HVE_EVENT'
S_BULL_WATCH = 'HVE_BULL_WATCH'
S_DIGESTION = 'DIGESTION'
S_BULL = 'HVE_BULL'
S_2ND = 'HVE_2ND'
S_FAIL = 'HVE_FAIL'

# ---- signal ----
SIG_BUY = 'BUY'
SIG_WATCH = 'WATCH'
SIG_CONDITIONAL = 'CONDITIONAL'
SIG_NONE = 'NO_TRADE'

# ---- §28 reason 码（机器可读，禁止用自然语言替代） ----
REASON_BULL = 'HVE + MA20_UP + STRUCTURE_OK + RE_EXPANSION'
REASON_2ND = 'HVE + DIGESTION + VOLUME_CONTRACTION + STRUCTURE_OK + BREAKOUT'
REASON_WATCH = 'HVE + STRUCTURE_OK + WAIT_RE_EXPANSION'
REASON_FAIL = 'HVE + STRUCTURE_BREAK'

# 附加后缀（仅追加在 reason 末尾，不替代 §28 主码）
SUF_COOLDOWN = ' + COOLDOWN'
SUF_LIMIT_UP = ' + LIMIT_UP_RISK'
SUF_ONE_PRICE = ' + ONE_PRICE_BOARD'

# §48 展示排序用的字段名（不得含 score / alpha）
SORT_BULL = 'reexpansion_strength'   # 再扩张强度：当前量比（VR20）
SORT_2ND = 'breakout_distance'       # 突破幅度（+ 量能确认另列）

# §33 主题仅作为输出字段，不参与核心算法
THEME_OUTPUT_FIELDS = ('theme', 'theme_heat', 'theme_rank')

# §27 每日输出字段顺序
DAILY_FIELDS = (
    'trade_date', 'ts_code', 'name',
    'signal_type', 'state', 'signal',
    'event_date', 'days_since_hve', 'event_cluster_id',
    'vr20', 'hve_return', 'hve_clv',
    'current_return', 'current_volume_ratio',
    'ma20', 'ma60', 'ma20_up',
    'drawdown_from_hve', 'volume_decay_5d',
    'consolidation_high', 'breakout_distance',
    'entry_price', 'trigger_price', 'invalid_price', 'invalid_rule',
    'market_regime', 'reason', 'tradability_flag', 'executable',
    'theme', 'theme_heat', 'theme_rank',
    'close',
)
