# -*- coding: utf-8 -*-
"""ZZ2000 每日仓位跟踪 —— 单一参数源 / 路径常量

策略框架：底仓 30% + 均线趋势仓 40% + 波段交易仓 30%，总敞口上限 100%。
这些数字是「策略初始研究预算」，不是用户实际持仓，也不是已验证的最优参数。
"""
import os

ROOT = r'D:\mystock'
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PKG_DIR, 'cache')
REPORT_DIR = os.path.join(PKG_DIR, 'reports')
for _d in (CACHE_DIR, REPORT_DIR):
    os.makedirs(_d, exist_ok=True)

# 本地缓存（本模块自建，全部由 Tushare 可验证接口落盘）
CACHE_DB = os.path.join(CACHE_DIR, 'zz2000.db')
ETF_UNIVERSE_CSV = os.path.join(CACHE_DIR, 'etf_universe_snapshot.csv')
ETF_SELECTION_MD = os.path.join(CACHE_DIR, 'etf_selection.md')
STATE_JSON = os.path.join(CACHE_DIR, 'state.json')
LEDGER_CSV = os.path.join(CACHE_DIR, 'benchmark_ledger.csv')

# 项目已有的 Tushare 本地缓存（只读）
ENV_FILE = os.path.join(ROOT, 'config', '.env')
MAIN_DB = os.path.join(ROOT, 'cache_daily', 'stock_data.db')          # daily_cache / index_daily_cache
MICRO_DB = os.path.join(ROOT, 'solo', 'cache_micro', 'micro_daily.db')  # 全市场涨跌/涨跌停家数
MA_DB = os.path.join(ROOT, 'solo', 'cache_backbone_tushare', 'market_analysis.db')  # 炸板/连板高度

INDEX_CODE = '932000.CSI'
INDEX_NAME = '中证2000'

MAIL_TO = 'stock1975@qq.com'
AGENT_MAIL_CLI = r'C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd'
CLOSE_TIME = '15:00'          # A股收盘；此前不认为当日收盘行情已生成

# ==================== 策略初始预算 ====================
BUDGET_BASE = 30.0
BUDGET_TREND = 40.0
BUDGET_SWING = 30.0
MAX_EXPOSURE = 100.0

# ==================== 均线 / 斜率 ====================
MA_WINDOWS = (20, 60, 120)
MA_SLOPE_LOOKBACK = {20: 5, 60: 10, 120: 20}   # 斜率 = N日前的MA到现在的变化百分比
MIN_BARS = 120                                  # MA120 所需最少交易日
VOL_MA_WINDOW = 20

# ==================== 底仓 / 系统性风险 ====================
BASE_TABLE = {          # (close>MA120, MA120斜率状态) -> 底仓占预算比例
    ('above', 'up'): 30.0,
    ('above', 'flat'): 25.0,
    ('above', 'down'): 20.0,
    ('below', 'up'): 15.0,
    ('below', 'flat'): 10.0,
    ('below', 'down'): 5.0,
}
MA120_SLOPE_FLAT_BAND = 0.5       # |20日MA120斜率| < 0.5% 视为走平
SYSRISK_DT_COUNT = 50             # 跌停家数 >= 50
SYSRISK_UP_RATIO = 0.20           # 全市场上涨占比 < 20%
SYSRISK_RET20 = -8.0              # 指数20日涨跌幅 <= -8%
SYSRISK_BASE_CAP = 15.0           # 触发系统性风险时底仓上限

# ==================== 趋势仓 ====================
TREND_WEIGHTS = {'position': 0.4, 'alignment': 0.3, 'slope': 0.3}
CHASE_PCT_CHG = 5.0               # 单日涨幅 > 5%
CHASE_DEV_MA20 = 6.0              # 且价格偏离MA20 > 6%  → 追涨抑制
TREND_ROUND = 5.0                 # 仓位取整到 5% 的整数倍

# ==================== 波段仓 ====================
SWING_A_MAX = 12.0                # 上升趋势缩量回踩再站稳
SWING_B_MAX = 10.0                # 恐慌衰竭后的超跌修复
SWING_C_MAX = 8.0                 # 均线多头平台整理后二次放量突破
PULLBACK_VOL_RATIO = 0.80         # 缩量：成交量 <= 0.8 * MA20量
PULLBACK_LOOKBACK = 5
PANIC_DROP5 = -6.0                # 近5日跌幅 <= -6%
PANIC_PREV_DT = 30                # 前一日跌停家数 >= 30（恐慌）
PANIC_MIN_REBOUND = 1.5           # 修复日涨幅 > 1.5%
PLATFORM_LOOKBACK = 10
PLATFORM_MAX_RANGE = 0.08         # 平台整理：前10日收盘高低振幅 <= 8%
BREAKOUT_VOL_RATIO = 1.30         # 放量：>= 1.3 * MA20量
SWING_EXT_DEV_MA20 = 6.0          # 波段A：偏离MA20 > 6% 视为追高，不参与

# ==================== 情绪过滤 ====================
SENT_UP_RATIO_MIN = 0.45
SENT_ZT_MIN = 30
SENT_DT_MAX = 40
YZT_AVG_MIN = -1.0                # 昨日涨停股次日平均涨跌幅下限
SENT_HAIRCUT = 0.5                # 单条情绪过滤不过关时，波段仓打折系数
MIN_REBALANCE_PCT = 3.0           # 仓位变化小于该值视为「信号不变」
ACTION_STEP_PCT = 5.0             # 变化 >= 5pp 才触发 BUY / REDUCE

# ==================== 绩效 / 基准 ====================
MIN_PERF_SAMPLE = 60              # 少于该交易日数不报告任何超额
BACKTEST_COST_BPS = 15.0          # 单边成本假设（基点），仅在未来正式回测时使用
BACKTEST_SLIPPAGE_BPS = 10.0      # 单边滑点假设（基点）

DISCLAIMER = (
    '本报告为研究与决策辅助，基于公开行情数据与规则化模型生成，不构成投资建议，'
    '不代替用户下单，也不代表用户实际持仓。策略预算为初始研究假设，未经最优性验证。'
    '不声称策略已实现超额收益。市场有风险，投资需谨慎。'
)
