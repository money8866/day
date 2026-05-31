# -*- coding: utf-8 -*-
"""
盘后日线分析 - 收盘后运行，输出当日完整市场复盘
python daily_analysis.py
"""
import os, sys, json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CACHE_DIR
from config import TDX_SH_LDAY, TDX_SZ_LDAY
from data_engine import (
    get_trade_dates_tdx, fetch_index_daily_tdx,
    fetch_concept_boards, fetch_industry_boards,
    calc_ma, calc_rsi, calc_macd,
    _cache_save, parse_tdx_day, get_tdx_all_dates
)
from market_state import classify_market_state, get_state_display
from sector_strength import analyze_sector_strength

def get_latest_trade_date():
    """获取最近一个交易日"""
    dates = get_tdx_all_dates()
    today = pd.Timestamp(datetime.now().strftime("%Y%m%d"))
    # 向前找最近的交易日
    for d in reversed(dates):
        if d <= today:
            return d.strftime("%Y%m%d")
    return dates[-1].strftime("%Y%m%d") if dates else today_str

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
            df = fetch_index_daily_tdx(ts_code)
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


def _scan_all_stocks_tdx(trade_date_str):
    """遍历通达信全部日线文件"""
    target = pd.Timestamp(trade_date_str)
    results = []
    for dirpath in [TDX_SH_LDAY, TDX_SZ_LDAY]:
        if not os.path.exists(dirpath):
            continue
        for fname in os.listdir(dirpath):
            if not fname.endswith('.day'):
                continue
            base = fname.replace('.day', '').lower()
            if len(base) != 7:
                continue
            code = base[2:]
            if base.startswith('sh') and code.startswith('000') and int(code) < 1000:
                continue
            if base.startswith('sz') and code.startswith('399'):
                continue
            try:
                df = parse_tdx_day(os.path.join(dirpath, fname))
                if len(df) < 2:
                    continue
                last = df.iloc[-1]
                if last['trade_date'] != target:
                    continue
                prev = df.iloc[-2]
                if prev['close'] <= 0:
                    continue
                pct = (last['close'] - prev['close']) / prev['close'] * 100
                results.append({'code': code, 'pct_chg': pct, 'vol': last['vol']})
            except:
                continue
    return pd.DataFrame(results) if results else pd.DataFrame()



def analyze_advance_decline(trade_date):
    """涨跌家数分析(TDX)"""
    print(f"\n📈 【涨跌家数 {trade_date}】")
    try:
        df = _scan_all_stocks_tdx(trade_date)
        if len(df) == 0:
            print("  无数据(TDX未更新)")
            return {}
        up = len(df[df['pct_chg'] > 0])
        down = len(df[df['pct_chg'] < 0])
        flat = len(df[df['pct_chg'] == 0])
        total = len(df)
        limit_up_mask = df.apply(lambda r: r['pct_chg'] >= (19.8 if r['code'].startswith(('3','688')) else 9.8), axis=1)
        limit_down_mask = df.apply(lambda r: r['pct_chg'] <= (-19.8 if r['code'].startswith(('3','688')) else -9.8), axis=1)
        limit_up = limit_up_mask.sum()
        limit_down = limit_down_mask.sum()
        big_up = len(df[(df['pct_chg'] >= 5) & (df['pct_chg'] < 9.8)])
        big_down = len(df[(df['pct_chg'] <= -5) & (df['pct_chg'] > -9.8)])
        ad_ratio = up / max(total, 1)
        ad_info = {
            "trade_date": trade_date, "total": total, "up": up, "down": down, "flat": flat,
            "limit_up": int(limit_up), "limit_down": int(limit_down),
            "big_up": big_up, "big_down": big_down,
            "ad_ratio": ad_ratio, "total_amount_yi": 0,
        }
        for l in [
            f"  总计: {total}只  上涨: {up}({up/total*100:.0f}%)  下跌: {down}({down/total*100:.0f}%)  平盘: {flat}",
            f"  涨停: {limit_up}  跌停: {limit_down}  大涨>5%: {big_up}  大跌>5%: {big_down}",
        ]:
            print(l)
        return ad_info
    except Exception as e:
        print(f"  获取失败: {e}")
        return {}


def analyze_sector_ranking():
    """板块涨跌排行"""
    print("\n🔥 【概念板块 TOP10 / BOTTOM5】")
    concepts = fetch_concept_boards(use_cache=False)
    if concepts is None or len(concepts) == 0:
        print("  无数据")
        return [], []

    top = concepts.head(10)
    bottom = concepts.tail(5).iloc[::-1]

    lines_top = []
    for _, r in top.iterrows():
        inflow = r.get('main_net_inflow', 0)
        inf_str = f"{inflow/1e8:+.1f}亿" if abs(inflow) > 1e7 else f"{inflow/1e4:+.0f}万"
        lead = r.get('lead_name', '')
        lead_pct = r.get('lead_pct', 0) if pd.notna(r.get('lead_pct', None)) else 0
        line = f"  🔥{r['name']:12s} {r.get('pct_chg',0):+6.2f}% {inf_str:>12s} 领涨:{lead}({lead_pct:+.1f}%)"
        lines_top.append(line)
        print(line)

    lines_bot = []
    print("  ---")
    for _, r in bottom.iterrows():
        inflow = r.get('main_net_inflow', 0)
        inf_str = f"{inflow/1e8:+.1f}亿" if abs(inflow) > 1e7 else f"{inflow/1e4:+.0f}万"
        line = f"  🧊{r['name']:12s} {r.get('pct_chg',0):+6.2f}% {inf_str:>12s}"
        lines_bot.append(line)
        print(line)

    return lines_top, lines_bot


def analyze_industry_ranking():
    """行业板块排行"""
    print("\n🏭 【行业板块 TOP5 / BOTTOM3】")
    industries = fetch_industry_boards(use_cache=False)
    if industries is None or len(industries) == 0:
        print("  无数据")
        return [], []

    top = industries.head(5)
    bottom = industries.tail(3).iloc[::-1]

    lines = []
    for _, r in top.iterrows():
        up_c = pd.to_numeric(r.get('up_count', 0), errors='coerce') or 0
        dn_c = pd.to_numeric(r.get('down_count', 0), errors='coerce') or 0
        line = f"  📈{r['name']:10s} {r.get('pct_chg',0):+6.2f}% 涨{up_c:.0f}/跌{dn_c:.0f}"
        lines.append(line)
        print(line)

    print("  ---")
    for _, r in bottom.iterrows():
        up_c = pd.to_numeric(r.get('up_count', 0), errors='coerce') or 0
        dn_c = pd.to_numeric(r.get('down_count', 0), errors='coerce') or 0
        line = f"  📉{r['name']:10s} {r.get('pct_chg',0):+6.2f}% 涨{up_c:.0f}/跌{dn_c:.0f}"
        lines.append(line)
        print(line)

    return lines


def analyze_limit_stocks(trade_date):
    """涨停板分析(TDX)"""
    print(f"\n🚀 【涨停/跌停 {trade_date}】")
    try:
        df = _scan_all_stocks_tdx(trade_date)
        if len(df) == 0:
            print("  无数据")
            return []
        limit_up_mask = df.apply(lambda r: r['pct_chg'] >= (19.8 if r['code'].startswith(('3','688')) else 9.8), axis=1)
        limit_down_mask = df.apply(lambda r: r['pct_chg'] <= (-19.8 if r['code'].startswith(('3','688')) else -9.8), axis=1)
        limit_up = df[limit_up_mask].sort_values('vol', ascending=False)
        limit_down = df[limit_down_mask]
        lines = []
        if len(limit_up) > 0:
            print(f"  涨停 {len(limit_up)} 只:")
            for _, r in limit_up.head(15).iterrows():
                vol_wan = r['vol'] / 10000 if r['vol'] > 0 else 0
                line = f"    {r['code']} {r['pct_chg']:+.1f}% 成交{vol_wan:.0f}万手"
                lines.append(line)
                print(line)
            if len(limit_up) > 15:
                print(f"    ... 等{len(limit_up)}只")
        if len(limit_down) > 0:
            print(f"\n  跌停 {len(limit_down)} 只:")
            for _, r in limit_down.head(5).iterrows():
                line = f"    {r['code']} {r['pct_chg']:+.1f}%"
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
    """市场宽度趋势(TDX)"""
    print(f"\n📐 【市场宽度趋势 近{lookback}日】")
    try:
        all_dates = get_tdx_all_dates()
        target = pd.Timestamp(trade_date)
        idx = None
        for i, d in enumerate(all_dates):
            if d == target:
                idx = i
                break
        if idx is None:
            return []
        start_idx = max(0, idx - lookback + 1)
        recent = all_dates[start_idx:idx + 1]
        lines = []
        for d in reversed(recent):
            d_str = d.strftime("%Y%m%d")
            df = _scan_all_stocks_tdx(d_str)
            if len(df) > 0:
                up = len(df[df['pct_chg'] > 0])
                total = len(df)
                ratio = up / max(total, 1)
                bar_len = int(ratio * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                line = f"  {d_str} {bar} {up}/{total} ({ratio:.0%})"
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
    print(f"  📋 A股盘后日线分析 [TDX]")
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
    sector_top, sector_bot = analyze_sector_ranking()
    industry_lines = analyze_industry_ranking()

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
    result_file = os.path.join(CACHE_DIR, f"analysis_{trade_date}_tdx.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {result_file}")

    return result


if __name__ == "__main__":
    result = run_daily_analysis()
