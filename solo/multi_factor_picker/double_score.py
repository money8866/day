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
                    'ROE%', '毛利率%', 'PEG', '估值空间%', '龙头类型', 'DoubleScore', '核心逻辑']
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