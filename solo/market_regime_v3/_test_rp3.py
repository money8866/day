"""临时测试 RallyPullbackEngine - 热门股票"""
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

# 近期热门/有涨停拉升的股票
hot_codes = [
    '002230.SZ',  # 科大讯飞
    '300033.SZ',  # 同花顺
    '000063.SZ',  # 中兴通讯
    '688256.SH',  # 寒武纪
    '601127.SH',  # 赛力斯
    '300502.SZ',  # 新易盛
    '688111.SH',  # 金山办公
    '002594.SZ',  # 比亚迪
    '601138.SH',  # 工业富联
    '300308.SZ',  # 中际旭创
    '688041.SH',  # 海光信息
    '002371.SZ',  # 北方华创
    '300394.SZ',  # 天孚通信
    '688012.SH',  # 中微公司
    '300124.SZ',  # 汇川技术
]

qualified = []
for code in hot_codes:
    r = engine.detect(code, td)
    if r is None:
        continue
    if r.is_qualified:
        qualified.append(r)
        print(f'  ✅ {r.name}({code}): 总分{r.total_score:.0f} '
              f'拉升+{r.rally_amplitude*100:.0f}% '
              f'涨停x{r.rally_limit_up_count}(连板{r.rally_max_consecutive_limit_up}) '
              f'放量{r.rally_vol_expansion:.1f}倍 '
              f'回撤{r.drawdown_from_high*100:.1f}% '
              f'低开{r.candle_open_gap*100:.1f}%->阳线{r.candle_body_pct*100:.1f}% '
              f'入场{r.ref_price:.2f} 止损{r.stop_loss:.2f}')
    else:
        reasons = []
        if r.rally_amplitude < 0.25:
            reasons.append(f'拉升+{r.rally_amplitude*100:.0f}%')
        if r.rally_vol_expansion < 1.5:
            reasons.append(f'放量{r.rally_vol_expansion:.1f}倍')
        if r.rally_limit_up_count < 2:
            reasons.append(f'涨停{r.rally_limit_up_count}次')
        if r.drawdown_from_high == 0:
            reasons.append(f'无回撤')
        if not r.above_ma60 and r.drawdown_from_high > 0:
            reasons.append(f'破MA60')
        if not r.is_low_open_positive and r.drawdown_from_high > 0:
            reasons.append(f'非低开阳线')
        if r.drawdown_from_high > 0:
            reasons.append(f'回撤{r.drawdown_from_high*100:.0f}%')
        msg = f'  ❌ {r.name}({code}): {", ".join(reasons)}'
        if len(reasons) <= 2:
            print(msg)

print(f'\n符合条件: {len(qualified)}只')
print('完成')