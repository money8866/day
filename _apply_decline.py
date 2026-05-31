# -*- coding: utf-8 -*-
"""
apply_decline_to_etf.py - Patch etf_quant.py to integrate decline risk control
Creates a patched version or modifies in-place
"""
import re

path = r'D:\mystock\etf_quant.py'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# =========================================================
# 1. Add import
# =========================================================
if 'import block_decline_risk' not in content:
    content = content.replace(
        'import tushare_quant,block,emotion',
        'import tushare_quant,block,emotion\nimport block_decline_risk as drc'
    )
    print("OK: added import block_decline_risk")

# =========================================================
# 2. After sector_df = block.analyze_hot_sectors(), apply decline risk
# =========================================================
old_sector = '    sector_df = block.analyze_hot_sectors()'
new_sector = '''    sector_df = block.analyze_hot_sectors()

    # =====================================================
    # Decline Risk Control
    # =====================================================
    decline_warnings = []
    if 'sector_state' in dir(block):
        for idx, row in sector_df.iterrows():
            name = row.get('zhuxian', row.get('name', ''))
            state = block.sector_state.get(name)
            if state and len(state.get('history', [])) >= 3:
                risk = drc.calc_decline_risk(name, state['history'][-1], state)
                sector_df.at[idx, 'tuichao_dengji'] = risk['level']
                sector_df.at[idx, 'tuichao_xinhao'] = ','.join(risk['signal_labels'])
                sector_df.at[idx, 'tuichao_zhekou'] = risk['discount']
                if risk['level'] >= 1:
                    decline_warnings.append(risk)

    if decline_warnings:
        # Sort by level desc
        decline_warnings.sort(key=lambda x: x['level'], reverse=True)
        decline_report = drc.format_decline_report(decline_warnings)
    else:
        decline_report = ''
    # Apply discount to scores
    if 'tuichao_zhekou' in sector_df.columns:
        mask = sector_df['tuichao_zhekou'] < 1.0
        if mask.any():
            sector_df.loc[mask, 'pingfen'] = sector_df.loc[mask, 'pingfen'] * sector_df.loc[mask, 'tuichao_zhekou']'''

if old_sector in content:
    content = content.replace(old_sector, new_sector)
    print("OK: added decline risk control after analyze_hot_sectors")
else:
    # Try Chinese column name version
    old_cn = '    sector_df = block.analyze_hot_sectors()'
    if old_cn in content:
        content = content.replace(old_cn, new_sector)
        print("OK: added decline risk control (CN)")
    else:
        print("ERROR: analyze_hot_sectors call not found")

# =========================================================
# 3. Inject decline_report into deepseek_report call
# =========================================================
# Find the deepseek_report call and add decline info
old_report_call = '''        report = deepseek_report(

        result_df,

        style_df,

        risk_state,
        emotion_text, sector_text, sector_text_his,
        portfolio_text=portfolio_text,
        last_report_summary=last_report_summary,
        history_snap_df=history_snap_df
    )'''

new_report_call = '''        report = deepseek_report(

        result_df,

        style_df,

        risk_state,
        emotion_text, sector_text, sector_text_his,
        portfolio_text=portfolio_text,
        last_report_summary=last_report_summary,
        history_snap_df=history_snap_df,
        decline_report=decline_report if decline_report else ''
    )'''

if old_report_call in content:
    content = content.replace(old_report_call, new_report_call)
    print("OK: added decline_report to deepseek_report call")
else:
    print("WARN: deepseek_report call pattern not found")

# =========================================================
# 4. Inject decline info into DeepSeek prompt
# =========================================================
old_prompt_end = '''    prompt = f"""你是一位资深ETF基金经理...'''

# Find the deepseek_report function and add decline section
# Instead, find the end of the prompt construction
# Let's find the prompt and add decline context

# Add decline warnings to print output
old_print = '''    if portfolio_text:
        print("\\n' + '[持仓]' + ' 持仓分析:")
        print(portfolio_text)'''

new_print = '''    if portfolio_text:
        print("\\n' + '[持仓]' + ' 持仓分析:")
        print(portfolio_text)

    if decline_warnings:
        print("\\n' + '[!!退潮预警!!]' + ' Decline Risk Warnings:")
        for w in decline_warnings:
            print(f"  L{w['level']}: {w['detail']}")'''

if old_print in content:
    content = content.replace(old_print, new_print)
    print("OK: added decline warnings to console output")
else:
    print("WARN: console output pattern not found")

# Save
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\nPatch complete!")
