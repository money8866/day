# -*- coding: utf-8 -*-
"""
「猎尾V5」NEXT-DAY ALPHA ENGINE (ND2) 全局配置
所有阈值/权重集中于此,禁止散落在引擎函数内部

核心目标: 最大化 P(次日最高价 >= 买入价×1.02), 同时控制 P(次日最低 <= 买入价×0.98)
"""

# ════════════════════════════════════════════════════════════
# L0 市场环境乘数
# ════════════════════════════════════════════════════════════
MARKET_MULTIPLIER = {
    # V3市场状态名 -> multiplier (compute_market_sentiment_report输出)
    '主升浪': 1.15,
    '强趋势': 1.05,
    '趋势良好': 1.00,
    '震荡': 0.90,
    '弱势': 0.75,
    '退潮': 0.50,
    '主跌段': 0.50,
    # 兼容别名
    '极强市场': 1.15, '偏强市场': 1.05, '正常市场': 1.00,
    '震荡市场': 0.90, '弱势市场': 0.75, '极弱市场': 0.50,
}

# 趋势总评分 -> 市场档位映射 (复用现有 market_status 体系)
# trend_score >= 80 主升浪 / >=70 强趋势 / >=60 趋势良好 / >=55 震荡 / >=45 弱势 / >=35 退潮 / else 主跌段
TREND_TO_MARKET = [
    (80, '极强市场'),
    (70, '偏强市场'),
    (60, '正常市场'),
    (55, '震荡市场'),
    (45, '弱势市场'),
    (35, '极弱市场'),
    (0,  '极弱市场'),
]

# BREAKOUT_TAIL 形态的最低市场乘数要求 (突破形态强依赖环境)
BREAKOUT_MIN_MULTIPLIER = 0.80


# ════════════════════════════════════════════════════════════
# L1 硬过滤
# ════════════════════════════════════════════════════════════
HARD_FILTER = {
    'min_mv': 80000,            # 总市值(万元) >= 8亿
    'max_ma20_dist': 25,        # 距MA20 > 25% 过滤
    'max_gain_5d': 15,          # 5日涨幅 > 15% 过滤(形态豁免见下)
    'max_turnover': 15,         # 换手 > 15% 过滤
    'min_turnover': 0.5,        # 换手 < 0.5% 过滤
    'min_theme_strength': -1,   # 主题强度 < -1 过滤(退潮)
    'max_consec_limit': 2,      # 连续涨停 >= 2板 过滤

    # 涨幅分层 (V5: 替代 V3 的 pct < 0.5 一刀切)
    'pct_layers': {
        # (下限, 上限, 处理): 'normal'正常 'best'最优 'acceptable'高位风险 'strong_only'仅强资金 'reject'过滤
        (None, 0.5):   'weak',         # <0.5%: 需 TailFlow强阈值+ClosePosition>=0.80 才保留
        (0.5, 1.5):    'normal',
        (1.5, 4.5):    'best',
        (4.5, 6.5):    'acceptable',
        (6.5, 8.0):    'strong_only',
        (8.0, None):   'reject',
    },
    'weak_pct_tailflow_min': 16,   # <0.5%涨幅时 TailFlow 需 >= 16/25
    'weak_pct_closepos_min': 0.80, # <0.5%涨幅时 ClosePosition 需 >= 0.80

    # 振幅分层 (V5: 替代 V3 的振幅>8% 一刀切)
    'max_amplitude': 8,           # 基础振幅上限
    # 振幅>8% 豁免路径: 早盘剧烈震荡 + 午后缩量稳定 + 14:30后放量上攻
    'amplitude_exemption': {
        'morning_vs_noon_vol_ratio_max': 1.3,  # 午后(14:00)量 <= 早盘量*1.3 视为缩量稳定
        'tail_rebound_min_pct': 0.3,           # 14:30后回涨 >= 0.3%
    },
}


# ════════════════════════════════════════════════════════════
# L2 形态分类 (PatternClassifier)
# ════════════════════════════════════════════════════════════
PATTERN = {
    # ── A类: PULLBACK_GAP 强势基因回调低吸 ──
    'pullback': {
        'limit_up_20d_min': 2,        # 20日涨停次数 >= 2 (强势基因)
        'pullback_days': (2, 7),      # 回调2~7个交易日
        'pullback_depth': (5, 15),    # 回撤5~15%
        'vol_shrink_ratio_max': 0.8,  # 回调末段量/上涨段量 <= 0.8 (缩量)
        'support': 'ma10_or_break_mid',  # MA10/前突破K线中轴附近企稳
        'tail_reflow': True,          # 14:30后资金回流
    },
    # ── B类: BREAKOUT_TAIL 平台突破尾盘 ──
    'breakout': {
        'platform_days': (5, 20),     # 横盘5~20日
        'platform_width_max': 8,      # 平台振幅 < 8%
        'breakout_vol_ratio_min': 1.5,  # 尾盘量比基准
        'close_pos_min': 0.75,        # 收盘位置 >= 75%
    },
    # ── C类: STEALTH_ACCUMULATION 隐蔽吸筹 ──
    'stealth': {
        'pct_range': (0.5, 3.0),      # 全天涨幅 0.5~3%
        'tail_start_hour': 14,        # 14:20后开始
        'tail_start_minute': 20,
        'vol_expand_min': 1.2,        # 尾盘量比 >= 1.2
        'shallow_pullback_max': 0.5,  # 尾盘阶段最大回撤 <= 0.5%
        'close_pos_min': 0.75,        # 收盘位置靠近全天最高
    },
}


# ════════════════════════════════════════════════════════════
# L3 评分模块权重 (Base 100)
# ════════════════════════════════════════════════════════════
SCORE_WEIGHTS = {
    'trend_structure': 15,    # 趋势结构
    'pattern_quality': 15,    # 形态质量(按形态映射)
    'tail_flow': 25,          # 尾盘资金 (V5核心)
    'strong_gene': 10,        # 强势基因
    'nd2_potential': 15,      # 次日+2%潜力
    'theme_alpha': 12,        # 主题Alpha (V3为20,降权)
    'market_alpha': 8,        # 市场Alpha
}

BONUS_MAX = 10                # 加分项上限
RISK_PENALTY_MAX = 20         # 风险扣分上限


# ════════════════════════════════════════════════════════════
# Tail Flow 引擎 (25分)
# ════════════════════════════════════════════════════════════
TAIL_FLOW = {
    # 1. 尾盘量能扩张 tail_volume_ratio = vol(14:30~14:50) / vol(14:00~14:30)
    'vol_expansion_table': [
        # (上限, 得分)  ratio < 上限时得分
        (0.8, 0), (1.2, 2), (1.8, 5), (2.5, 8),
    ],
    'vol_expansion_top_score': 10,   # ratio > 2.5 得10分

    # 2. 尾盘价格加速度 tail_return = price_1450/price_1430 - 1
    'price_accel_table': [
        # (tail_return上限%, 得分)
        (0.0, 0), (0.3, 1), (0.8, 3), (1.8, 5), (3.0, 4),
    ],
    'price_accel_top_score': 1,      # > 3% 得1分(暴力拉升透支次日)

    # 3. 收盘位置 close_position = (price-low)/(high-low)
    'close_pos_table': [
        (0.60, 0), (0.75, 2), (0.85, 3), (0.93, 5),
    ],
    'close_pos_top_score': 6,        # > 0.93 得6分

    # 4. 买压 (无逐笔数据时用代理: 尾盘阳线效率)
    'buy_pressure_table': [
        (50, 0), (55, 1), (60, 3),
    ],
    'buy_pressure_top_score': 4,     # > 60% 得4分
}


# ════════════════════════════════════════════════════════════
# Pullback Quality (15分, 仅 PULLBACK_GAP)
# 非 Pullback 模型映射为 Breakout/Accumulation Quality
# ════════════════════════════════════════════════════════════
PULLBACK_QUALITY = {
    'days': {'range': (2, 7), 'score': 3},
    'depth': {'range': (5, 15), 'score': 3},
    'vol_shrink': {'max_ratio': 0.8, 'score': 3},
    'ma10_stabilize': {'max_dist_pct': 3.0, 'score': 2},
    'break_kline_mid': {'max_dist_pct': 2.0, 'score': 2},
    'tail_reflow': {'min_vol_ratio': 1.1, 'score': 2},
}


# ════════════════════════════════════════════════════════════
# Strong Gene (10分)
# ════════════════════════════════════════════════════════════
STRONG_GENE = {
    'limit_up_scores': [(1, 3), (2, 5), (3, 6)],  # (次数下限, 分)
    'limit_up_default': 6,       # >=3次
    'trend_ma_aligned': 2,       # MA5>MA10>MA20 加分
    'trend_ma20_up': 1,          # MA20向上 加分
    'gene_quality_penalty': 3,   # 连续暴涨后高位涨停基因质量扣分
    'high_gain_threshold': 20,   # 20日涨幅超过此值视为连续暴涨
}


# ════════════════════════════════════════════════════════════
# ND2 Potential (15分) -- 次日+2%概率
# ════════════════════════════════════════════════════════════
ND2_POTENTIAL = {
    'grade_scores': {
        # 评分分档: 0~15
        '极差': (0, 3), '一般': (4, 6), '中等': (7, 9),
        '较强': (10, 12), '极强': (13, 15),
    },
    # 特征权重 (用于规则引擎线性加权 -> 映射到0~15)
    'feature_weights': {
        'dist_20d_high': 2.0,       # 距20日高点距离(适中最优)
        'overhead_pressure': 2.0,   # 上方套牢盘压力(越小越好)
        'gain_5d': 1.5,             # 近5日涨幅(适中)
        'today_pct': 1.0,           # 当日涨幅(适中)
        'tail_accel': 2.0,          # 14:30后价格加速度(温和最优)
        'tail_vol_change': 1.5,     # 尾盘量能变化
        'close_position': 1.5,      # 收盘位置(越高越好)
        'limit_up_gene': 1.5,       # 前期涨停基因
        'pullback_complete': 1.5,   # 回踩完成度
        'gap_room': 1.5,            # 次日常见缺口空间
    },
}


# ════════════════════════════════════════════════════════════
# Theme Alpha (12分)
# ════════════════════════════════════════════════════════════
THEME_ALPHA = {
    'theme_strength': 4,        # 主题主线强度
    'theme_reflow': 3,          # 主题当日资金回流
    'stock_theme_position': 3,  # 个股在主题中的强度
    'leader_linkage': 2,        # 龙头/中军联动
}


# ════════════════════════════════════════════════════════════
# Risk Penalty (-20)
# ════════════════════════════════════════════════════════════
RISK = {
    'high_position': 8,         # 高位风险(距20日高过近+连续上涨+距MA20远)
    'high_turnover': 5,         # 高换手(区分低位抢筹/高位派发)
    'tail_distribution': 10,    # 尾盘诱多(量暴增价滞涨+长上影+收盘弱)
    'isolated_rise': 5,         # 孤立上涨(主题全弱个股独涨, stealth减半)
    # 高位/低位换手区分
    'turnover_high_pos_threshold': 8,     # 距20日高<8%视为高位
    'turnover_low_pos_gain_max': 5,       # 低位定义: 距20日高>8%且20日涨幅<5%? 不,直接用位置
}


# ════════════════════════════════════════════════════════════
# S/A/B 交易分级门槛 (多因子, 禁止仅按FinalScore)
# ════════════════════════════════════════════════════════════
GRADE_THRESHOLDS = {
    'S': {
        'final_score': 82,
        'tail_flow': 18,         # /25
        'pattern_quality': 10,   # /15
        'nd2_potential': 10,     # /15
        'risk_penalty_max': 5,
        'market_multiplier_min': 0.80,
        'forbid_tail_distribution': True,   # 不得尾盘诱多
        'forbid_high_pos_overdraw': True,   # 不得连续加速后高位
    },
    'A': {
        'final_score': 75,
        'tail_flow': 15,
        'nd2_potential': 8,
        'risk_penalty_max': 8,
    },
    'B': {
        'final_score': 65,       # 65 <= score < 75 仅Watchlist
    },
    'reject': 65,                # < 65 淘汰
}


# ════════════════════════════════════════════════════════════
# 最终决策公式权重 (final_alpha)
# ════════════════════════════════════════════════════════════
ALPHA_WEIGHTS = {
    'tail_flow': 0.30,
    'pattern_quality': 0.25,
    'nd2_potential': 0.20,
    'strong_gene': 0.10,
    'theme_alpha': 0.10,
    'market_alpha': 0.05,
}


# ════════════════════════════════════════════════════════════
# 排名公式权重 (rank_score)
# ════════════════════════════════════════════════════════════
RANK_WEIGHTS = {
    'p_up_2': 0.40,
    'no_drawdown': 0.20,        # (1 - P_DRAWDOWN_2)
    'final_score': 0.20,
    'probability_confidence': 0.10,
    'expected_alpha': 0.10,
}


# ════════════════════════════════════════════════════════════
# 历史统计 / 概率引擎
# ════════════════════════════════════════════════════════════
PROBABILITY = {
    'min_sample_size': 30,      # 分桶最少样本量,否则置信度降低
    'good_sample_size': 100,    # 样本充足
    # 样本量->置信度函数参数: confidence = min(1.0, 0.3 + 0.7*(n-good)/(min-good))  简化线性
    'confidence_base': 0.3,
    'fallback_p_up_2': 0.45,    # 无历史样本时的先验概率
    'fallback_p_close_2': 0.30,
    'fallback_p_dd_2': 0.25,
    # 桶定义: (维度, 分箱边界)
    'buckets': {
        'pattern': None,            # 类别: PULLBACK_GAP/BREAKOUT_TAIL/STEALTH_ACCUMULATION/OTHER
        'tail_flow_bin': [0, 8, 14, 18, 25],     # TailFlow得分段
        'nd2_bin': [0, 6, 10, 15],               # ND2得分段
        'strong_gene_bin': [0, 4, 7, 10],        # 强势基因得分段
        'theme_bin': [0, 5, 9, 12],              # 主题Alpha得分段
        'market_bin': None,         # 类别: 极强/偏强/正常/震荡/弱势/极弱
    },
}


# ════════════════════════════════════════════════════════════
# 快照与标签 (ND2SnapshotStore)
# ════════════════════════════════════════════════════════════
SNAPSHOT = {
    'db_path': r'D:\mystock\cache_daily\nd2_snapshot.db',
    'table': 'nd2_snapshot',
    'label_table': 'nd2_label',
    'target_pct': 0.02,           # Y_UP_2 / Y_CLOSE_2 阈值
    'drawdown_pct': -0.02,        # Y_DD_2 阈值
    'min_score_to_save': 60,      # 保存快照的最低分(保留足够负样本)
    'max_stocks_per_day': 60,     # 每日快照上限(控制DB体积)
}


# ════════════════════════════════════════════════════════════
# ML 接口预留 (第二阶段,当前规则引擎独立运行)
# ════════════════════════════════════════════════════════════
ML_CONFIG = {
    'enabled': False,
    'model_type': None,           # 'logistic' / 'lightgbm' / 'xgboost'
    'model_path': None,
}
