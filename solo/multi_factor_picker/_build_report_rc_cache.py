# -*- coding: utf-8 -*-
"""
report_rc 缓存首建 & 增量刷新脚本
独立运行，不依赖 bull_scan 的其他数据流
---
运行方式: python _build_report_rc_cache.py
首次运行：约 10-15 分钟（5297 只 × 120ms 限速）
之后运行：仅刷新新增股票 + 超 7 天未查的股票，增量模式
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import load_config, get_token
from data_fetcher import DataFetcher
from datetime import datetime

config = load_config()
token = get_token(config)
fetcher = DataFetcher(token, config)

# 1) 拉全市场股票列表（与 bull_scan 保持一致口径）
print(f"[{datetime.now().strftime('%H:%M:%S')}] 获取全市场股票列表...")
pro = fetcher.pro
basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry,list_date')
print(f"  共 {len(basic)} 只 A股")

stock_codes = basic['ts_code'].tolist()

# 2) 增量拉取 report_rc
print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 启动 report_rc 增量刷新")
print(f"  cache_dir = {fetcher.cache_dir}")
t0 = datetime.now()

rc_map = fetcher.get_report_rc_batch(stock_list=stock_codes, force_refresh=False, cache_days=7)

elapsed = (datetime.now() - t0).total_seconds()
print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 完成，用时 {elapsed/60:.1f} 分钟")

# 3) 统计输出
with_analyst = sum(1 for v in rc_map.values() if v['analyst_count'] >= 1)
with_3plus = sum(1 for v in rc_map.values() if v['analyst_count'] >= 3)
with_10plus = sum(1 for v in rc_map.values() if v['analyst_count'] >= 10)
high_growth = sum(1 for v in rc_map.values() if v['np_growth_current'] > 0.5)
high_buy = sum(1 for v in rc_map.values() if v['buy_ratio'] >= 0.8)

print("\n" + "=" * 60)
print("  report_rc 缓存统计")
print("=" * 60)
print(f"  总股票数:             {len(stock_codes)}")
print(f"  有一致性预测的:        {len(rc_map)}")
print(f"  至少 1 家机构覆盖:     {with_analyst}")
print(f"  ≥ 3 家机构覆盖:        {with_3plus}")
print(f"  ≥ 10 家机构覆盖:       {with_10plus}")
print(f"  一致预期增速 ≥ 50%:    {high_growth}")
print(f"  买入评级占比 ≥ 80%:    {high_buy}")
print("=" * 60)

# Top 10 样例
print("\nTop 20 高机构覆盖样例:")
sorted_items = sorted(rc_map.items(), key=lambda x: x[1]['analyst_count'], reverse=True)
for code, data in sorted_items[:20]:
    print(f"  {code}: {data['analyst_count']}家, np_growth={data['np_growth_current']*100:.1f}%, "
          f"buy={data['buy_ratio']*100:.1f}%, revision_30d={data['analyst_revision_30d']*100:.1f}%, "
          f"latest={data['latest_report_date']}")
