#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成集成历史跟踪的 main.py"""
import os

NEW_MAIN_CONTENT = '''# -*- coding: utf-8 -*-
"""
主线龙头分歧策略 - 日常调度 + 信号输出
运行: python main.py
"""
import os, sys, json
import pandas as pd
from datetime import datetime

# 确保项目目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import PROJECT_DIR, CACHE_DIR
from market_state import classify_market_state, get_state_display
from sector_strength import (
    analyze_sector_strength, get_main_theme_stocks,
    track_history_themes, find_recurring_themes
)
from dragon_score import score_dragon_candidates
from divergence import check_divergence
from position import calc_position, check_stop_conditions
sys.path.insert(0, r'C:\\Users\\kongx\\mystock')
from tushare_quant import get_last_trade_date


def run_daily_scan():
    """日常扫描: 市场→板块→龙头→分歧→仓位→信号"""
    print("=" * 60)
    print("  🐉 主线龙头分歧策略 - 日常扫描")
    print("  %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 60)

    # 1. 市场状态
    print("\\n📍 【1. 市场状态】")
    ms = classify_market_state()
    print(f"  状态: {get_state_display(ms['state'])} (评分{ms['score']}/100)")
    print(f"  依据: {ms.get('reason', '-')}")
    if ms.get('metrics'):
        m = ms['metrics']
        print(f"  指数: {m.get('close', 0):.2f}  涨跌: {m.get('pct_chg', 0):+.2f}%  涨跌比: {m.get('ad_ratio', 0):.0%}")

    # 2. 板块强度 + 历史跟踪
    print("\\n📍 【2. 主线板块 TOP5】")
    trade_date = get_last_trade_date()  # 使用最近交易日
    sr = analyze_sector_strength(trade_date=trade_date)
    themes = sr.get("main_themes", [])[:5]
    
    # 历史主线跟踪
    if themes:
        top5_names = [t['name'] for t in themes]
        history = track_history_themes(trade_date, top5_names)
        recurring = find_recurring_themes(history, min_count=3)
    else:
        history = []
        recurring = []
    
    if themes:
        for t in themes:
            emoji = "🔥" if t["rank"] <= 2 else "📌"
            score = t.get('score', 0)
            print(f"  {emoji} {t['rank']}. {t['name']}  涨幅{t.get('pct_change', 0):+.2f}%  评分{score:.0f}")
    else:
        print("  (无数据)")
    
    # 显示反复活跃板块
    if recurring:
        print("\\n  📌 反复活跃板块 (≥3日):")
        for item in recurring[:3]:
            print(f"    {item['name']} ({item['count']}日)")

    # 3. 龙头评分
    print("\\n📍 【3. 龙头评分 TOP10】")
    candidates = get_main_theme_stocks(themes, top_n_boards=5)
    scored = None  # 初始化
    if candidates is not None and len(candidates) > 0:
        # 传递反复活跃板块信息
        recurring_set = set(item['name'] for item in recurring) if recurring else set()
        scored = score_dragon_candidates(candidates, recurring_themes=recurring_set)
        if scored is not None and len(scored) > 0:
            dragons = scored[scored['is_dragon'] == True].head(10)
            for _, row in dragons.iterrows():
                emoji = "👑" if row['total_score'] >= 80 else "⭐"
                print(f"  {emoji} {row['name']}({row['code']}) "
                      f"评分:{row['total_score']:.0f} "
                      f"价:{row['price']:.2f} "
                      f"5日:{row.get('mom5', 0):+.1f}% "
                      f"换手:{row.get('turn_over', 0):.1f}% "
                      f"板块:{row['board_name']}")
        else:
            print("  (评分无结果)")
    else:
        print("  (无候选股)")

    # 4. 分歧预警
    print("\\n📍 【4. 分歧预警】")
    div = check_divergence(ms, sr, scored if 'scored' in dir() else None)
    print(f"  风险等级: {div['risk_level_name']}")
    print(f"  操作建议: {div['action']}")
    for w in div.get('warnings', []):
        print(f"  {w}")
    for s in div.get('signals', []):
        print(f"  {s}")

    # 5. 仓位建议
    print("\\n📍 【5. 仓位控制】")
    pos = calc_position(ms, div)
    print(f"  {pos['summary']}")

    # 6. 买入/卖出信号
    print("\\n📍 【6. 操作信号】")
    if scored is not None and len(scored) > 0:
        buy_signals = scored[scored['is_dragon'] == True].head(3)
        if len(buy_signals) > 0 and pos['can_add_stocks'] > 0:
            for _, row in buy_signals.iterrows():
                print(f"  🟢 买入信号: {row['name']}({row['code']}) "
                      f"评分{row['total_score']:.0f} 建议仓位{pos['single_max']:.0%}")
        elif pos['can_add_stocks'] <= 0:
            print("  ⏸ 持仓已满，暂不新增")

    if div['risk_level'] >= 2:
        print(f"  🔴 {div['action']}")

    # 汇总
    print("\\n" + "=" * 60)
    print(f"  📊 综合判断: {get_state_display(ms['state'])} | "
          f"风险: {div['risk_level_name']} | "
          f"仓位: {pos['target_position']:.0%}")
    print("=" * 60)

    return {
        "market_state": ms,
        "sector": sr,
        "dragons": scored if 'scored' in dir() else None,
        "divergence": div,
        "position": pos,
    }


def format_wechat_message(result):
    """格式化为微信推送消息"""
    ms = result['market_state']
    div = result['divergence']
    pos = result['position']
    dragons = result.get('dragons')

    lines = [
        f"🐉 主线龙头策略 {datetime.now().strftime('%m/%d')}",
        "",
        f"市场: {get_state_display(ms['state'])} ({ms.get('metrics', {}).get('pct_chg', 0):+.2f}%)",
        f"风险: {div['risk_level_name']}",
        f"仓位: {pos['target_position']:.0%} | 可加{pos['can_add_stocks']}只",
    ]

    # 主线板块
    themes = result['sector'].get('main_themes', [])[:3]
    if themes:
        lines.append("")
        lines.append("🔥 主线:")
        for t in themes:
            lines.append(f" {t['name']} {t.get('pct_change', 0):+.1f}%")

    # 龙头
    if dragons is not None and len(dragons) > 0:
        top = dragons[dragons['is_dragon'] == True].head(5)
        if len(top) > 0:
            lines.append("")
            lines.append("👑 龙头:")
            for _, r in top.iterrows():
                lines.append(f" {r['name']} 评{r['total_score']:.0f} {r.get('mom5',0):+.1f}%")

    # 预警
    if div.get('warnings') or div.get('signals'):
        lines.append("")
        lines.append("⚠️ 预警:")
        for w in div.get('warnings', [])[:2]:
            lines.append(f" {w}")
        for s in div.get('signals', [])[:2]:
            lines.append(f" {s}")

    return "\\n".join(lines)


if __name__ == "__main__":
    result = run_daily_scan()

    # 也输出微信格式
    msg = format_wechat_message(result)
    print("\\n\\n--- 微信推送格式 ---")
    print(msg)

    # 保存结果
    result_file = os.path.join(CACHE_DIR, f"scan_{datetime.now().strftime('%Y%m%d')}.json")
    # Convert non-serializable types
    def convert(obj):
        if isinstance(obj, (pd.Timestamp, datetime)):
            return str(obj)
        if isinstance(obj, pd.DataFrame):
            return obj.to_dict('records')
        return obj

    serializable = {}
    for k, v in result.items():
        try:
            json.dumps({k: v}, default=convert)
            serializable[k] = v
        except:
            serializable[k] = str(v)

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2, default=convert)
    print(f"\\n结果已保存: {result_file}")
'''

# 写入临时文件
temp_file = r'C:\Users\kongx\.qclaw\workspace\temp_main_py_content.txt'
with open(temp_file, 'w', encoding='utf-8') as f:
    f.write(NEW_MAIN_CONTENT)

print(f'✅ 临时文件已生成: {temp_file}')
print(f'   字符数: {len(NEW_MAIN_CONTENT)}')
print()
print('下一步：用 write_file.py 写入目标文件')
print(f'  python "C:\\Program Files\\QClaw\\resources\\openclaw\\config\\skills\\qclaw-text-file\\scripts\\write_file.py" --content-file "{temp_file}" --path "C:\\Users\\kongx\\mystock\\dragon\\main.py"')
