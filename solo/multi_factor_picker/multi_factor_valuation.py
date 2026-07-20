import pandas as pd
import numpy as np


def _normalize_code(code) -> str:
    code_str = str(code).strip()
    if code_str.isdigit() and len(code_str) < 6:
        code_str = code_str.zfill(6)
    return code_str


INDUSTRY_PE_MAP = {
    '火电': 11.0, '电力链': 11.0, '红利公用事业': 12.0, '公用事业': 12.0,
    '建筑央企': 7.5, '建筑装饰': 8.0, '基建地产链': 8.0, '建筑': 8.0,
    '交通运输物流': 10.0, '航空运输': 10.0, '铁路运输': 12.0,
    '钢铁': 9.0, '商用车': 12.0, '汽车零部件': 18.0, '汽车': 15.0,
    '新能源汽车链': 18.0, '新能源车': 18.0, '锂电上游': 15.0, '固态电池': 25.0,
    '有色金属': 15.0, '环保': 18.0, '化纤化工': 15.0, '化工农药链': 15.0, '化工材料': 15.0,
    '创新药': 35.0, '医药产业链': 28.0, '中药': 22.0, '医疗服务': 28.0, '医药': 28.0,
    'AI芯片': 35.0, 'AI算力': 32.0, 'AI文娱内容': 30.0, 'AI新消费': 25.0, 'AI应用': 30.0,
    '光模块': 30.0, 'PCB': 28.0, '消费白马': 25.0, '军工': 30.0, '航天军工': 30.0,
    '金融科技': 22.0, '数据要素': 25.0, '机器人': 30.0, '人形机器人': 30.0,
    '软件与IT服务': 22.0, '消费电子': 25.0, '半导体': 35.0, '存储芯片': 35.0,
    '餐饮食品链': 20.0, '必选消费红利链': 20.0, '纺织服饰': 18.0, '食品饮料': 22.0,
    '工程机械与重型装备': 12.0, '石油石化': 10.0, '电力设备': 18.0, '特高压': 18.0,
    '银行': 6.0, '券商': 15.0, '保险': 8.0, '多元金融': 12.0,
    '家电家居链': 14.0, '家用电器': 14.0, '新能源': 20.0, '玻璃建材': 12.0,
    '创新药/生物技术': 35.0, '医疗器械': 25.0, '生物医药': 28.0,
    '石油': 8.0, '煤炭': 8.0, '煤炭开采': 8.0, '石油天然气': 8.0,
    '电子': 25.0, '通信': 22.0, '机械': 18.0, '造纸轻工': 15.0,
    '商超零售链': 18.0, '零售': 15.0, '旅游酒店': 20.0, '航空': 15.0,
    '大农业': 15.0, '农业': 15.0, '畜牧业': 12.0,
    '水电': 12.0, '水务': 12.0, '燃气': 12.0, '高速公路': 10.0, '港口': 10.0,
    '轨交设备': 15.0, '船舶制造': 20.0, '建材': 10.0, '水泥': 8.0,
    '锂电设备': 20.0, '光伏': 15.0, '风电': 15.0, '氢能': 25.0,
    '半导体设备': 35.0, '半导体材料': 35.0, '低空经济': 35.0, '商业航天': 35.0,
    '工业互联网': 25.0, '云计算': 30.0, '大数据': 25.0, '人工智能': 35.0,
    '储能': 22.0, '核能核电': 18.0, '智能驾驶': 30.0,
}
DEFAULT_INDUSTRY_PE = 18.0


def lookup_industry_pe(theme: str, industry: str = '') -> float:
    target = (theme or '') + (industry or '')
    if theme and theme in INDUSTRY_PE_MAP:
        return INDUSTRY_PE_MAP[theme]
    for kw, pe in sorted(INDUSTRY_PE_MAP.items(), key=lambda x: -len(x[0])):
        if kw in target:
            return pe
    return DEFAULT_INDUSTRY_PE


def run_multifactor_selection(df: pd.DataFrame) -> pd.DataFrame:
    """
    多因子重估与选股系统

    Step 1: 行业动态 PE 映射 → 获取行业基准 PE
    Step 2: 基于净利润增速 (YoY%) 的动态 PE 修正
    Step 3: Bear/Base/Bull 三态估值空间计算
    Step 4: PEG 因子计算与安全风控拦截
    Step 5: 综合多因子打分 (Composite Score)

    Parameters
    ----------
    df : pd.DataFrame
        输入数据，需包含字段:
        code, name, theme, pe_ttm, current_price, net_profit_yoy
        (可选: chip_score, safety_score)

    Returns
    -------
    pd.DataFrame
        按 composite_score 降序排列，含估值空间/PEG/综合分等
    """
    data = df.copy()

    required_cols = ['code', 'name', 'theme', 'pe_ttm', 'current_price', 'net_profit_yoy']
    for col in required_cols:
        if col not in data.columns:
            raise ValueError(f"缺少必要字段: {col}")

    data['code'] = data['code'].apply(_normalize_code)

    data['pe_ttm'] = pd.to_numeric(data['pe_ttm'], errors='coerce')
    data['current_price'] = pd.to_numeric(data['current_price'], errors='coerce')
    data['net_profit_yoy'] = pd.to_numeric(data['net_profit_yoy'], errors='coerce')
    data['chip_score'] = pd.to_numeric(data.get('chip_score', pd.Series(50.0, index=data.index)), errors='coerce').fillna(50)
    data['safety_score'] = pd.to_numeric(data.get('safety_score', pd.Series(50.0, index=data.index)), errors='coerce').fillna(50)
    data['industry'] = data.get('industry', '').fillna('')

    results = []
    for idx, row in data.iterrows():
        try:
            code = row['code']
            name = row['name']
            theme = str(row.get('theme', ''))
            industry = str(row.get('industry', ''))
            pe_ttm = float(row['pe_ttm'])
            price = float(row['current_price'])
            net_profit_yoy = float(row['net_profit_yoy'])
            chip_score = float(row['chip_score'])
            safety_score = float(row['safety_score'])
        except (ValueError, TypeError):
            continue

        # Step 1: 行业基准 PE
        base_pe = lookup_industry_pe(theme, industry)

        # Step 2: 基于成长的动态 PE 修正
        g = net_profit_yoy
        k = np.clip(g / 100.0, -0.3, 0.5)
        pe_adjusted = base_pe * (1.0 + k)

        # pe_adjusted 行业封顶：防止高增速导致传统行业PE过度膨胀
        HIGH_TECH_SET = {'AI芯片', '创新药', '光模块', 'AI算力', 'AI应用', 'AI文娱内容',
                         '半导体', '存储芯片', '半导体设备', '半导体材料', '低空经济',
                         '商业航天', '人工智能', '机器人', '人形机器人', '云计算', 'AI新消费'}
        MID_TECH_SET = {'环保', '金融科技', '医药产业链', '工业金属', '有色金属',
                        '医疗服务', '中药', '医药', '生物医药', '医疗器械', '消费电子',
                        '数据要素', '软件与IT服务', 'PCB', '军工', '航天军工',
                        '储能', '固态电池', '氢能', '智能驾驶', '电力设备', '特高压',
                        '锂电设备', '船舶制造', '大数据', '工业互联网', '消费白马'}
        if theme in HIGH_TECH_SET:
            pe_adjusted = min(pe_adjusted, 42.0)
        elif theme in MID_TECH_SET:
            pe_adjusted = min(pe_adjusted, 28.0)

        # Step 3: 三态估值空间
        if not (3.0 <= pe_ttm <= 200.0) or price <= 0:
            results.append({
                'code': code, 'name': name, 'theme': theme,
                'pe_ttm': round(pe_ttm, 1), 'net_profit_yoy': round(net_profit_yoy, 1),
                'peg': 99.0, 'pe_adjusted': round(pe_adjusted, 1),
                'realistic_upside_%': 0.0, 'composite_score': 0.0,
                'filter_reason': 'PE无效或价格无效',
            })
            continue

        eps = price / pe_ttm
        bear_pe = pe_adjusted * 0.75
        bull_pe = pe_adjusted * 1.25

        bear_price = eps * bear_pe
        base_price = eps * pe_adjusted
        bull_price = eps * bull_pe

        weighted_target = bear_price * 0.25 + base_price * 0.50 + bull_price * 0.25
        upside = (weighted_target - price) / price * 100.0
        upside = max(upside, -80.0)

        # Step 4: PEG 与安全风控
        if net_profit_yoy > 0:
            peg = pe_ttm / max(net_profit_yoy, 0.01)
        else:
            peg = 99.0
        peg_high_attr = peg < 0.8

        # 拦截规则：PEG>2.0 且增速<5% → 价值陷阱
        is_value_trap = peg > 2.0 and net_profit_yoy < 5.0
        if is_value_trap:
            results.append({
                'code': code, 'name': name, 'theme': theme,
                'pe_ttm': round(pe_ttm, 1), 'net_profit_yoy': round(net_profit_yoy, 1),
                'peg': round(peg, 2), 'pe_adjusted': round(pe_adjusted, 1),
                'realistic_upside_%': round(upside, 1),
                'composite_score': 0.0,
                'filter_reason': '价值陷阱',
            })
            continue

        # Step 5: 综合多因子打分
        # S_upside: 估值空间 Min-Max 映射到 0~100
        all_upsides = [r['realistic_upside_%'] for r in results
                       if 'realistic_upside_%' in r and r['filter_reason'] in ('', '通过')]
        temp_upside = upside
        s_upside = max(0, min(100, (temp_upside + 20) / 50 * 100))

        # S_peg: PEG 越小越好，PEG<0.5=100, PEG>3=0
        s_peg = max(0, min(100, (3.0 - min(peg, 3.0)) / 2.5 * 100))

        # S_chip / S_safety: 直接用输入值
        s_chip = chip_score
        s_safety = safety_score

        composite = 0.35 * s_upside + 0.25 * s_peg + 0.20 * s_chip + 0.20 * s_safety

        features = []
        if is_value_trap:
            features.append('价值陷阱')
        if peg_high_attr:
            features.append('高PEG性价比')

        results.append({
            'code': code, 'name': name, 'theme': theme,
            'pe_ttm': round(pe_ttm, 1), 'net_profit_yoy': round(net_profit_yoy, 1),
            'peg': round(peg, 2), 'pe_adjusted': round(pe_adjusted, 1),
            'realistic_upside_%': round(upside, 1),
            'composite_score': round(composite, 1),
            'filter_reason': '通过',
            'features': ';'.join(features),
        })

    result_df = pd.DataFrame(results)

    # 按综合分降序排列
    result_df = result_df.sort_values('composite_score', ascending=False).reset_index(drop=True)

    # 保留展示列
    display_cols = ['code', 'name', 'theme', 'pe_ttm', 'net_profit_yoy',
                    'peg', 'pe_adjusted', 'realistic_upside_%', 'composite_score',
                    'filter_reason', 'features']
    display_cols = [c for c in display_cols if c in result_df.columns]
    return result_df[display_cols]