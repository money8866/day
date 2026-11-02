# -*- coding: utf-8 -*-
"""V10.2 参数寻优：滑动窗口参数对全年状态分布与仓位的影响"""
import os, sys, subprocess, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(BASE, "config_v10_market_regime.json")
PY = r"C:/Users/kongx/AppData/Local/Python/bin/python.exe"


def run(w, mv):
    with open(CFG, encoding='utf-8') as f:
        txt = f.read()
    import re
    txt = re.sub(r'"vote_window": \d+', f'"vote_window": {w}', txt)
    txt = re.sub(r'"min_votes": \d+', f'"min_votes": {mv}', txt)
    with open(CFG, 'w', encoding='utf-8') as f:
        f.write(txt)
    subprocess.run([PY, os.path.join(BASE, "_bt_position_2026.py")],
                   cwd=BASE, capture_output=True, timeout=900)
    df = pd.read_csv(os.path.join(BASE, "_bt_out", "position_2026.csv"))
    df['trade_date'] = df['trade_date'].astype(str)
    df['ym'] = df['trade_date'].str[:6]
    vc = df['state'].value_counts().to_dict()
    sw = int(df['switched'].sum())
    return {
        'w': w, 'mv': mv,
        'S1': vc.get('S1', 0), 'S2': vc.get('S2', 0), 'S3': vc.get('S3', 0),
        'S4': vc.get('S4', 0), 'S5': vc.get('S5', 0), 'S6': vc.get('S6', 0),
        'auth_mean': round(df['authority_max'].mean(), 1),
        'sug_mean': round(df['suggested'].mean(), 1),
        'sug_max': int(df['suggested'].max()),
        'switch_rate': round(sw / len(df) * 100, 1),
        'days_ge60': int((df['suggested'] >= 60).sum()),
        'days_ge40': int((df['suggested'] >= 40).sum()),
        'df': df,
    }


if __name__ == '__main__':
    import io
    orig = io.open(CFG, encoding='utf-8').read()
    rows = []
    try:
        for w, mv in [(5, 2), (5, 3), (7, 2), (7, 3), (9, 3), (10, 3), (7, 4), (9, 4)]:
            r = run(w, mv)
            r.pop('df')
            rows.append(r)
            print(f"window={r['w']:>2} min_votes={r['mv']} | "
                  f"S1={r['S1']:>3} S2={r['S2']:>3} S3={r['S3']:>3} "
                  f"S4={r['S4']:>3} S5={r['S5']:>3} S6={r['S6']:>3} | "
                  f"权限均值={r['auth_mean']:>5} 建议均值={r['sug_mean']:>5} "
                  f"最大={r['sug_max']:>3} | >=40%: {r['days_ge40']:>3} >=60%: {r['days_ge60']:>3} | "
                  f"切换率={r['switch_rate']}%", flush=True)
    finally:
        with open(CFG, 'w', encoding='utf-8') as f:
            f.write(orig)
    pd.DataFrame(rows).to_csv(os.path.join(BASE, "_bt_out", "v102_tuning.csv"), index=False)
    print("\n✓ 已恢复原配置")
