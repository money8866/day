"""
翻倍黑马综合评分系统 (DoubleScore v1.0)

从 40+ 维度中提取 12 个核心因子，计算综合得分，筛选具备翻倍潜力的标的。
应用一票否决机制：PEG>1.2、估值空间<30%、扣非利润为负 直接剔除。
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

    # ── Step 1: 强制过滤（一票否决） ──
    # PEG > 1.2 → 剔除
    data['_peg_val'] = pd.to_numeric(data['PEG'], errors='coerce').fillna(99.0)
    data['_peg_val'] = data['_peg_val'].clip(0, 99)
    mask_peg = data['_peg_val'] > 1.2

    # 估值空间 < 30% → 剔除（注意：原始估值空间%是旧模型的，用新的多因子估值空间替换）
    data['_upside_val'] = pd.to_numeric(data['估值空间%'], errors='coerce').fillna(-999)
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
    # 1. 营收同比
    rev = pd.to_numeric(passed['营收同比'], errors='coerce').fillna(0)
    passed['_s_营收同比'] = rev.apply(lambda x: _score_one(x, 30.0, 80.0, True))

    # 2. 利润同比
    prof = pd.to_numeric(passed['利润同比'], errors='coerce').fillna(0)
    passed['_s_利润同比'] = prof.apply(lambda x: _score_one(x, 50.0, 150.0, True))

    # 3. 业绩超预期 (PEAD) — 使用预告变动%，若超预期>20%给满分
    pead = pd.to_numeric(passed['业绩超预期'], errors='coerce').fillna(0)
    passed['_s_业绩超预期'] = pead.apply(lambda x: 100.0 if x > 20.0 else _score_one(x, 0, 20.0, True))

    # 4. ROE
    roe = pd.to_numeric(passed['ROE'], errors='coerce').fillna(0)
    passed['_s_ROE'] = roe.apply(lambda x: _score_one(x, 15.0, 25.0, True))

    # 5. 毛利率
    gm = pd.to_numeric(passed['毛利率'], errors='coerce').fillna(0)
    passed['_s_毛利率'] = gm.apply(lambda x: _score_one(x, 30.0, 60.0, True))

    # 6. 研发投入
    rd = pd.to_numeric(passed['研发投入%'], errors='coerce').fillna(0)
    passed['_s_研发投入'] = rd.apply(lambda x: _score_one(x, 8.0, 15.0, True))

    # 7. PEG（越低越好，PEG<0.8满分，PEG>1.2否决）
    peg = passed['_peg_val']
    passed['_s_PEG'] = peg.apply(lambda x: _score_one(x, 0.0, 1.2, False))

    # 8. 估值空间（>=80%满分）
    ups = passed['_upside_val']
    passed['_s_估值空间'] = ups.apply(lambda x: _score_one(x, 30.0, 80.0, True))

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

    # ── Step 3: 加权综合得分 ──
    passed['DoubleScore'] = (
        passed['_s_营收同比'] * 0.10 +
        passed['_s_利润同比'] * 0.15 +
        passed['_s_业绩超预期'] * 0.10 +
        passed['_s_ROE'] * 0.10 +
        passed['_s_毛利率'] * 0.08 +
        passed['_s_研发投入'] * 0.07 +
        passed['_s_PEG'] * 0.15 +
        passed['_s_估值空间'] * 0.10 +
        passed['_s_流通市值'] * 0.05 +
        passed['_s_机构动向'] * 0.05 +
        passed['_s_龙头类型'] * 0.05
    )

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
            'PEG': round(float(row['_peg_val']), 2),
            '估值空间%': round(float(row['_upside_val']), 1),
            '龙头类型': row['龙头类型'],
            '非经常损益%': row.get('非经常损益%', ''),
            '增强提示': row.get('_enhance_tags', ''),
            'DoubleScore': round(float(row['DoubleScore']), 1),
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

        logic.append(' | '.join(parts) if parts else '估值修复')
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
                if vr['_peg_val'] > 1.2:
                    reasons.append(f'PEG={vr["_peg_val"]:.1f}>1.2')
                if vr['_upside_val'] < 30:
                    reasons.append(f'估值空间={vr["_upside_val"]:.1f}%<30%')
                if vr['_profit_val'] < 0:
                    reasons.append(f'利润同比={vr["_profit_val"]:.1f}%<0')
                output.at[i, '_否决'] = '; '.join(reasons)

    return output


def print_top(result: pd.DataFrame, n: int = 15):
    """打印 TOP N 结果"""
    display_cols = ['代码', '名称', '主题', '市值(亿)', '营收YoY%', '利润YoY%', 'Q1利润YoY%',
                    'ROE%', '毛利率%', 'PEG', '估值空间%', '龙头类型', '增强提示', 'DoubleScore', '核心逻辑']
    cols = [c for c in display_cols if c in result.columns]

    print(f"\n{'='*120}")
    print(f"  🏆 翻倍黑马综合评分 TOP {min(n, len(result))}")
    print(f"{'='*120}")
    header = ' | '.join([f'{c:>8}' for c in cols])
    print(f'  {"排名":>3} {header}')
    print(f'  {"-"*116}')

    for i, (_, row) in enumerate(result.head(n).iterrows(), 1):
        vals = []
        for c in cols:
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