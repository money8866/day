# 修复V7.5评分计算
with open(r'd:\mystock\solo\tushare_quant.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_part1 = '''    theme_confidence = calc_theme_confidence(stock_info, theme) if theme else 30
    # 归一化到0-1范围
    theme_confidence_01 = theme_confidence / 100.0'''

new_part1 = '''    theme_confidence = calc_theme_confidence(stock_info, theme) if theme else 30'''

old_part2 = '''                    if ts_code in core_companies:
                        theme_rank_bonus = 0.2  # 核心公司额外0.2分'''

new_part2 = '''                    if ts_code in core_companies:
                        theme_rank_bonus = 15  # 核心公司额外15分'''

old_part3 = '''    base_score = (
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

new_part3 = '''    base_score = (
        # 正向因子（乘以权重）
        trend_strength * 100 * 15 +        # 趋势强度
        trend_probability * 100 * 15 +      # 趋势概率
        theme_confidence * 12 +              # 主题真实性
        theme_confidence * 10 +              # 主题纯度
        leader_factor * 100 * 10 +           # 龙头因子
        money_momentum * 100 * 10 +        # 资金动量
        breakout_strength * 100 * 8 +      # 突破强度
        volume_explosion * 100 * 8 +       # 量能爆发
        trend_stability * 100 * 6 +        # 稳定性评分
        compression_score * 100 * 6 +        # 压缩度
        theme_rank_bonus                    # 核心公司加成
    )
    
    # 归一化（权重总和是100）
    base_score = base_score / 100

    # 减去失败概率惩罚
    failure_penalty = fail_prob * 20

    # V7.5总分
    v75_total = base_score - failure_penalty
    
    # 放大评分，让最高分可达70-80
    v75_total = v75_total * 1.3

    # 确保在0-100范围内
    v75_total = np.clip(v75_total, 0, 100)'''

# 逐个替换
content = content.replace(old_part1, new_part1)
content = content.replace(old_part2, new_part2)
content = content.replace(old_part3, new_part3)

with open(r'd:\mystock\solo\tushare_quant.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ V7.5评分已修复！")
