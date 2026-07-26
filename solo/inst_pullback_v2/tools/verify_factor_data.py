"""
全面因子数据采集验证脚本
检查每个因子从原始数据到最终分数的完整链路
"""
import os, sys, json, sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import stock_cache as sc
from data.loader import DataLoader, load_config
from data.indicators import ema, sma, slope, rsi, macd, adx, atr, new_high_count, price_position, volume_ratio

OK = "✅"
ERR = "❌"
WARN = "⚠️"
INFO = "🔍"

td = "20260724"
pd_td = pd.to_datetime(td)
start_date = (pd_td - timedelta(days=250)).strftime('%Y%m%d')

print("=" * 70)
print("  逐因子数据采集验证")
print(f"  交易日期: {td}")
print("=" * 70)

# ============================================================
# 1. 数据源底层检查
# ============================================================
print("\n── 1. 数据源底层检查 ──")

db_path = r"D:\mystock\cache_daily\stock_data.db"
conn = sqlite3.connect(db_path)

# 1a. stk_factor_pro 表
cnt = conn.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date = ?", (td,)).fetchone()[0]
print(f"  {OK if cnt > 4000 else ERR} stk_factor_pro {td}: {cnt} 行")

cols = [r[1] for r in conn.execute("PRAGMA table_info(stk_factor_pro)").fetchall()]
print(f"  {INFO} stk_factor_pro 字段: {cols}")

# 1b. 检查关键字段是否有值
sample = conn.execute(
    "SELECT close, amount, vol, high, low, pct_chg, pe_ttm, pb FROM stk_factor_pro WHERE trade_date = ? LIMIT 5",
    (td,)
).fetchall()
names = ['close', 'amount', 'vol', 'high', 'low', 'pct_chg', 'pe_ttm', 'pb']
for name, vals in zip(names, zip(*sample)):
    nulls = sum(1 for v in vals if v is None)
    print(f"  {OK if nulls == 0 else WARN} 字段 {name}: 抽样5条, NULL={nulls}")

# 1c. 检查是否有 ETF 数据
etf_cnt = conn.execute(
    "SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date = ? AND (ts_code LIKE '159%' OR ts_code LIKE '51%' OR ts_code LIKE '56%' OR ts_code LIKE '58%')",
    (td,)
).fetchone()[0]
print(f"  {WARN if etf_cnt == 0 else OK} ETF数据在stk_factor_pro: {etf_cnt} 行")

# 1d. 检查各个代码前缀的数量
for prefix in ['000', '002', '300', '600', '601', '603', '605', '688', '159', '510', '512', '515', '516', '561', '562']:
    cnt = conn.execute(
        f"SELECT COUNT(DISTINCT ts_code) FROM stk_factor_pro WHERE trade_date = ? AND ts_code LIKE '{prefix}%'",
        (td,)
    ).fetchone()[0]
    if cnt > 0:
        print(f"  {INFO} {prefix}xxx: {cnt} 只")

conn.close()

# 1e. Moneyflow 数据
loader = DataLoader(td)
mf = loader.load_moneyflow(td)
if mf is not None:
    print(f"  {OK} moneyflow {td}: {len(mf)} 行, 字段: {list(mf.columns)[:10]}")
    if 'net_mf_amount' in mf.columns:
        nonzero = (mf['net_mf_amount'] != 0).sum()
        print(f"  {OK if nonzero > 100 else WARN} net_mf_amount 非零: {nonzero}/{len(mf)}")
else:
    print(f"  {ERR} moneyflow 无数据")

# 1f. Theme stock map
theme_map = loader.load_theme_stock_map()
if theme_map:
    print(f"  {OK} theme_stock_map: {len(theme_map)} 个主题")
    sample_theme = list(theme_map.keys())[0]
    sample_stocks = theme_map[sample_theme]
    stock_type = type(sample_stocks[0]) if sample_stocks else 'empty'
    print(f"  {INFO} 示例主题 '{sample_theme}': {len(sample_stocks)} 只, 类型={stock_type}")
    if sample_stocks and isinstance(sample_stocks[0], dict):
        print(f"  {INFO} 示例股票: {sample_stocks[0]}")
else:
    print(f"  {ERR} theme_stock_map 无数据")

# 1g. Theme score JSON
for fpath in [r"D:\mystock\cache_daily\theme_score.json", r"D:\mystock\cache_daily\hot_theme.json"]:
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"  {OK} {os.path.basename(fpath)}: 存在 (keys={list(data.keys())[:5]})")
    else:
        print(f"  {WARN} {os.path.basename(fpath)}: 不存在")

# ============================================================
# 2. 指标库验证
# ============================================================
print("\n── 2. 指标库函数验证 ──")
close = pd.Series([100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                    110, 111, 112, 113, 114, 115, 116, 117, 118, 119,
                    120, 119, 118, 117, 116, 115, 114, 113, 112, 111])

for fn_name, fn in [('ema', ema), ('sma', sma), ('slope', slope), ('rsi', rsi)]:
    try:
        result = fn(close, 10)
        if result is not None:
            print(f"  {OK} {fn_name}: OK")
        else:
            print(f"  {WARN} {fn_name}: 返回 None")
    except Exception as e:
        print(f"  {ERR} {fn_name}: {e}")

# atr/adx 需要 high, low, close 三个参数
try:
    high = close + np.random.uniform(1, 3, len(close))
    low = close - np.random.uniform(1, 3, len(close))
    result = atr(high, low, close, 14)
    print(f"  {OK} atr: OK (last_val={result.iloc[-1]:.4f})" if result is not None else f"  {WARN} atr: 返回 None")
except Exception as e:
    print(f"  {ERR} atr: {e}")

try:
    result = adx(high, low, close, 14)
    print(f"  {OK} adx: OK (last_val={result.iloc[-1]:.4f})" if result is not None else f"  {WARN} adx: 返回 None")
except Exception as e:
    print(f"  {ERR} adx: {e}")

# ============================================================
# 3. ETF 数据加载链路
# ============================================================
print("\n── 3. ETF 数据加载链路 ──")

# 3a. 检查 load_index_data 的 ETF 代码后缀处理
pro = sc._get_pro()
pro_available = pro is not None
print(f"  {OK if pro_available else ERR} Tushare Pro API: {'可用' if pro_available else '不可用'}")

# 3b. 测试各 ETF 的数据加载
etf_test_codes = {
    '159828.SZ': '创新药ETF',
    '512480.SH': '半导体ETF',
    '510300.SH': '沪深300ETF', 
    '515050.SH': '5GETF',
    '561910.SH': '电池ETF',
}

for code, name in etf_test_codes.items():
    df = loader.load_index_data(code, start_date, td, silent=True)
    if df is not None and not df.empty and len(df) >= 20:
        close_val = df['close'].iloc[-1]
        cols = list(df.columns)
        print(f"  {OK} {code} ({name}): {len(df)}行, close={close_val:.2f}, fields={cols}")
    else:
        rows = len(df) if df is not None else 0
        print(f"  {ERR} {code} ({name}): 无数据 (rows={rows})")

# 3c. 检查市场状态引擎中 ETF 资金评分的 ETF 列表
etf_pool = loader.get_etf_pool()
print(f"  {INFO} ETF Pool: {len(etf_pool)} 只ETF")
sample_etf = list(etf_pool.keys())[:5]
for code in sample_etf:
    df = loader.load_index_data(code, start_date, td, silent=True)
    status = OK if df is not None and not df.empty else ERR
    print(f"  {status} ETF Pool {code} ({etf_pool[code]}): {'有数据' if df is not None and not df.empty else '无数据'}")

# ============================================================
# 4. 股票数据加载验证
# ============================================================
print("\n── 4. 股票数据加载验证 ──")
test_codes = ["002317.SZ", "688266.SH", "300750.SZ", "600519.SH", "000858.SZ"]
for code in test_codes:
    df = loader.load_stk_factor(code, start_date, td, silent=True)
    if df is not None and not df.empty:
        close_val = df['close'].iloc[-1]
        has_amount = 'amount' in df.columns
        has_vol = 'vol' in df.columns
        print(f"  {OK} {code}: {len(df)}行, close={close_val:.2f}, amount={has_amount}, vol={has_vol}")
    else:
        print(f"  {ERR} {code}: 无数据")

# ============================================================
# 5. Market State 因子子项验证
# ============================================================
print("\n── 5. Market State 因子子项验证 ──")
from engines.market_state import MarketStateEngine
ms_engine = MarketStateEngine(load_config())
ms_engine.loader = loader

# 5a. 趋势评分 - 各指数
indices = ["000300.SH", "000852.SH", "399006.SZ", "000688.SH"]
for idx in indices:
    df = loader.load_index_data(idx, start_date, td, silent=True)
    if df is not None and not df.empty:
        close = df['close']
        ma20 = sma(close, 20)
        ma60 = sma(close, 60)
        sl = slope(ma20.dropna().reset_index(drop=True), 5)
        print(f"  {INFO} {idx}: close={close.iloc[-1]:.2f}, ma20={ma20.iloc[-1]:.2f}, ma60={ma60.iloc[-1]:.2f}, sl20={sl.iloc[-1] if sl is not None and len(sl) > 0 else 'N/A':.4f}")

# 5b. 新高评分
for idx in indices:
    df = loader.load_index_data(idx, (pd_td - timedelta(days=60)).strftime('%Y%m%d'), td, silent=True)
    if df is not None and not df.empty:
        nh = new_high_count(df['close'], 20)
        print(f"  {INFO} {idx} 新高: nh_20d={nh.iloc[-1] if nh is not None and len(nh) > 0 else 'N/A'}")

# 5c. 情绪评分
df_300 = loader.load_index_data("000300.SH", (pd_td - timedelta(days=30)).strftime('%Y%m%d'), td, silent=True)
if df_300 is not None and not df_300.empty:
    ret_5d = df_300['close'].pct_change(5).iloc[-1]
    print(f"  {INFO} 沪深300 5日收益: {ret_5d:.4%}")

# ============================================================
# 6. Theme Engine 因子子项验证
# ============================================================
print("\n── 6. Theme Engine 因子子项验证 ──")
from engines.theme_engine import InstitutionThemeEngine, load_etf_mapping
te_engine = InstitutionThemeEngine(load_config())
te_engine.loader = loader

# 6a. 检查主题评分文件
theme_score_path = r"D:\mystock\cache_daily\theme_score.json"
if os.path.exists(theme_score_path):
    with open(theme_score_path, 'r', encoding='utf-8') as f:
        ts_data = json.load(f)
    print(f"  {OK} theme_score.json: keys={list(ts_data.keys())[:3]}")
    scores = ts_data.get('scores', {})
    for theme in ['创新药', '中药', 'AI芯片', '军工']:
        if theme in scores:
            print(f"  {INFO} 主题评分 '{theme}': {scores[theme]}")
        else:
            print(f"  {WARN} 主题评分 '{theme}': 不在文件中")
else:
    print(f"  {WARN} theme_score.json: 文件不存在")

# 6b. 检查每个主题的ETF映射
_etf_map = load_etf_mapping()
for theme in ['创新药', '中药', '保险', '银行', '红利公用事业', 'AI芯片', '军工', '人形机器人']:
    etf = _etf_map.get(theme, 'N/A')
    print(f"  {INFO} {theme} -> ETF: {etf}")

# 6c. 检查Theme Engine各子项计算
# 取一个主题来验证
theme_map = loader.load_theme_stock_map()
if theme_map and '创新药' in theme_map:
    raw_stocks = theme_map['创新药']
    codes = []
    for s in raw_stocks:
        if isinstance(s, dict) and 'code' in s:
            codes.append(s['code'])
        elif isinstance(s, str):
            codes.append(s)
    
    # preload
    te_engine._preload_bulk(codes, start_date, td)
    
    # 趋势评分
    trend = te_engine._calc_theme_trend(codes)
    print(f"  {INFO} 创新药 趋势评分: {trend:.4f}")
    
    # 资金评分
    money = te_engine._calc_theme_money(codes, td)
    print(f"  {INFO} 创新药 资金评分: {money:.4f}")
    
    # 持续时间
    dur = te_engine._calc_theme_duration(codes, td)
    print(f"  {INFO} 创新药 持续时间: {dur:.4f}")
    
    # ETF趋势
    etf_code = _etf_map.get('创新药', '')
    etf_trend = te_engine._calc_etf_trend(etf_code, start_date, td)
    print(f"  {INFO} 创新药 ETF({etf_code})趋势: {etf_trend:.4f}")
    
    # 龙头强度
    leader_str = te_engine._calc_leader_strength(codes)
    print(f"  {INFO} 创新药 龙头强度: {leader_str:.4f}")

# ============================================================
# 7. Leader Engine 因子子项验证
# ============================================================
print("\n── 7. Leader Engine 因子子项验证 ──")
from engines.leader_engine import LeaderEngineV3, LeaderHistoryDB

# 7a. 历史DB
hist_db = LeaderHistoryDB()
hist = hist_db.get_theme_history('创新药', td, 120)
print(f"  {OK if len(hist) > 0 else WARN} leader_history_db: {len(hist)} 行")
if len(hist) > 0:
    recent_dates = sorted(hist['trade_date'].unique(), reverse=True)[:5]
    print(f"  {INFO} 最近5天有记录: {recent_dates}")

# 7b. 检查截面因子
if theme_map and '创新药' in theme_map:
    le_engine = LeaderEngineV3(load_config())
    le_engine.loader = loader
    results = le_engine.evaluate(codes, theme_name='创新药', etf_code='159828', trade_date=td)
    
    if results:
        for r in results:
            print(f"  {INFO} {r.name}: ret_60d={r.ret_60d:.4f}, ret_20d={r.ret_20d:.4f}, "
                  f"amount={r.amount_score:.4f}, nh={r.new_high_score:.4f}, "
                  f"etf_corr={r.etf_corr_score:.4f}")

# ============================================================
# 8. Pullback Detector 因子子项验证
# ============================================================
print("\n── 8. Pullback Detector 因子子项验证 ──")
from engines.pullback_detector import PullbackDetector
pb_engine = PullbackDetector(load_config())
pb_engine.loader = loader

for code in ["002317.SZ", "688266.SH"]:
    result = pb_engine.detect(code, td)
    if result:
        print(f"  {INFO} {result.name}: qualified={result.is_qualified}, "
              f"ret_60d={result.ret_60d:.4f}, drawdown={result.drawdown_from_high:.4f}, "
              f"ma_type={result.pullback_ma}, first={result.is_first_pullback}, "
              f"no_panic={result.no_volume_panic}, quality={result.quality_score:.4f}")
    else:
        print(f"  {WARN} {code}: 数据不足")

# 逐个检查Pullback的每个子步骤
for code in ["002317.SZ"]:
    df = loader.load_stk_factor(code, start_date, td, silent=True)
    if df is not None and not df.empty:
        close = df['close'].values
        ma60 = pd.Series(close).rolling(60).mean()
        sl_60 = slope(ma60.dropna().reset_index(drop=True), 5)
        s60 = sl_60.iloc[-1] if sl_60 is not None and len(sl_60) > 0 else 0
        print(f"  {INFO} MA60斜率: {s60:.6f} (>0 = {s60 > 0})")
        
        ret_60d = close[-1] / close[-min(60, len(close))] - 1
        print(f"  {INFO} 60日涨幅: {ret_60d:.4%} (>=30% = {ret_60d >= 0.30})")
        
        high_20 = close[-20:].max()
        print(f"  {INFO} 20日新高: {high_20:.2f}, 当前: {close[-1]:.2f}, 低于新高={close[-1] < high_20}")
        
        high_60 = close[-60:].max()
        dd = (high_60 - close[-1]) / high_60
        print(f"  {INFO} 回撤幅度: {dd:.4%} (5%-20% = {0.05 <= dd <= 0.20})")
        
        for ma_p in [10, 20, 30]:
            ma = pd.Series(close).rolling(ma_p).mean()
            dist = abs(close[-1] - ma.iloc[-1]) / ma.iloc[-1]
            print(f"  {INFO} MA{ma_p}: {ma.iloc[-1]:.2f}, 距离: {dist:.4%} (<3% = {dist < 0.03})")

# ============================================================
# 9. Chip Analyzer 因子子项验证
# ============================================================
print("\n── 9. Chip Analyzer 因子子项验证 ──")
from engines.chip_analyzer import ChipAnalyzer
chip_engine = ChipAnalyzer(load_config())
chip_engine.loader = loader

for code in ["002317.SZ", "688266.SH"]:
    result = chip_engine.analyze(code, td)
    if result:
        print(f"  {INFO} {result.name}: stable={result.is_stable}, "
              f"stability={result.stability_score:.4f}, "
              f"centroid_shift={result.centroid_shift:.4f}, "
              f"profit_ratio={result.profit_ratio:.4f}, "
              f"concentration={result.concentration:.4f}, "
              f"avg_cost={result.avg_cost:.2f}, peak={result.chip_peak:.2f}")
    else:
        print(f"  {WARN} {code}: 数据不足")

# ============================================================
# 10. ETF Resonance 因子子项验证
# ============================================================
print("\n── 10. ETF Resonance 因子子项验证 ──")
from engines.chip_analyzer import ETFResonance
etf_res_engine = ETFResonance(load_config())
etf_res_engine.loader = loader

test_pairs = [("002317.SZ", "159828"), ("688266.SH", "159828"), ("300750.SZ", "561910")]
for code, etf in test_pairs:
    name = loader.get_stock_name(code)
    result = etf_res_engine.evaluate(code, etf, td)
    if result:
        print(f"  {INFO} {name} (ETF={etf}): resonant={result.get('is_resonant')}, "
              f"score={result.get('score')}, "
              f"ma20_up={result.get('etf_ma20_up')}, "
              f"ma60_up={result.get('etf_ma60_up')}, "
              f"ret_20d={result.get('etf_ret_20d')}, "
              f"nh={result.get('new_high_recent')}")
    else:
        print(f"  {WARN} {name} (ETF={etf}): 无结果")

# ============================================================
# 11. Fund Flow 因子子项验证
# ============================================================
print("\n── 11. Fund Flow 因子子项验证 ──")
from engines.chip_analyzer import FundFlow
ff_engine = FundFlow(load_config())
ff_engine.loader = loader

for code in ["002317.SZ", "688266.SH", "300750.SZ"]:
    name = loader.get_stock_name(code)
    result = ff_engine.evaluate(code, td)
    if result:
        print(f"  {INFO} {name}: recovering={result.get('is_recovering')}, "
              f"score={result.get('score')}, "
              f"net_flows={result.get('net_flows')}")
    else:
        print(f"  {WARN} {name}: 无结果")

# 检查moneyflow数据范围
mf_dates = loader.get_recent_trade_dates(5)
print(f"  {INFO} 最近5个交易日: {mf_dates}")
mf_multi = loader.load_moneyflow_multi(mf_dates)
if mf_multi is not None:
    print(f"  {INFO} moneyflow_multi: {len(mf_multi)}行, 日期范围: {sorted(mf_multi['trade_date'].unique())}")

# ============================================================
# 12. Trend Health 因子子项验证
# ============================================================
print("\n── 12. Trend Health 因子子项验证 ──")
from engines.chip_analyzer import TrendHealth
th_engine = TrendHealth(load_config())
th_engine.loader = loader

for code in ["002317.SZ", "688266.SH"]:
    name = loader.get_stock_name(code)
    result = th_engine.evaluate(code, td)
    if result:
        print(f"  {INFO} {name}: healthy={result.get('is_healthy')}, "
              f"score={result.get('score')}, "
              f"ema20={result.get('ema20')}, ema60={result.get('ema60')}, ema120={result.get('ema120')}, "
              f"aligned={result.get('ema_aligned')}, "
              f"adx={result.get('adx')}, macd={result.get('macd_score')}")
    else:
        print(f"  {WARN} {name}: 无结果")

# ============================================================
# 13. Lifecycle 因子子项验证
# ============================================================
print("\n── 13. Lifecycle 因子子项验证 ──")
from engines.chip_analyzer import ThemeLifecycleFilter
lc_engine = ThemeLifecycleFilter(load_config())
lc_engine.loader = loader

for theme in ['创新药', 'AI芯片', '军工']:
    theme_stocks = []
    if theme_map and theme in theme_map:
        raw_stocks = theme_map[theme]
        for s in raw_stocks:
            if isinstance(s, dict) and 'code' in s:
                theme_stocks.append(s['code'])
            elif isinstance(s, str):
                theme_stocks.append(s)
    
    result = lc_engine.evaluate(theme, theme_stocks, td)
    print(f"  {INFO} {theme}: stage={result.get('stage')}, "
          f"allowed={result.get('is_allowed')}, "
          f"momentum={result.get('momentum')}, "
          f"stocks_analyzed={len(theme_stocks)}")

# 验证单个股票的生命周期momentum计算
if theme_stocks:
    code = theme_stocks[0]
    df = loader.load_stk_factor(code, start_date, td, silent=True)
    if df is not None and len(df) >= 60:
        close = df['close']
        ret_20d = close.iloc[-1] / close.iloc[-min(20, len(close))] - 1
        ret_60d = close.iloc[-1] / close.iloc[-min(60, len(close))] - 1
        ratio = ret_20d / (abs(ret_60d) + 1e-10) if ret_60d != 0 else 0
        print(f"  {INFO} 示例: {code} ret_20d={ret_20d:.4f}, ret_60d={ret_60d:.4f}, ratio={ratio:.4f}")

# ============================================================
# 14. Risk Filter 因子子项验证
# ============================================================
print("\n── 14. Risk Filter 因子子项验证 ──")
from engines.chip_analyzer import RiskFilter
rf_engine = RiskFilter(load_config())
rf_engine.loader = loader

for code in ["002317.SZ", "000001.SZ", "300750.SZ", "600519.SH"]:
    name = loader.get_stock_name(code)
    result = rf_engine.evaluate(code, td)
    print(f"  {INFO} {name}: clean={result.get('is_clean')}, "
          f"score={result.get('score')}, "
          f"issues={result.get('issues')}")

# 检查几个ST股票
for code in ["000005.SZ"]:
    name = loader.get_stock_name(code)
    df = loader.load_stk_factor(code, (pd_td - timedelta(days=60)).strftime('%Y%m%d'), td, silent=True)
    if df is not None and not df.empty:
        if 'amount' in df.columns:
            avg_amount = df['amount'].tail(20).mean()
            print(f"  {INFO} {name} ({code}): 日均成交={avg_amount:.0f} ({avg_amount/1e5:.1f}亿)")

# ============================================================
# 15. Alpha Scorer 验证
# ============================================================
print("\n── 15. Alpha Scorer 验证 ──")
from engines.alpha_scorer import AlphaScorer
as_engine = AlphaScorer(load_config())

components = {
    'ts_code': 'TEST.SZ', 'name': '测试股票', 'theme': '创新药',
    'market_state': 0.51, 'theme_strength': 0.337, 'leader_score': 0.672,
    'pullback_quality': 0.85, 'etf_resonance': 0.75, 'chip_stability': 0.70,
    'fund_flow_recovery': 0.60, 'trend_health': 0.55,
    'buy_type': 'MA20回踩', 'etf_code': '159828', 'suggestion': '分批买入',
}
result = as_engine.score(components)
print(f"  {OK} Alpha: {result.alpha}, Rating: {result.rating}")

# 验证公式
expected = (20*0.51 + 15*0.337 + 15*0.672 + 20*0.85 + 10*0.75 + 10*0.70 + 5*0.60 + 5*0.55)
print(f"  {INFO} 公式验证: expected={expected:.1f}, actual={result.alpha}")

print("\n" + "=" * 70)
print("  数据采集验证完成")
print("=" * 70)