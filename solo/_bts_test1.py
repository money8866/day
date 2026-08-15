# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r'd:\mystock\solo')
sys.stdout.reconfigure(encoding='utf-8')
from bts.data import load_daily, market_regime, get_name_map
from bts.engine import BTSEngine

ts = '300404.SZ'
name = get_name_map().get(ts, '博瑞医药')
eng = BTSEngine()
for d in ('20260807', '20260810', '20260811', '20260812', '20260814'):
    df = load_daily(ts, d, 260)
    print('='*70)
    print(f'date={d} bars={0 if df is None else len(df)} regime={market_regime(d)}')
    if df is None:
        continue
    r = eng.score(df, ts_code=ts, name=name, market_regime=market_regime(d))
    print(f'  BTS={r.bts_score} Entry={r.entry_score} grade={r.grade} signal={r.signal} buy={r.buy_point}')
    print(f'  breakout={r.breakout_date} days_after={r.days_after_breakout} base_days={r.base_days} '
          f'range={r.base_range*100:.1f}% res={r.resistance:.2f} touches={r.resistance_touches}')
    print(f'  amp={r.breakout_amp*100:+.1f}% vr_bo={r.vol_ratio_breakout:.2f} candle={r.candle_pos*100:.0f}%')
    print(f'  close={r.close} ma5={r.ma5:.2f} dist_ma5={r.distance_ma5*100:+.1f}% ma5streak={r.ma5_up_streak} accel={r.ma5_accel}')
    print(f'  vol_ratio={r.vol_ratio:.2f} v2={r.v2:.2f} persist={r.volume_persistence} spike={r.spike_volume} up_dn={r.up_down_ratio:.2f}')
    print(f'  gates: bo={r.gate_breakout} ma5={r.gate_ma5} vol={r.gate_vol}')
    print(f'  scores: base={r.score_base} bo={r.score_breakout} ma5={r.score_ma5} vol={r.score_vol} vp={r.score_vol_price} pb={r.score_pullback} ext={r.score_ext}')
