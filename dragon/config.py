# -*- coding: utf-8 -*-
"""主线龙头分歧策略 - 全局配置"""
import os

# === 项目路径 ===
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PROJECT_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# === Tushare ===
TUSHARE_TOKEN = "bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d"

# === 通达信本地数据 ===
TDX_SH_LDAY = r"C:\new_tdx\vipdoc\sh\lday"
TDX_SZ_LDAY = r"C:\new_tdx\vipdoc\sz\lday"
TDX_1MIN = r"C:\new_tdx\vipdoc"  # sh\minline\, sz\minline\

# === 市场状态机参数 ===
MARKET_STATE = {
    "ice": {        # 冰点
        "ad_ratio": 0.25,        # 上涨家数比 < 25%
        "vol_shrink": 0.7,       # 量能萎缩 < 0.7
        "idx_ma5_below_ma20": True,  # 指数MA5 < MA20
    },
    "recover": {    # 修复
        "ad_ratio": (0.25, 0.50),
        "idx_above_ma5": False,
    },
    "bull": {       # 主升
        "ad_ratio": 0.50,
        "vol_expand": 1.2,       # 量能放大 > 1.2
        "idx_above_ma5": True,
        "idx_above_ma20": True,
    },
    "ebb": {        # 退潮
        "ad_ratio": 0.40,
        "ad_declining": True,    # 连续3天下降
    },
}

# === 板块强度参数 ===
SECTOR = {
    "top_concepts": 5,          # 取前N个概念主线
    "top_industries": 3,        # 取前N个行业主线
    "capital_flow_weight": 0.4, # 主力资金权重
    "momentum_weight": 0.3,     # 涨幅权重
    "breadth_weight": 0.3,      # 板块内部上涨比权重
    "lookback": 10,             # 评判周期(天)
}

# === 龙头评分参数 ===
DRAGON = {
    "min_market_cap": 50e8,     # 最小市值50亿
    "csi2000_only": True,      # 仅中证2000成分股（精确列表，Tushare index_weight接口）
    "min_turnover": 2.0,        # 最低换手率2%
    "max_rsi": 85,              # RSI上限
    "min_price": 10.0,          # 最低价格
    "weights": {
        "sector_rank": 0.25,    # 板块排名
        "momentum_5d": 0.15,    # 5日涨幅
        "momentum_10d": 0.15,   # 10日涨幅
        "momentum_20d": 0.10,   # 20日涨幅
        "turnover": 0.10,       # 换手率
        "volume_ratio": 0.10,   # 量比
        "ma_alignment": 0.08,   # 均线多头排列
        "limit_days": 0.07,     # 连板天数
    },
    "top_pct": 0.10,            # 取Top 10%
    "min_score": 60,            # 最低入选分
}

# === 分歧预警参数 ===
DIVERGENCE = {
    "sector_rank_drop": 7,      # 板块排名下降超过N名
    "volume_shrink_days": 3,    # 量能连续萎缩天数
    "breadth_decline_days": 3,  # 市场宽度连续下降天数
    "idx_above_ma5_pct": 0.10,  # 指数偏离MA5超10%
    "rsi_overbought": 80,       # RSI超买
}

# === 仓位控制 ===
POSITION = {
    "state_base": {             # 按市场状态的基准仓位
        "ice": 0.20,
        "recover": 0.40,
        "bull": 0.80,
        "ebb": 0.30,
    },
    "divergence_discount": 0.5, # 分歧时仓位打折
    "max_stocks": 3,            # 最大持仓数
    "single_max": 0.35,         # 单只最大仓位
    "stop_loss": -0.05,         # 个股止损-5%
    "take_profit": 0.20,        # 个股止盈+20%
    "trailing_stop": 0.08,      # 移动止损8%
}

# === 回测参数 ===
BACKTEST = {
    "start_date": "20250101",
    "end_date": "20260522",
    "init_cash": 1_000_000,
    "commission": 0.0003,       # 万三佣金
    "stamp_tax": 0.001,         # 印花税(卖出)
    "slippage": 0.001,          # 滑点
}

# === 东财API ===
EASTMONEY = {
    "concept_url": "http://push2.eastmoney.com/api/qt/clist/get",
    "industry_url": "http://push2.eastmoney.com/api/qt/clist/get",
    "stock_list_url": "http://push2.eastmoney.com/api/qt/clist/get",
}

# === 个股池过滤 ===
STOCK_FILTER = {
    "exclude_st": True,
    "exclude_new_days": 60,     # 上市不足60天排除
    "min_price": 5.0,
    "max_pe": 200,
    "suspend_days_max": 5,
}
