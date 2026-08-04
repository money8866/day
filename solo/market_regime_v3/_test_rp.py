"""临时测试 RallyPullbackEngine"""
import sys, os, yaml
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'inst_pullback_v2'))

import stock_cache as sc
from market_regime_v3.engines.rally_pullback_engine import RallyPullbackEngine

with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

engine = RallyPullbackEngine(config)
td = sc.get_effective_date()
print(f'trade_date: {td}')

test_codes = ['300750.SZ', '002709.SZ', '300274.SZ', '688981.SH', '000977.SZ']
for code in test_codes:
    r = engine.detect(code, td)
    if r is None:
        print(f'  {code}: 数据不足')
        continue
    if r.is_qualified:
        print(f'  {r.name}({code}): 总分{r.total_score:.0f} '
              f'拉升+{r.rally_amplitude*100:.0f}% '
              f'涨停x{r.rally_limit_up_count}(连板{r.rally_max_consecutive_limit_up}) '
              f'放量{r.rally_vol_expansion:.1f}倍 '
              f'回撤{r.drawdown_from_high*100:.1f}% '
              f'低开{r.candle_open_gap*100:.1f}%->阳线{r.candle_body_pct*100:.1f}% '
              f'入场{r.ref_price:.2f} 止损{r.stop_loss:.2f}')
    else:
        reasons = []
        if r.rally_amplitude < 0.25:
            reasons.append(f'拉升不足:+{r.rally_amplitude*100:.1f}%')
        if r.rally_vol_expansion < 1.5:
            reasons.append(f'放量不足:{r.rally_vol_expansion:.1f}倍')
        if r.rally_limit_up_count < 2:
            reasons.append(f'涨停不足:{r.rally_limit_up_count}次')
        if r.drawdown_from_high == 0:
            reasons.append(f'无回撤')
        if not r.above_ma60 and r.drawdown_from_high > 0:
            reasons.append(f'跌破MA60')
        if not r.is_low_open_positive and r.drawdown_from_high > 0:
            reasons.append(f'非低开阳线')
        if r.drawdown_from_high > 0:
            reasons.append(f'拉升+{r.rally_amplitude*100:.0f}%涨停{r.rally_limit_up_count}放量{r.rally_vol_expansion:.1f}回撤{r.drawdown_from_high*100:.1f}%')
        print(f'  {r.name}({code}): 未通过 - {", ".join(reasons)}')
print('完成')