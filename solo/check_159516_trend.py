"""检查半导体设备ETF的趋势分和扩散度。"""
import os, sys, json
import numpy as np
import pandas as pd
sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from etf_resonance.core.trend import TrendScorer
from etf_resonance.core.persistence import PersistenceScorer
from etf_resonance.core.diffusion import DiffusionScorer
from etf_resonance.utils.helpers import Config
from multi_factor_picker.data_fetcher import DataFetcher
from dotenv import load_dotenv

load_dotenv(r'd:\mystock\config\.env' if os.path.exists(r'd:\mystock\config\.env') else r'd:\mystock\solo\.env')
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
dfetcher = DataFetcher(TS_TOKEN, {
    'cache': {'enabled': True, 'dir': r'd:\mystock\solo\multi_factor_picker\cache', 'expire_hours': 168},
    'tushare': {'max_retry': 3, 'retry_delay': 5}
})

START_DATE = '20251001'
END_DATE = '20260710'

print("下载半导体设备ETF日线...")
etf_df = dfetcher.get_fund_daily(ts_code='159516.SZ', start_date=START_DATE, end_date=END_DATE)
if etf_df is not None and not etf_df.empty:
    etf_df = etf_df[etf_df['trade_date'] <= END_DATE].sort_values('trade_date').reset_index(drop=True)
    print(f"  {len(etf_df)}天, 末日{etf_df['trade_date'].iloc[-1]}, 收盘{etf_df['close'].iloc[-1]}")

etf_data = {'159516.SZ': etf_df}

config = Config(r'd:\mystock\solo\etf_resonance\config.yaml')
trend_scorer = TrendScorer(config)
persist_scorer = PersistenceScorer(config)

trend_results = trend_scorer.score(etf_data)
persist_results = persist_scorer.score(etf_data)

tr = trend_results.get('159516.SZ')
pr = persist_results.get('159516.SZ')

if tr:
    print(f"\n趋势分: {tr.trend_score}")
    print(f"  ema20_above_ema60: {tr.ema20_above_ema60}")
    print(f"  符合趋势筛选(>=55): {tr.trend_score >= 55}")
if pr:
    print(f"持久分: {pr.persistence_score}")
    print(f"  符合持久筛选(>=40): {pr.persistence_score >= 40}")

qualifying = (tr and pr and tr.trend_score >= 55 and pr.persistence_score >= 40 and tr.ema20_above_ema60)
print(f"\n是否通过趋势筛选: {qualifying}")

# 扩散度
json_path = r'd:\mystock\cache_daily\etf_constituents_all.json'
all_etf_constituents = {}
if os.path.exists(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        all_etf_constituents = json.load(f)

stock_codes = [s for s in all_etf_constituents.get('159516.SZ', [])
               if not s.endswith('.BJ') and s != 'Au9999'][:50]

stock_data = {}
for code in stock_codes:
    try:
        df = dfetcher.get_daily_by_code(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is not None and not df.empty:
            if 'vol' not in df.columns and 'volume' in df.columns:
                df['vol'] = df['volume']
            stock_data[code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass

diffusion_scorer = DiffusionScorer(config)
diffusion_results = diffusion_scorer.score(
    stock_data, etf_data, {'159516.SZ': stock_codes}, {'159516.SZ': '半导体设备'}
)
dr = diffusion_results.get('159516.SZ')
if dr:
    print(f"扩散度: {dr.diffusion_score}")
    print(f"  符合扩散筛选(>50): {dr.diffusion_score > 50}")
