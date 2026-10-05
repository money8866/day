# -*- coding: utf-8 -*-
"""CSI2000 Position Gate V1.0 — 参数配置（单一来源）

§4 只保留 5 类核心变量：Trend / Breadth / Relative / Volume / Risk-Off。
所有阈值集中在本文件，禁止散落到计算逻辑里，便于 §25 参数稳定性扰动。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, 'output')
CACHE_DIR = os.path.join(BASE_DIR, 'cache')
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# ==================== 数据源 (§2) ====================
DB_PATH = r'D:\mystock\cache_daily\stock_data.db'   # 唯一数据源：本地 Tushare 缓存
MEMBER_TABLE = 'index_member_cache'                 # 新建：指数成分股月度快照

# ==================== 核心标的 (§3) ====================
CORE_INDEX = '932000.CSI'
CORE_INDEX_NAME = '中证2000'
RISK_ASSET = '932000.CSI'                           # 回测中被控风险的资产(Beta载体)
COMPARATORS = {                                     # 仅作辅助相对强度参考
    '000985.CSI': '全A',
    '000852.SH': '中证1000',
    '000905.SH': '中证500',
    '000300.SH': '沪深300',
}

# ==================== §5 Trend ====================
MA_WINDOWS = (5, 10, 20, 60, 120)
MA20_SLOPE_WINDOWS = (5, 10)
MA60_SLOPE_WINDOWS = (20,)

# ==================== §6/§7 Breadth ====================
BREADTH_MA_WINDOWS = (5, 20, 60, 120)
BREADTH_CHANGE_WINDOWS = (5, 10, 20)
SIZE_UNIVERSE_SKIP = 1800     # 动态小盘池：先剔除总市值最大的 N 只(近似剔中证800+中证1000)
SIZE_UNIVERSE_N = 2000        # 再取剩余中最小的 N 只
MIN_LIST_DAYS = 60            # 上市满 60 自然日才计入
EXCLUDE_ST = True             # 剔除 ST/*ST
NEW_HIGH_WINDOW = 20          # §7 20日新高/新低

# ==================== §8 Relative ====================
RS_WINDOWS = (5, 20, 60)
RS_MAIN = 20                  # 状态机使用的主 RS 窗口（§25 扰动为 10/30）

# ==================== 指数预热 ====================
# 个股日线缓存起点为 20210104，个股类特征（宽度）无预热可借用；
# 指数类特征（趋势/RS/量价）借用 2018 起的指数历史预热，避免 2021 上半年 MA60/MA120 为空。
WARMUP_DAYS = 60              # 计入预热后仍从输出中剔除的前 N 个交易日（burn-in）

# ==================== §9 Volume ====================
VOL_RATIO_WINDOWS = (5, 20)

# ==================== §10 Risk-Off ====================
R2_VOL_RATIO = 1.3            # R2: Close<MA20 且 volume_ratio_20>1.3
R3_DROP5 = -5.0               # R3: 5日跌幅 <= -5%
R4_DROP20 = -8.0              # R4: 20日跌幅 <= -8%
R5_BREADTH_DROP = 15.0        # R5: breadth_ma20 5日下降 >= 15pp
R7_RS20_DETERIORATION = -5.0  # R7: RS20 5日变化 <= -5pp
RISK_OFF_EVENTS = ('R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7')

# ==================== §11/§12 六档状态 ====================
# (min%, target%, max%)
REGIME_BANDS = {
    'OFF':          (0.0,   5.0,  15.0),
    'RECOVERY':     (15.0, 20.0,  30.0),
    'RANGE':        (25.0, 35.0,  45.0),
    'EXPANSION':    (40.0, 50.0,  65.0),
    'TREND':        (60.0, 70.0,  85.0),
    'STRONG_TREND': (80.0, 90.0, 100.0),
}
STRONG_TREND_CAP_NO_FULL = 85.0   # §13/§14 未取得满仓资格时 STRONG_TREND 上限

REGIME_TH = {
    'off_breadth': 0.35,
    'off_riskoff': 2,
    'range_breadth_lo': 0.40,
    'range_breadth_hi': 0.60,
    'range_rs_neutral': 2.0,
    'expansion_breadth': 0.55,
    'trend_breadth': 0.60,
    'trend_riskoff_max': 1,
    'strong_breadth': 0.65,
    'strong_riskoff_max': 0,
}

# ==================== §13 满仓确认 ====================
FULL_CONFIRM_MIN_DAYS = 2     # 全部条件连续满足的最少交易日(§13 要求 2~3)
FULL_CONFIRM_LOOKBACK = 5     # §13.7/§13.8「最近5日」无 Risk-Off / 无连续放量下跌

# ==================== §15/§16 仓位阶梯 ====================
STEP_UP = 10.0
STEP_UP_STRONG = 15.0
STEP_DOWN = 10.0
STEP_DOWN_RISK = 20.0
POSITION_STEP_PCT = 5.0

# ==================== §23/§24 回测 ====================
BENCHMARKS = {'B100': 100.0, 'B50': 50.0, 'B30': 30.0}
COST_BPS = (0, 15, 30, 50)
TRADING_DAYS = 252

# ==================== §26 时间切分 ====================
# 本地股票日线缓存起点为 20210104，§22 要求的 2018~2020 无法覆盖 → DATA_INCOMPLETE
TARGET_START = '20180101'
DATA_START = '20210104'
DATA_END = '20260930'
SPLITS = {
    'IS':  ('20210104', '20231231'),
    'VAL': ('20240101', '20251231'),
    'OOS': ('20260101', '20261231'),
}

# ==================== §21 组合集中度默认建议(只校验不选股) ====================
CONCENTRATION = {
    'single_stock_max': 10.0,
    'theme_exposure_max': 20.0,
    'strategy_exposure_max': 30.0,
}

# ==================== 输出文件 (§28) ====================
OUT_FILES = [
    'csi2000_position_gate_daily.csv',
    'csi2000_position_gate_events.csv',
    'csi2000_breadth.csv',
    'csi2000_relative_strength.csv',
    'csi2000_riskoff.csv',
    'csi2000_regime.csv',
    'csi2000_position_backtest.csv',
    'csi2000_position_oos.csv',
    'csi2000_parameter_stability.csv',
    'csi2000_position_report.md',
]

# ==================== ETF 执行计划 ====================
# 默认 ETF 仅作为 CLI 默认值；如实盘使用其它中证2000ETF，运行 daily.py 时传 --etf-code 覆盖。
DEFAULT_ETF_CODE = '159531.SZ'
DEFAULT_ETF_NAME = '中证2000ETF'
ETF_LOT_SIZE = 100
RISK_PER_TRADE_PCT = 1.0
ATR_WINDOW = 14
ATR_MULTIPLIER = 2.0
MAX_SINGLE_ADJUST_PCT = 20.0
MIN_REBALANCE_PCT = 3.0
PLAN_OUT_FILE = 'csi2000_etf_trade_plan.json'
