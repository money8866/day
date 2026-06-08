# 重新设计V7.5评分，让评分更合理
with open(r'd:\mystock\solo\tushare_quant.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_v75_function = '''# =========================================================
# V7.5 综合评分系统
# =========================================================
def calc_dual_layer_score_v75(df, ts_code='', stock_info=None, theme=''):
    """
    V7.5综合评分系统 - 基于用户提供的公式
    
    V7_5_SCORE = (
        trend_strength      * 15 +
        trend_probability   * 15 +
        theme_confidence    * 12 +
        theme_purity        * 10 +
        leader_factor       * 10 +
        capital_momentum    * 10 +
        breakout_strength   * 8  +
        volume_explosion    * 8  +
        stability_score     * 6  +
        compression_score   * 6
        -
        failure_probability * 20
    )
    
    说明：
    - 所有因子范围0-1，乘以权重后转换为0-100
    - theme_confidence: 主题真实性（防止蹭概念）
    - theme_purity: 主题纯度（与theme_confidence相同）
    - leader_factor: 龙头因子（基于趋势强度，资金动量）
    - stability_score: 稳定性评分（趋势稳定度）
    """

    # =========================
    # 获取V6技术指标
    # =========================
    v6_result = calc_dual_layer_score_v6(df, ts_code, theme)

    # V6各指标（0-1范围）
    trend_probability = float(v6_result.get('趋势概率', 0.5))
    fail_prob = float(v6_result.get('失败概率', 0.5))
    breakout_strength = float(v6_result.get('突破强度', 0.5))
    money_momentum = float(v6_result.get('资金动量', 0.5))
    trend_stability = float(v6_result.get('趋势稳定', 0.5))
    volume_explosion = float(v6_result.get('量能爆发', 0.5))
    compression_score = float(v6_result.get('压缩度', 0.5))
    trend_strength = float(v6_result.get('趋势强度', 0.5))

    # =========================
    # 自动选择纯度最高的主题
    # =========================
    if not theme and stock_info:
        theme = _find_best_theme(stock_info)

    # =========================
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
    v75_total = np.clip(v75_total, 0, 100)

    # =========================
    # 输出结果
    # =========================
    return {
        # V6技术指标
        "趋势概率": round(trend_probability, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(v6_result.get('洗盘概率', 0), 4),
        "交易优势": round(v6_result.get('交易优势', 0), 4),
        "趋势强度": round(trend_strength, 3),
        "趋势稳定": round(trend_stability, 3),
        "资金动量": round(money_momentum, 3),
        "突破强度": round(breakout_strength, 3),
        "压缩度": round(compression_score, 3),
        "量能爆发": round(volume_explosion, 3),
        "风险等级": v6_result.get('风险等级', '低'),

        # V7.5新指标
        "所属主题": theme,
        "主题纯度": round(theme_confidence, 2),
        "龙头因子": round(leader_factor, 3),
        "主题排名加成": round(theme_rank_bonus, 2),

        # V7.5总分
        "V7总评分": round(v75_total, 2)
    }


'''

new_v75_function = '''# =========================================================
# V7.5 综合评分系统
# =========================================================
def calc_dual_layer_score_v75(df, ts_code='', stock_info=None, theme=''):
    """
    V7.5综合评分系统 - 基于用户提供的公式
    
    V7_5_SCORE = (
        trend_strength      * 15 +
        trend_probability   * 15 +
        theme_confidence    * 12 +
        theme_purity        * 10 +
        leader_factor       * 10 +
        capital_momentum    * 10 +
        breakout_strength   * 8  +
        volume_explosion    * 8  +
        stability_score     * 6  +
        compression_score   * 6
        -
        failure_probability * 20
    )
    
    说明：
    - 所有因子先转换为0-100，然后乘以权重，权重总和归一化
    - theme_confidence: 主题真实性（防止蹭概念）
    - theme_purity: 主题纯度（与theme_confidence相同）
    - leader_factor: 龙头因子（基于趋势强度，资金动量）
    - stability_score: 稳定性评分（趋势稳定度）
    """

    # =========================
    # 获取V6技术指标
    # =========================
    v6_result = calc_dual_layer_score_v6(df, ts_code, theme)

    # V6各指标（0-1范围）
    trend_probability = float(v6_result.get('趋势概率', 0.5))
    fail_prob = float(v6_result.get('失败概率', 0.5))
    breakout_strength = float(v6_result.get('突破强度', 0.5))
    money_momentum = float(v6_result.get('资金动量', 0.5))
    trend_stability = float(v6_result.get('趋势稳定', 0.5))
    volume_explosion = float(v6_result.get('量能爆发', 0.5))
    compression_score = float(v6_result.get('压缩度', 0.5))
    trend_strength = float(v6_result.get('趋势强度', 0.5))

    # =========================
    # 自动选择纯度最高的主题
    # =========================
    if not theme and stock_info:
        theme = _find_best_theme(stock_info)

    # =========================
    # 1. 主题真实性（theme_confidence）
    # =========================
    theme_confidence = calc_theme_confidence(stock_info, theme) if theme else 30

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
                        theme_rank_bonus = 15  # 核心公司额外15分
        except:
            pass

    # =========================
    # 4. 计算V7.5综合评分
    # =========================
    # 基础分：所有因子先转0-100，然后乘以权重
    base_score = (
        # 正向因子（乘以权重）
        trend_strength * 100 * 15 +    # 趋势强度
        trend_probability * 100 * 15 +  # 趋势概率
        theme_confidence * 12 +         # 主题真实性
        theme_confidence * 10 +         # 主题纯度
        leader_factor * 100 * 10 +      # 龙头因子
        money_momentum * 100 * 10 +     # 资金动量
        breakout_strength * 100 * 8 +   # 突破强度
        volume_explosion * 100 * 8 +    # 量能爆发
        trend_stability * 100 * 6 +     # 稳定性评分
        compression_score * 100 * 6 +    # 压缩度
        theme_rank_bonus                # 核心公司加成
    )

    # 总权重：15+15+12+10+10+10+8+8+6+6 = 100，归一化
    base_score = base_score / 100

    # 减去失败概率惩罚
    failure_penalty = fail_prob * 20

    # V7.5总分
    v75_total = base_score - failure_penalty

    # 放大评分，让最高分可达70-80
    v75_total = v75_total * 1.3

    # 确保在0-100范围内
    v75_total = np.clip(v75_total, 0, 100)

    # =========================
    # 输出结果
    # =========================
    return {
        # V6技术指标
        "趋势概率": round(trend_probability, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(v6_result.get('洗盘概率', 0), 4),
        "交易优势": round(v6_result.get('交易优势', 0), 4),
        "趋势强度": round(trend_strength, 3),
        "趋势稳定": round(trend_stability, 3),
        "资金动量": round(money_momentum, 3),
        "突破强度": round(breakout_strength, 3),
        "压缩度": round(compression_score, 3),
        "量能爆发": round(volume_explosion, 3),
        "风险等级": v6_result.get('风险等级', '低'),

        # V7.5新指标
        "所属主题": theme,
        "主题纯度": round(theme_confidence, 2),
        "龙头因子": round(leader_factor, 3),
        "主题排名加成": round(theme_rank_bonus, 2),

        # V7.5总分
        "V7总评分": round(v75_total, 2)
    }


'''

if old_v75_function in content:
    content = content.replace(old_v75_function, new_v75_function)
    with open(r'd:\mystock\solo\tushare_quant.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ V7.5评分已修复！")
else:
    print("❌ 未找到目标代码")
