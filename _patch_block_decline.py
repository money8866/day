"""Patch block.py: inject decline risk control system"""
import re

path = r'D:\mystock\block.py'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# =========================================================
# 1. 在 is_decline() 后面插入退潮风控函数
# =========================================================
decline_risk_code = r'''

# =========================================================
# 退潮风控系统 (Decline Risk Control)
# =========================================================

def calc_decline_risk(name, today_score, state, daily_df=None):
    """
    Calculate decline risk level for a sector.
    Returns dict with level(0-3), signals list, discount, detail.
    """
    h = state["history"]

    if len(h) < 3:
        return {
            'level': 0, 'signals': [], 'discount': 1.0,
            'detail': 'N/A'
        }

    signals = []

    # Signal 1: Surge-then-crash
    if len(h) >= 5:
        threshold = sorted(h[:-1])[int(len(h[:-1]) * 0.9)]
        recent_high = all(x >= threshold * 0.95 for x in h[-3:-1])
        today_drop = h[-1] < h[-2] * 0.90
        if recent_high and today_drop:
            signals.append('surge_crash')

    # Signal 2: Continuous decay (momentum + acceleration both negative)
    if len(h) >= 3:
        momentum = h[-1] - h[-3]
        acc1 = h[-1] - h[-2]
        acc2 = h[-2] - h[-3]
        if momentum < 0 and acc1 < 0 and acc2 < 0:
            signals.append('decay_3d')
        elif momentum < 0 and acc1 < 0:
            signals.append('decay_2d')

    # Signal 3: Leader underperforming
    if daily_df is not None and len(daily_df) > 3:
        leader_pct = daily_df['pct_chg'].max()
        median_pct = daily_df['pct_chg'].median()
        avg_pct = daily_df['pct_chg'].mean()
        if median_pct > 0.5 and leader_pct < median_pct:
            signals.append('leader_weak')
        elif avg_pct > 0.5 and leader_pct < 0:
            signals.append('leader_green')

    # Signal 4: Downside spreading
    if daily_df is not None and len(daily_df) > 3:
        down_ratio = (daily_df['pct_chg'] < 0).mean()
        if down_ratio > 0.4 and today_score > 500:
            signals.append('downside_spread')

    # Signal 5: Volume-price divergence
    if daily_df is not None and len(daily_df) > 3:
        total_amount = daily_df['amount'].sum()
        avg_pct = daily_df['pct_chg'].mean()
        if total_amount > 50 and abs(avg_pct) < 0.3:
            signals.append('vol_price_diverge')

    # Signal 6: Extreme peak
    if len(h) >= 5:
        avg_5 = sum(h[-6:-1]) / 5
        if h[-1] > avg_5 * 2.0:
            signals.append('extreme_peak')

    # Calculate level
    n_signals = len(signals)
    if n_signals >= 3:
        level = 3
    elif n_signals == 2:
        level = 2
    elif n_signals == 1:
        level = 1
    else:
        level = 0

    discount = [1.0, 0.9, 0.7, 0.4][level]

    return {
        'level': level,
        'signals': signals,
        'discount': discount,
        'detail': f"L{level}({n_signals}sig)"
    }


def calc_sector_score_v2(df, name, state, daily_df=None):
    """
    v2 score: base score * decline discount
    """
    base_score = calc_sector_score(df)
    risk = calc_decline_risk(name, base_score, state, daily_df)
    final_score = round(base_score * risk['discount'], 2)
    return final_score, risk


def get_decline_level(name):
    """Get current decline level for a sector."""
    state = sector_state.get(name)
    if not state or len(state["history"]) < 3:
        return 0
    risk = calc_decline_risk(name, state["history"][-1], state)
    return risk['level']

'''

# Insert after is_decline function
marker = "    return h[-1] < h[-2] < h[-3]\n"
if marker in content:
    idx = content.index(marker) + len(marker)
    content = content[:idx] + decline_risk_code + content[idx:]
    print("OK: decline risk functions inserted")
else:
    print("ERROR: marker not found")
    exit(1)

# =========================================================
# 2. Update analyze_concepts to use v2
# =========================================================
# Replace score = calc_sector_score(df) in analyze_concepts
# Pattern: within analyze_concepts, change score and add risk fields

old_concept_score = '''        score = calc_sector_score(df)
        
        state = update_state(concept_name, score)
        
        strength = calc_strength(score, state)
        
        leader_code, leader_name, leader_score = find_leader(df)
        
        result.append({
            "type": "concept",'''

new_concept_score = '''        state = update_state(concept_name, 0)  # temp score
        
        score, risk = calc_sector_score_v2(df, concept_name, state, df)
        
        # Re-update state with real score
        state["history"][-1] = score
        
        strength = calc_strength(score, state)
        
        leader_code, leader_name, leader_score = find_leader(df)
        
        result.append({
            "type": "concept",'''

if old_concept_score in content:
    content = content.replace(old_concept_score, new_concept_score)
    print("OK: analyze_concepts updated to v2")
else:
    print("WARN: analyze_concepts pattern not found, trying manual...")

# Also add risk fields to concept result.append
old_concept_append = '''            "leader_strength": leader_score,
            "is_decline": is_decline(state),
            "n_stocks": len(stocks)
        })
    
    print(f"Concept analysis done")'''

new_concept_append = '''            "leader_strength": leader_score,
            "is_decline": is_decline(state),
            "decline_level": risk["level"],
            "decline_signals": ",".join(risk["signals"]),
            "decline_discount": risk["discount"],
            "n_stocks": len(stocks)
        })
    
    print(f"Concept analysis done")'''

# Try Chinese version
old_cn = '''            "是否退潮": is_decline(state),
            "成分股数": len(stocks)
        })
    
    print(f"概念板块分析完成'''

new_cn = '''            "是否退潮": is_decline(state),
            "退潮等级": risk["level"],
            "退潮信号": ",".join(risk["signals"]),
            "退潮折扣": risk["discount"],
            "成分股数": len(stocks)
        })
    
    print(f"概念板块分析完成'''

if old_cn in content:
    content = content.replace(old_cn, new_cn)
    print("OK: concept append updated (CN)")
else:
    print("WARN: CN pattern not found")

# =========================================================
# 3. Update analyze_industry to use v2
# =========================================================
old_industry_score = '''            score = calc_sector_score(df)

            state = update_state(name, score)

            strength = calc_strength(score, state)

            leader_code, leader_name, leader_score = find_leader(df)
            state["leader"] = leader_code

            result.append({

                "type": "industry",'''

new_industry_score = '''            state = update_state(name, 0)
            
            score, risk = calc_sector_score_v2(df, name, state, df)
            
            state["history"][-1] = score

            strength = calc_strength(score, state)

            leader_code, leader_name, leader_score = find_leader(df)
            state["leader"] = leader_code

            result.append({

                "type": "industry",'''

if old_industry_score in content:
    content = content.replace(old_industry_score, new_industry_score)
    print("OK: analyze_industry updated to v2")
else:
    # Try Chinese
    old_ind_cn = '''            score = calc_sector_score(df)

            state = update_state(name, score)

            strength = calc_strength(score, state)

            leader_code, leader_name, leader_score = find_leader(df)
            state["leader"] = leader_code

            result.append({

                "类型": level,'''

    if old_ind_cn in content:
        content = content.replace(old_ind_cn, new_industry_score)
        print("OK: analyze_industry updated (CN)")

# Add risk fields to industry append
old_ind_append = '''                "是否退潮": is_decline(state),
                "成分股数": len(stocks)                
            })
    
    return result'''

new_ind_append = '''                "是否退潮": is_decline(state),
                "退潮等级": risk["level"],
                "退潮信号": ",".join(risk["signals"]),
                "退潮折扣": risk["discount"],
                "成分股数": len(stocks)                
            })
    
    return result'''

if old_ind_append in content:
    content = content.replace(old_ind_append, new_ind_append)
    print("OK: industry append updated")

# =========================================================
# 4. Update analyze_themes to use v2
# =========================================================
old_theme_score = '''        score = calc_sector_score(df)

        state = update_state(theme, score)

        strength = calc_strength(score, state)

        leader_code, leader_name, leader_score = find_leader(df)

        result.append({

            "type": "theme",'''

new_theme_score = '''        state = update_state(theme, 0)
        
        score, risk = calc_sector_score_v2(df, theme, state, df)
        
        state["history"][-1] = score

        strength = calc_strength(score, state)

        leader_code, leader_name, leader_score = find_leader(df)

        result.append({

            "type": "theme",'''

if old_theme_score in content:
    content = content.replace(old_theme_score, new_theme_score)
    print("OK: analyze_themes updated to v2")
else:
    old_theme_cn = '''        score = calc_sector_score(df)

        state = update_state(theme, score)

        strength = calc_strength(score, state)

        leader_code, leader_name, leader_score = find_leader(df)

        result.append({

            "类型": "主题",'''
    if old_theme_cn in content:
        content = content.replace(old_theme_cn, new_theme_score)
        print("OK: analyze_themes updated (CN)")

# Add risk fields to theme append
old_theme_append = '''            "是否退潮": is_decline(state),
            "成分股数": len(stocks)

        })

    return result'''

new_theme_append = '''            "是否退潮": is_decline(state),
            "退潮等级": risk["level"],
            "退潮信号": ",".join(risk["signals"]),
            "退潮折扣": risk["discount"],
            "成分股数": len(stocks)

        })

    return result'''

if old_theme_append in content:
    content = content.replace(old_theme_append, new_theme_append)
    print("OK: theme append updated")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\nPatch complete!")
