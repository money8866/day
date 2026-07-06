# -*- coding: utf-8 -*-
"""
个股股价连续跟踪分析
==================
扫描最近30天的 Final_Self_*.md 报告，提取其中所有出现的个股代码，
按首次出现日建立跟踪表，盘后更新每只股票的 1日/5日/20日/30日 涨幅，
并计算自首次出现日（"买入日"）起的累计涨幅与持有天数。

用法:
    python stock_tracker.py                       # 盘后运行，使用当日数据
    python stock_tracker.py --date 20260703       # 指定截止日期
    python stock_tracker.py --days 30             # 扫描最近30天报告（默认）
    python stock_tracker.py --simple              # 精简输出，不打印股票明细

输出:
    D:\\mystock\\report_daily\\stock_tracking_YYYYMMDD.csv      当日跟踪快照
    D:\\mystock\\report_daily\\stock_tracking_history.csv       持久化跟踪记录（按日期追加）
"""
import os
import re
import glob
import argparse
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

# ===================== 配置 =====================
REPORT_DIR = r'D:\mystock\report_daily'
CACHE_DIR = r'D:\mystock\cache_daily'
OUTPUT_DIR = r'D:\mystock\report_daily'
TRACKING_HISTORY_FILE = os.path.join(OUTPUT_DIR, 'stock_tracking_history.csv')

# 股票代码正则：匹配 6位数字.SH / 6位数字.SZ 格式（可能带括号或【】等修饰）
STOCK_PATTERN = re.compile(r'(\d{6}\.(?:SH|SZ))')

# A股代码过滤规则：6xxxxx.SH / 0xxxxx.SZ / 3xxxxx.SZ / 688xxx.SH
A_SHARE_PATTERN = re.compile(r'^(6\d{5}\.SH|(0|3)\d{5}\.SZ|688\d{3}\.SH)$')

# 报告章节标记关键词（用于识别股票来源章节）
SECTION_KEYWORDS = {
    '强势股池': ['今日强势股票池分析', '强势股池'],
    '低吸股池': ['低吸股池', '低吸'],
    '中线股池': ['中线股池', '中线'],
    '量能爆发观察': ['量能爆发', '宽幅震荡'],
    'BWave中线': ['BWave', 'B浪'],
}


def is_a_share(ts_code):
    """判断是否为沪深A股（排除北交所、指数、ETF等）"""
    return bool(A_SHARE_PATTERN.match(ts_code))


def extract_stocks_from_md(filepath):
    """从Final报告md文件中提取所有A股代码（保持顺序去重）"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    matches = STOCK_PATTERN.findall(content)
    seen = set()
    stocks = []
    for m in matches:
        if not is_a_share(m):
            continue
        if m not in seen:
            seen.add(m)
            stocks.append(m)
    return stocks


def detect_section(filepath, ts_code):
    """识别股票在报告中所属的章节（粗略匹配）"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except:
        return '未分类'

    # 找到股票代码最后一次出现的位置
    pos = content.rfind(ts_code)
    if pos < 0:
        return '未分类'

    # 反向查找最近的章节标题
    before_text = content[:pos]
    for section, keywords in SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw in before_text[-2000:]:  # 只看前2000字符，避免误判
                return section
    return '其他'


def scan_final_reports(days=30, end_date=None):
    """扫描最近N天的Final_Self报告，提取股票池及首次出现日期"""
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')

    start_date = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=days)).strftime('%Y%m%d')

    pattern = os.path.join(REPORT_DIR, 'Final_Self_*.md')
    files = glob.glob(pattern)

    # 解析每个文件的日期
    file_date_map = {}
    for f in files:
        m = re.search(r'Final_Self_(\d{8})\.md$', os.path.basename(f))
        if m:
            date_str = m.group(1)
            if start_date <= date_str <= end_date:
                file_date_map[date_str] = f

    sorted_dates = sorted(file_date_map.keys())

    # 股票首次出现日期 + 章节映射
    stock_info = {}  # ts_code -> {'first_date': str, 'source_file': str, 'section': str}
    for d in sorted_dates:
        f = file_date_map[d]
        stocks = extract_stocks_from_md(f)
        for s in stocks:
            if s not in stock_info:
                section = detect_section(f, s)
                stock_info[s] = {
                    'first_date': d,
                    'source_file': os.path.basename(f),
                    'section': section,
                }

    return stock_info


def load_stock_df(ts_code):
    """从缓存CSV读取日线数据"""
    csv_file = os.path.join(CACHE_DIR, f'{ts_code}.csv')
    if not os.path.exists(csv_file):
        return None
    try:
        df = pd.read_csv(csv_file)
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date').reset_index(drop=True)
        return df[['trade_date', 'close', 'pct_chg']]
    except:
        return None


def compute_returns(df, target_date, periods=(1, 5, 20, 30)):
    """计算各周期涨幅（基于target_date或之前最近的交易日）"""
    if df is None or df.empty:
        return {}

    mask = df['trade_date'] <= target_date
    if not mask.any():
        return {}

    today_idx = mask.values.nonzero()[0][-1]
    today_row = df.iloc[today_idx]
    today_close = float(today_row['close'])

    returns = {
        'current_price': today_close,
        'current_date': today_row['trade_date'],
    }

    for p in periods:
        if today_idx - p >= 0:
            past_close = float(df.iloc[today_idx - p]['close'])
            if past_close > 0:
                returns[f'return_{p}d'] = (today_close / past_close - 1) * 100
            else:
                returns[f'return_{p}d'] = None
        else:
            returns[f'return_{p}d'] = None

    return returns


def update_tracking(end_date=None, days=30, simple=False):
    """主入口：更新跟踪数据"""
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')

    print(f"========== 个股股价连续跟踪分析 ==========", flush=True)
    print(f"扫描范围: 最近 {days} 天 Final_Self 报告", flush=True)
    print(f"截止日期: {end_date}", flush=True)
    print(flush=True)

    # 1. 扫描报告，提取股票池
    stock_info = scan_final_reports(days=days, end_date=end_date)
    print(f"扫描到 {len(stock_info)} 只个股", flush=True)

    if not stock_info:
        print("未找到股票数据", flush=True)
        return

    # 2. 计算每只股票的涨幅
    results = []
    total = len(stock_info)
    for i, (ts_code, info) in enumerate(sorted(stock_info.items())):
        if (i + 1) % 50 == 0:
            print(f"  处理中 {i+1}/{total}...", flush=True)

        df = load_stock_df(ts_code)
        if df is None:
            continue

        returns = compute_returns(df, end_date)
        if not returns:
            continue

        # 计算自首次出现日（买入日）起的累计涨幅
        first_date = info['first_date']
        first_mask = df['trade_date'] >= first_date
        if first_mask.any():
            first_close = float(df[first_mask].iloc[0]['close'])
            current_close = returns['current_price']
            total_return = (current_close / first_close - 1) * 100 if first_close > 0 else None
            holding_days = int(first_mask.sum())
            first_close_val = first_close
        else:
            total_return = None
            holding_days = 0
            first_close_val = None

        results.append({
            'ts_code': ts_code,
            'first_seen_date': first_date,
            'section': info['section'],
            'source_file': info['source_file'],
            'first_close': round(first_close_val, 2) if first_close_val else None,
            'holding_days': holding_days,
            'current_price': round(returns['current_price'], 2),
            'current_date': returns['current_date'],
            'return_1d': round(returns['return_1d'], 2) if returns.get('return_1d') is not None else None,
            'return_5d': round(returns['return_5d'], 2) if returns.get('return_5d') is not None else None,
            'return_20d': round(returns['return_20d'], 2) if returns.get('return_20d') is not None else None,
            'return_30d': round(returns['return_30d'], 2) if returns.get('return_30d') is not None else None,
            'total_return_since_first': round(total_return, 2) if total_return is not None else None,
        })

    if not results:
        print("未获取到有效价格数据", flush=True)
        return

    # 3. 保存为当日快照CSV
    df_result = pd.DataFrame(results)
    df_result = df_result.sort_values(['first_seen_date', 'ts_code']).reset_index(drop=True)

    snapshot_file = os.path.join(OUTPUT_DIR, f'stock_tracking_{end_date}.csv')
    df_result.to_csv(snapshot_file, index=False, encoding='utf-8-sig')
    print(f"\n当日快照已保存: {snapshot_file}", flush=True)

    # 4. 更新持久化历史记录（同日覆盖，不同日追加）
    df_result['update_date'] = end_date
    if os.path.exists(TRACKING_HISTORY_FILE):
        try:
            df_history = pd.read_csv(TRACKING_HISTORY_FILE)
            df_history = df_history[df_history['update_date'].astype(str) != end_date]
            df_history = pd.concat([df_history, df_result], ignore_index=True)
        except:
            df_history = df_result
    else:
        df_history = df_result
    df_history.to_csv(TRACKING_HISTORY_FILE, index=False, encoding='utf-8-sig')
    print(f"历史记录已更新: {TRACKING_HISTORY_FILE}", flush=True)

    # 5. 统计输出
    print("\n========== 跟踪统计 ==========", flush=True)
    print(f"跟踪股票数: {len(df_result)}", flush=True)

    # 按首次出现日期分布
    by_date = df_result.groupby('first_seen_date').size()
    print("\n按首次出现日期分布:", flush=True)
    for d, cnt in by_date.items():
        print(f"  {d}: {cnt}只", flush=True)

    # 按章节分布
    by_section = df_result.groupby('section').size().sort_values(ascending=False)
    print("\n按报告章节分布:", flush=True)
    for s, cnt in by_section.items():
        print(f"  {s}: {cnt}只", flush=True)

    # 涨幅统计
    print("\n各周期涨幅统计:", flush=True)
    for period, label in [
        ('return_1d', '1日涨幅'),
        ('return_5d', '5日涨幅'),
        ('return_20d', '20日涨幅'),
        ('return_30d', '30日涨幅'),
        ('total_return_since_first', '自首次出现累计涨幅'),
    ]:
        valid = df_result[df_result[period].notna()]
        if not valid.empty:
            avg = valid[period].mean()
            win = (valid[period] > 0).sum() / len(valid) * 100
            max_v = valid[period].max()
            min_v = valid[period].min()
            print(f"  {label}: 平均{avg:+.2f}% | 胜率{win:.1f}% | 最大{max_v:+.2f}% | 最小{min_v:+.2f}% (n={len(valid)})", flush=True)

    # 6. 显示明细（除非simple模式）
    if not simple:
        print("\n========== 个股跟踪明细（前30只） ==========", flush=True)
        display_cols = ['ts_code', 'first_seen_date', 'section', 'current_price',
                        'return_1d', 'return_5d', 'return_20d', 'return_30d',
                        'total_return_since_first', 'holding_days']
        # 仅显示有数据的列
        display_df = df_result[display_cols].head(30).copy()
        for col in ['return_1d', 'return_5d', 'return_20d', 'return_30d', 'total_return_since_first']:
            display_df[col] = display_df[col].apply(lambda x: f"{x:+.2f}%" if pd.notna(x) else "N/A")
        print(display_df.to_string(index=False), flush=True)

        # 显示涨幅Top10和Bottom10（按自首次出现累计涨幅）
        valid_total = df_result[df_result['total_return_since_first'].notna()].copy()
        if not valid_total.empty:
            print("\n========== 累计涨幅Top10 ==========", flush=True)
            top10 = valid_total.nlargest(10, 'total_return_since_first')
            for _, r in top10.iterrows():
                print(f"  {r['ts_code']} | 首现{r['first_seen_date']} | 累计{r['total_return_since_first']:+.2f}% | 持{r['holding_days']}日 | 现{r['current_price']}", flush=True)

            print("\n========== 累计涨幅Bottom10 ==========", flush=True)
            bot10 = valid_total.nsmallest(10, 'total_return_since_first')
            for _, r in bot10.iterrows():
                print(f"  {r['ts_code']} | 首现{r['first_seen_date']} | 累计{r['total_return_since_first']:+.2f}% | 持{r['holding_days']}日 | 现{r['current_price']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description='个股股价连续跟踪分析')
    parser.add_argument('--date', type=str, default=None,
                        help='截止日期 YYYYMMDD（默认今天）')
    parser.add_argument('--days', type=int, default=30,
                        help='扫描最近N天的Final_Self报告（默认30）')
    parser.add_argument('--simple', action='store_true',
                        help='精简输出，不打印股票明细')
    args = parser.parse_args()

    update_tracking(end_date=args.date, days=args.days, simple=args.simple)


if __name__ == '__main__':
    main()
