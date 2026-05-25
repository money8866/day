# -*- coding: utf-8 -*-
"""
盘后日线分析 - 收盘后运行，输出当日完整市场复盘
python daily_analysis.py
"""
import os, sys, json, io

# 修复Windows控制台GBK编码不支持emoji的问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CACHE_DIR
from data_engine import (
    get_ts_api, get_trade_dates, fetch_index_daily,
    fetch_all_concept_boards_daily, fetch_all_industry_boards_daily,
    fetch_stock_basic, calc_ma, calc_rsi, calc_macd,
    _cache_save
)
from market_state import classify_market_state, get_state_display
from sector_strength import analyze_sector_strength

def get_latest_trade_date():
    """获取最近一个交易日"""
    dates = get_trade_dates(start="20260101")
    today_str = datetime.now().strftime("%Y%m%d")
    # 向前找最近的交易日
    for d in reversed(dates):
        if d <= today_str:
            return d
    return dates[-1] if dates else today_str

def analyze_indices():
    """主要指数分析"""
    indices = {
        "000001.SH": "上证指数",
        "399001.SZ": "深证成指",
        "399006.SZ": "创业板指",
        "000688.SH": "科创50",
        "399303.SZ": "国证2000",
        "000852.SH": "中证1000",
        "000905.SH": "中证500",
    }
    print("📊 【主要指数】")
    lines = []
    for ts_code, name in indices.items():
        try:
            df = fetch_index_daily(ts_code, start_date="20260301")
            if df is None or len(df) < 5:
                continue
            df = calc_ma(df, [5, 10, 20, 60])
            l = df.iloc[-1]
            p = df.iloc[-2] if len(df) >= 2 else l

            chg = (l['close'] - p['close']) / p['close'] * 100
            vol_chg = l['vol'] / p['vol'] * 100 if p['vol'] > 0 else 100

            ma5_tag = "🟢" if l['close'] >= l.get('ma5', 0) else "🔴"
            ma20_tag = "🟢" if l['close'] >= l.get('ma20', 0) else "🔴"

            line = f"  {name}: {l['close']:.2f} ({chg:+.2f}%) 量{vol_chg:.0f}% {ma5_tag}MA5 {ma20_tag}MA20"
            lines.append(line)
            print(line)
        except:
            lines.append(f"  {name}: 数据获取失败")
    return lines


def analyze_advance_decline(trade_date):
    """涨跌家数分析"""
    print(f"\n📈 【涨跌家数 {trade_date}】")
    try:
        pro = get_ts_api()
        df = pro.daily(trade_date=trade_date, fields="ts_code,pct_chg,amount,vol")
        if df is None or len(df) == 0:
            print("  无数据")
            return {}

        up = len(df[df['pct_chg'] > 0])
        down = len(df[df['pct_chg'] < 0])
        flat = len(df[df['pct_chg'] == 0])
        total = len(df)

        # 涨停/跌停 (约>=9.8%为涨停)
        limit_up = len(df[df['pct_chg'] >= 9.8])
        limit_down = len(df[df['pct_chg'] <= -9.8])

        # 大涨/大跌 (>5%)
        big_up = len(df[(df['pct_chg'] >= 5) & (df['pct_chg'] < 9.8)])
        big_down = len(df[(df['pct_chg'] <= -5) & (df['pct_chg'] > -9.8)])

        # 金额 (amount单位千元, ×1000转元再÷1e8得亿元)
        total_amount = df['amount'].sum() / 100000 if 'amount' in df.columns else 0  # 亿元

        ad_ratio = up / max(total, 1)
        ad_info = {
            "trade_date": trade_date,
            "total": total, "up": up, "down": down, "flat": flat,
            "limit_up": limit_up, "limit_down": limit_down,
            "big_up": big_up, "big_down": big_down,
            "ad_ratio": ad_ratio,
            "total_amount_yi": total_amount,
        }

        lines = [
            f"  总计: {total}只  上涨: {up}({up/total*100:.0f}%)  下跌: {down}({down/total*100:.0f}%)  平盘: {flat}",
            f"  涨停: {limit_up}  跌停: {limit_down}  大涨>5%: {big_up}  大跌>5%: {big_down}",
            f"  成交额: {total_amount:.0f}亿",
        ]
        for l in lines:
            print(l)
        return ad_info
    except Exception as e:
        print(f"  获取失败: {e}")
        return {}


def analyze_sector_ranking(trade_date=None):
    """板块涨跌排行(同花顺接口)"""
    print("\n🔥 【概念板块 TOP10 / BOTTOM5】")
    concepts = fetch_all_concept_boards_daily(trade_date=trade_date, use_cache=False)
    if concepts is None or len(concepts) == 0:
        print("  无数据")
        return [], []

    # 过滤有效名称
    concepts = concepts[concepts['name'].notna()]
    top = concepts.head(10)
    bottom = concepts.tail(5).iloc[::-1]

    lines_top = []
    for _, r in top.iterrows():
        turnover = r.get('turnover_rate', 0) or 0
        line = f"  🔥{r['name']:12s} {r.get('pct_change',0):+6.2f}% 换手{turnover:.1f}%"
        lines_top.append(line)
        print(line)

    lines_bot = []
    print("  ---")
    for _, r in bottom.iterrows():
        turnover = r.get('turnover_rate', 0) or 0
        line = f"  🧊{r['name']:12s} {r.get('pct_change',0):+6.2f}% 换手{turnover:.1f}%"
        lines_bot.append(line)
        print(line)

    return lines_top, lines_bot


def analyze_industry_ranking(trade_date=None):
    """行业板块排行(同花顺接口)"""
    print("\n🏭 【行业板块 TOP5 / BOTTOM3】")
    industries = fetch_all_industry_boards_daily(trade_date=trade_date, use_cache=False)
    if industries is None or len(industries) == 0:
        print("  无数据")
        return [], []

    industries = industries[industries['name'].notna()]
    top = industries.head(5)
    bottom = industries.tail(3).iloc[::-1]

    lines = []
    for _, r in top.iterrows():
        turnover = r.get('turnover_rate', 0) or 0
        line = f"  📈{r['name']:10s} {r.get('pct_change',0):+6.2f}% 换手{turnover:.1f}%"
        lines.append(line)
        print(line)

    print("  ---")
    for _, r in bottom.iterrows():
        turnover = r.get('turnover_rate', 0) or 0
        line = f"  📉{r['name']:10s} {r.get('pct_change',0):+6.2f}% 换手{turnover:.1f}%"
        lines.append(line)
        print(line)

    return lines


def analyze_limit_stocks(trade_date):
    """涨停板分析"""
    print(f"\n🚀 【涨停/跌停 {trade_date}】")
    try:
        pro = get_ts_api()
        df = pro.limit_list_d(date=trade_date,
                             fields="ts_code,trade_date,name,close,pct_chg,amount,limit_amount,fund,limit")
        if df is None or len(df) == 0:
            print("  无涨停数据")
            return []

        # 去重(同一股票可能有多条记录)
        df = df.drop_duplicates(subset=['ts_code'], keep='first')

        # 涨停
        limit_up = df[df['limit'] == 'U'].sort_values('amount', ascending=False)
        limit_down = df[df['limit'] == 'D'].sort_values('amount', ascending=False)

        lines = []
        if len(limit_up) > 0:
            print(f"  涨停 {len(limit_up)} 只:")
            for _, r in limit_up.head(15).iterrows():
                amt = r.get('amount', 0) / 1e8 if pd.notna(r.get('amount')) else 0  # 元→亿元
                fund_val = r.get('fund', 0) if pd.notna(r.get('fund')) else 0
                fund_str = f"封单{fund_val/1e8:.1f}亿" if fund_val > 0 else ""
                line = f"    {r['name']}({r['ts_code'][:6]}) {r.get('pct_chg', 0):+.1f}% 成交{amt:.1f}亿 {fund_str}"
                lines.append(line)
                print(line)
            if len(limit_up) > 15:
                print(f"    ... 等{len(limit_up)}只")

        if len(limit_down) > 0:
            print(f"\n  跌停 {len(limit_down)} 只:")
            for _, r in limit_down.head(5).iterrows():
                line = f"    {r['name']}({r['ts_code'][:6]}) {r.get('pct_chg', 0):+.1f}%"
                lines.append(line)
                print(line)

        return lines
    except Exception as e:
        print(f"  获取失败: {e}")
        return []


def analyze_capital_flow():
    """主力资金流向(东财行业板块资金流)"""
    print("\n💰 【主力资金流】")
    try:
        # 用东财行业板块主力净流入近似
        from sector_strength import analyze_sector_strength
        sr = analyze_sector_strength()
        concepts = sr.get('concepts', pd.DataFrame())
        industries = sr.get('industries', pd.DataFrame())

        lines = []
        if concepts is not None and len(concepts) > 0 and 'main_net_inflow' in concepts.columns:
            total_inflow = concepts['main_net_inflow'].sum()
            unit = '亿'
            val = total_inflow / 1e8
            if abs(val) < 1:
                val = total_inflow / 1e4
                unit = '万'
            direction = '🟢' if val > 0 else '🔴'
            line = f"  {direction} 概念板块主力净流入: {val:+.1f}{unit}"
            lines.append(line)
            print(line)

        if industries is not None and len(industries) > 0 and 'main_net_inflow' in industries.columns:
            total_inflow = industries['main_net_inflow'].sum()
            val = total_inflow / 1e8
            unit = '亿'
            if abs(val) < 1:
                val = total_inflow / 1e4
                unit = '万'
            direction = '🟢' if val > 0 else '🔴'
            line = f"  {direction} 行业板块主力净流入: {val:+.1f}{unit}"
            lines.append(line)
            print(line)

        # 个股主力净流入TOP5
        if concepts is not None and len(concepts) > 0:
            top_inflow = concepts.nlargest(3, 'main_net_inflow')
            for _, r in top_inflow.iterrows():
                val = r['main_net_inflow'] / 1e8
                lines.append(f"  💸 {r['name']} 主力{val:+.1f}亿")
                print(f"  💸 {r['name']} 主力{val:+.1f}亿")

        return lines
    except Exception as e:
        print(f"  获取失败: {e}")
        return []


def analyze_market_width(trade_date, lookback=5):
    """市场宽度趋势(近N天涨跌比变化)"""
    print(f"\n📐 【市场宽度趋势 近{lookback}日】")
    try:
        pro = get_ts_api()
        dates = get_trade_dates()
        # 找trade_date之前的lookback个交易日
        idx = dates.index(trade_date) if trade_date in dates else -1
        if idx < lookback:
            start_idx = 0
        else:
            start_idx = idx - lookback + 1
        recent_dates = dates[start_idx:idx + 1]

        lines = []
        for d in recent_dates:
            df = pro.daily(trade_date=d, fields="pct_chg")
            if df is not None and len(df) > 0:
                up = len(df[df['pct_chg'] > 0])
                total = len(df)
                ratio = up / max(total, 1)
                bar_len = int(ratio * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                line = f"  {d} {bar} {up}/{total} ({ratio:.0%})"
                lines.append(line)
                print(line)
        return lines
    except Exception as e:
        print(f"  获取失败: {e}")
        return []


def format_wechat_daily(trade_date, ad_info, sector_top=None, index_lines=None,
                         capital_lines=None, width_lines=None, limit_lines=None):
    """格式化盘后分析为微信推送"""
    lines = [
        f"📋 盘后分析 {trade_date[:4]}/{trade_date[4:6]}/{trade_date[6:8]}",
        "",
    ]

    # 指数
    if index_lines:
        lines.append("📊 大盘")
        for l in index_lines[:5]:
            # 提取关键信息
            lines.append(f" {l.strip().split('量')[0]}")
        lines.append("")

    # 涨跌
    if ad_info:
        a = ad_info
        lines.append(f"📈 涨跌: {a.get('up',0)}涨 {a.get('down',0)}跌 | "
                      f"涨停{a.get('limit_up',0)} 跌停{a.get('limit_down',0)}")
        lines.append(f"💰 成交: {a.get('total_amount_yi', 0):.0f}亿")
        lines.append("")

    # 主线板块
    if sector_top:
        lines.append("🔥 主线:")
        for l in sector_top[:5]:
            # 提取板块名和涨幅
            parts = l.strip().split()
            if len(parts) >= 2:
                lines.append(f" {parts[0]} {parts[1]}")
        lines.append("")

    # 资金
    if capital_lines:
        lines.append("💰 资金:")
        for l in capital_lines[-3:]:
            lines.append(f" {l.strip()}")
        lines.append("")

    # 涨停
    if limit_lines:
        count_up = sum(1 for l in limit_lines if "涨停" not in l and "🚀" not in l)
        if count_up > 0:
            lines.append("🚀 涨停:")
            for l in limit_lines[:5]:
                lines.append(f" {l.strip()}")
            lines.append("")

    return "\n".join(lines)


def run_daily_analysis():
    """完整盘后分析"""
    trade_date = get_latest_trade_date()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    print("=" * 65)
    print(f"  📋 A股盘后日线分析")
    print(f"  交易日期: {trade_date}  运行时间: {now_str}")
    print("=" * 65)

    # 1. 市场状态
    print("\n🎯 【市场状态】")
    ms = classify_market_state()
    print(f"  当前: {get_state_display(ms['state'])} (评分{ms['score']}/100)")
    print(f"  依据: {ms.get('reason', '-')}")
    metrics = ms.get('metrics', {})
    if metrics:
        print(f"  指数: {metrics.get('close', 0):.2f}  "
              f"涨跌: {metrics.get('pct_chg', 0):+.2f}%  "
              f"涨跌比: {metrics.get('ad_ratio', 0):.0%}  "
              f"量能: {metrics.get('vol_ratio', 0):.1f}x")

    # 2. 主要指数
    index_lines = analyze_indices()

    # 3. 涨跌家数
    ad_info = analyze_advance_decline(trade_date)

    # 4. 市场宽度趋势
    width_lines = analyze_market_width(trade_date, lookback=5)

    # 5. 板块排行
    sector_top, sector_bot = analyze_sector_ranking(trade_date)
    industry_lines = analyze_industry_ranking(trade_date)

    # 6. 涨停板
    limit_lines = analyze_limit_stocks(trade_date)

    # 7. 资金流向
    capital_lines = analyze_capital_flow()

    # 汇总
    print("\n" + "=" * 65)
    state_name = get_state_display(ms['state'])
    ad = ad_info.get('ad_ratio', 0.5)
    print(f"  📊 总结: {state_name} | 涨跌比{ad:.0%} | "
          f"涨停{ad_info.get('limit_up', 0)} / 跌停{ad_info.get('limit_down', 0)}")
    print("=" * 65)

    # 微信格式
    wx_msg = format_wechat_daily(
        trade_date, ad_info, sector_top, index_lines,
        capital_lines, width_lines, limit_lines
    )
    print("\n--- 微信推送 ---")
    print(wx_msg)

    # 保存
    result = {
        "trade_date": trade_date,
        "market_state": ms,
        "ad_info": ad_info,
        "wx_message": wx_msg,
    }
    result_file = os.path.join(CACHE_DIR, f"analysis_{trade_date}.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {result_file}")

    return result


if __name__ == "__main__":
    result = run_daily_analysis()
