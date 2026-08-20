# -*- coding: utf-8 -*-
"""
backfill_fin_ind_2026H1.py - 中报/Q1 fina_indicator 全字段缓存维护
============================================================
缓存策略(每日盘后运行, 断点续传, 不覆盖 treasure 文件):
  默认模式 : 全市场逐股拉 period=20260630 全字段 → fin_ind_2026H1_full.parquet (首次/重建)
  --daily  : 每日盘后增量, 跳过已缓存 ts_code, 只补当日新披露中报 → 合并回 H1 文件
  --q1     : 全市场逐股拉 period=20260331 全字段 → fin_ind_2026Q1_full.parquet
             (Q1 于 4 月底已披露完毕, 属静态数据, 一次回填长期复用)
设计:
  - 每股 1 次调用 (period 过滤, 只取该期记录; end_date 参数不生效, 勿用)
  - 限速 120ms/次 (Tushare 500 calls/min 硬限)
  - 每 300 只增量落盘(断点续传安全), 支持 --resume 跳过已存在 ts_code
  - --limit N 调试
用法: python -X utf8 backfill_fin_ind_2026H1.py [--resume] [--limit N] [--daily] [--q1]
"""
import os
import sys
import time
import argparse

import numpy as np
import pandas as pd

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r'D:\mystock\cache_daily'
OUT = os.path.join(CACHE_DIR, 'fin_ind_2026H1_full.parquet')
OUT_Q1 = os.path.join(CACHE_DIR, 'fin_ind_2026Q1_full.parquet')

if 'TUSHARE_TOKEN' not in os.environ:
    envp = os.path.join(SOLO_DIR, '.env')
    if os.path.exists(envp):
        for _l in open(envp, encoding='utf-8'):
            if _l.strip().startswith('TUSHARE_TOKEN='):
                os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')

import tushare as ts
pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))

FIELDS = [
    "ts_code", "ann_date", "end_date", "eps", "dt_eps", "total_revenue_ps",
    "revenue_ps", "capital_rese_ps", "surplus_rese_ps", "undist_profit_ps",
    "extra_item", "profit_dedt", "gross_margin", "current_ratio", "quick_ratio",
    "cash_ratio", "ar_turn", "ca_turn", "fa_turn", "assets_turn", "op_income",
    "ebit", "ebitda", "fcff", "fcfe", "current_exint", "noncurrent_exint",
    "interestdebt", "netdebt", "tangible_asset", "working_capital",
    "networking_capital", "invest_capital", "retained_earnings", "diluted2_eps",
    "bps", "ocfps", "retainedps", "cfps", "ebit_ps", "fcff_ps", "fcfe_ps",
    "netprofit_margin", "grossprofit_margin", "cogs_of_sales", "expense_of_sales",
    "profit_to_gr", "saleexp_to_gr", "adminexp_of_gr", "finaexp_of_gr", "impai_ttm",
    "gc_of_gr", "op_of_gr", "ebit_of_gr", "roe", "roe_waa", "roe_dt", "roa",
    "npta", "roic", "roe_yearly", "roa2_yearly", "debt_to_assets", "assets_to_eqt",
    "dp_assets_to_eqt", "ca_to_assets", "nca_to_assets", "tbassets_to_totalassets",
    "int_to_talcap", "eqt_to_talcapital", "currentdebt_to_debt", "longdeb_to_debt",
    "ocf_to_shortdebt", "debt_to_eqt", "eqt_to_debt", "eqt_to_interestdebt",
    "tangibleasset_to_debt", "tangasset_to_intdebt", "tangibleasset_to_netdebt",
    "ocf_to_debt", "turn_days", "roa_yearly", "roa_dp", "fixed_assets",
    "profit_to_op", "q_saleexp_to_gr", "q_gc_to_gr", "q_roe", "q_dt_roe",
    "q_npta", "q_ocf_to_sales", "basic_eps_yoy", "dt_eps_yoy", "cfps_yoy",
    "op_yoy", "ebt_yoy", "netprofit_yoy", "dt_netprofit_yoy", "ocf_yoy",
    "roe_yoy", "bps_yoy", "assets_yoy", "eqt_yoy", "tr_yoy", "or_yoy",
    "q_sales_yoy", "q_op_qoq", "equity_yoy",
]

PERIOD = '20260630'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--resume', action='store_true', help='跳过已落盘 ts_code')
    ap.add_argument('--limit', type=int, default=0, help='只扫前 N 只(调试)')
    ap.add_argument('--q1', action='store_true', help='Q1 模式: 全市场拉 20260331 期全字段')
    ap.add_argument('--daily', action='store_true', help='每日盘后增量: 跳过已缓存 ts_code, 只补新披露')
    args = ap.parse_args()

    basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
    codes_all = basic['ts_code'].tolist()
    name_map = dict(zip(basic['ts_code'], basic['name']))

    if args.q1:
        period = '20260331'
        out = OUT_Q1
        fields = FIELDS
        print(f'Q1 全字段模式: 全市场 {len(codes_all)} 只, period={period}')
    else:
        period = PERIOD
        out = OUT
        fields = FIELDS
        print(f'H1 模式: 全市场 {len(codes_all)} 只, period={period}'
              + (' (每日增量)' if args.daily else ''))

    codes = codes_all

    done = set()
    frames = []
    # --resume 或 --daily 均跳过已缓存 ts_code
    if (args.resume or args.daily) and os.path.exists(out):
        old = pd.read_parquet(out)
        done = set(old['ts_code'])
        frames.append(old)
        print(f'{"--daily" if args.daily else "--resume"}: 已有 {len(done)} 只, 增量补充')

    if args.limit:
        codes = codes[:args.limit]

    total = len(codes)
    hit, fail, i = 0, 0, 0
    t0 = time.time()
    def persist():
        """增量落盘: 按 ann_date 排序后每股取最新披露(修订覆盖旧值)"""
        allf = pd.concat(frames, ignore_index=True)
        if 'ann_date' in allf.columns:
            allf = allf.sort_values('ann_date').drop_duplicates('ts_code', keep='last')
        else:
            allf = allf.drop_duplicates('ts_code', keep='last')
        allf.to_parquet(out, index=False)
        return allf

    for code in codes:
        i += 1
        if code in done:
            continue
        try:
            df = pro.fina_indicator(ts_code=code, period=period, fields=fields)
        except Exception as e:
            fail += 1
            if i % 50 == 0 or fail < 5:
                print(f'  [{i}/{total}] {code} 出错: {e}')
            time.sleep(0.3)
            continue
        if df is not None and not df.empty:
            rec = df.copy()
            rec['name'] = name_map.get(code, '')
            frames.append(rec)
            hit += 1
        time.sleep(0.12)
        if i % 300 == 0:
            allf = persist()
            el = time.time() - t0
            print(f'[{i}/{total}] 命中 {hit} 失败 {fail} 耗时 {el / 60:.1f}min '
                  f'(速率 {i / el:.0f}只/min, 预计剩 {(total - i) / max(i / el, 1) / 60:.0f}min)')

    allf = persist()
    el = time.time() - t0
    print(f'\n完成: 目标 {total} 只, 新增命中 {hit} 只, 失败 {fail}, 耗时 {el / 60:.1f}min')
    print(f'输出: {out} (共 {len(allf)} 行)')
    if 'ann_date' in allf.columns:
        print(f'披露日分布: {allf["ann_date"].astype(str).str[:8].value_counts().head(10).to_dict()}')


if __name__ == '__main__':
    main()
