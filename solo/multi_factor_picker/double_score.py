"""
翻倍黑马综合评分系统 (DoubleScore V13)

从 40+ 维度中提取核心因子，计算综合得分，筛选具备翻倍潜力的标的。
应用一票否决机制：PEG>1.2、估值空间<30%、扣非利润为负 直接剔除。

V12.2 标准化评分体系（成长因子）:
    原始财务数据 → 缩尾(1%/99%) → 对数压缩(signed-log1p) → 行业内Z-Score
    → clip[-2.5,2.5] → 0-100分 → 按权重融合
    解决极端同比数据、低基数效应、不同量纲不可比，与主题轮动系统同量纲融合。

V12.3 优化（机构研究框架）:
    1. PEG 改用 FutureGrowth = 0.5×营收YoY + 0.3×扣非利润YoY + 0.2×3年利润CAGR，
       解决低基数利润同比导致 PEG≈0 的失真。
    2. 新增质量因子 QualityScore = 0.4×ROE + 0.3×CFO + 0.3×扣非占比，
       DoubleScore = 0.7×GrowthScore + 0.3×QualityScore。
    3. 核心逻辑增加可信度星级（★★★★★~★☆☆☆☆）。

V13 双榜单:
    - DoubleScore 排行榜（1~6个月爆发弹性）
    - SustainableScore 持续成长榜（6~24个月配置）= 40%行业景气 + 30%成长持续性
      + 20%竞争壁垒 + 10%估值安全
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

    # ── Step 1: 强制过滤（一票否决） ──
    # PEG > 1.2 → 剔除（使用 V12.1 重新计算的 PEG；PE<=0 时 PEG=None 不否决）
    data['_peg_val'] = data['_peg_new']
    data['_peg_na'] = data['_peg_val'].isna()
    data['_peg_eff'] = data['_peg_val'].where(data['_peg_na'] == False, 0.0)
    mask_peg = (data['_peg_na'] == False) & (data['_peg_val'] > 1.2)

    # 估值空间 < 30% → 剔除（使用限幅后的 Upside）
    data['_upside_val'] = data['_upside_capped'].fillna(-999)
    mask_upside = data['_upside_val'] < 30.0

    # 利润为负（利润同比 < 0 且 利润同比 严重负值）→ 剔除
    data['_profit_val'] = pd.to_numeric(data['利润同比'], errors='coerce').fillna(-999)
    mask_negative = data['_profit_val'] < 0

    # 合并否决
    data['_vetoed'] = mask_peg | mask_upside | mask_negative

    passed = data[~data['_vetoed']].copy()
    vetoed = data[data['_vetoed']].copy()

    print(f"一票否决统计:")
    print(f"  总样本: {len(data)}")
    if mask_peg.any():
        print(f"  ❌ PEG>1.2: {mask_peg.sum()} 只")
    if mask_upside.any():
        print(f"  ❌ 估值空间<30%: {mask_upside.sum()} 只")
    if mask_negative.any():
        print(f"  ❌ 利润同比<0: {mask_negative.sum()} 只")
    print(f"  ✅ 通过: {len(passed)} 只")

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

    # ── Step 4: 排序输出 ──
    result = passed.sort_values('DoubleScore', ascending=False).reset_index(drop=True)

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
            'PEGScore': round(float(row['_peg_score']), 1),
            'UpsideScore': round(float(row['_upside_score']), 1),
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
    # 核心逻辑 → 可信度星级（V12.3）
    # 逻辑可信度越高星级越高: 高毛利产品放量/技术升级=高可信,
    # 非经常损益/低基数=低可信, 帮助一眼识别"有多确定"
    DRIVER_STARS = {
        '高毛利产品放量': '★★★★★',
        '技术产品升级驱动': '★★★★★',
        '规模效应显现': '★★★★☆',
        '规模效应驱动': '★★★★☆',
        '收入增长+毛利率改善': '★★★★☆',
        '毛利率提升驱动': '★★★★☆',
        '降本增效驱动': '★★★★☆',
        '收入高增长驱动': '★★★★☆',
        '收入增长驱动': '★★★☆☆',
        '综合因素驱动': '★★★☆☆',
        '估值修复': '★★★☆☆',
        '扭亏/减亏驱动': '★★☆☆☆',
        '低基数/周期反转': '★★☆☆☆',
        '利润增速较低': '★★☆☆☆',
        '非经常损益驱动': '★☆☆☆☆',
    }
    for _, row in result.iterrows():
        parts = []

        # 第一：核心驱动因素（最主要的标签）
        driver = _analyze_profit_driver(row)
        parts.append(driver)

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

        stars = DRIVER_STARS.get(driver, '★★★☆☆')
        logic.append(f"{stars} {' | '.join(parts) if parts else '估值修复'}")
    output['核心逻辑'] = logic

    # 标注否决原因
    output['_否决'] = ''
    for i, row in output.iterrows():
        code = row['代码']
        vrow = data[data['code'] == code]
        if len(vrow) > 0:
            vr = vrow.iloc[0]
            reasons = []
            if vr.get('_vetoed', False):
                if not vr['_peg_na'] and vr['_peg_val'] > 1.2:
                    reasons.append(f'PEG={vr["_peg_val"]:.1f}>1.2')
                if vr['_upside_val'] < 30:
                    reasons.append(f'估值空间={vr["_upside_val"]:.1f}%<30%')
                if vr['_profit_val'] < 0:
                    reasons.append(f'利润同比={vr["_profit_val"]:.1f}%<0')
                output.at[i, '_否决'] = '; '.join(reasons)

    return output


def print_top(result: pd.DataFrame, n: int = 15):
    """打印 TOP N 结果（V13：双榜单 — DoubleScore 爆发榜 + SustainableScore 持续成长榜）"""
    display_cols = ['代码', '名称', '主题', '市值(亿)', '营收YoY%', '利润YoY%', 'AdjustedProfitGrowth', 'Q1利润YoY%',
                    'ROE%', '毛利率%', 'PEG', 'PEGScore', '估值空间%', 'UpsideScore', '龙头类型', '增强提示', 'DoubleScore',
                    'GrowthScore', 'QualityScore', 'SustainableScore', '核心逻辑',
                    '营收分', '利润分', '超预期分', 'ROE分', '毛利分', '研发分', 'CFO分', '扣非占比分']
    cols = [c for c in display_cols if c in result.columns]

    print(f"\n{'═'*120}")
    print(f"  🏆 翻倍黑马综合评分 TOP {min(n, len(result))}（V13 双榜单）")
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

    # ── V13: SustainableScore 排行榜（持续成长, 6~24个月） ──
    if 'SustainableScore' in result.columns:
        sus_cols = ['代码', '名称', '主题', '产业景气', '技术壁垒', '估值安全', '营收YoY%', '利润YoY%',
                    '3年利润CAGR', '扣非利润同比', 'SustainableScore', 'DoubleScore', '核心逻辑']
        sus_cols = [c for c in sus_cols if c in result.columns]
        sus = result.sort_values('SustainableScore', ascending=False).head(n)
        print(f"\n{'═'*120}")
        print(f"  🌱 V13 SustainableScore 持续成长榜 TOP {min(n, len(sus))}（6~24个月 配置维度）")
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
if __name__ == '__main__':
    csv_path = Path(__file__).parent.parent / 'report_daily' / 'bull_stocks_all.csv'
    result = run_double_score(csv_path=str(csv_path))
    print_top(result, n=15)

    # 保存
    out_path = Path(__file__).parent.parent / 'report_daily' / 'double_score_top.csv'
    result.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f'\n✅ 结果已保存: {out_path}')