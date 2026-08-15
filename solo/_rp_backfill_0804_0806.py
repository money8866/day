"""补扫 20260804-0806 主线第一次回调信号（与 main.py 扫描逻辑一致）"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml
import pandas as pd

from market_regime_v3.engines.rally_pullback_engine import RallyPullbackEngine
import stock_cache as sc


def load_config():
    with open(os.path.join('market_regime_v3', 'config.yaml'), encoding='utf-8') as f:
        return yaml.safe_load(f)


def scan_date(trade_date: str, cfg: dict):
    rp_engine = RallyPullbackEngine(cfg)
    pro = sc._get_pro()
    sb = sc.load_stock_basic()
    sb = sb[sb['ts_code'].str.endswith(('.SH', '.SZ'))]

    db = pro.daily_basic(trade_date=trade_date,
                         fields='ts_code,total_mv,circ_mv,close,turnover_rate')
    if db is None or db.empty:
        print(f"  {trade_date} daily_basic 无数据")
        return []
    db = db[db['ts_code'].str.startswith(('60', '00'))]
    db = db[db['total_mv'] > 800_000].sort_values('total_mv', ascending=False)
    codes = db['ts_code'].tolist()
    print(f"  {trade_date} 候选池: {len(codes)} 只（主板 总市值>80亿）")

    qualified = []
    for i, code in enumerate(codes, 1):
        name_row = sb.loc[sb['ts_code'] == code, 'name']
        name = name_row.values[0] if not name_row.empty else code
        rp = rp_engine.detect(code, trade_date)
        if rp and rp.is_qualified:
            qualified.append({
                "ts_code": code,
                "name": name,
                "theme": "",
                "subtheme": "",
                "dominant_theme": "",
                "leader_score": 0,
                "total_score": rp.total_score,
                "rally_amplitude": rp.rally_amplitude,
                "rally_vol_expansion": rp.rally_vol_expansion,
                "rally_limit_up_count": rp.rally_limit_up_count,
                "rally_max_consecutive_lu": rp.rally_max_consecutive_limit_up,
                "rally_high_date": rp.rally_high_date,
                "drawdown": rp.drawdown_from_high,
                "pullback_days": rp.pullback_days,
                "is_low_open_positive": rp.is_low_open_positive,
                "candle_open_gap": rp.candle_open_gap,
                "candle_body_pct": rp.candle_body_pct,
                "subs": rp.subs,
                "ref_price": rp.ref_price,
                "stop_loss": rp.stop_loss,
                "take_profit": rp.take_profit,
                "atr": rp.atr,
            })
        if i % 500 == 0:
            print(f"    扫描 {i}/{len(codes)} 命中 {len(qualified)}")
        time.sleep(0.02)
    qualified.sort(key=lambda x: x['total_score'], reverse=True)
    return qualified


def main():
    cfg = load_config()
    out_dir = os.path.join('report_daily')
    for d in ['20260804', '20260805', '20260806']:
        q = scan_date(d, cfg)
        rp_file = os.path.join(out_dir, f"rally_pullback_{d}.json")
        with open(rp_file, 'w', encoding='utf-8') as f:
            json.dump({"trade_date": d, "market_score": None, "regime": None,
                       "signals": q}, f, ensure_ascii=False, indent=2, default=str)
        print(f"  ✅ {d}: {len(q)} 只信号 -> {rp_file}")
        for s in q:
            print(f"    {s['name']}({s['ts_code']}) 总分{s['total_score']:.0f}")


if __name__ == '__main__':
    main()
