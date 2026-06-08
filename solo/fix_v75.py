# 修复V7.5评分低的问题
with open(r'd:\mystock\solo\tushare_quant.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_v75 = '''    # =========================
    # 1. 主题真实性（theme_confidence）
    # =========================
    theme_confidence = calc_theme_confidence(stock_info, theme) if theme else 30
    # 归一化到0-1范围
    theme_confidence_01 = theme_confidence / 100.0

    # =========================
    # 2. 龙头因子（leader_factor）
    # 基于趋势强度，资金动量和趋势概率
    # =========================
    leader_factor = (
        trend_strength * 0.40 +
        money_momentum * 0.35 +
        trend_probability * 0.25
    )

    # =========================
    # 3. 主题排名加成（如果股票是主题核心公司）
    # =========================
    theme_rank_bonus = 0
    if stock_info and theme:
        # 检查是否是核心公司
        try:
            cfg_path = os.path.join(BASE_DIR, 'theme.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    theme_cfg = json.load(f).get('HOT_THEMES', {})
                
                if theme in theme_cfg:
                    core_companies = theme_cfg[theme].get('core_companies', [])
                    if ts_code in core_companies:
                        theme_rank_bonus = 0.2  # 核心公司额外0.2分
        except:
            pass

    # =========================
    # 4. 计算V7.5综合评分
    # =========================
    # 基础分：所有因子乘以权重得到0-100的分数
    base_score = (
        # 正向因子（乘以权重）
        trend_strength * 15 +        # 趋势强度
        trend_probability * 15 +      # 趋势概率
        theme_confidence_01 * 12 +   # 主题真实性
        theme_confidence_01 * 10 +   # 主题纯度（与真实性相同）
        (leader_factor + theme_rank_bonus) * 10 +  # 龙头因子 + 核心公司加成
        money_momentum * 10 +        # 资金动量
        breakout_strength * 8 +      # 突破强度
        volume_explosion * 8 +       # 量能爆发
        trend_stability * 6 +        # 稳定性评分
        compression_score * 6         # 压缩度
    )

    # 减去失败概率惩罚
    failure_penalty = fail_prob * 20

    # V7.5总分
    v75_total = base_score - failure_penalty

    # 确保在0-100范围内
    v75_total = np.clip(v75_total, 0, 100)'''

new_v75 = '''    # =========================
    # 1. 主题真实性（theme_confidence）
    # =========================
    theme_confidence = calc_theme_confidence(stock_info, theme) if theme else 30
    # 主题置信度是0-100，直接使用不归一化

    # =========================
    # 2. 龙头因子（leader_factor）
    # 基于趋势强度，资金动量和趋势概率
    # =========================
    leader_factor = (
        trend_strength * 0.40 +
        money_momentum * 0.35 +
        trend_probability * 0.25
    )

    # =========================
    # 3. 主题排名加成（如果股票是主题核心公司）
    # =========================
    theme_rank_bonus = 0
    if stock_info and theme:
        # 检查是否是核心公司
        try:
            cfg_path = os.path.join(BASE_DIR, 'theme.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    theme_cfg = json.load(f).get('HOT_THEMES', {})
                
                if theme in theme_cfg:
                    core_companies = theme_cfg[theme].get('core_companies', [])
                    if ts_code in core_companies:
                        theme_rank_bonus = 10  # 核心公司额外10分（0-100范围）
        except:
            pass

    # =========================
    # 4. 计算V7.5综合评分
    # =========================
    # 基础分：所有因子乘以权重得到0-100的分数
    # 注意：
    # - trend_strength, trend_probability等是0-1范围，需要先乘以100
    # - theme_confidence已经是0-100范围，直接使用
    base_score = (
        # 正向因子（乘以权重）
        trend_strength * 100 * 15 +   # 趋势强度（0-1转0-100后再乘15）
        trend_probability * 100 * 15 + # 趋势概率（0-1转0-100后再乘15）
        theme_confidence * 12 / 100 + # 主题真实性（0-100直接用，除以100是权重归一化）
        theme_confidence * 10 / 100 + # 主题纯度（与真实性相同）
        (leader_factor * 100 + theme_rank_bonus) * 10 / 100 + # 龙头因子 + 核心公司加成
        money_momentum * 100 * 10 +   # 资金动量
        breakout_strength * 100 * 8 + # 突破强度
        volume_explosion * 100 * 8 +  # 量能爆发
        trend_stability * 100 * 6 +   # 稳定性评分
        compression_score * 100 * 6    # 压缩度
    )

    # 减去失败概率惩罚
    failure_penalty = fail_prob * 100 * 20 / 100

    # V7.5总分
    v75_total = base_score - failure_penalty

    # 放大评分，让最高分可达70-80
    v75_total = v75_total * 1.5

    # 确保在0-100范围内
    v75_total = np.clip(v75_total, 0, 100)'''

if old_v75 in content:
    content = content.replace(old_v75, new_v75)
    with open(r'd:\mystock\solo\tushare_quant.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ V7.5评分已修复！")
else:
    print("❌ 未找到目标代码")
