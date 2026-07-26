"""测试：龙头回调检测 + 入场逻辑"""
import sys, json, os, yaml, pandas as pd
sys.path.insert(0, r'd:\mystock\solo')

from market_regime_v3.engines.leader_quality import LeaderQualityEngine
from market_regime_v3.engines.theme_resonance import ThemeResonanceEngine
from inst_pullback_v2.engines.pullback_detector import PullbackDetector
import stock_cache as sc

with open(r'd:\mystock\solo\market_regime_v3\config.yaml') as f:
    config = yaml.safe_load(f)
with open(r'd:\mystock\solo\inst_pullback_v2\config.yaml') as f:
    pb_config = yaml.safe_load(f)

te = ThemeResonanceEngine(config)
tr = te.evaluate('20260724')
top_themes = [t['name'] for t in (tr.top_themes or [])[:5]]

with open(r'D:\mystock\cache_daily\theme_stock_map_v2_20260724.json') as f:
    raw = json.load(f)
theme_map = raw.get('themes', raw) if isinstance(raw, dict) else {}

lq = LeaderQualityEngine(config)
lr = lq.evaluate('20260724', theme_map, top_themes)
pd_engine = PullbackDetector(pb_config)

lines = []
for ld in lr.top_leaders[:5]:
    code = ld['ts_code']
    name = ld['name']
    theme = ld.get('theme', '')
    pb = pd_engine.detect(code, '20260724')
    if pb and pb.is_qualified:
        df = sc.cached_stk_factor_pro(code, '20260326', '20260724', silent=True)
        if df is not None and not df.empty:
            close_hfq = df['close_hfq'].values
            ma5 = df['ma_bfq_5'].values if 'ma_bfq_5' in df.columns else None
            ma10 = df['ma_bfq_10'].values if 'ma_bfq_10' in df.columns else None
            latest_close = close_hfq[-1]
            ref_price = min(ma5[-1], latest_close * 0.985) if ma5 is not None else latest_close * 0.985
            stop_loss = ma10[-1] if ma10 is not None else ref_price * 0.93
            atr_val = float(df['atr_bfq'].iloc[-1]) if 'atr_bfq' in df.columns and pd.notna(df['atr_bfq'].iloc[-1]) else 0.0
            take_profit = ref_price + 3.0 * atr_val
        else:
            ref_price = stop_loss = take_profit = atr_val = 0

        lines.append(f'{name}（{code}）')
        lines.append(f'  主题: {theme} | 龙头评分: {ld["total_score"]:.0f}分')
        lines.append(f'  60日涨幅: {pb.ret_60d*100:.0f}%  回撤: {pb.drawdown_from_high*100:.1f}%  质量分: {pb.quality_score:.2f}')
        lines.append(f'  回踩均线: {pb.pullback_ma}  首次回调: {pb.is_first_pullback}')
        lines.append(f'  ── 入场逻辑 ──')
        lines.append(f'  低吸参考价: {ref_price:.2f}（{pb.pullback_ma}附近）')
        lines.append(f'  防守止损: {stop_loss:.2f}（{stop_loss/ref_price-1:.1%}）')
        if take_profit > ref_price:
            lines.append(f'  目标止盈: {take_profit:.2f}（+{take_profit/ref_price-1:.1%}）')
        lines.append(f'  ATR: {atr_val:.2f}')
        lines.append('')

if not lines:
    lines.append('当前无符合回踩条件的标的')

out_path = r'd:\mystock\solo\output\entry_check_v2.txt'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('ok:' + out_path)
