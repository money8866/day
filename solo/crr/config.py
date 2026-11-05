# -*- coding: utf-8 -*-
"""
CRR V1.0（Crash Repricing & Recovery）配置

目标：市场明显下跌后，识别「基本面优质、盈利预期未明显恶化、因系统性/恐慌抛售
      导致股价过度下跌、有估值修复潜力」的错杀股；只输出 BUY / WATCH / NO TRADE。

数据源边界（硬约束）：
  * 只用 Tushare 本地缓存（SQLite + parquet）+ 本地 sli/industry DB，禁止引入 TDX。
  * 不因接口缺失编造数据：缺失标 UNKNOWN 并降低置信度。
  * as-of 防未来：财务 ann_date/eval_date <= scan_date，行情 trade_date <= scan_date。

评分边界（硬约束）：
  * 用户消息只定义到模块四：模块三 30 分 + 模块四 25 分 = SCORE_CAP = 55。
  * 其余 45 分、抛压衰竭、资金回流/趋势修复、模块五/六 = 未定义，列入
    PENDING_MODULES，不实现、不臆造、不参与评分。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOLO_DIR = os.path.dirname(BASE_DIR)

# ---- 数据源路径（无任何 TDX 路径）----
CACHE_DIR = r"D:\mystock\cache_daily"
CACHE_DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
STOCK_BASIC_CSV = os.path.join(CACHE_DIR, "stock_basic.csv")
PARQUET_DIR = os.path.join(CACHE_DIR, "parquet")
SLI_DB = os.path.join(SOLO_DIR, "sli", "db", "sli.db")
INDUSTRY_DB = os.path.join(SOLO_DIR, "cache_industry", "industry_holdings.db")
REPORT_DIR = os.path.join(SOLO_DIR, "output", "crr")

# ---- 评分上限与未定义模块 ----
SCORE_CAP = 55
PENDING_MODULES = [
    "抛压衰竭量化（成交量/换手/振幅收敛、卖压衰减）——开发目标第5点，用户未给指标/阈值/评分",
    "资金回流与趋势修复条件（资金流、均线修复、相对强度）——开发目标第6点，用户未给指标/阈值/评分",
    "评分剩余 45 分（55→100 的构成）及模块五/模块六——用户消息仅定义到模块四",
]

MARKET_STATES = ("NORMAL", "CORRECTION", "SELLOFF", "PANIC",
                 "SELLING_EXHAUSTION", "RECOVERY")
SCAN_STATES = ("SELLOFF", "PANIC", "SELLING_EXHAUSTION", "RECOVERY")

# ---- 模块一：市场暴跌识别 ----
MARKET = {
    "windows": (5, 10, 20),
    "vol_window": 20,
    "amount_ma": 20,
    # 分级阈值（全A等权日收益累乘 + 全市场广度）
    "th_correction_ret20": -0.05,
    "th_correction_ret10": -0.04,
    "th_selloff_ret10": -0.08,
    "th_selloff_ret5": -0.05,
    "th_selloff_down_ratio": 0.72,
    "th_panic_ret5": -0.10,
    "th_panic_down_ratio": 0.88,
    "th_panic_limit_down": 60,       # 跌停家数（pct_chg 阈值代理）
    "th_panic_vol_ratio": 1.8,       # 近20日波动率 / 基准波动率
    # 涨跌停代理（本地无 limit_list_d 全量）：按板块阈值判定，主板10/创业科创20/北交30/ST5
    "limit_board_thresholds": {
        "main": 9.8, "gem_star": 19.8, "bj": 29.8, "st": 4.8,
    },
    "limit_anomaly_clip": 31.0,      # |pct_chg|>31 视为新股/复牌/异常，剔除不计数
    # 抛压衰竭：仍处跌后、但卖压边际收缩
    "th_exhaust_down_ratio": 0.65,
    "th_exhaust_amount_ratio": 0.90,  # 成交额 / 近5日均额
    "th_exhaust_limit_shrink": 0.50,  # 跌停家数 / 近5日峰值
    # 修复：跌后转正
    "th_recovery_ret5": 0.0,
    "th_recovery_up_ratio": 0.58,
    # 连续性约束：单日反弹不得直接判定暴跌结束
    "min_crash_days": 5,
    "exit_confirm_days": 3,
    "exhaust_confirm_days": 2,
    "recovery_confirm_days": 3,
    "min_breadth_days": 25,
}

# ---- 模块二：错杀程度 ----
MISPRICING = {
    "windows": (5, 10, 20, 60),
    "primary_window": 20,          # 主错杀窗口
    "beta_window": 120,            # 双因子回归窗口
    "vol_window": 250,             # 过去一年波动率
    "min_beta_bars": 60,
    "min_industry_members": 3,
    "z_clip": 6.0,
    "min_drop": 0.05,              # 20日跌幅 < 5% 不视为错杀候选
    # 归因：解释占跌幅的份额
    "sys_dominant": 0.55,
    "ind_dominant": 0.50,
    "specific_dominant": 0.50,
}

# ---- 模块三：基本面质量 30 分 ----
QUALITY_WEIGHTS = {
    "roe": 4,
    "roic": 3,
    "revenue_growth": 3,
    "deducted_profit_growth": 3,
    "ocf_match": 3,
    "gross_margin": 3,
    "gross_margin_trend": 2,
    "debt_to_assets": 3,
    "interest_debt": 1,
    "goodwill_ratio": 2,
    "recent_3q": 2,
    "forecast_change": 1,
}  # = 30

VETO = {
    "forecast_loss_types": ("首亏", "续亏", "增亏", "预减"),
    "forecast_big_drop_pct": -50.0,   # p_change_max < -50 视为重大预亏
    "ocf_negative_quarters": 2,       # 连续 N 季经营现金流为负
    "revenue_shrink_pct": -30.0,      # 营收同比 < -30% 视为核心业务萎缩
    "debt_to_assets": 85.0,           # 资产负债率 > 85 债务风险（金融股豁免）
    "goodwill_ratio": 0.30,           # 商誉/净资产 > 30%
    "financial_l1": ("银行", "非银金融", "保险", "证券", "多元金融"),
}

# ---- 模块四：估值错杀 25 分 ----
VALUATION_WEIGHTS = {
    "pe_ttm_pct": 6,
    "pb_pct": 5,
    "ev_ebitda_pct": 3,        # 无字段 → 恒 UNKNOWN，按可用权重归一化
    "fair_value_gap": 4,       # 合理盈利与当前市值偏离
    "forecast_downgrade": 3,   # 盈利预期下修幅度（下修越小越好）
    "drop_vs_downgrade": 4,    # 股价跌幅 - 盈利预期下修幅度
}  # = 25

VALUATION = {
    "percentile_years": 5,
    "percentile_min_obs": 250,
    "pe_cheap_cap": 60.0,        # PE_TTM 高于此值不做「便宜」加分
    "pb_cheap_cap": 12.0,
    "pseudo_value_pe_pct": 0.60,  # 估值分位高于此 + 盈利下修 → 伪低估值
}

# ---- 决策与置信度 ----
DECISION = {
    "buy_score_ratio": 0.68,
    "buy_min_confidence": 0.55,
    "watch_score_ratio": 0.45,
    "watch_min_confidence": 0.34,
    "buy_max_specific_share": 0.50,  # 个股特异抛售占比过高不得 BUY
    "top_n": 30,
    "min_data_completeness": 0.30,   # 低于此值一律 NO TRADE
}

CONFIDENCE = {
    "w_completeness": 0.50,
    "w_state": 0.20,
    "w_coverage": 0.30,
}

# ---- 数据源声明（供 --audit 输出；如实标注已知缺口）----
DATA_SOURCES = {
    "daily_cache": "SQLite 7.23M 行, 20210104~20261104(1413交易日)；pct_chg/100 累乘构造复权收益（含 open/high/low/close/pre_close/change/vol/amount）",
    "daily_basic_cache": "SQLite 7.2M 行, 同期；pe_ttm/pb 可直接算历史5年分位；含 total_mv/circ_mv/turnover_rate/volume_ratio/float_share/total_share/dv_ttm",
    "fina_indicator_cache": "SQLite 76773 行, ann_date 20230407~20260829；as-of 财务主源；含 grossprofit_margin/netprofit_yoy/or_yoy/ocf_to_or/ocf_yoy/roe/debt_to_assets；缺 roic/扣非/商誉/有息负债（由 sli 与 parquet 补）",
    "sli.leaderboard_v2": "47515 行, eval_date 20260825~20261030(10快照,最新4756代码)；l1_name/l2_name 申万分类 + roic/扣非增强(gm_prev/q_profit_yoy/dt_netprofit_yoy/ocf_to_profit 等)；仅 eval_date<=scan_date 可用",
    "sw_industry / sw_member": "industry_holdings.db；31个申万一级 + 7740 成分；按时点 in_date<=scan_date 且(out_date IS NULL 或 out_date>scan_date)过滤；用于自建行业收益",
    "index_daily_cache": "000001.SH/000688.SH/399001.SZ/399006.SZ=1413d 到 1104；000300.SH/000852.SH/000905.SH/932000.CSI=2143d；000985.CSI=2124d 到 1103(尾部 nan)。主市场收益=全A聚合(daily_cache)，指数仅交叉校验",
    "adj_factor_cache / stk_factor_pro / factor_snapshot_cache": "均存在；stk_factor_pro 4.9M 行 260+ 技术列(qfq/hfq)；本版仅作可选交叉校验，不参与主计算",
    "parquet income_*.parquet": "10164 文件；含 ann_date；total_revenue/revenue/n_income/n_income_attr_p/total_cogs/operate_profit/basic_eps/report_type；用于营收/净利增长",
    "parquet cashflow_*.parquet": "4926 文件；含 ann_date；n_cashflow_act(经营现金流净额)；用于 OCF 匹配与连续为负否决",
    "parquet balance_v*.parquet / balance_*.parquet": "4928 文件；含 ann_date；goodwill/total_assets/total_hldr_eqy_exc_min_int；用于商誉/净资产",
    "parquet fina_indicator_*.parquet": "18193 文件；含 ann_date；roe/roic/profit_dedt(扣非)/debt_to_assets/eps；注意 gross_margin 为毛利额(绝对值)非比率，比率改用 fina_indicator_cache.grossprofit_margin 或 sli",
    "parquet forecast_vip_*.parquet": "25 文件(按 end_date)；仅 20260630 中报预告样本 1913 行；type(首亏/续亏/增亏/预减/略减/预增)+p_change_min/max；ann_date 20260422~20260824；用于业绩预告/预期变化与重大预亏否决",
    "parquet express_vip / actual_vip": "仅 20260630 快报 62 行 / 实际 6 文件；覆盖极低 → UNKNOWN",
    "limit_list_d": "本地仅 6 个交易日，不足以全量判定 → 涨跌停改用 pct_chg 板块阈值代理(limit_proxy=True)，并按 |pct_chg|>31 剔除异常",
    "suspend_d / sw_daily": "本地无文件 → 停牌用近20日交易天数近似(UNKNOWN)；行业收益用 sw_member 成分或 sli.l2_name 自建",
    "EV/EBITDA、有息负债、重大客户流失、重大监管及经营风险、逐日盈利一致预期": "无对应字段 → UNKNOWN + 置信度降级；相关否决项无证据不触发",
}

UNKNOWN = "UNKNOWN"
