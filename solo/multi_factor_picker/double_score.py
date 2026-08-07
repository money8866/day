"""
翻倍黑马综合评分系统 (DoubleScore V15 机构成长版)

从 40+ 维度中提取核心因子，计算综合得分，筛选具备翻倍潜力的标的。
应用一票否决机制：PEG>1.2、估值空间<30%、利润同比<0 直接剔除。

V12.2 标准化评分体系（成长因子）:
    原始财务数据 → 缩尾(1%/99%) → 对数压缩(signed-log1p) → 行业内Z-Score
    → clip[-2.5,2.5] → 0-100分 → 按权重融合

V12.3 优化（机构研究框架）:
    PEG 改用 FutureGrowth 前瞻增速；新增质量因子 QualityScore。

V13 双榜单:
    DoubleScore 排行榜（1~6个月爆发弹性）+ SustainableScore 持续成长榜。

V14 机构成长版（四维评分体系）:
    ① DoubleScore（短期爆发力 3~6个月）
       成长35% + PEG20% + 估值20% + 行业景气15% + 盈利加速度10%
    ② SustainableScore（持续成长 1~3年）
       成长持续性35% + 行业景气25% + 盈利质量20% + ROE稳定性10% + 现金流10%
    ③ MoatScore（竞争壁垒，新增）
       行业地位25% + 研发投入20% + 毛利率20% + 客户壁垒15% + 市场份额10% + 产品壁垒10%
    ④ RiskScore（风险评分，新增）: 业绩波动/负债率/商誉/应收/存货/减持/解禁/周期
    FinalScore = Double×30% + Sustainable×35% + Moat×25% + (100-Risk)×10%，按 FinalScore 排序。

V14.1 MoatScore 增强（机构研究框架六维度）:
    回答"未来3~10年能否持续创造超额收益"，只评价长期竞争优势，
    不受利润同比/单季度利润/短期股价/市场热点影响。
    ① 市场地位25% ② 技术壁垒20% ③ 产品竞争力15% ④ 客户壁垒15%
    ⑤ 盈利能力15% ⑥ 成长护城河10% → 输出 MoatScore + MoatLevel + MoatExplain。

V15 升级（行业护城河 + 解释引擎）:
    ① MoatScore 行业模板化：半导体/创新药/AI软件/游戏/化工/机器人/航运/有色资源
       各行业采用不同护城河维度权重（技术壁垒/客户认证/产品管线/商业化/IP/成本/船队等），
       未命中行业回退通用六维度；统一输出 MoatScore + MoatLevel(★★★★★~★) + MoatExplain。
    ② Explain Engine 解释引擎（券商研报可读性，全部基于财报/行业/主营数据，不编造）：
       TopReasons（为什么排名高，3~5条）
       Weakness（哪些因素拖累评分，1~3条）
       LogicEvidence（财报证据引用，如营收+126%/毛利率+8.2pct）
       NextQuarterWatch（下一份财报重点验证，按行业模板）
       InvestmentSummary（一句话投资逻辑 50~80字）
       TopRisk（最大的两个风险，具体到经营/行业/政策/估值）
       IndustryRank / IndustryPercentile（行业内排名，判断是否行业龙头）
       Recommendation（统一推荐动作：★★★★★重点配置~★回避，FinalScore+Risk+行业景气）
    ③ 保持所有已有评分框架、CSV 字段及 API 兼容，仅新增解释层，不影响计算流程。

V14 指标层升级（必须）:
    - AdjustedProfitGrowth = 100×log2(1+利润同比×质量系数/100)（低基数修正 + 对数压缩）
    - RevenueQuality = log2压缩营收增速标准化分 + 毛利率协同（高毛利放量奖励/低毛利走量降分）
    - ProfitQualityPenalty: 经营现金流为负 或 扣非明显低于净利润 → 降低成长评分
    - FutureGrowth = 0.5×营收YoY + 0.3×AdjustedProfitGrowth + 0.2×3年利润CAGR
    - PEGScore = 100/(1+PEG) 连续函数（替代阶梯评分）
    - IndustryCycleScore 0~100 直接参与权重（替代固定 1.05/1.10 乘法）
    - 主题体系: IndustryTheme(主营产业) + ConceptTheme(市场概念) 双主题输出
    - 核心逻辑: 一级逻辑(需求/产品/成本/周期/政策/一次性) + 二级逻辑 + 可信度星级
"""
import pandas as pd
import numpy as np
from pathlib import Path


# ─────────────────────────────────────────────
# 12 因子评分配置
# ─────────────────────────────────────────────
SCORE_CONFIG = {
    # 因子名称: (权重, 门槛值, 满分阈值, 是否越高越好)
    '营收同比':        (0.10, 30.0,  80.0,  True),
    '利润同比':        (0.15, 50.0,  150.0, True),
    '业绩超预期':      (0.10, 0.0,   20.0,  True),
    'ROE':             (0.10, 15.0,  25.0,  True),
    '毛利率':          (0.08, 30.0,  60.0,  True),
    '研发投入':        (0.07, 8.0,   15.0,  True),
    'PEG':             (0.15, 0.0,   0.8,   False),  # 越低越好
    '估值空间':        (0.10, 30.0,  80.0,  True),
    '流通市值':        (0.05, 200.0, 800.0, True),  # 200~800亿最佳
    '机构动向':        (0.05, 0.0,   60.0,  True),
    '龙头类型':        (0.05, 0.0,   100.0, True),
    '主题主线匹配':    (0.00, 0.0,   100.0, True),  # 静态加分，不计入权重
    '非经常损益':      (0.00, 0.0,   100.0, True),  # 扣分项，以乘数方式作用于总分
    'Q1加速对比':      (0.00, 0.0,   100.0, True),  # 扣分项，以乘数方式作用于总分
}

# 龙头类型 → 分值映射
LEADER_SCORE = {
    '行业龙头': 100,
    '行业龙二': 85,
    '中军': 70,
    '龙二': 60,
    '补涨': 30,
    '普通': 0,
}

# 核心主线主题（加分项）
CORE_THEMES = {
    'AI芯片', 'AI算力', 'AI应用', 'AI文娱内容', 'AI新消费', '光模块',
    '半导体', '存储芯片', '半导体设备', '半导体材料',
    '创新药', '创新药/生物技术', '生物医药',
    '机器人', '人形机器人', '智能驾驶', '低空经济', '商业航天',
    '固态电池', '氢能', '储能', '新能源汽车链',
    '数据要素', '云计算', '工业互联网', '人工智能',
    '军工', '航天军工', '船舶制造',
    '出海链', '医疗器械',
}

# ─────────────────────────────────────────────
# 增强维度配置 (v1.1)
# ─────────────────────────────────────────────

# 1. 利润质量过滤 — 低基数识别
# 利润同比 > 1000% 且 营收同比 < 30% → 低基数/一次性恢复嫌疑
LOW_BASE_PROFIT_YOY = 1000.0   # 触发低基数检查的利润同比阈值
LOW_BASE_REV_YOY = 30.0        # 营收同比低于该值视为"营收无法支撑利润高增"
LOW_BASE_Q1_PROOF = 500.0      # Q1利润同比高于该值 → 趋势延续(Q1已高增)，低基数嫌疑较小
LOW_BASE_DISCOUNT_MILD = 0.90  # Q1已高增时的折扣
LOW_BASE_DISCOUNT_SEVERE = 0.75  # 无Q1支撑时的折扣(疑似一次性恢复)
LOW_BASE_NR_EXTRA = 0.85       # 叠加非经常损益>20%的额外折扣

# 2. 主题真实性校验 — 主题 → 允许的行业白名单(关键词子串匹配)
# 行业不在白名单 → 主题-主业匹配存疑(如航运公司被归入"低空经济")
# 白名单覆盖主题的产业链上下游，避免误伤合理相关行业
THEME_INDUSTRY_MAP = {
    '低空经济':    ['航空', '航天', '通信设备', '电气设备', 'IT设备', '元器件', '电器仪表', '通用机械', '专用机械', '仪器仪表', '汽车配件', '电机', '仓储物流', '快递'],
    '存储芯片':    ['半导体', '元器件', 'IT设备'],
    'AI算力':     ['半导体', '通信设备', 'IT设备', '软件服务', '元器件', '互联网', '电器仪表', '电气设备', '数据中心'],
    'AI芯片':     ['半导体', '元器件'],
    'AI应用':     ['软件服务', '互联网', 'IT设备', '传媒', '游戏', '影视音像'],
    '光模块':     ['通信设备', '元器件', '半导体'],
    '半导体设备':   ['半导体', '专用机械', '电气设备', '机械基件', '电器仪表', '元器件', '化工原料', '仪器仪表'],
    '半导体材料':   ['半导体', '化工原料', '玻璃', '材料', '元器件'],
    '机器人':     ['机械基件', '电气设备', '元器件', '专用机械', '通用机械', '仪器仪表', '汽车配件', '电机', '轻工机械'],
    '人形机器人':   ['机械基件', '电气设备', '元器件', '专用机械', '通用机械', '仪器仪表', '汽车配件', '电机', '轻工机械'],
    '智能驾驶':    ['汽车配件', '通信设备', '软件服务', '元器件', '半导体', 'IT设备', '摩托车', '汽车整车'],
    '创新药':     ['化学制药', '医疗保健', '生物制品', '医药', '化学原料药', '医疗', '生物制药', '生物科技'],
    '新能源车':    ['电气设备', '汽车配件', '小金属', '铝', '化工原料', '能源金属', '汽车整车', '电机', '铜', '塑料', '玻璃'],
    '固态电池':    ['电气设备', '化工原料', '能源金属', '小金属'],
    '储能':       ['电气设备', '电力', '化工原料', '元器件', '能源金属'],
    '氢能':       ['电气设备', '化工原料', '煤气', '专用机械'],
    '数据要素':    ['软件服务', '互联网', 'IT设备', '传媒', '通信设备'],
    '云计算':     ['软件服务', 'IT设备', '通信设备'],
    '工业互联网':   ['软件服务', 'IT设备', '电气设备', '通信设备', '元器件'],
    '人工智能':    ['软件服务', 'IT设备', '通信设备', '互联网', '半导体', '元器件'],
    '军工':       ['航空', '国防军工', '航天', '通信设备', '船舶', '专用机械', '电气设备', '军工', '兵器', '电器仪表', '元器件', '玻璃', '仪器仪表'],
    '航天军工':    ['航空', '航天', '国防军工', '通信设备', '军工'],
    '商业航天':    ['航空', '航天', '通信设备', '军工', '专用机械', '互联网'],
    '船舶制造':    ['船舶', '机械基件', '专用机械'],
    '出海链':     ['汽车配件', '家电', '纺织', '机械', '元器件', '电气设备', '家用电器', '摩托车', '汽车整车', '船舶'],
    '医疗器械':    ['医疗保健', '医疗器械', '化学制药', '医药'],
    '半导体':     ['半导体', '元器件', 'IT设备', '电器仪表'],
    'PCB':       ['元器件', '半导体', '电气设备', '机械基件', '专用机械', '玻璃', '化工原料'],
    '消费电子':    ['元器件', 'IT设备', '半导体', '通信设备', '家用电器', '电器仪表', '汽车配件', '塑料', '电气设备', '机械基件'],
    '游戏':       ['互联网', '软件服务', '传媒', '影视音像'],
    '信创':       ['软件服务', 'IT设备', '通信设备', '元器件'],
    '氟化工制冷剂':  ['化工原料', '化工', '氟化工', '化纤', '塑料', '农药化肥'],
    '高端材料':    ['化工原料', '小金属', '玻璃', '材料', '塑料', '化纤', '染料涂料', '纺织', '矿物制品', '石油加工'],
    '电力链':     ['电气设备', '电力', '元器件', '仪器仪表', '煤炭开采', '水力发电', '通信设备', '软件服务', '化工原料', '玻璃', '火力发电', '铝'],
    '工业金属':    ['铜', '铝', '铅锌', '小金属', '有色金属', '矿物制品'],
    '小金属':     ['小金属', '有色金属', '稀有金属', '化工原料', '铅锌', '铜'],
    '能源金属':    ['小金属', '能源金属', '有色金属', '电气设备', '化工原料'],
    '黄金':       ['黄金', '有色金属'],
    '稀土永磁':    ['小金属', '稀土', '元器件', '电气设备', '矿物制品'],
    '量子计算':    ['IT设备', '软件服务', '通信设备', '半导体', '元器件'],
    '液冷服务器':   ['IT设备', '元器件', '电气设备', '通信设备'],
    '光伏链':     ['电气设备', '玻璃', '化工原料', '元器件', '有色金属', '矿物制品'],
    '煤炭':       ['煤炭开采', '焦炭'],
    '证券':       ['证券', '多元金融'],
    '合成生物':    ['化工原料', '化学制药', '生物制品', '食品', '医疗保健', '医药', '生物制药'],
    '化工农药链':   ['农药化肥', '化工原料', '化纤', '石油加工', '石油开采'],
    '汽车零部件':   ['汽车配件', '电气设备', '电机', '汽车整车'],
    '高端装备':    ['专用机械', '通用机械', '电气设备', '船舶', '轻工机械', '机械基件'],
    '先进封装':    ['半导体', '元器件'],
    '算力租赁':    ['软件服务', 'IT设备', '互联网'],
    '云计算':     ['软件服务', 'IT设备', '通信设备'],
    '半导体设备':   ['半导体', '专用机械', '电气设备', '机械基件', '电器仪表', '元器件', '化工原料', '仪器仪表', '装修装饰'],
}

# 主题真实性折扣
THEME_AUTH_MATCHED = 1.00     # 行业在主营业白名单内
THEME_AUTH_UNKNOWN = 0.95     # 主题未配置白名单(不惩罚过重)
THEME_AUTH_MISMATCH = 0.80    # 行业不在白名单内 → 主题-主业存疑

# 3. 景气周期因子 — 作为乘数而非加分项
# 基于产业景气分(0~100)非线性映射：上行周期给更高权重，下行周期压制
CYCLE_MULT = [
    (85.0, 1.10),   # 强上行(存储芯片/AI算力/先进封装等景气高峰)
    (75.0, 1.05),   # 上行
    (65.0, 1.00),   # 平稳
    (55.0, 0.92),   # 下行初期
    (0.0,  0.82),   # 下行
]

# 4. 估值约束 — 在成长评分之外叠加估值纪律
# PE_TTM / 合理PE 比值过高 → 高成长+高估值的中线回撤风险
VALUATION_PE_RATIO_STRICT = 1.50   # 超过该比值开始打折
VALUATION_PE_RATIO_MILD = 2.00
VALUATION_PE_RATIO_SEVERE = 3.00
VALUATION_DISCOUNT_MILD = 0.90
VALUATION_DISCOUNT_MILD2 = 0.80
VALUATION_DISCOUNT_SEVERE = 0.70

# ─────────────────────────────────────────────
# V12.1 成长评分增强配置
# ─────────────────────────────────────────────

# 1. 利润质量系数（低基数衰减）— ProfitQualityFactor
# ProfitYoY 越大，系数越小，避免低基数暴增主导排名
# 表: (利润同比阈值下限, 系数)，按从高到低匹配（>阈值即取该档）
PROFIT_QUALITY_BANDS = [
    (5000.0, 0.45),   # >5000% → 0.45
    (1000.0, 0.65),   # 1000~5000% → 0.65
    (300.0, 0.85),    # 300~1000% → 0.85
    (100.0, 0.95),    # 100~300% → 0.95
    (0.0, 1.00),      # <=100% → 1.00
]
PROFIT_QUALITY_MIN = 0.40      # 系数下限（加强版最低值）
PROFIT_QUALITY_MAX = 1.00      # 系数上限
PROFIT_QUALITY_BOOST = 0.05    # 加强版加成：扣非同比>利润同比×70% 且 现金流同比>0

# 2. PEG 重新计算
PEG_MIN_GROWTH = 20.0          # 增速<20%时按20%计算（GrowthForPEG下限）
# PEG 分段评分（越低越好）
PEG_SCORE_BANDS = [
    (0.0,  0.5, 100),   # <0.5 → 100
    (0.5,  1.0,  90),   # 0.5~1 → 90
    (1.0,  1.5,  75),   # 1~1.5 → 75
    (1.5,  2.0,  60),   # 1.5~2 → 60
    (2.0,  3.0,  40),   # 2~3 → 40
    (3.0,  None, 20),   # >3 → 20
]

# 3. 估值空间异常修正 — Upside 上下限
UPSIDE_MAX = 200.0             # 估值空间最大 200%
UPSIDE_MIN = -50.0             # 估值空间最小 -50%
# 估值空间分段评分
UPSIDE_SCORE_BANDS = [
    (150.0, None, 100),   # >150 → 100
    (100.0, 150.0,  90),  # 100~150 → 90
    (50.0,  100.0,  80),  # 50~100 → 80
    (20.0,  50.0,   65),  # 20~50 → 65
    (0.0,   20.0,   50),  # 0~20 → 50
    (None,  0.0,    20),  # <0 → 20
]


# ─────────────────────────────────────────────
# V12.2 标准化评分体系配置
# ─────────────────────────────────────────────
# 成长因子标准化管线: 原始财务数据 → 缩尾 → 对数压缩(signed-log1p)
#                     → 行业内Z-Score(小样本回退全局) → clip → 0-100分 → 按权重融合
# 解决: 极端同比数据、低基数效应、不同量纲之间不可比
STD_MIN_GROUP = 8            # 行业内标准化的最小样本数，低于该值回退全局统计
STD_Z_CLIP = 2.5             # Z-Score 截断值，控制离群值影响
STD_WINSORIZE = (0.01, 0.99) # 对数压缩前缩尾分位
# 需对数压缩的成长因子（右偏严重：极端同比/低基数暴增）
STD_LOG_FACTORS = {'营收同比', '_adjusted_profit_growth', '扣非利润同比', '3年利润CAGR'}
# 参与行业内标准化的成长因子（原始财务数据）
STD_FACTORS = ['营收同比', '_adjusted_profit_growth', '扣非利润同比', '3年利润CAGR',
               'ROE', '毛利率', '研发投入%']


# ─────────────────────────────────────────────
# V14 机构成长版配置
# ─────────────────────────────────────────────

# ① DoubleScore（短期爆发力 3~6个月）权重
V14_DOUBLE_WEIGHTS = {
    '成长': 0.35,     # 0.5×RevenueQuality + 0.5×利润质量修正分，叠加 ProfitQualityPenalty
    'PEG': 0.20,      # PEGScore = 100/(1+PEG) 连续函数
    '估值': 0.20,     # UpsideScore + ValueBonus（上限100）
    '行业景气': 0.15,  # IndustryCycleScore 0~100 直接加权（不再用固定乘法）
    '盈利加速度': 0.10,  # H1/Q1 加速 × 业绩超预期
}

# ② SustainableScore（持续成长 1~3年）权重
V14_SUSTAIN_WEIGHTS = {
    '成长持续性': 0.35,  # 0.4×营收增速分 + 0.4×3年利润CAGR分 + 0.2×扣非利润同比分
    '行业景气': 0.25,    # IndustryCycleScore
    '盈利质量': 0.20,    # 0.6×扣非占比分 + 0.4×利润质量分
    'ROE稳定性': 0.10,   # 0.7×ROE分 + 0.3×扣非占比分（稳定盈利代理）
    '现金流': 0.10,      # 现金流/营收比 标准化分
}

# ③ MoatScore（竞争壁垒）权重 — V14.1 机构研究框架六维度
# 回答"未来3~10年能否持续创造超额收益"，只使用长期竞争优势指标，
# 不允许受利润同比/单季度利润/短期股价/市场热点影响
V14_MOAT_WEIGHTS = {
    '市场地位': 0.25,   # 行业龙头/前三/前五/细分龙头/普通/小众（龙头类型+龙头地位）
    '技术壁垒': 0.20,   # 研发投入占营收 + 技术壁垒分 + 国产替代主题
    '产品竞争力': 0.15,  # 毛利率 + 高毛利产品/创新药主题 + 订单爆发(新品放量)
    '客户壁垒': 0.15,   # 机构认可 + 大客户/央企主题 + 海外客户
    '盈利能力': 0.15,   # 毛利率 + ROE + 现金流 + 扣非占比（长期盈利质量，非单季）
    '成长护城河': 0.10,  # 产业景气 + 3年CAGR + 国产替代/政策支持主题
}

# 龙头类型 → 市场地位基础分（V14.1）
MOAT_LEADER_TYPES = {
    '行业龙头': 100,
    '行业龙二': 90,
    '龙二': 80,
    '细分龙头': 85,
    '中军': 80,
    '补涨': 60,
    '普通': 60,
}

# MoatLevel 星级（按 MoatScore）
MOAT_LEVELS = [
    (90.0, '★★★★★'), (80.0, '★★★★☆'), (70.0, '★★★★'),
    (60.0, '★★★'), (50.0, '★★'), (0.0, '★'),
]

# 成长护城河主题关键词（国产替代/政策支持/全球竞争力）
MOAT_THEME_GROWTH = [
    '半导体', '存储', 'AI', '算力', '光模块', '创新药', '生物医药', '机器人',
    '高端装备', '商业航天', '低空经济', '智能驾驶', '芯片', '数据要素', '信创',
    '军工', '卫星', 'HBM', 'IGBT', '逆变器', '储能',
]
# 大客户/央企/海外主题关键词（客户壁垒加分）
MOAT_THEME_CUSTOMER = [
    '中字头', '央企', '国企', '军工', '电力', '运营商', '华为', '苹果',
    '宁德', '特斯拉', '海外', '出口', '出海', '全球', '一带一路',
]
# 高毛利产品/新品放量主题关键词（产品竞争力加分）
MOAT_THEME_PRODUCT = [
    'AI服务器', 'SSD', '存储', 'HBM', 'IGBT', '创新药', '光模块', '机器人',
    '半导体设备', '高端装备', 'CPO', '算力', 'AI', '芯片', '数据中心',
]

# ══════════════════════════════════════════════════════════════════════
# V15 行业模板护城河（MoatScore 行业化）
# 不同产业采用不同护城河维度权重，仍统一输出 MoatScore/MoatLevel/MoatExplain
# ══════════════════════════════════════════════════════════════════════
# 行业模板关键词匹配（按主营产业 industry / 主营产业 匹配，顺序优先）
MOAT_TEMPLATES = {
    '半导体': {
        'kw': ['半导体', '电子元件', '消费电子', '通信', '集成电路', '芯片'],
        'weights': {'技术壁垒': 0.30, '客户认证': 0.25, '研发投入': 0.20,
                    '国产替代': 0.15, '产品结构升级': 0.10},
        'name': '半导体',
    },
    '创新药': {
        'kw': ['医药', '生物', '医疗器械', '创新药', 'CXO'],
        'weights': {'产品管线': 0.30, '商业化能力': 0.25, '研发投入': 0.20,
                    '专利壁垒': 0.15, '医保覆盖': 0.10},
        'name': '创新药/医药',
    },
    '游戏': {
        'kw': ['游戏'],
        'weights': {'IP': 0.25, '产品生命周期': 0.25, '研发团队': 0.20,
                    '现金流': 0.15, '海外收入': 0.15},
        'name': '游戏',
    },
    'AI软件': {
        'kw': ['软件', '互联网', '云计算', '数据', '传媒', 'AI软件'],
        'weights': {'产品': 0.30, '用户': 0.25, 'IP': 0.20,
                    '现金流': 0.15, '品牌': 0.10},
        'name': 'AI软件/互联网',
    },
    '化工': {
        'kw': ['化工', '农化', '石油', '石化', '建材', '基础化工'],
        'weights': {'成本优势': 0.30, '资源优势': 0.25, '一体化': 0.20,
                    '规模': 0.15, '现金流': 0.10},
        'name': '化工',
    },
    '机器人': {
        'kw': ['机器人', '智能制造', '自动化', '高端装备', '汽车零部件', '工业机械'],
        'weights': {'客户': 0.30, '技术壁垒': 0.25, '国产替代': 0.20,
                    '产品升级': 0.15, '自动化能力': 0.10},
        'name': '机器人/智能制造',
    },
    '航运': {
        'kw': ['航运', '物流', '港口', '船舶', '航空运输', '交通运输', '快递'],
        'weights': {'船队规模': 0.30, '成本优势': 0.25, '航线': 0.20,
                    '行业周期': 0.15, '资产质量': 0.10},
        'name': '航运/物流',
    },
    '有色资源': {
        'kw': ['有色', '金属', '黄金', '煤炭', '矿业', '能源', '锂', '稀土'],
        'weights': {'资源储量': 0.30, '成本优势': 0.25, '规模': 0.20,
                    '现金流': 0.15, '行业周期': 0.10},
        'name': '有色/资源',
    },
}
# 通用模板（未匹配到行业时使用，V14.1 六维度）
GENERAL_MOAT_WEIGHTS = {
    '市场地位': 0.25, '技术壁垒': 0.20, '产品竞争力': 0.15, '客户壁垒': 0.15,
    '盈利能力': 0.15, '成长护城河': 0.10,
}

# ④ FinalScore 权重
V14_FINAL_WEIGHTS = {
    'DoubleScore': 0.30,
    'SustainableScore': 0.35,
    'MoatScore': 0.25,
    'RiskSafety': 0.10,   # (100 - RiskScore)
}

# 行业生命周期 → IndustryCycleScore（产业景气 0~100 映射）
# (阈值, 分数, 阶段标签)
CYCLE_STAGE = [
    (85.0, 95, '主升'),
    (70.0, 85, '景气上行'),
    (55.0, 75, '复苏'),
    (40.0, 60, '震荡'),
    (25.0, 40, '衰退'),
    (0.0,  20, '下行'),
]

# 推荐等级（按 FinalScore）
RECOMMEND_LEVELS = [
    (80.0, '★★★★★ 机构级成长'),
    (70.0, '★★★★☆ 优质成长'),
    (60.0, '★★★★ 成长观察'),
    (50.0, '★★★ 周期修复'),
    (40.0, '★★ 主题投机'),
    (0.0,  '★ 回避'),
]

# 风险评分权重（越高风险越大）
V14_RISK_WEIGHTS = {
    '业绩波动': 0.25,
    '资产负债率': 0.15,
    '商誉': 0.15,
    '应收账款': 0.10,
    '存货': 0.10,
    '股东减持': 0.10,
    '大额解禁': 0.10,
    '行业周期': 0.05,
}

# 一级逻辑 → 可信度星级
PRIMARY_DRIVER_STARS = {
    '需求驱动': '★★★★☆',
    '产品驱动': '★★★★★',
    '成本驱动': '★★★★☆',
    '周期驱动': '★★★☆☆',
    '政策驱动': '★★★★☆',
    '一次性收益': '★☆☆☆☆',
}


def _signed_log1p(x) -> float:
    """带符号对数压缩: sign(x)*log1p(|x|)，压缩极端值并保留负值信息"""
    if pd.isna(x):
        return np.nan
    x = float(x)
    return np.log1p(x) if x >= 0 else -np.log1p(-x)


def _standardize_factor(data: pd.DataFrame, factor: str, log: bool = True) -> pd.Series:
    """
    单因子标准化评分 0~100:
    缩尾 → (对数压缩) → 行业内Z-Score(小样本回退全局) → clip[-2.5,2.5] → 线性映射
    标准化在全候选池(data)上进行，保证行业统计稳定；行业缺失或样本不足回退全局统计。
    """
    raw = pd.to_numeric(data[factor], errors='coerce')
    s = raw.copy()
    # 1. 缩尾（样本充足时按1%/99%截断，剔除数据源异常值）
    if s.count() >= 20:
        lo, hi = s.quantile(STD_WINSORIZE[0]), s.quantile(STD_WINSORIZE[1])
        s = s.clip(lo, hi)
    # 2. 对数压缩（右偏因子用 signed-log1p）
    if log:
        s = s.apply(_signed_log1p)
    # 3. 行业内 Z-Score，小样本行业回退全局统计
    global_mean, global_std = s.mean(), s.std()
    ind_key = data['industry'].fillna('').astype(str)
    z = pd.Series(np.nan, index=data.index)
    for _, idx in data.groupby(ind_key).groups.items():
        sub = s.loc[idx]
        n = sub.notna().sum()
        if n >= STD_MIN_GROUP:
            mu, sd = sub.mean(), sub.std()
            if pd.isna(sd) or sd == 0:
                mu, sd = global_mean, global_std
        else:
            mu, sd = global_mean, global_std
        z.loc[idx] = (sub - mu) / sd if (sd and not pd.isna(sd)) else 0.0
    # 4. clip → 0-100 线性映射
    z = z.clip(-STD_Z_CLIP, STD_Z_CLIP)
    return (z + STD_Z_CLIP) / (2 * STD_Z_CLIP) * 100.0


def _normalize_code(code) -> str:
    code_str = str(code).strip()
    if code_str.isdigit() and len(code_str) < 6:
        code_str = code_str.zfill(6)
    return code_str


def _score_one(value, min_val, max_val, higher_is_better):
    """单因子线性评分 0~100"""
    if pd.isna(value) or value is None:
        return 0.0
    value = float(value)
    if higher_is_better:
        if value >= max_val:
            return 100.0
        if value <= min_val:
            return 0.0
        return (value - min_val) / (max_val - min_val) * 100.0
    else:
        # 越低越好（PEG）
        if value <= min_val:
            return 100.0
        if value >= max_val:
            return 0.0
        return (max_val - value) / (max_val - min_val) * 100.0


# ─────────────────────────────────────────────
# 增强维度计算函数 (v1.1)
# ─────────────────────────────────────────────

def _calc_low_base_discount(row) -> float:
    """
    利润质量过滤 — 低基数识别
    利润同比 > 1000% 且 营收同比 < 30% → 低基数/一次性恢复嫌疑
    Q1利润同比也超高(>500%) → 趋势延续，低基数嫌疑较小(轻微折扣)
    无Q1支撑 → 疑似一次性恢复(较重折扣)；叠加非经常损益>20%再额外扣
    """
    profit_yoy = pd.to_numeric(row.get('利润同比', 0), errors='coerce')
    rev_yoy = pd.to_numeric(row.get('营收同比', 0), errors='coerce')
    q1_yoy = pd.to_numeric(row.get('Q1利润同比', None), errors='coerce')
    nr = pd.to_numeric(row.get('非经常损益%', 0), errors='coerce')

    # 利润同比未超过阈值 → 不做低基数检查
    if pd.isna(profit_yoy) or profit_yoy <= LOW_BASE_PROFIT_YOY:
        return 1.0
    # 营收同比 >= 30% → 利润高增有营收支撑，非低基数
    if pd.isna(rev_yoy) or rev_yoy >= LOW_BASE_REV_YOY:
        return 1.0

    # 低基数嫌疑成立：利润高增但营收低增
    if pd.isna(q1_yoy) or q1_yoy <= LOW_BASE_Q1_PROOF:
        discount = LOW_BASE_DISCOUNT_SEVERE  # 疑似一次性恢复
    else:
        discount = LOW_BASE_DISCOUNT_MILD    # Q1已高增，趋势延续
    # 非经常损益占比高 → 利润含金量再打折扣
    if pd.notna(nr) and nr > 20:
        discount *= LOW_BASE_NR_EXTRA
    return discount


def _calc_theme_authenticity(row) -> float:
    """
    主题真实性校验 — 主营业务(行业)与主题映射交叉验证
    行业在主题白名单内 → 1.00；主题未配置白名单 → 0.95(不惩罚过重)；行业不匹配 → 0.80
    """
    theme = row.get('theme', None)
    industry = row.get('industry', None)
    if pd.isna(theme) or pd.isna(industry):
        return 1.0  # 主题缺失不是不匹配，不惩罚
    theme_str = str(theme).strip()
    industry_str = str(industry).strip()
    if not theme_str or not industry_str:
        return 1.0
    # 在主题白名单中查找
    for t, allowed in THEME_INDUSTRY_MAP.items():
        if t in theme_str:
            # 行业是否在允许列表内
            for kw in allowed:
                if kw in industry_str:
                    return THEME_AUTH_MATCHED
            return THEME_AUTH_MISMATCH
    return THEME_AUTH_UNKNOWN


def _calc_cycle_multiplier(row) -> float:
    """
    景气周期因子 — 作为乘数而非加分项
    基于产业景气分(0~100)映射：上行周期 >1.0，下行周期 <1.0
    """
    cycle = pd.to_numeric(row.get('产业景气', None), errors='coerce')
    if pd.isna(cycle):
        return 1.0
    for threshold, mult in CYCLE_MULT:
        if cycle >= threshold:
            return mult
    return CYCLE_MULT[-1][1]


def _calc_valuation_discount(row) -> float:
    """
    估值约束 — PE_TTM/合理PE 比值过高 → 高成长+高估值的中线回撤风险
    比值 > 3.0 → 0.70；> 2.0 → 0.80；> 1.5 → 0.90；其余 → 1.00
    """
    pe_ttm = pd.to_numeric(row.get('PE_TTM', None), errors='coerce')
    fair_pe = pd.to_numeric(row.get('合理PE', None), errors='coerce')
    if pd.isna(pe_ttm) or pd.isna(fair_pe) or fair_pe <= 0 or pe_ttm <= 0:
        return 1.0
    ratio = pe_ttm / fair_pe
    if ratio >= VALUATION_PE_RATIO_SEVERE:
        return VALUATION_DISCOUNT_SEVERE
    if ratio >= VALUATION_PE_RATIO_MILD:
        return VALUATION_DISCOUNT_MILD2
    if ratio >= VALUATION_PE_RATIO_STRICT:
        return VALUATION_DISCOUNT_MILD
    return 1.0


# ─────────────────────────────────────────────
# V12.1 成长评分增强计算函数
# ─────────────────────────────────────────────

def _calc_profit_quality_factor(row) -> float:
    """
    利润质量系数 ProfitQualityFactor — 低基数衰减
    ProfitYoY <= 100% → 1.00；100~300% → 0.95；300~1000% → 0.85；
    1000~5000% → 0.65；>5000% → 0.45
    加强版：扣非利润同比 > 利润同比×70% 且 经营现金流同比 > 0 → +0.05
    上限 1.00，下限 0.40（数据源无扣非/现金流字段时跳过加强版）
    """
    profit_yoy = pd.to_numeric(row.get('利润同比', 0), errors='coerce')
    if pd.isna(profit_yoy) or profit_yoy <= 0:
        return PROFIT_QUALITY_MAX

    factor = PROFIT_QUALITY_BANDS[-1][1]
    for threshold, f in PROFIT_QUALITY_BANDS:
        if profit_yoy > threshold:
            factor = f
            break

    # 加强版：扣非利润同比 与 经营现金流同比（若数据源提供）
    kf_yoy = row.get('扣非利润同比', None)
    ocf_yoy = row.get('经营现金流同比', None)
    if kf_yoy is not None and ocf_yoy is not None:
        kf = pd.to_numeric(kf_yoy, errors='coerce')
        oc = pd.to_numeric(ocf_yoy, errors='coerce')
        if pd.notna(kf) and pd.notna(oc):
            if kf > profit_yoy * 0.7 and oc > 0:
                factor = min(PROFIT_QUALITY_MAX, factor + PROFIT_QUALITY_BOOST)
    return max(PROFIT_QUALITY_MIN, min(PROFIT_QUALITY_MAX, factor))


def _calc_adjusted_profit_growth(profit_yoy, quality_factor) -> float:
    """
    AdjustedProfitGrowth = ProfitYoY × ProfitQualityFactor
    所有成长评分必须使用修正后的值，不得直接使用 ProfitYoY
    """
    if pd.isna(profit_yoy) or profit_yoy is None:
        return 0.0
    return float(profit_yoy) * quality_factor


def _calc_future_growth(row) -> float:
    """
    FutureGrowth（V12.3 前瞻增速）:
        = 0.5×营收YoY + 0.3×扣非利润YoY + 0.2×最近3年利润CAGR
    相比直接用利润同比：营收增速更稳健，扣非利润过滤非经常损益，
    3年CAGR 平滑低基数效应，避免"利润同比+68299% → PEG≈0"的失真。
    缺失字段时权重重归一化；全部缺失回退 AdjustedProfitGrowth。
    """
    rev_yoy = pd.to_numeric(row.get('营收同比', np.nan), errors='coerce')
    kf_yoy = pd.to_numeric(row.get('扣非利润同比', np.nan), errors='coerce')
    cagr = pd.to_numeric(row.get('3年利润CAGR', np.nan), errors='coerce')

    parts = []  # (权重, 值)
    if pd.notna(rev_yoy) and not pd.isna(rev_yoy):
        parts.append((0.5, float(rev_yoy)))
    if pd.notna(kf_yoy) and not pd.isna(kf_yoy):
        parts.append((0.3, float(kf_yoy)))
    if pd.notna(cagr) and not pd.isna(cagr):
        parts.append((0.2, float(cagr)))
    if not parts:
        # 全部缺失 → 回退 V12.1 修正增速
        apg = row.get('_adjusted_profit_growth', np.nan)
        if pd.notna(apg) and not pd.isna(apg):
            return float(apg)
        return 0.0
    w_sum = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / w_sum


def _calc_peg(pe_ttm, future_growth) -> float:
    """
    重新计算 PEG（V12.3：使用 FutureGrowth 前瞻增速）
    PE <= 0 → PEG = None
    FutureGrowth < 20% → GrowthForPEG = 20
    否则 GrowthForPEG = FutureGrowth
    PEG = PE / GrowthForPEG，保留两位小数
    """
    if pd.isna(pe_ttm) or pe_ttm is None or pe_ttm <= 0:
        return None
    if pd.isna(future_growth) or future_growth is None:
        growth = PEG_MIN_GROWTH
    else:
        growth = max(PEG_MIN_GROWTH, float(future_growth))
    if growth <= 0:
        return None
    return round(float(pe_ttm) / growth, 2)


def _calc_peg_score(peg) -> float:
    """
    PEGScore 分段评分（PEG 为 None 时给中性分 50）
    <0.5→100；0.5~1→90；1~1.5→75；1.5~2→60；2~3→40；>3→20
    """
    if pd.isna(peg) or peg is None:
        return 50.0
    peg = float(peg)
    for lo, hi, score in PEG_SCORE_BANDS:
        if (lo is None or peg >= lo) and (hi is None or peg < hi):
            return float(score)
    return 20.0


def _calc_upside_capped(upside) -> float:
    """
    Upside 上下限修正：最大 200%，最小 -50%
    """
    if pd.isna(upside) or upside is None:
        return 0.0
    return max(UPSIDE_MIN, min(UPSIDE_MAX, float(upside)))


def _calc_upside_score(upside_capped) -> float:
    """
    UpsideScore 分段评分（仅使用限幅后的 Upside，不得直接使用原始 Upside%）
    >150→100；100~150→90；50~100→80；20~50→65；0~20→50；<0→20
    """
    if pd.isna(upside_capped) or upside_capped is None:
        return 20.0
    u = float(upside_capped)
    for lo, hi, score in UPSIDE_SCORE_BANDS:
        if (lo is None or u >= lo) and (hi is None or u < hi):
            return float(score)
    return 20.0


# ─────────────────────────────────────────────
# V14 指标层计算函数
# ─────────────────────────────────────────────

def _log2_compress_pct(yoy_pct) -> float:
    """
    V14 对数压缩: 100 × log2(1 + yoy/100)
    对极端同比(5000%/60000%)进行压缩，避免异常值主导排序。
    """
    if pd.isna(yoy_pct) or yoy_pct is None:
        return np.nan
    v = float(yoy_pct)
    return 100.0 * np.log2(1.0 + v / 100.0)


def _calc_adjusted_profit_growth_v14(row) -> float:
    """
    AdjustedProfitGrowth (V14) = 100 × log2(1 + ProfitYoY_adjusted/100)
    ProfitYoY_adjusted = 利润同比 × ProfitQualityFactor（低基数修正）
    先低基数修正、再做 log2 压缩，禁止直接使用 ProfitYoY。
    """
    profit_yoy = pd.to_numeric(row.get('利润同比', np.nan), errors='coerce')
    if pd.isna(profit_yoy):
        return 0.0
    quality = float(row.get('_profit_quality', 1.0) or 1.0)
    adj = float(profit_yoy) * quality
    return 100.0 * np.log2(1.0 + adj / 100.0)


def _calc_profit_quality_penalty(row) -> float:
    """
    ProfitQualityPenalty — 利润质量惩罚（V14 必须）:
      经营现金流为负（现金流/营收比 < 0）→ ×0.80
      扣非利润明显低于净利润（非经常损益% > 30）→ ×0.85；>20 → ×0.92
    作用于成长评分，降低利润含金量不足标的的成长分。
    """
    cash_ratio = pd.to_numeric(row.get('现金流/营收比', np.nan), errors='coerce')
    nr = pd.to_numeric(row.get('非经常损益%', np.nan), errors='coerce')
    penalty = 1.0
    if pd.notna(cash_ratio) and cash_ratio < 0:
        penalty *= 0.80
    if pd.notna(nr) and nr > 30:
        penalty *= 0.85
    elif pd.notna(nr) and nr > 20:
        penalty *= 0.92
    return penalty


def _calc_revenue_quality(row, rev_score: float) -> float:
    """
    RevenueQuality（V14 营收质量，0~100）:
      base = log2压缩营收增速的行业内标准化分
      收入增长 且 毛利率同步提升（高毛利+高增速/高利润弹性）→ 奖励
      收入增长 但 毛利率低（低毛利走量/毛利率下降代理）→ 降分
    """
    rev_yoy = pd.to_numeric(row.get('营收同比', np.nan), errors='coerce')
    gm = pd.to_numeric(row.get('毛利率', np.nan), errors='coerce')
    profit_yoy = pd.to_numeric(row.get('利润同比', np.nan), errors='coerce')
    score = 50.0 if pd.isna(rev_score) else float(rev_score)
    if pd.isna(rev_yoy):
        return score
    leverage = None
    if pd.notna(profit_yoy) and rev_yoy > 5:
        leverage = float(profit_yoy) / rev_yoy
    if rev_yoy >= 30:
        if pd.notna(gm):
            if gm >= 40:
                score = min(100.0, score + 12)   # 高毛利放量 → 高质量增长
            elif gm < 20:
                score = max(0.0, score - 15)     # 低毛利走量 → 质量存疑
        if leverage is not None and leverage >= 2.0:
            score = min(100.0, score + 8)        # 利润弹性强
    elif rev_yoy >= 15:
        if pd.notna(gm):
            if gm >= 35:
                score = min(100.0, score + 8)
            elif gm < 15:
                score = max(0.0, score - 10)
    return score


def _calc_future_growth_v14(row) -> float:
    """
    FutureGrowth (V14) = 0.5×营收YoY + 0.3×AdjustedProfitGrowth + 0.2×3年利润CAGR
    AdjustedProfitGrowth 为低基数修正+log2压缩后的值（V14 禁止直接用 ProfitYoY）。
    缺失字段时权重重归一化。
    """
    rev_yoy = pd.to_numeric(row.get('营收同比', np.nan), errors='coerce')
    apg = pd.to_numeric(row.get('_adjusted_profit_growth_v14', np.nan), errors='coerce')
    cagr = pd.to_numeric(row.get('3年利润CAGR', np.nan), errors='coerce')
    parts = []
    if pd.notna(rev_yoy):
        parts.append((0.5, float(rev_yoy)))
    if pd.notna(apg):
        parts.append((0.3, float(apg)))
    if pd.notna(cagr):
        parts.append((0.2, float(cagr)))
    if not parts:
        return 0.0
    w = sum(p[0] for p in parts)
    return sum(p[0] * p[1] for p in parts) / w


def _calc_peg_score_v14(peg) -> float:
    """
    PEGScore (V14) = 100/(1+PEG) — 连续函数（替代阶梯评分）
    PEG=0→100；PEG=1→50；PEG=3→25；PEG→∞→0；缺失给中性分50。
    """
    if pd.isna(peg) or peg is None:
        return 50.0
    peg = max(0.0, float(peg))
    return 100.0 / (1.0 + peg)


def _calc_value_bonus(row, pe_median, pb_median) -> float:
    """
    ValueBonus 估值安全垫（V14）: PE_TTM 与 PB 均低于行业均值 → 加分（各5分，上限10分）
    EV/EBITDA 数据源不可用，以 PE+PB 双维度近似。
    """
    bonus = 0.0
    pe = pd.to_numeric(row.get('PE_TTM', np.nan), errors='coerce')
    if pd.notna(pe) and pe > 0 and pd.notna(pe_median) and pe < float(pe_median):
        bonus += 5.0
    pb = pd.to_numeric(row.get('PB', np.nan), errors='coerce')
    if pd.notna(pb) and pb > 0 and pd.notna(pb_median) and pb < float(pb_median):
        bonus += 5.0
    return bonus


def _calc_industry_cycle_score(cycle):
    """
    IndustryCycleScore（V14）: 产业景气(0~100) → 0~100 分 + 阶段标签
    主升95 / 景气上行85 / 复苏75 / 震荡60 / 衰退40 / 下行20
    直接参与权重计算，不再作为固定 1.05/1.10 乘法。
    """
    if pd.isna(cycle):
        return 50.0, '未知'
    for threshold, score, label in CYCLE_STAGE:
        if float(cycle) >= threshold:
            return float(score), label
    return 20.0, '下行'


def _calc_acceleration_score(row) -> float:
    """
    盈利加速度分（V14，0~100）:
      = 0.6×H1/Q1加速分 + 0.4×业绩超预期分
      H1/Q1 加速: 加速倍数≥1.5 → 100；1.0~1.5 → 60~100；0.6~1.0 → 20~60；<0.6 → 0
    """
    q1 = pd.to_numeric(row.get('Q1利润同比', np.nan), errors='coerce')
    half = pd.to_numeric(row.get('利润同比', np.nan), errors='coerce')
    accel_score = 50.0
    if pd.notna(q1) and q1 > 0 and pd.notna(half):
        accel = float(half) / float(q1)
        if accel >= 1.5:
            accel_score = 100.0
        elif accel >= 1.0:
            accel_score = 60.0 + (accel - 1.0) / 0.5 * 40.0
        elif accel >= 0.6:
            accel_score = 20.0 + (accel - 0.6) / 0.4 * 40.0
        else:
            accel_score = 0.0
    surprise = pd.to_numeric(row.get('业绩超预期', np.nan), errors='coerce')
    if pd.isna(surprise):
        surprise = 50.0
    return 0.6 * accel_score + 0.4 * float(surprise)


def _moat_txt(row) -> str:
    """拼接主题文本（主营产业 + 概念主题）用于关键词匹配"""
    parts = []
    for k in ('主营产业', '概念主题', '主题', '行业', '龙头类型'):
        v = row.get(k, '')
        if v and str(v) not in ('nan', 'None'):
            parts.append(str(v))
    return ' '.join(parts)


def _moat_match_keywords(row, keywords) -> list:
    """返回命中的主题关键词列表"""
    txt = _moat_txt(row)
    return [kw for kw in keywords if kw.lower() in txt.lower()]


def _detect_moat_template(row) -> str:
    """识别行业模板（主营产业→模板 key），未命中返回 ''（通用模板）"""
    txt = _moat_txt(row)
    if not txt:
        return ''
    for tkey, tpl in MOAT_TEMPLATES.items():
        for kw in tpl['kw']:
            if kw.lower() in txt.lower():
                return tkey
    return ''


def _moat_factor_pool(row, share_score) -> dict:
    """V15 MoatScore 因子池：各维度基础分（0~100），供行业模板加权复用"""
    def _f(x, default=50.0):
        if pd.isna(x) or x is None or x == '':
            return default
        try:
            return float(x)
        except (ValueError, TypeError):
            return default

    leader_type = str(row.get('龙头类型', ''))
    leader_base = MOAT_LEADER_TYPES.get(leader_type, 60.0)
    leader_score = _f(pd.to_numeric(row.get('龙头地位', np.nan), errors='coerce'))
    rd = _f(row.get('_std_研发投入%', np.nan))
    tech = _f(pd.to_numeric(row.get('技术壁垒', np.nan), errors='coerce'))
    gm = _f(row.get('_std_毛利率', np.nan))
    roe = _f(row.get('_std_ROE', np.nan))
    cfo = _f(row.get('_std_现金流占比', np.nan))
    nr = _f(row.get('_std_扣非占比', np.nan))
    inst = _f(pd.to_numeric(row.get('机构认可', np.nan), errors='coerce'))
    order = _f(pd.to_numeric(row.get('订单爆发', np.nan), errors='coerce'))
    cycle = _f(row.get('_cycle_score', np.nan))
    cagr = _f(row.get('_std_3年利润CAGR', np.nan))
    rev = _f(row.get('_std_rev_v14', np.nan))
    share = _f(share_score)

    growth_kw = _moat_match_keywords(row, MOAT_THEME_GROWTH)
    cust_kw = _moat_match_keywords(row, MOAT_THEME_CUSTOMER)
    prod_kw = _moat_match_keywords(row, MOAT_THEME_PRODUCT)

    return {
        'leader_base': leader_base, 'leader_score': leader_score,
        'rd': rd, 'tech': tech, 'gm': gm, 'roe': roe, 'cfo': cfo, 'nr': nr,
        'inst': inst, 'order': order, 'cycle': cycle, 'cagr': cagr, 'rev': rev,
        'share': share,
        'growth_kw': growth_kw, 'cust_kw': cust_kw, 'prod_kw': prod_kw,
    }


def _moat_dim_value(row, pool: dict, dim: str) -> float:
    """V15 单维度评分（0~100）—— 所有行业模板维度共用因子池派生"""
    p = pool
    def _cap(x): return min(100.0, max(0.0, x))
    kw_bonus = 1.0 if p['growth_kw'] else 0.85   # 高成长赛道有限加成

    if dim == '市场地位':
        return _cap(0.5 * p['leader_base'] + 0.3 * p['leader_score'] + 0.2 * p['share'])
    if dim == '技术壁垒':
        return _cap((0.5 * p['rd'] + 0.5 * p['tech']) * kw_bonus)
    if dim == '研发投入':
        return _cap(p['rd'])
    if dim == '客户认证':
        return _cap(0.6 * p['inst'] + 0.4 * p['order'])
    if dim == '国产替代':
        return _cap(85.0 if p['growth_kw'] else 55.0)
    if dim == '产品结构升级':
        return _cap(0.5 * p['gm'] + 0.3 * p['rev'] + 0.2 * p['tech'])
    if dim == '产品管线':
        return _cap(0.4 * p['cagr'] + 0.3 * p['rev'] + 0.3 * p['order'])
    if dim == '商业化能力':
        return _cap(0.4 * p['gm'] + 0.3 * p['rev'] + 0.3 * p['inst'])
    if dim == '专利壁垒':
        return _cap(0.5 * p['rd'] + 0.5 * p['tech'])
    if dim == '医保覆盖':
        return _cap(80.0 if ('创新药' in _moat_txt(row) or '医保' in _moat_txt(row)) else 55.0)
    if dim == '产品':
        return _cap(0.5 * p['tech'] + 0.3 * p['order'] + 0.2 * p['rev'])
    if dim == '用户':
        return _cap(0.5 * p['inst'] + 0.5 * p['order'])
    if dim == 'IP':
        return _cap(0.4 * p['leader_base'] + 0.3 * p['inst'] + 0.3 * (90.0 if p['cust_kw'] else 55.0))
    if dim == '现金流':
        return _cap(p['cfo'])
    if dim == '品牌':
        return _cap(0.6 * p['inst'] + 0.4 * p['leader_base'])
    if dim == '产品生命周期':
        return _cap(0.4 * p['gm'] + 0.3 * p['cagr'] + 0.3 * p['rev'])
    if dim == '研发团队':
        return _cap(p['rd'])
    if dim == '海外收入':
        return _cap(80.0 if ('海外' in _moat_txt(row) or '出口' in _moat_txt(row)) else 55.0)
    if dim == '成本优势':
        return _cap(0.5 * p['gm'] + 0.5 * p['cfo'])
    if dim == '资源优势':
        return _cap(0.4 * p['share'] + 0.3 * p['leader_base'] + 0.3 * p['gm'])
    if dim == '一体化':
        return _cap(0.4 * p['share'] + 0.3 * p['leader_base'] + 0.3 * p['cycle'])
    if dim == '规模':
        return _cap(p['share'])
    if dim == '客户':
        return _cap(0.6 * p['inst'] + 0.4 * p['order'])
    if dim == '产品升级':
        return _cap(0.4 * p['gm'] + 0.3 * p['rev'] + 0.3 * p['tech'])
    if dim == '自动化能力':
        return _cap(0.4 * p['order'] + 0.3 * p['tech'] + 0.3 * p['cycle'])
    if dim == '船队规模':
        return _cap(0.5 * p['share'] + 0.5 * p['leader_base'])
    if dim == '航线':
        return _cap(0.4 * p['share'] + 0.3 * p['leader_base'] + 0.3 * p['cycle'])
    if dim == '行业周期':
        return _cap(p['cycle'])
    if dim == '资产质量':
        return _cap(0.6 * p['cfo'] + 0.4 * p['nr'])
    if dim == '资源储量':
        return _cap(0.4 * p['share'] + 0.3 * p['leader_base'] + 0.3 * p['cycle'])
    if dim == '产品竞争力':
        prod_bonus = 10.0 if p['prod_kw'] else 0.0
        return _cap(0.5 * p['gm'] + 0.3 * p['order'] + 0.2 * p['tech'] + prod_bonus)
    if dim == '客户壁垒':
        cust_bonus = 12.0 if p['cust_kw'] else 0.0
        return _cap(0.7 * p['inst'] + 0.3 * p['tech'] + cust_bonus)
    if dim == '盈利能力':
        return _cap(0.35 * p['gm'] + 0.30 * p['roe'] + 0.20 * p['cfo'] + 0.15 * p['nr'])
    if dim == '成长护城河':
        grow_bonus = 10.0 if p['growth_kw'] else 0.0
        return _cap(0.5 * p['cycle'] + 0.3 * p['cagr'] + 0.2 * 60.0 + grow_bonus)
    return 50.0


def _calc_moat_score(row, share_score) -> float:
    """
    MoatScore（V15 行业模板护城河，0~100）:
      按主营产业匹配行业模板（半导体/创新药/AI软件/游戏/化工/机器人/航运/有色资源），
      不同产业采用不同护城河维度权重；未命中行业用通用六维度（V14.1）。
      只评价长期竞争优势，不受利润同比/单季度利润/短期股价/市场热点影响。
    """
    template = _detect_moat_template(row)
    pool = _moat_factor_pool(row, share_score)
    if template and template in MOAT_TEMPLATES:
        weights = MOAT_TEMPLATES[template]['weights']
    else:
        weights = GENERAL_MOAT_WEIGHTS
    total_w = sum(weights.values())
    moat = sum(_moat_dim_value(row, pool, dim) * w for dim, w in weights.items()) / total_w
    return min(100.0, max(0.0, moat))


def _calc_moat_level(score) -> str:
    """MoatLevel（V14.1，★★★★★~★）"""
    if pd.isna(score):
        return '★'
    for threshold, level in MOAT_LEVELS:
        if float(score) >= threshold:
            return level
    return '★'


def _calc_moat_explain(row, moat_score) -> str:
    """
    MoatExplain（V15）— 一句话说明"为什么有护城河"，禁止泛化描述。
    命中行业模板时：以该行业权重最高的 1~2 个护城河维度为主导句，
    再追加量化证据（研发/毛利/ROE/现金流/主题）；未命中回退通用六维度。
    """
    def _f(x, default=50.0):
        if pd.isna(x) or x is None:
            return default
        return float(x)

    template = _detect_moat_template(row)
    share = pd.to_numeric(row.get('_std_市值份额', np.nan), errors='coerce')
    pool = _moat_factor_pool(row, share)

    parts = []
    if template and template in MOAT_TEMPLATES:
        weights = MOAT_TEMPLATES[template]['weights']
        name = MOAT_TEMPLATES[template]['name']
        scored = sorted(((dim, _moat_dim_value(row, pool, dim)) for dim in weights),
                        key=lambda x: -x[1])
        top_dim = scored[0][0]
        top2_dim = scored[1][0]
        dim_parts = [f"{name}板块{top_dim}为最核心壁垒"]
        if scored[1][1] >= 65:
            dim_parts.append(f"{top2_dim}形成第二道护城河")
        parts.append("，".join(dim_parts))
    else:
        # 通用模板：市场地位 + 技术壁垒
        parts.append(f"市场地位({pool['leader_base']:.0f}分)与盈利能力为核心壁垒")

    # ── 量化证据追加（不因行业模板省略） ──
    rd_raw = pd.to_numeric(row.get('研发投入%', np.nan), errors='coerce')
    if pd.notna(rd_raw) and float(rd_raw) >= 10:
        parts.append(f"研发投入{float(rd_raw):.1f}%")
    gm_raw = pd.to_numeric(row.get('毛利率', np.nan), errors='coerce')
    if pd.notna(gm_raw) and float(gm_raw) >= 40:
        parts.append(f"高毛利{float(gm_raw):.1f}%")
    roe_raw = pd.to_numeric(row.get('ROE', np.nan), errors='coerce')
    if pd.notna(roe_raw) and float(roe_raw) >= 20:
        parts.append(f"ROE {float(roe_raw):.1f}%")
    cfo_raw = pd.to_numeric(row.get('现金流/营收比', np.nan), errors='coerce')
    if pd.notna(cfo_raw) and float(cfo_raw) >= 0.2:
        parts.append("现金流充沛")
    prod_kw = _moat_match_keywords(row, MOAT_THEME_PRODUCT)
    if prod_kw:
        parts.append(f"{'/'.join(prod_kw[:2])}赛道放量")
    cust_kw = _moat_match_keywords(row, MOAT_THEME_CUSTOMER)
    if cust_kw:
        parts.append(f"绑定{'/'.join(cust_kw[:2])}客户")
    growth_kw = _moat_match_keywords(row, MOAT_THEME_GROWTH)
    if growth_kw:
        parts.append(f"{'/'.join(growth_kw[:2])}国产替代")

    if len(parts) == 0:
        return "商业模式稳定，具备一定客户基础"
    return "，".join(parts[:5]) + "。"


def _calc_risk_score(row) -> float:
    """
    RiskScore（V14 风险评分，0~100，越高风险越大）:
      = 业绩波动25% + 资产负债率15% + 商誉15% + 应收账款10% + 存货10%
        + 股东减持10% + 大额解禁10% + 行业周期5%
    缺失字段给中性风险分（不因缺数据误伤）。
    """
    # 业绩波动（利润同比极端或为负 → 高风险）
    profit_yoy = pd.to_numeric(row.get('利润同比', np.nan), errors='coerce')
    if pd.isna(profit_yoy):
        vol = 30.0
    elif profit_yoy < 0:
        vol = 70.0
    elif profit_yoy > 300:
        vol = 55.0
    elif profit_yoy > 100:
        vol = 40.0
    elif profit_yoy > 30:
        vol = 20.0
    else:
        vol = 30.0
    # 资产负债率
    debt = pd.to_numeric(row.get('资产负债率%', np.nan), errors='coerce')
    if pd.isna(debt):
        debt_r = 30.0
    elif debt >= 75: debt_r = 90.0
    elif debt >= 60: debt_r = 65.0
    elif debt >= 45: debt_r = 40.0
    else: debt_r = 15.0
    # 商誉占比
    gw = pd.to_numeric(row.get('商誉占比%', np.nan), errors='coerce')
    if pd.isna(gw):
        gw_r = 30.0
    elif gw >= 30: gw_r = 90.0
    elif gw >= 15: gw_r = 65.0
    elif gw >= 5:  gw_r = 40.0
    else: gw_r = 10.0
    # 应收账款增速（显著快于营收增速 → 收入质量风险）
    receiv = pd.to_numeric(row.get('应收增速%', np.nan), errors='coerce')
    rev_yoy = pd.to_numeric(row.get('营收同比', np.nan), errors='coerce')
    if pd.isna(receiv):
        ar_r = 30.0
    elif receiv >= 100 or (pd.notna(rev_yoy) and rev_yoy > 0 and receiv > rev_yoy * 1.5):
        ar_r = 80.0
    elif receiv >= 50: ar_r = 60.0
    elif receiv >= 20: ar_r = 40.0
    else: ar_r = 15.0
    # 存货增速（显著快于营收增速 → 滞销/积压风险）
    invent = pd.to_numeric(row.get('存货增速%', np.nan), errors='coerce')
    if pd.isna(invent):
        inv_r = 30.0
    elif invent >= 80 or (pd.notna(rev_yoy) and rev_yoy > 0 and invent > rev_yoy * 1.5):
        inv_r = 75.0
    elif invent >= 40: inv_r = 55.0
    elif invent >= 15: inv_r = 35.0
    else: inv_r = 15.0
    # 股东减持（股东数增加=筹码分散 + 公募大幅减仓=派发）
    holder = pd.to_numeric(row.get('股东数变化%', np.nan), errors='coerce')
    holder_r = 30.0
    if pd.notna(holder):
        if holder > 20: holder_r = 75.0
        elif holder > 10: holder_r = 55.0
        elif holder > 0: holder_r = 35.0
    fund = pd.to_numeric(row.get('公募持仓变化%', np.nan), errors='coerce')
    if pd.notna(fund):
        if fund < -30: holder_r = min(95.0, holder_r + 20)
        elif fund < -10: holder_r = min(95.0, holder_r + 10)
    # 大额解禁
    fl = pd.to_numeric(row.get('解禁占比%', np.nan), errors='coerce')
    if pd.isna(fl):
        fl_r = 30.0
    elif fl >= 30: fl_r = 85.0
    elif fl >= 15: fl_r = 60.0
    elif fl >= 5:  fl_r = 35.0
    else: fl_r = 10.0
    # 行业周期风险（产业景气低 → 周期下行风险高）
    cycle = pd.to_numeric(row.get('产业景气', np.nan), errors='coerce')
    if pd.isna(cycle):
        cyc_r = 30.0
    elif cycle >= 80: cyc_r = 5.0
    elif cycle >= 60: cyc_r = 20.0
    elif cycle >= 40: cyc_r = 45.0
    elif cycle >= 25: cyc_r = 65.0
    else: cyc_r = 85.0

    risk = (vol * V14_RISK_WEIGHTS['业绩波动'] +
            debt_r * V14_RISK_WEIGHTS['资产负债率'] +
            gw_r * V14_RISK_WEIGHTS['商誉'] +
            ar_r * V14_RISK_WEIGHTS['应收账款'] +
            inv_r * V14_RISK_WEIGHTS['存货'] +
            holder_r * V14_RISK_WEIGHTS['股东减持'] +
            fl_r * V14_RISK_WEIGHTS['大额解禁'] +
            cyc_r * V14_RISK_WEIGHTS['行业周期'])
    return min(100.0, max(0.0, risk))


def _risk_tips(row) -> str:
    """风险提示文字（从 RiskScore 明细生成）"""
    tips = []
    debt = pd.to_numeric(row.get('资产负债率%', np.nan), errors='coerce')
    if pd.notna(debt) and debt >= 60:
        tips.append(f'高负债{debt:.0f}%')
    gw = pd.to_numeric(row.get('商誉占比%', np.nan), errors='coerce')
    if pd.notna(gw) and gw >= 15:
        tips.append(f'商誉{gw:.0f}%')
    receiv = pd.to_numeric(row.get('应收增速%', np.nan), errors='coerce')
    rev_yoy = pd.to_numeric(row.get('营收同比', np.nan), errors='coerce')
    if pd.notna(receiv) and (receiv >= 50 or (pd.notna(rev_yoy) and rev_yoy > 0 and receiv > rev_yoy * 1.5)):
        tips.append('应收过快')
    invent = pd.to_numeric(row.get('存货增速%', np.nan), errors='coerce')
    if pd.notna(invent) and (invent >= 50 or (pd.notna(rev_yoy) and rev_yoy > 0 and invent > rev_yoy * 1.5)):
        tips.append('存货积压')
    holder = pd.to_numeric(row.get('股东数变化%', np.nan), errors='coerce')
    if pd.notna(holder) and holder > 10:
        tips.append('股东数增加')
    fund = pd.to_numeric(row.get('公募持仓变化%', np.nan), errors='coerce')
    if pd.notna(fund) and fund < -20:
        tips.append('公募减仓')
    fl = pd.to_numeric(row.get('解禁占比%', np.nan), errors='coerce')
    if pd.notna(fl) and fl >= 15:
        tips.append(f'解禁{fl:.0f}%')
    cycle = pd.to_numeric(row.get('产业景气', np.nan), errors='coerce')
    if pd.notna(cycle) and cycle < 40:
        tips.append('行业周期下行')
    return '; '.join(tips) if tips else '一般'


def _calc_final_score(double, sustain, moat, risk) -> float:
    """FinalScore = Double×30% + Sustainable×35% + Moat×25% + (100-Risk)×10%"""
    return (double * V14_FINAL_WEIGHTS['DoubleScore'] +
            sustain * V14_FINAL_WEIGHTS['SustainableScore'] +
            moat * V14_FINAL_WEIGHTS['MoatScore'] +
            (100.0 - risk) * V14_FINAL_WEIGHTS['RiskSafety'])


def _recommend_level(final_score) -> str:
    """推荐等级（★★★★★~★）"""
    for threshold, label in RECOMMEND_LEVELS:
        if final_score >= threshold:
            return label
    return RECOMMEND_LEVELS[-1][1]


def _classify_logic_v14(row):
    """
    V14 核心逻辑: 一级逻辑（需求/产品/成本/周期/政策/一次性收益）
                + 二级逻辑 + 可信度星级
    关键词匹配仅使用 主题 + 主营产业（IndustryTheme），禁止用市场概念（ConceptTheme）
    匹配，避免"招商轮船→低空经济"这类错误映射。
    """
    nr = float(pd.to_numeric(row.get('非经常损益%', 0), errors='coerce') or 0)
    rev_yoy = float(pd.to_numeric(row.get('营收同比', 0), errors='coerce') or 0)
    profit_yoy = float(pd.to_numeric(row.get('利润同比', 0), errors='coerce') or 0)
    gm = float(pd.to_numeric(row.get('毛利率', 0), errors='coerce') or 0)
    rd = float(pd.to_numeric(row.get('研发投入%', 0), errors='coerce') or 0)
    search_txt = f"{row.get('主题', '')} {row.get('主营产业', '')}"

    # 一次性收益（非经常损益占比高 → 利润含金量低）
    if nr > 30:
        return '一次性收益', '资产处置/非经常损益', PRIMARY_DRIVER_STARS['一次性收益']
    # 政策驱动（主题/主营产业含政策主线关键词）
    for kw in ['国产替代', '自主可控', '信创', '低空经济', '商业航天', '数据要素', '航天军工', '半导体设备']:
        if kw in search_txt:
            return '政策驱动', kw, PRIMARY_DRIVER_STARS['政策驱动']
    # 周期驱动（利润高增但营收低增 → 价格/周期弹性，如航运、化工）
    if profit_yoy > 120 and rev_yoy < 40:
        return '周期驱动', '行业涨价/周期反转', PRIMARY_DRIVER_STARS['周期驱动']
    # 产品驱动（高毛利 + 强利润弹性 + 有营收增长基础 → 高毛利产品放量/产品升级）
    leverage = profit_yoy / rev_yoy if rev_yoy > 5 else None
    if leverage is not None and leverage >= 2.0 and gm >= 35 and rev_yoy >= 15:
        sec = '产品升级' if rd >= 8 else '高毛利产品放量'
        return '产品驱动', sec, PRIMARY_DRIVER_STARS['产品驱动']
    # 成本驱动（利润高增但营收低增且非周期 → 降本增效/规模效应）
    if profit_yoy > 50 and 0 < rev_yoy < 15:
        return '成本驱动', '降本增效', PRIMARY_DRIVER_STARS['成本驱动']
    # 需求驱动（营收高增）
    if rev_yoy >= 30:
        sec = '规模效应' if gm < 35 else '需求景气'
        return '需求驱动', sec, PRIMARY_DRIVER_STARS['需求驱动']
    return '需求驱动', '温和增长', PRIMARY_DRIVER_STARS['需求驱动']


# ══════════════════════════════════════════════════════════════════════
# V15 Explain Engine（解释引擎）
# 原则：所有解释基于财报/行业/主营数据，禁止空泛结论；体现行业差异；
#       风险提示具体到经营/行业/政策/估值。仅新增解释层，不影响评分计算。
# ══════════════════════════════════════════════════════════════════════

def _fnum(x, default=None):
    """安全转 float；None/NaN/'' 返回 default（默认 None 表示缺失，调用方须判 None）"""
    if x is None:
        return default
    try:
        f = float(x)
        if pd.isna(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


# ── Explain 1: TopReasons（为什么排名这么高，3~5 条） ──
def _gen_top_reasons(row) -> str:
    """
    自动生成排名靠前的原因（3~5 条，编号输出）。
    来源：营收/利润增速、毛利、PEG、估值空间、行业景气、订单、机构认可、龙头地位。
    """
    rev_yoy = _fnum(row.get('营收同比'), default=-999.0)
    profit_yoy = _fnum(row.get('利润同比'), default=-999.0)
    gm = _fnum(row.get('毛利率'), default=-999.0)
    peg = row.get('_peg_v14', None)
    peg_v = _fnum(peg, default=-1.0)
    upside = _fnum(row.get('估值空间%'), default=-999.0)
    cycle = _fnum(row.get('产业景气'), default=-999.0)
    order = _fnum(row.get('订单爆发'), default=-999.0)
    inst = _fnum(row.get('机构认可'), default=-999.0)
    leader = str(row.get('龙头类型', ''))
    moat = _fnum(row.get('MoatScore'), default=-999.0)
    txt = _moat_txt(row)

    reasons = []
    # ① 营收高增
    if rev_yoy >= 30:
        reasons.append(f"营收同比+{rev_yoy:.0f}%，需求持续高景气")
    elif rev_yoy >= 15:
        reasons.append(f"营收同比+{rev_yoy:.0f}%，收入稳健增长")
    # ② 利润高增
    if profit_yoy >= 100:
        reasons.append(f"利润同比+{profit_yoy:.0f}%，盈利弹性大")
    elif profit_yoy >= 30:
        reasons.append(f"利润同比+{profit_yoy:.0f}%，盈利改善")
    # ③ 高毛利（产品结构/壁垒佐证）
    if gm >= 40:
        reasons.append(f"毛利率{gm:.1f}%，高毛利产品放量")
    # ④ 估值合理（PEG）
    if 0 <= peg_v < 0.7:
        reasons.append(f"PEG {peg_v:.2f}，成长与估值匹配")
    elif 0 <= peg_v < 1.0:
        reasons.append(f"PEG {peg_v:.2f}，估值尚属合理")
    # ⑤ 估值空间
    if upside >= 60:
        reasons.append(f"估值空间{upside:.0f}%")
    # ⑥ 行业景气
    if cycle >= 70:
        reasons.append(f"行业景气{cycle:.0f}分，处于{row.get('_cycle_label', '')}阶段")
    # ⑦ 订单/机构
    if order >= 70:
        reasons.append(f"订单爆发{order:.0f}分，需求可见度高")
    if inst >= 70:
        reasons.append(f"机构认可{inst:.0f}分")
    # ⑧ 龙头地位
    if leader and leader != '普通':
        reasons.append(f"{leader}地位，市占率领先")
    # ⑨ 护城河
    if moat >= 70:
        reasons.append(f"MoatScore {moat:.0f}，护城河深厚")
    # ⑩ 高成长赛道关键词
    for kw in MOAT_THEME_GROWTH:
        if kw in txt:
            reasons.append(f"{kw}赛道景气+国产替代空间")
            break
    if not reasons:
        reasons.append("业绩与估值性价比均衡")
    return "；".join(reasons[:5])


# ── Explain 2: Weakness（哪些因素拖累评分，1~3 条） ──
def _gen_weakness(row) -> str:
    """
    自动生成拖累评分的因素（1~3 条，编号输出）。
    来源：RiskScore 明细、PEG、波动率、低基数、行业周期。
    """
    peg = row.get('_peg_v14', None)
    peg_v = _fnum(peg, default=-1.0)
    profit_yoy = _fnum(row.get('利润同比'), default=-999.0)
    rev_yoy = _fnum(row.get('营收同比'), default=-999.0)
    risk = _fnum(row.get('RiskScore'), default=-1.0)
    cycle = _fnum(row.get('产业景气'), default=-1.0)
    txt = _moat_txt(row)

    weak = []
    # ① 波动率高（60日收益波动大或行业周期强）
    ret60 = _fnum(row.get('60日收益%'), default=None)
    if ret60 is not None and (ret60 >= 60 or ret60 <= -40):
        weak.append("股价波动率较高")
    elif any(kw in txt for kw in ('航运', '化工', '有色', '煤炭', '石油', '资源')):
        weak.append("行业周期属性较强")
    # ② 利润低基数影响
    if profit_yoy is not None and rev_yoy is not None and profit_yoy >= 200 and rev_yoy < 60:
        weak.append("利润高增受低基数影响")
    # ③ PEG 偏高
    if peg_v >= 1.0:
        weak.append(f"PEG {peg_v:.2f}，估值弹性有限")
    # ④ 风险细分
    debt = _fnum(row.get('资产负债率%'), default=None)
    if debt is not None and debt >= 60:
        weak.append(f"资产负债率{debt:.0f}%偏高")
    receiv = _fnum(row.get('应收增速%'), default=None)
    if receiv is not None and receiv >= 60:
        weak.append(f"应收账款增速{receiv:.0f}%过快")
    invent = _fnum(row.get('存货增速%'), default=None)
    if invent is not None and invent >= 60:
        weak.append(f"存货增速{invent:.0f}%需关注")
    holder = _fnum(row.get('股东数变化%'), default=None)
    if holder is not None and holder >= 10:
        weak.append(f"股东数+{holder:.0f}%筹码分散")
    fund = _fnum(row.get('公募持仓变化%'), default=None)
    if fund is not None and fund <= -20:
        weak.append(f"公募减仓{fund:.0f}%")
    fl = _fnum(row.get('解禁占比%'), default=None)
    if fl is not None and fl >= 15:
        weak.append(f"解禁{fl:.0f}%")
    # ⑤ 行业景气偏低
    if cycle is not None and 0 < cycle < 40:
        weak.append(f"行业景气{cycle:.0f}分，景气度偏低")
    if risk is not None and risk >= 60:
        weak.append(f"综合RiskScore {risk:.0f}偏高")
    if not weak:
        weak.append("暂无显著拖累因素")
    return "；".join(weak[:3])


# ── Explain 3: LogicEvidence（财报证据，禁止无证据描述） ──
def _gen_logic_evidence(row) -> str:
    """
    自动引用最近财报关键数字（营收/利润/毛利率/研发/ROE/CAGR/现金流/订单）。
    所有内容均来自输入数据字段，缺失项自动跳过，不编造。
    """
    evid = []
    rev_yoy = _fnum(row.get('营收同比'), default=-999.0)
    if rev_yoy >= 0:
        evid.append(f"营业收入同比+{rev_yoy:.0f}%")
    profit_yoy = _fnum(row.get('利润同比'), default=-999.0)
    if profit_yoy >= 0:
        evid.append(f"归母净利润同比+{profit_yoy:.0f}%")
    gm = _fnum(row.get('毛利率'), default=-1.0)
    if gm >= 0:
        evid.append(f"毛利率{gm:.1f}%")
    rd = _fnum(row.get('研发投入%'), default=-1.0)
    if rd >= 0:
        evid.append(f"研发投入占比{rd:.1f}%")
    roe = _fnum(row.get('ROE'), default=-999.0)
    if roe >= 0:
        evid.append(f"ROE {roe:.1f}%")
    cagr = _fnum(row.get('3年利润CAGR'), default=-999.0)
    if cagr >= 0:
        evid.append(f"近3年利润CAGR {cagr:.1f}%")
    cfo = _fnum(row.get('现金流/营收比'), default=-999.0)
    if cfo >= 0.1:
        evid.append(f"现金流/营收 {cfo:.2f}")
    order = _fnum(row.get('订单爆发'), default=-1.0)
    if order >= 60:
        evid.append(f"订单爆发{order:.0f}分")
    if not evid:
        return "财报数据缺失，建议查阅最新定期报告"
    return "；".join(evid[:6])


# ── Explain 4: NextQuarterWatch（下一份财报重点验证） ──
def _gen_next_quarter_watch(row) -> str:
    """
    按行业模板生成下一份财报的验证要点（2~3 条，编号输出）。
    未命中行业模板时按通用财务验证点。
    """
    template = _detect_moat_template(row)
    watch = {
        '半导体': ["企业级SSD/存储产品收入是否继续增长", "毛利率能否继续提升", "库存是否回落"],
        '创新药': ["核心产品销量/放量节奏", "销售费用率变化", "医保谈判放量进展"],
        'AI软件': ["订阅/云收入增速", "客户留存与现金流质量", "AI产品商业化进展"],
        '游戏': ["新游流水与版号节奏", "海外收入占比变化", "研发团队效率与买量成本"],
        '化工': ["产品价差与开工率", "成本端（原料价格）变化", "一体化产能利用率"],
        '机器人': ["大客户订单落地/量产进度", "国产替代认证进展", "毛利率随规模爬坡情况"],
        '航运': ["运价指数（BDI/CCFI）走势", "船队利用率与运力投放", "燃油成本与资产减值"],
        '有色资源': ["金属价格走势", "产量释放与资源储量", "单位成本变化"],
    }.get(template, ["营收增速能否延续", "毛利率/费用率变化", "存货与应收账款质量"])
    return "；".join([f"① {watch[0]}", f"② {watch[1]}", f"③ {watch[2]}"])


# ── Explain 5: InvestmentSummary（一句话投资逻辑，50~80 字） ──
def _gen_investment_summary(row) -> str:
    """
    自动生成一句话投资逻辑（50~80 字，正反两面）。
    正面：主营+成长驱动；反面：需关注的核心风险。
    """
    rev_yoy = _fnum(row.get('营收同比'), default=-999.0)
    profit_yoy = _fnum(row.get('利润同比'), default=-999.0)
    gm = _fnum(row.get('毛利率'), default=-999.0)
    cycle = _fnum(row.get('产业景气'), default=-999.0)

    # 正面驱动
    drivers = []
    if rev_yoy >= 30:
        drivers.append("收入高增长")
    if profit_yoy >= 50:
        drivers.append("盈利快速改善")
    if gm >= 40:
        drivers.append("高毛利产品占比提升")
    if cycle >= 70:
        drivers.append(f"行业景气（{row.get('_cycle_label', '')}）")
    if not drivers:
        drivers.append("主业稳健")
    # 行业词（取主营产业首个词，避免整段行业名过长）
    ind = str(row.get('主营产业', '')).strip()
    ind_short = ind.split('/')[0][:12] if ind else "公司"

    # 反面风险
    risks = []
    peg = row.get('_peg_v14', None)
    peg_v = _fnum(peg, default=-1.0)
    if peg_v >= 1.0:
        risks.append("估值偏高")
    debt = _fnum(row.get('资产负债率%'), default=None)
    if debt is not None and debt >= 60:
        risks.append("负债水平偏高")
    invent = _fnum(row.get('存货增速%'), default=None)
    if invent is not None and invent >= 60:
        risks.append("存货积压风险")
    risk_txt = "、".join(risks[:2]) if risks else "业绩兑现节奏"

    summary = f"{ind_short}受益于{drivers[0]}，利润弹性与估值性价比兼顾，需重点跟踪{risk_txt}。"
    if len(summary) > 85:
        summary = summary[:82] + "。"
    return summary


# ── Explain 6: TopRisk（最大的两个风险） ──
def _gen_top_risk(row) -> str:
    """
    自动输出最大的两个风险（具体到经营/行业/政策/估值，不得仅输出"存在风险"）。
    按 RiskScore 明细维度风险贡献排序取前 2。
    """
    risks = []
    profit_yoy = _fnum(row.get('利润同比'), default=None)
    if profit_yoy is not None and profit_yoy < 0:
        risks.append(("经营", 80, "利润同比转负，业绩下滑风险"))
    elif profit_yoy is not None and profit_yoy > 300:
        risks.append(("经营", 55, "利润高增或含低基数/一次性因素"))
    gm = _fnum(row.get('毛利率'), default=None)
    if gm is not None and 0 <= gm < 20:
        risks.append(("经营", 50, f"毛利率{gm:.1f}%偏低，盈利空间薄"))

    debt = _fnum(row.get('资产负债率%'), default=None)
    if debt is not None and debt >= 60:
        risks.append(("财务", 50, f"资产负债率{debt:.0f}%偏高"))
    receiv = _fnum(row.get('应收增速%'), default=None)
    rev_yoy = _fnum(row.get('营收同比'), default=None)
    if receiv is not None and (receiv >= 50 or (rev_yoy is not None and rev_yoy > 0 and receiv > rev_yoy * 1.5)):
        risks.append(("财务", 45, f"应收账款增速{receiv:.0f}%快于营收"))
    invent = _fnum(row.get('存货增速%'), default=None)
    if invent is not None and (invent >= 50 or (rev_yoy is not None and rev_yoy > 0 and invent > rev_yoy * 1.5)):
        risks.append(("经营", 45, f"存货增速{invent:.0f}%，或有去库压力"))

    holder = _fnum(row.get('股东数变化%'), default=None)
    if holder is not None and holder >= 10:
        risks.append(("筹码", 40, f"股东数+{holder:.0f}%，筹码分散"))
    fund = _fnum(row.get('公募持仓变化%'), default=None)
    if fund is not None and fund <= -20:
        risks.append(("筹码", 40, f"公募减仓{fund:.0f}%"))
    fl = _fnum(row.get('解禁占比%'), default=None)
    if fl is not None and fl >= 15:
        risks.append(("筹码", 40, f"解禁{fl:.0f}%，或有抛压"))

    cycle = _fnum(row.get('产业景气'), default=None)
    if cycle is not None and 0 < cycle < 40:
        risks.append(("行业", 45, f"行业景气{cycle:.0f}分，周期下行风险"))
    peg = row.get('_peg_v14', None)
    peg_v = _fnum(peg, default=-1.0)
    if peg_v >= 1.2:
        risks.append(("估值", 40, f"PEG {peg_v:.2f}，估值偏高"))

    if not risks:
        return "暂无显著风险（风险评分偏低）"
    risks.sort(key=lambda x: -x[1])
    return "；".join(f"{tag}：{desc}" for tag, _, desc in risks[:2])


# ── Explain 7: Recommendation（推荐动作，六档） ──
def _calc_recommendation(row) -> str:
    """
    统一推荐动作（★★★★★~★），由 FinalScore + RiskScore + 行业景气共同决定。
    """
    final = _fnum(row.get('FinalScore'), default=-1.0)
    risk = _fnum(row.get('RiskScore'), default=-1.0)
    cycle = _fnum(row.get('产业景气'), default=-1.0)
    if final >= 80 and risk < 45 and cycle >= 70:
        return "★★★★★ 重点配置"
    if final >= 75 and risk < 55:
        return "★★★★☆ 回调布局"
    if final >= 68 and risk < 65:
        return "★★★★ 持续跟踪"
    if final >= 60:
        return "★★★ 观察等待"
    if final >= 50:
        return "★★ 谨慎参与"
    return "★ 回避"


# ── Explain 8: IndustryRank / IndustryPercentile（行业内排名） ──
def _calc_industry_rank(result: pd.DataFrame, full_df: pd.DataFrame) -> None:
    """
    行业内排名（基于全池）：
      分子 = 候选池（passed）内该主营产业的 FinalScore 排名
      分母 = 全池该主营产业的股票总数
    例如"存储芯片 2/38"：全池 38 只存储芯片中，该股成长评分排第 2。
    """
    ind = result['主营产业'].fillna('未知行业').astype(str)
    # 候选池内排名（FinalScore 降序；NaN 保留为 NaN，最后统一处理）
    result['_ind_rank'] = result.groupby(ind, sort=False)['FinalScore'].rank(
        ascending=False, method='min')
    # 分母：全池行业股票数
    full_ind = full_df['主营产业'].fillna('未知行业').astype(str)
    _full_cnt = full_ind.value_counts()
    result['_ind_count'] = result['主营产业'].fillna('未知行业').astype(str).map(
        lambda x: int(_full_cnt.get(x, 0)))
    # 排名 2/38 形式（NaN 排名的股票显示 0/N 并置 Percentile NaN）
    result['IndustryRank'] = result.apply(
        lambda r: f"{int(r['_ind_rank'])}/{int(r['_ind_count'])}" if pd.notna(r['_ind_rank']) else f"0/{int(r['_ind_count'])}",
        axis=1)
    # 百分位：越高越靠前（1 名 → 100%）；无排名（未通过）置 NaN
    result['IndustryPercentile'] = result.apply(
        lambda r: round((1 - (r['_ind_rank'] - 1) / r['_ind_count']) * 100, 1)
        if pd.notna(r['_ind_rank']) and r['_ind_count'] > 0 else np.nan, axis=1)


def run_double_score(csv_path: str = None, df: pd.DataFrame = None) -> pd.DataFrame:
    """
    翻倍黑马综合评分主入口

    Parameters
    ----------
    csv_path : str, optional
        bull_stocks_all.csv 路径
    df : pd.DataFrame, optional
        直接传入 DataFrame

    Returns
    -------
    pd.DataFrame
        按 DoubleScore 降序排列，已通过一票否决筛选
    """
    if df is None:
        df = pd.read_csv(csv_path, encoding='utf-8-sig')
    else:
        df = df.copy()

    data = df.copy()
    data['code'] = data['code'].apply(_normalize_code)

    # ── Step 0: V12.1 成长评分增强字段计算 ──
    # 1. ProfitQualityFactor（低基数衰减）
    data['_profit_quality'] = data.apply(_calc_profit_quality_factor, axis=1)
    # 2. AdjustedProfitGrowth = ProfitYoY × ProfitQualityFactor
    data['_adjusted_profit_growth'] = data.apply(
        lambda r: _calc_adjusted_profit_growth(
            pd.to_numeric(r.get('利润同比', 0), errors='coerce'),
            r['_profit_quality'],
        ), axis=1)
    # 3. 重新计算 PEG = PE_TTM / max(FutureGrowth, 20)
    #    FutureGrowth = 0.5×营收YoY + 0.3×扣非利润YoY + 0.2×3年利润CAGR
    #    解决低基数利润同比导致的 PEG≈0 失真（V12.3）
    data['_future_growth'] = data.apply(_calc_future_growth, axis=1)
    data['_peg_new'] = data.apply(
        lambda r: _calc_peg(
            pd.to_numeric(r.get('PE_TTM', None), errors='coerce'),
            r['_future_growth'],
        ), axis=1)
    data['_peg_score'] = data['_peg_new'].apply(_calc_peg_score)
    # 4. 估值空间限幅（最大200%，最小-50%）
    data['_upside_capped'] = pd.to_numeric(data['估值空间%'], errors='coerce').apply(_calc_upside_capped)
    data['_upside_score'] = data['_upside_capped'].apply(_calc_upside_score)

    # ── Step 0b: V12.2 成长因子标准化评分 ──
    # 原始财务数据 → 缩尾 → 对数压缩(signed-log1p) → 行业内Z-Score → clip → 0-100
    # 在全候选池(data)上标准化，保证行业统计稳定；行业缺失/小样本回退全局统计
    for fac in STD_FACTORS:
        if fac not in data.columns:
            continue
        need_log = fac in STD_LOG_FACTORS
        data[f'_std_{fac}'] = _standardize_factor(data, fac, log=need_log)
    # 业绩超预期已是 bull_scorer 输出的 0~100 分（PEAD 信号），直接使用、不重复标准化
    data['_std_业绩超预期'] = pd.to_numeric(data['业绩超预期'], errors='coerce').fillna(0)

    # ── Step 0c: V12.3 质量因子标准化 ──
    # QualityScore = 0.4×ROE + 0.3×CFO + 0.3×扣非占比
    # CFO = 经营现金流/营收；扣非占比 = 扣非净利润/归母净利润(由非经常损益%换算)
    if '现金流/营收比' in data.columns:
        data['_std_现金流占比'] = _standardize_factor(data, '现金流/营收比', log=False)
    else:
        data['_std_现金流占比'] = 50.0
    if '非经常损益%' in data.columns:
        _nr = pd.to_numeric(data['非经常损益%'], errors='coerce')
    else:
        _nr = pd.Series(0.0, index=data.index)
    data['_扣非占比'] = (100.0 - _nr).clip(0, 100)
    data['_std_扣非占比'] = _standardize_factor(data, '_扣非占比', log=False)

    # ── Step 0d: V14 指标层（对数压缩 + 营收/利润质量 + PEG 连续化） ──
    # 1. AdjustedProfitGrowth(V14) = 100×log2(1+利润同比×质量系数/100)
    #    低基数修正 + 对数压缩，禁止直接使用 ProfitYoY
    data['_adjusted_profit_growth_v14'] = data.apply(_calc_adjusted_profit_growth_v14, axis=1)
    # 2. 营收 log2 压缩
    data['_revenue_compressed'] = pd.to_numeric(data['营收同比'], errors='coerce').apply(_log2_compress_pct)
    # 3. 营收/利润 log2压缩值 行业内标准化（0~100）
    data['_std_rev_v14'] = _standardize_factor(data, '_revenue_compressed', log=False)
    data['_std_profit_v14'] = _standardize_factor(data, '_adjusted_profit_growth_v14', log=False)
    # 4. RevenueQuality = 标准化分 + 毛利率协同（高毛利放量奖励/低毛利走量降分）
    data['RevenueQuality'] = data.apply(
        lambda r: _calc_revenue_quality(r, r.get('_std_rev_v14', np.nan)), axis=1)
    # 5. ProfitQualityPenalty（现金流为负 / 扣非明显低于净利润 → 降低成长评分）
    data['_profit_quality_penalty'] = data.apply(_calc_profit_quality_penalty, axis=1)
    # 6. FutureGrowth(V14) = 0.5×营收YoY + 0.3×AdjustedProfitGrowth + 0.2×3年利润CAGR
    data['_future_growth_v14'] = data.apply(_calc_future_growth_v14, axis=1)
    data['_peg_v14'] = data.apply(
        lambda r: _calc_peg(
            pd.to_numeric(r.get('PE_TTM', None), errors='coerce'),
            r['_future_growth_v14'],
        ), axis=1)
    # 7. PEGScore(V14) = 100/(1+PEG) 连续函数
    data['_peg_score_v14'] = data['_peg_v14'].apply(_calc_peg_score_v14)
    # 8. 估值: UpsideScore(限幅) + ValueBonus（PE/PB 均低于行业均值）
    data['_upside_score_v14'] = data['_upside_capped'].apply(_calc_upside_score)
    _pe_pos = pd.to_numeric(data['PE_TTM'], errors='coerce').where(
        pd.to_numeric(data['PE_TTM'], errors='coerce') > 0)
    _pb_pos = pd.to_numeric(data['PB'], errors='coerce').where(
        pd.to_numeric(data['PB'], errors='coerce') > 0)
    _ind_key = data['industry'].fillna('').astype(str)
    data['_pe_industry_median'] = _pe_pos.groupby(_ind_key).transform('median')
    data['_pb_industry_median'] = _pb_pos.groupby(_ind_key).transform('median')
    data['_value_bonus'] = data.apply(
        lambda r: _calc_value_bonus(r, r.get('_pe_industry_median', np.nan),
                                    r.get('_pb_industry_median', np.nan)), axis=1)
    # 9. IndustryCycleScore（产业景气 → 0~100 + 阶段标签，直接加权）
    _cycle_map = data['产业景气'].apply(_calc_industry_cycle_score)
    data['_cycle_score'] = _cycle_map.apply(lambda x: x[0])
    data['_cycle_label'] = _cycle_map.apply(lambda x: x[1])
    # 10. 盈利加速度分（H1/Q1 加速 × 业绩超预期）
    data['_accel_score'] = data.apply(_calc_acceleration_score, axis=1)
    # 11. 市场份额分（市值规模行业内标准化，对数压缩；大市值=高份额代理）
    data['_std_市值份额'] = _standardize_factor(data, '市值(亿)', log=True)

    # ── Step 1: 评分基础字段（V15.2：已去掉一票否决，全量参与评分） ──
    # 背景：药康生物(688046) 2026-08-07 20cm 涨停被"估值空间<30%"否决误杀，
    #       估值/利润/PE 等指标改为在评分内连续计分，不再硬性剔除（详见 _calc_upside_score/PEGScore）
    data['_peg_val'] = data['_peg_v14']
    data['_peg_na'] = data['_peg_val'].isna()
    data['_peg_eff'] = data['_peg_val'].where(data['_peg_na'] == False, 0.0)
    data['_upside_val'] = data['_upside_capped'].fillna(-999)
    data['_profit_val'] = pd.to_numeric(data['利润同比'], errors='coerce').fillna(-999)
    data['_vetoed'] = False  # 保留字段以兼容下游，但不再有任何否决

    passed = data.copy()
    vetoed = data[data['_vetoed']].copy()

    print(f"评分统计（已去掉一票否决，全量参与评分）:")
    print(f"  总样本: {len(data)} 只，全部纳入评分")

    # ── Step 2: 12 因子评分 ──
    # 成长类因子 (1~6) 采用 V12.2 标准化评分（对数压缩 + 行业内Z-Score），0~100 同量纲可比
    # 1. 营收同比（对数压缩 + 行业内标准化）
    passed['_s_营收同比'] = passed['_std_营收同比'].fillna(0)

    # 2. 利润同比（V12.1 AdjustedProfitGrowth 修正值 + 对数压缩 + 行业内标准化）
    passed['_s_利润同比'] = passed['_std__adjusted_profit_growth'].fillna(0)

    # 3. 业绩超预期 (PEAD) — bull_scorer 已输出 0~100 分，直接使用
    passed['_s_业绩超预期'] = passed['_std_业绩超预期'].fillna(0)

    # 4. ROE（行业内标准化）
    passed['_s_ROE'] = passed['_std_ROE'].fillna(0)

    # V12.3 质量因子: CFO(现金流占比) + 扣非占比（行业内标准化）
    passed['_s_CFO'] = passed['_std_现金流占比'].fillna(50)
    passed['_s_扣非占比'] = passed['_std_扣非占比'].fillna(50)
    passed['QualityScore'] = (
        passed['_s_ROE'] * 0.4 +
        passed['_s_CFO'] * 0.3 +
        passed['_s_扣非占比'] * 0.3
    )

    # 5. 毛利率（行业内标准化）
    passed['_s_毛利率'] = passed['_std_毛利率'].fillna(0)

    # 6. 研发投入（行业内标准化）
    passed['_s_研发投入'] = passed['_std_研发投入%'].fillna(0)

    # 7. PEG（V12.1：使用 PEGScore 分段评分，PEG<0.5满分，>3得20分；None 给中性分）
    passed['_s_PEG'] = passed['_peg_score']

    # 8. 估值空间（V12.1：使用限幅后的 UpsideScore 分段评分）
    passed['_s_估值空间'] = passed['_upside_score']

    # 9. 流通市值（200~800亿最佳，非对称U型评分）
    mcap = pd.to_numeric(passed['市值(亿)'], errors='coerce').fillna(0)
    def _score_mcap(v):
        if 200 <= v <= 800:
            return 100.0
        elif 100 <= v < 200:
            return 50.0 + (v - 100) / 100 * 50.0
        elif 800 < v <= 1500:
            return 100.0 - (v - 800) / 700 * 50.0
        elif v < 100:
            return v / 100 * 50.0
        else:
            return max(0, 50.0 - (v - 1500) / 500 * 50.0)
    passed['_s_流通市值'] = mcap.apply(_score_mcap)

    # 10. 机构动向（筹码面+资金流入代理）
    chip = pd.to_numeric(passed['筹码面'], errors='coerce').fillna(50)
    passed['_s_机构动向'] = chip.apply(lambda x: _score_one(x, 0, 60, True))

    # 11. 龙头类型
    leader = passed['龙头类型'].map(LEADER_SCORE).fillna(0)
    passed['_s_龙头类型'] = leader

    # 12. 主题主线匹配（静态加分，不计入权重）
    def _score_theme(theme):
        if pd.isna(theme):
            return 0
        for t in CORE_THEMES:
            if t in str(theme):
                return 100
        return 0
    passed['_s_主题匹配'] = passed['theme'].apply(_score_theme)

    # ── 非经常性损益扣分 ──
    # 非经常损益% = (n_income - n_income_attr_p) / n_income * 100
    # 正值表示非经常性收益增厚了利润，占比越高，利润质量越差
    # 以乘数方式作用于总分：占比>50%打7折，>30%打8折，>20%打9折，≤20%不打折
    def _calc_nonrecurring_discount(row):
        nr_raw = pd.to_numeric(row.get('非经常损益%', 0), errors='coerce')
        if pd.isna(nr_raw) or nr_raw == 0:
            return 1.0
        # 负值：扣非 > 归母，核心业务更强，不扣分
        if nr_raw < 0:
            return 1.0
        # 正值：非经常性收益增厚了利润，占比越高利润质量越差
        if nr_raw > 50:
            return 0.70
        elif nr_raw > 30:
            return 0.80
        elif nr_raw > 20:
            return 0.90
        else:
            return 1.0
    passed['_nonrecurring_discount'] = passed.apply(_calc_nonrecurring_discount, axis=1)

    # ── Q1净利润同比加速对比 ──
    # 半年度预告增长率 vs Q1实际增长率：只有半年度超预期才是真的好
    # 加速倍数 = 半年度预告利润同比 / Q1实际利润同比
    # 加速倍数越高，说明业绩加速越明显，利润质量越高
    def _calc_q1_accel_discount(row):
        q1_yoy = pd.to_numeric(row.get('Q1利润同比', None), errors='coerce')
        half_yoy = pd.to_numeric(row.get('利润同比', 0), errors='coerce')
        # Q1数据不可用或Q1利润为负（基数低），不扣分
        if pd.isna(q1_yoy) or q1_yoy == 0 or q1_yoy is None:
            return 1.0
        # Q1利润同比为负：公司Q1在衰退，半年度预告只要有增长就算好转
        if q1_yoy <= 0:
            if half_yoy > 0:
                return 1.0
            else:
                return 0.70
        # 计算加速倍数
        accel = half_yoy / q1_yoy
        if accel >= 1.2:
            return 1.0       # 明显加速，不扣分
        elif accel >= 1.0:
            return 1.0       # 持平加速，不扣分
        elif accel >= 0.8:
            return 0.90      # 轻微减速
        elif accel >= 0.5:
            return 0.75      # 明显减速
        else:
            return 0.50      # 严重减速（半年度远不及Q1）
    passed['_q1_accel_discount'] = passed.apply(_calc_q1_accel_discount, axis=1)

    # ── 增强维度 v1.1：4 个乘数因子 ──
    # 1. 利润质量过滤（低基数识别）
    passed['_low_base_discount'] = passed.apply(_calc_low_base_discount, axis=1)
    # 2. 主题真实性校验（主营业务×主题交叉验证）
    passed['_theme_auth'] = passed.apply(_calc_theme_authenticity, axis=1)
    # 3. 景气周期因子（行业景气度作为乘数）
    passed['_cycle_mult'] = passed.apply(_calc_cycle_multiplier, axis=1)
    # 4. 估值约束（PE_TTM/合理PE 比值）
    passed['_valuation_discount'] = passed.apply(_calc_valuation_discount, axis=1)

    # 增强标签（输出诊断用）
    def _enhance_tags(row):
        tags = []
        lb = row.get('_low_base_discount', 1.0)
        if lb < 1.0:
            tags.append(f'低基数{lb:.0%}')
        ta = row.get('_theme_auth', 1.0)
        if ta < 1.0:
            tags.append(f'主题存疑{ta:.0%}')
        cm = row.get('_cycle_mult', 1.0)
        if cm > 1.0:
            tags.append(f'景气上行x{cm:.2f}')
        elif cm < 1.0:
            tags.append(f'景气下行{cm:.0%}')
        vd = row.get('_valuation_discount', 1.0)
        if vd < 1.0:
            tags.append(f'估值约束{vd:.0%}')
        return ' | '.join(tags) if tags else ''
    passed['_enhance_tags'] = passed.apply(_enhance_tags, axis=1)

    # ── Step 3: V12.3 加权综合得分 ──
    # GrowthScore（成长爆发，1~6个月）：去掉ROE(已归入质量)，权重重归一化
    passed['GrowthScore'] = (
        passed['_s_营收同比'] * 0.10 +
        passed['_s_利润同比'] * 0.15 +
        passed['_s_业绩超预期'] * 0.10 +
        passed['_s_毛利率'] * 0.08 +
        passed['_s_研发投入'] * 0.07 +
        passed['_s_PEG'] * 0.15 +
        passed['_s_估值空间'] * 0.10 +
        passed['_s_流通市值'] * 0.05 +
        passed['_s_机构动向'] * 0.05 +
        passed['_s_龙头类型'] * 0.05
    ) / 0.90

    # DoubleScore = 0.7×GrowthScore + 0.3×QualityScore（V12.3）
    passed['DoubleScore'] = 0.7 * passed['GrowthScore'] + 0.3 * passed['QualityScore']

    # 主题匹配加分（不超额外10分）
    passed['DoubleScore'] += passed['_s_主题匹配'] * 0.05

    # 非经常性损益扣分（以乘数方式作用于总分）
    # 非经常性损益占比越高，利润质量越差，折扣越大
    passed['DoubleScore'] = passed['DoubleScore'] * passed['_nonrecurring_discount']

    # Q1加速对比扣分（以乘数方式作用于总分）
    # 半年度预告增长率不及Q1实际增长率，说明业绩在减速，折扣越大
    passed['DoubleScore'] = passed['DoubleScore'] * passed['_q1_accel_discount']

    # ── 增强维度 v1.1：4 个乘数作用于总分 ──
    passed['DoubleScore'] = passed['DoubleScore'] * passed['_low_base_discount']  # 利润质量(低基数)
    passed['DoubleScore'] = passed['DoubleScore'] * passed['_theme_auth']         # 主题真实性
    passed['DoubleScore'] = passed['DoubleScore'] * passed['_cycle_mult']         # 景气周期乘数
    passed['DoubleScore'] = passed['DoubleScore'] * passed['_valuation_discount'] # 估值约束

    # ── V13 SustainableScore（持续成长分，6~24个月） ──
    # = 40%行业景气 + 30%成长持续性 + 20%竞争壁垒 + 10%估值安全
    # 与 DoubleScore(爆发弹性,1~6个月) 互补，两张榜单分别对应短中线与长期配置
    _industry_boom = pd.to_numeric(passed['产业景气'], errors='coerce').fillna(50)  # 行业景气(0-100)
    _tech_barrier = pd.to_numeric(passed['技术壁垒'], errors='coerce').fillna(50)  # 竞争壁垒(0-100)
    _val_safety = pd.to_numeric(passed['估值安全'], errors='coerce').fillna(50)    # 估值安全(0-100)
    # 成长持续性: 营收分0.5 + 3年CAGR分0.3 + 扣非利润同比分0.2（均为行业内标准化0-100）
    _growth_persist = (
        passed['_std_营收同比'].fillna(0) * 0.5 +
        passed['_std_3年利润CAGR'].fillna(0) * 0.3 +
        passed['_std_扣非利润同比'].fillna(0) * 0.2
    )
    passed['SustainableScore'] = (
        _industry_boom * 0.40 +
        _growth_persist * 0.30 +
        _tech_barrier * 0.20 +
        _val_safety * 0.10
    )
    passed['_s_成长持续性'] = _growth_persist

    # ── Step 3b: V14 机构成长版四大评分体系 ──
    # ① DoubleScore（短期爆发力 3~6个月）= 成长35% + PEG20% + 估值20% + 行业景气15% + 加速度10%
    #    成长 = (0.5×RevenueQuality + 0.5×利润质量修正分) × ProfitQualityPenalty
    _growth_v14 = (passed['RevenueQuality'].fillna(0) * 0.5 +
                   passed['_std_profit_v14'].fillna(0) * 0.5) * passed['_profit_quality_penalty']
    _val_v14 = np.minimum(100.0, passed['_upside_score_v14'].fillna(20) + passed['_value_bonus'].fillna(0))
    passed['DoubleScore'] = (_growth_v14 * V14_DOUBLE_WEIGHTS['成长'] +
                             passed['_peg_score_v14'].fillna(50) * V14_DOUBLE_WEIGHTS['PEG'] +
                             _val_v14 * V14_DOUBLE_WEIGHTS['估值'] +
                             passed['_cycle_score'].fillna(50) * V14_DOUBLE_WEIGHTS['行业景气'] +
                             passed['_accel_score'].fillna(50) * V14_DOUBLE_WEIGHTS['盈利加速度'])

    # ② SustainableScore（持续成长 1~3年）= 成长持续性35% + 行业景气25% + 盈利质量20% + ROE稳定性10% + 现金流10%
    _growth_persist_v14 = (passed['_std_rev_v14'].fillna(0) * 0.4 +
                           passed['_std_3年利润CAGR'].fillna(0) * 0.4 +
                           passed['_std_扣非利润同比'].fillna(0) * 0.2)
    _profit_quality_v14 = (passed['_s_扣非占比'].fillna(50) * 0.6 +
                           passed['_s_CFO'].fillna(50) * 0.4)
    passed['_s_盈利质量_v14'] = _profit_quality_v14
    _roe_stable_v14 = (passed['_s_ROE'].fillna(0) * 0.7 +
                       passed['_s_扣非占比'].fillna(50) * 0.3)
    passed['SustainableScore'] = (_growth_persist_v14 * V14_SUSTAIN_WEIGHTS['成长持续性'] +
                                  passed['_cycle_score'].fillna(50) * V14_SUSTAIN_WEIGHTS['行业景气'] +
                                  _profit_quality_v14 * V14_SUSTAIN_WEIGHTS['盈利质量'] +
                                  _roe_stable_v14 * V14_SUSTAIN_WEIGHTS['ROE稳定性'] +
                                  passed['_s_CFO'].fillna(50) * V14_SUSTAIN_WEIGHTS['现金流'])
    passed['_s_成长持续性'] = _growth_persist_v14

    # ③ MoatScore（竞争壁垒）V14.1 六维度 = 市场地位25 + 技术壁垒20 + 产品竞争力15 + 客户壁垒15 + 盈利能力15 + 成长护城河10
    passed['MoatScore'] = passed.apply(
        lambda r: _calc_moat_score(r, r.get('_std_市值份额', np.nan)), axis=1)
    passed['MoatLevel'] = passed['MoatScore'].apply(_calc_moat_level)
    passed['MoatExplain'] = passed.apply(
        lambda r: _calc_moat_explain(r, r.get('MoatScore', np.nan)), axis=1)

    # ④ RiskScore（风险 0~100，越高风险越大）
    passed['RiskScore'] = passed.apply(_calc_risk_score, axis=1)

    # FinalScore = Double×30% + Sustainable×35% + Moat×25% + (100-Risk)×10%
    passed['FinalScore'] = passed.apply(
        lambda r: _calc_final_score(r['DoubleScore'], r['SustainableScore'],
                                    r['MoatScore'], r['RiskScore']), axis=1)
    passed['推荐等级'] = passed['FinalScore'].apply(_recommend_level)

    # ── Step 4: 排序输出（V14 按 FinalScore 排序） ──
    result = passed.sort_values('FinalScore', ascending=False).reset_index(drop=True)

    # ── Step 4b: V15 Explain Engine（解释引擎，仅新增解释层，不参与评分） ──
    _calc_industry_rank(result, data)                 # IndustryRank / IndustryPercentile（基于全池）
    result['TopReasons'] = result.apply(_gen_top_reasons, axis=1)
    result['Weakness'] = result.apply(_gen_weakness, axis=1)
    result['LogicEvidence'] = result.apply(_gen_logic_evidence, axis=1)
    result['NextQuarterWatch'] = result.apply(_gen_next_quarter_watch, axis=1)
    result['InvestmentSummary'] = result.apply(_gen_investment_summary, axis=1)
    result['TopRisk'] = result.apply(_gen_top_risk, axis=1)
    result['Recommendation'] = result.apply(_calc_recommendation, axis=1)

    # 构建输出列（从 result 逐行取值，避免索引错位）
    output_rows = []
    for _, row in result.iterrows():
        output_rows.append({
            '代码': row['code'],
            '名称': row['name'],
            '主题': row['theme'],
            '市值(亿)': round(float(row['_s_流通市值_score_raw']), 1) if '_s_流通市值_score_raw' in result.columns else round(float(pd.to_numeric(row.get('市值(亿)', 0), errors='coerce') or 0), 1),
            '营收YoY%': round(float(pd.to_numeric(row.get('营收同比', 0), errors='coerce') or 0), 1),
            '利润YoY%': round(float(pd.to_numeric(row.get('利润同比', 0), errors='coerce') or 0), 1),
            'Q1利润YoY%': round(float(pd.to_numeric(row.get('Q1利润同比', 0), errors='coerce') or 0), 1),
            'ROE%': round(float(pd.to_numeric(row.get('ROE', 0), errors='coerce') or 0), 1),
            '毛利率%': round(float(pd.to_numeric(row.get('毛利率', 0), errors='coerce') or 0), 1),
            'PEG': '' if pd.isna(row['_peg_val']) else round(float(row['_peg_val']), 2),
            '估值空间%': round(float(row['_upside_val']), 1),
            '龙头类型': row['龙头类型'],
            '非经常损益%': row.get('非经常损益%', ''),
            '增强提示': row.get('_enhance_tags', ''),
            # V12.1 新增字段
            'ProfitQualityFactor': round(float(row['_profit_quality']), 2),
            'AdjustedProfitGrowth': round(float(row['_adjusted_profit_growth']), 1),
            'PEGScore': round(float(row['_peg_score_v14']), 1),
            'UpsideScore': round(float(row['_upside_score_v14']), 1),
            # V12.2 标准化评分（0~100，行业内标准化）
            '营收分': round(float(row.get('_std_营收同比', 0) or 0), 1),
            '利润分': round(float(row.get('_std__adjusted_profit_growth', 0) or 0), 1),
            '超预期分': round(float(row.get('_std_业绩超预期', 0) or 0), 1),
            'ROE分': round(float(row.get('_std_ROE', 0) or 0), 1),
            '毛利分': round(float(row.get('_std_毛利率', 0) or 0), 1),
            '研发分': round(float(row.get('_std_研发投入%', 0) or 0), 1),
            # V12.3 质量分 + V13 持续成长分
            'CFO分': round(float(row.get('_s_CFO', 0) or 0), 1),
            '扣非占比分': round(float(row.get('_s_扣非占比', 0) or 0), 1),
            'GrowthScore': round(float(row['GrowthScore']), 1),
            'QualityScore': round(float(row['QualityScore']), 1),
            'DoubleScore': round(float(row['DoubleScore']), 1),
            'SustainableScore': round(float(row['SustainableScore']), 1),
            # ── V14 机构成长版新增字段 ──
            'IndustryTheme': row.get('主营产业', ''),
            'ConceptTheme': row.get('概念主题', ''),
            'AdjustedProfitGrowthV14': round(float(row.get('_adjusted_profit_growth_v14', 0) or 0), 1),
            'RevenueQuality': round(float(row.get('RevenueQuality', 0) or 0), 1),
            'ProfitQualityPenalty': round(float(row.get('_profit_quality_penalty', 1.0) or 1.0), 2),
            'PEG_V14': '' if pd.isna(row.get('_peg_v14')) else round(float(row['_peg_v14']), 2),
            'ValueBonus': round(float(row.get('_value_bonus', 0) or 0), 1),
            'IndustryCycleScore': round(float(row.get('_cycle_score', 50) or 50), 1),
            '行业景气阶段': row.get('_cycle_label', ''),
            '加速度分': round(float(row.get('_accel_score', 50) or 50), 1),
            'MoatScore': round(float(row['MoatScore']), 1),
            'MoatLevel': row.get('MoatLevel', '★'),
            'MoatExplain': row.get('MoatExplain', ''),
            'RiskScore': round(float(row['RiskScore']), 1),
            'FinalScore': round(float(row['FinalScore']), 1),
            '推荐等级': row['推荐等级'],
            '行业景气': f"{round(float(row.get('_cycle_score', 50) or 50), 1)}({row.get('_cycle_label', '')})",
            '成长质量': round(float(row.get('_s_成长持续性', 0) or 0), 1),
            '盈利质量': round(float(row.get('_s_盈利质量_v14', 0) or 0), 1),
            '风险提示': _risk_tips(row),
            # ── V15 Explain Engine 新增字段 ──
            'TopReasons': row.get('TopReasons', ''),
            'Weakness': row.get('Weakness', ''),
            'LogicEvidence': row.get('LogicEvidence', ''),
            'NextQuarterWatch': row.get('NextQuarterWatch', ''),
            'InvestmentSummary': row.get('InvestmentSummary', ''),
            'TopRisk': row.get('TopRisk', ''),
            'IndustryRank': row.get('IndustryRank', ''),
            'IndustryPercentile': row.get('IndustryPercentile', ''),
            'Recommendation': row.get('Recommendation', ''),
        })
    output = pd.DataFrame(output_rows)

    # 净利润增长驱动因素分析（从半年预告数据中提炼核心原因）
    def _analyze_profit_driver(row):
        """分析净利润增长的核心驱动因素"""
        rev_yoy = float(pd.to_numeric(row.get('营收同比', 0), errors='coerce') or 0)
        profit_yoy = float(pd.to_numeric(row.get('利润同比', 0), errors='coerce') or 0)
        gross_margin = float(pd.to_numeric(row.get('毛利率', 0), errors='coerce') or 0)
        rd_ratio = float(pd.to_numeric(row.get('研发投入%', 0), errors='coerce') or 0)
        nr_ratio = float(pd.to_numeric(row.get('非经常损益%', 0), errors='coerce') or 0)

        if profit_yoy <= 20:
            return '利润增速较低'

        if nr_ratio > 20:
            return '非经常损益驱动'

        # 利润相对于营收的弹性（营收增速>5%才有意义）
        leverage = profit_yoy / rev_yoy if rev_yoy > 5 else None

        # ── 营收高增 (≥30%) ──
        if rev_yoy >= 30:
            if leverage is not None and leverage >= 2.0:
                if gross_margin >= 35:
                    return '高毛利产品放量'
                else:
                    return '规模效应显现'
            elif leverage is not None and leverage >= 1.2:
                return '收入增长+毛利率改善'
            else:
                return '收入高增长驱动'

        # ── 营收中增 (15~30%) ──
        if rev_yoy >= 15:
            if leverage is not None and leverage >= 2.0:
                if gross_margin >= 35 and rd_ratio >= 8:
                    return '技术产品升级驱动'
                elif gross_margin >= 35:
                    return '毛利率提升驱动'
                else:
                    return '规模效应驱动'
            else:
                return '收入增长驱动'

        # ── 营收低增或负增 (<15%) ──
        if profit_yoy > 50:
            if rev_yoy > 0:
                return '降本增效驱动'
            else:
                return '扭亏/减亏驱动'

        # 周期反转
        if profit_yoy > 500:
            return '低基数/周期反转'

        return '综合因素驱动'

    logic = []
    logic_stars = []
    # V14 核心逻辑: 一级逻辑（需求/产品/成本/周期/政策/一次性收益）
    #            + 二级逻辑 + 可信度星级（V12.3 基础上拆分升级）
    for _, row in result.iterrows():
        parts = []

        # 第一：V14 一级/二级逻辑分类
        primary, secondary, stars = _classify_logic_v14(row)
        parts.append(f'{primary}:{secondary}')

        # 第二：Q1加速对比（验证业绩趋势质量）
        q1_yoy = float(pd.to_numeric(row.get('Q1利润同比', 0), errors='coerce') or 0)
        if q1_yoy > 0:
            half_yoy = float(pd.to_numeric(row.get('利润同比', 0), errors='coerce') or 0)
            accel = half_yoy / q1_yoy if q1_yoy > 0 else 0
            if accel >= 1.5:
                parts.append(f'H1/Q1加速{accel:.1f}x')
            elif accel <= 0.6:
                parts.append(f'H1减速{accel:.1f}x')

        # 第三：非经常性损益预警（仅正值扣分才显示）
        nr_val = float(pd.to_numeric(row.get('非经常损益%', 0), errors='coerce') or 0)
        if nr_val > 20:
            parts.append(f'非经常扣分{row.get("_nonrecurring_discount", 1.0):.0%}')

        logic.append(f"{stars} {' | '.join(parts) if parts else '估值修复'}")
        logic_stars.append(stars)
    output['核心逻辑'] = logic
    output['核心逻辑可信度'] = logic_stars

    # 标注否决原因
    output['_否决'] = ''
    for i, row in output.iterrows():
        code = row['代码']
        vrow = data[data['code'] == code]
        if len(vrow) > 0:
            vr = vrow.iloc[0]
            reasons = []
            # V15.2: 已去掉一票否决，全量参与评分，不再标注否决原因
            output.at[i, '_否决'] = '; '.join(reasons)

    return output


def print_top(result: pd.DataFrame, n: int = 15):
    """打印 TOP N 结果（V15：FinalScore 总榜 + 研报式解读 + Moat 壁垒榜 + Risk 风险榜 + Sustainable 榜）"""
    display_cols = ['代码', '名称', '主题', '市值(亿)', '营收YoY%', '利润YoY%', 'Q1利润YoY%',
                    'ROE%', '毛利率%', 'PEG', 'PEGScore', '估值空间%', 'UpsideScore', '龙头类型',
                    'DoubleScore', 'SustainableScore', 'MoatScore', 'RiskScore', 'FinalScore',
                    '推荐等级', 'Recommendation', 'IndustryRank', '增强提示', '核心逻辑',
                    '营收分', '利润分', '超预期分', 'ROE分', '毛利分', '研发分', 'CFO分', '扣非占比分']
    cols = [c for c in display_cols if c in result.columns]

    print(f"\n{'═'*120}")
    print(f"  🏆 翻倍黑马综合评分 TOP {min(n, len(result))}（V15 机构成长版，按 FinalScore 排序）")
    print(f"{'═'*120}")
    header = ' | '.join([f'{c:>8}' for c in cols])
    print(f'  {"排名":>3} {header}')
    print(f'  {"─"*116}')

    for i, (_, row) in enumerate(result.head(n).iterrows(), 1):
        vals = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                vals.append(f'{v:>8.1f}')
            else:
                vals.append(f'{str(v):>8}')
        print(f'  {i:>3}  {"  ".join(vals)}')

    # ── V15: 研报式解读榜（TopReasons/LogicEvidence/InvestmentSummary/NextQuarterWatch/TopRisk） ──
    if 'TopReasons' in result.columns:
        print(f"\n{'═'*120}")
        print(f"  📋 V15 研报式解读 TOP {min(n, len(result))}（为什么排第一 + 证据 + 下季验证 + 风险）")
        print(f"{'═'*120}")
        for i, (_, row) in enumerate(result.head(min(n, 10)).iterrows(), 1):
            print(f"  ─ {'─'*116}")
            print(f"  {i:>2}. {row['代码']} {row['名称']}  [{'/'.join([row.get('行业景气', ''), row.get('IndustryRank', '')])}]  {row.get('Recommendation', '')}")
            print(f"      为什么高: {row.get('TopReasons', '')}")
            print(f"      拖累因素: {row.get('Weakness', '')}")
            print(f"      财报证据: {row.get('LogicEvidence', '')}")
            print(f"      投资逻辑: {row.get('InvestmentSummary', '')}")
            print(f"      下季验证: {row.get('NextQuarterWatch', '')}")
            print(f"      最大风险: {row.get('TopRisk', '')}")

    # ── V15: MoatScore 竞争壁垒榜 ──
    if 'MoatScore' in result.columns:
        moat_cols = ['代码', '名称', '主题', 'MoatLevel', '龙头类型', '研发投入%', '毛利率%', 'ROE%',
                     'MoatScore', 'MoatExplain']
        moat_cols = [c for c in moat_cols if c in result.columns]
        moat = result.sort_values('MoatScore', ascending=False).head(n)
        print(f"\n{'═'*120}")
        print(f"  🛡️ V15 MoatScore 行业护城河榜 TOP {min(n, len(moat))}（行业模板评分，长期竞争优势 3~10年）")
        print(f"{'═'*120}")
        header = ' | '.join([f'{c:>8}' for c in moat_cols if c != 'MoatExplain'])
        print(f'  {"排名":>3} {header}  MoatExplain')
        print(f'  {"─"*116}')
        for i, (_, row) in enumerate(moat.iterrows(), 1):
            vals = []
            for c in moat_cols:
                if c == 'MoatExplain':
                    continue
                v = row[c]
                if isinstance(v, float):
                    vals.append(f'{v:>8.1f}')
                else:
                    vals.append(f'{str(v)[:8]:>8}')
            explain = str(row.get('MoatExplain', ''))[:70]
            print(f'  {i:>3}  {"  ".join(vals)}  {explain}')

    # ── V14: RiskScore 风险榜（提示性，风险越高越靠前） ──
    if 'RiskScore' in result.columns:
        risk_cols = ['代码', '名称', '主题', 'RiskScore', '资产负债率%', '商誉占比%', '应收增速%', '存货增速%',
                     '股东数变化%', '解禁占比%', 'FinalScore', '风险提示']
        risk_cols = [c for c in risk_cols if c in result.columns]
        risk = result.sort_values('RiskScore', ascending=False).head(min(n, 10))
        print(f"\n{'═'*120}")
        print(f"  ⚠️ V14 RiskScore 风险榜 TOP {min(n, len(risk))}（风险越高越需警惕）")
        print(f"{'═'*120}")
        header = ' | '.join([f'{c:>8}' for c in risk_cols])
        print(f'  {"排名":>3} {header}')
        print(f'  {"─"*116}')
        for i, (_, row) in enumerate(risk.iterrows(), 1):
            vals = []
            for c in risk_cols:
                v = row[c]
                if isinstance(v, float):
                    vals.append(f'{v:>8.1f}')
                else:
                    vals.append(f'{str(v):>8}')
            print(f'  {i:>3}  {"  ".join(vals)}')

    # ── V13 兼容: SustainableScore 排行榜（持续成长, 6~24个月） ──
    if 'SustainableScore' in result.columns:
        sus_cols = ['代码', '名称', '主题', '产业景气', '技术壁垒', '估值安全', '营收YoY%', '利润YoY%',
                    '3年利润CAGR', '扣非利润同比', 'SustainableScore', 'DoubleScore', '核心逻辑']
        sus_cols = [c for c in sus_cols if c in result.columns]
        sus = result.sort_values('SustainableScore', ascending=False).head(n)
        print(f"\n{'═'*120}")
        print(f"  🌱 V14 SustainableScore 持续成长榜 TOP {min(n, len(sus))}（1~3年 配置维度）")
        print(f"{'═'*120}")
        header = ' | '.join([f'{c:>8}' for c in sus_cols])
        print(f'  {"排名":>3} {header}')
        print(f'  {"─"*116}')
        for i, (_, row) in enumerate(sus.iterrows(), 1):
            vals = []
            for c in sus_cols:
                v = row[c]
                if isinstance(v, float):
                    vals.append(f'{v:>8.1f}')
                else:
                    vals.append(f'{str(v):>8}')
            print(f'  {i:>3}  {"  ".join(vals)}')

    # 否决统计
    vetoed_count = result[result['_否决'] != ''].shape[0]
    if vetoed_count > 0:
        print(f"\n  ⚠️ 以下标的虽在通过名单中，但原始数据有否决标记（可能因数据源差异）:")
        for _, row in result[result['_否决'] != ''].head(5).iterrows():
            print(f'    {row["代码"]} {row["名称"]}: {row["_否决"]}')


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────
def _export_full_csv(result: pd.DataFrame, full_df: pd.DataFrame, out_path: Path) -> None:
    """
    V15 全量 CSV 导出（861 只全部保留并纳入评分）：
    - 对全池每只股票生成 Explain Engine 解释字段（TopReasons/Weakness/LogicEvidence/
      NextQuarterWatch/InvestmentSummary/TopRisk/MoatExplain）
    - 全量股票并入 FinalScore/DoubleScore/SustainableScore/MoatScore/RiskScore/
      Recommendation 等评分列（V15.2 已去掉一票否决）
    - 行业内排名基于全池
    """
    full = full_df.copy()
    if 'code' in full.columns and '代码' not in full.columns:
        full['代码'] = full['code']
    full['_code'] = full['代码'].apply(_normalize_code)

    # ① 全池解释字段（不依赖评分，仅依赖原始财务/行业字段）
    full['TopReasons'] = full.apply(_gen_top_reasons, axis=1)
    full['Weakness'] = full.apply(_gen_weakness, axis=1)
    full['LogicEvidence'] = full.apply(_gen_logic_evidence, axis=1)
    full['NextQuarterWatch'] = full.apply(_gen_next_quarter_watch, axis=1)
    full['InvestmentSummary'] = full.apply(_gen_investment_summary, axis=1)
    full['TopRisk'] = full.apply(_gen_top_risk, axis=1)

    # ② 通过股票评分并入（按代码对齐）
    res = result.copy()
    res['_code'] = res['代码'].apply(_normalize_code)
    score_cols = ['DoubleScore', 'SustainableScore', 'MoatScore', 'MoatLevel', 'MoatExplain',
                  'RiskScore', 'FinalScore', '推荐等级', 'Recommendation', '行业景气',
                  '核心逻辑', '增强提示', '龙头类型', '主题',
                  '营收分', '利润分', '超预期分', 'ROE分', '毛利分', '研发分', 'CFO分', '扣非占比分']
    score_cols = [c for c in score_cols if c in res.columns]
    merge = full.merge(res[['_code'] + score_cols], on='_code', how='left')

    # ③ 行业排名（全池）
    _calc_industry_rank(merge, merge)

    # ④ V15.2: 已去掉一票否决，全量评分，不再标注否决原因/置空评分
    passed_mask = merge['FinalScore'].notna()

    # ⑤ 保存（保留全部原始列 + 新字段，按 FinalScore 降序）
    merge = merge.sort_values(['FinalScore'], ascending=False, na_position='last')
    drop_tmp = [c for c in ['_code', '_ind_rank', '_ind_count'] if c in merge.columns]
    merge = merge.drop(columns=drop_tmp)
    merge.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f'✅ 全量 CSV 已导出: {out_path}（{len(merge)} 只，全部纳入评分 {int(passed_mask.sum())} 只）')


if __name__ == '__main__':
    csv_path = Path(__file__).parent.parent / 'report_daily' / 'bull_stocks_all.csv'
    result = run_double_score(csv_path=str(csv_path))
    print_top(result, n=20)

    # 保存（通过一票否决的评分结果，62 列含 V15 解释字段）
    out_path = Path(__file__).parent.parent / 'report_daily' / 'double_score_top.csv'
    result.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f'\n✅ 结果已保存: {out_path}')

    # V15 全量 CSV（861 只全部保留，含被否决股票与解释字段）
    full_df = pd.read_csv(csv_path, encoding='utf-8-sig')
    full_path = Path(__file__).parent.parent / 'report_daily' / 'double_score_full.csv'
    _export_full_csv(result, full_df, full_path)