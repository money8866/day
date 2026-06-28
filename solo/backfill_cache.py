# -*- coding: utf-8 -*-
"""
全市场 stk_factor_pro 缓存补足工具
一次性扫描所有股票，逐只补充缺失的历史数据到 SQLite

用法:
  python backfill_cache.py                        # 全量补足
  python backfill_cache.py --start-date 20250101   # 指定起始日期
  python backfill_cache.py --resume                # 从断点续传
  python backfill_cache.py --status                # 只看当前缓存状态
  python backfill_cache.py --codes 600519.SH 300750.SZ  # 指定股票
"""
import os, sys, time, datetime, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('TUSHARE_TOKEN', '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

import pandas as pd
import stock_cache as sc

CACHE_DIR = r'D:\mystock\cache_daily'
STOCK_BASIC = os.path.join(CACHE_DIR, 'stock_basic.csv')
PROGRESS_FILE = os.path.join(CACHE_DIR, 'backfill_progress.txt')  # 断点：已完成的股票代码集合

LOOKBACK_DAYS = 500  # 补足500个交易日的数据约2年

def get_stock_list():
    """从 stock_basic.csv 获取全市场股票列表（排除北交所）"""
    if not os.path.exists(STOCK_BASIC):
        print(f"[错误] 找不到 {STOCK_BASIC}，请先运行主程序生成")
        return []
    sb = pd.read_csv(STOCK_BASIC)
    if sb.empty or 'ts_code' not in sb.columns:
        print(f"[错误] {STOCK_BASIC} 格式异常")
        return []
    codes = sb['ts_code'].dropna().unique().tolist()
    # 过滤北交所
    codes = [c for c in codes if not c.startswith(('8', '4')) and not c.startswith('9')]
    codes.sort()
    return codes

def load_progress():
    """读取已有进度"""
    if not os.path.exists(PROGRESS_FILE):
        return set()
    try:
        with open(PROGRESS_FILE, 'r') as f:
            return set(line.strip() for line in f if line.strip())
    except:
        return set()

def save_progress(code, progress_set):
    """记录已完成股票"""
    progress_set.add(code)
    try:
        with open(PROGRESS_FILE, 'w') as f:
            f.write('\n'.join(sorted(progress_set)))
    except:
        pass

def calc_required_start():
    """计算需要的起始日期（500个交易日前，约2年）"""
    today = datetime.date.today()
    # 跳过周末
    while today.weekday() >= 5:
        today -= datetime.timedelta(days=1)
    d = today - datetime.timedelta(days=LOOKBACK_DAYS + 1)
    return d.strftime('%Y%m%d')

def show_status():
    """显示缓存状态"""
    cached_stocks = sc.count_stk_factor_stocks()
    cached_rows = sc.count_stk_factor_rows()
    total_list = get_stock_list()
    print(f"\n{'='*55}")
    print(f"  缓存状态")
    print(f"{'='*55}")
    print(f"  SQLite 已缓存: {cached_stocks} 只股票, {cached_rows} 行数据")
    print(f"  全市场待补:    {len(total_list)} 只（过滤北交所后）")
    if os.path.exists(PROGRESS_FILE):
        done = load_progress()
        print(f"  断点进度:      {len(done)} 只已完成")
    print(f"  DB 路径:       {sc.DB_PATH}")
    print(f"{'='*55}")

def backfill(codes=None, start_date=None, resume=False):
    """执行缓存补足"""
    if codes is None:
        codes = get_stock_list()
    if not codes:
        print("[错误] 没有股票需要补足")
        return

    if start_date is None:
        start_date = calc_required_start()

    # 断点续传
    progress = load_progress() if resume else set()
    if resume and progress:
        print(f"  断点续传: 跳过 {len(progress)} 只已完成的股票")

    # 跳过已经缓存完整的
    skip_count = 0
    need_count = 0
    for code in codes:
        if code in progress:
            skip_count += 1
            continue
        cached_min, cached_max = sc.get_stk_factor_range(code)
        if cached_min and cached_min <= start_date:
            progress.add(code)
            skip_count += 1
            continue
        need_count += 1

    pending = [c for c in codes if c not in progress]
    total = len(pending)
    print(f"\n{'='*55}")
    print(f"  全市场 stk_factor_pro 缓存补足")
    print(f"  起始日期: {start_date} (约2年数据)")
    print(f"  已缓存完整: {skip_count} 只, 需要补充: {total} 只")
    print(f"{'='*55}\n")

    if total == 0:
        print("  所有股票缓存完整，无需补充")
        return

    t0 = time.time()
    ok, fail = 0, 0

    for i, code in enumerate(pending):
        # 进度提示
        elapsed = time.time() - t0
        eta = elapsed / max(i + 1, 1) * (total - i - 1) if i > 0 else 0
        pct = (i + 1) / total * 100
        print(f"  [{i+1}/{total}] {pct:.0f}% {code}  ETA {eta:.0f}s", end='')

        try:
            # 补一份默认日期范围的数据（同时检验缓存完整性）
            end_date = sc.get_effective_date()
            df = sc.cached_stk_factor_pro(code, start_date, end_date)
            if df is not None and len(df) >= 60:
                ok += 1
                save_progress(code, progress)
                cached_min, cached_max = sc.get_stk_factor_range(code)
                print(f"  OK ({len(df)}行, {cached_min}~{cached_max})")
            else:
                # 可能是次新股或停牌股，缓存几条算几条
                rows = len(df) if df is not None else 0
                cached_min, cached_max = sc.get_stk_factor_range(code)
                print(f"  部分({rows}行, {cached_min}~{cached_max})")
                save_progress(code, progress)
                ok += 1
        except Exception as e:
            fail += 1
            print(f"  失败: {e}")
            time.sleep(1)  # 失败后多等一下

    # 汇总
    total_time = time.time() - t0
    print(f"\n{'='*55}")
    print(f"  完成！成功 {ok} 只, 失败 {fail} 只")
    print(f"  耗时 {total_time:.0f}s, 平均 {total_time/max(ok+fail,1):.1f}s/只")
    cached_stocks = sc.count_stk_factor_stocks()
    cached_rows = sc.count_stk_factor_rows()
    print(f"  SQLite 累计: {cached_stocks} 只, {cached_rows} 行")
    print(f"{'='*55}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='全市场 stk_factor_pro 缓存补足工具')
    parser.add_argument('--start-date', help='起始日期 YYYYMMDD，默认自动计算约2年前')
    parser.add_argument('--resume', action='store_true', help='从断点续传')
    parser.add_argument('--status', action='store_true', help='只看缓存状态')
    parser.add_argument('--codes', nargs='+', help='指定股票代码列表')
    parser.add_argument('--clear-progress', action='store_true', help='清除断点记录重新补')
    args = parser.parse_args()

    if args.clear_progress and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print("  断点记录已清除")

    if args.status:
        show_status()
    elif args.codes:
        backfill(codes=args.codes, start_date=args.start_date)
    else:
        backfill(start_date=args.start_date, resume=args.resume)
