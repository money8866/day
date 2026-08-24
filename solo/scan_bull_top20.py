# -*- coding: utf-8 -*-
"""Bull中报股池Top20：20260824 状态 + 8月逐日 PRIMARY_BUY 扫描。"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rib.engine import RIBEngine

# (临时文件, ts_code, name)
BULL = [
    (r"ff47f66b-75a9-4da2-b7eb-a8dc517d1fab.txt", "601872.SH", "招商轮船"),
    (r"2653f5c5-01bc-4523-8949-7c511e313982.txt", "002709.SZ", "天赐材料"),
    (r"95bc4b83-07c0-4bed-9082-209f2eb0a542.txt", "688002.SH", "睿创微纳"),
    (r"5750e63a-a49d-40a6-99a6-ec80772e10dc.txt", "600989.SH", "宝丰能源"),
    (r"8820de66-f513-4f7b-8deb-4f4e4b8448f6.txt", "000807.SZ", "云铝股份"),
    (r"30178983-d764-4a3e-83c2-3879b0df64cd.txt", "002558.SZ", "巨人网络"),
    (r"437a0cb9-cab6-42dc-9cea-b4d86d54fc2b.txt", "000426.SZ", "兴业银锡"),
    (r"331a0859-8f1c-4a04-87dd-40cd1f88e025.txt", "688578.SH", "艾力斯"),
    (r"b6b62668-daa1-4d17-9d41-ba32d28999cf.txt", "688111.SH", "金山办公"),
    (r"65b97a56-387c-4a0f-9572-59dfea3810b5.txt", "000792.SZ", "盐湖股份"),
    (r"49ce3550-4cea-4adb-976f-dc1790ea7e2c.txt", "002648.SZ", "卫星化学"),
    (r"7abdd6df-62d4-41a9-8a04-47ffeafcc055.txt", "002611.SZ", "东方精工"),
    (r"c01f1747-28c3-47ac-ad89-f818ec87a94d.txt", "600026.SH", "中远海能"),
    (r"39ea80c7-63e8-46f6-8a62-811e5965b1ba.txt", "002414.SZ", "高德红外"),
    (r"94acd851-be15-414d-846b-58f654bc3fc5.txt", "000567.SZ", "海德股份"),
    (r"7fd623aa-8ecb-4841-bef5-57564e3f3be4.txt", "002653.SZ", "海思科"),
    (r"98f2e9c0-ae55-49ae-a15b-c3a87fc398ee.txt", "002039.SZ", "黔源电力"),
    (r"26f82033-f61b-4aab-9567-b96fed0e4da6.txt", "000603.SZ", "盛达资源"),
    (r"798200ea-b107-4f25-8e14-e502b36977f2.txt", "301500.SZ", "飞南资源"),
    (r"d28578b7-272b-492c-9465-603bc3a9d82c.txt", "688059.SH", "华锐精密"),
]
TMP = r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output"


def parse(path: str) -> pd.DataFrame:
    import json
    from datetime import datetime, timezone, timedelta
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if content.startswith("The MCP server responded with:"):
        content = content[len("The MCP server responded with:"):].strip()
    data = json.loads(json.loads(content)[0]["text"])["data"]
    tz = timezone(timedelta(hours=8))
    recs = []
    for it in data["item"]:
        dt = datetime.fromtimestamp(it["date_ms"] / 1000, tz=tz)
        recs.append({
            "trade_date": dt.strftime("%Y%m%d"),
            "open": it["open_price"], "high": it["high_price"],
            "low": it["low_price"], "close": it["close_price"],
            "vol": it["volume"], "amount": it["turnover"],
        })
    return pd.DataFrame(recs).sort_values("trade_date").reset_index(drop=True)


def main():
    engine = RIBEngine()
    print("=" * 72)
    print("Bull中报股池 Top20 — 20260824 收盘状态")
    print("=" * 72)

    results = {}
    for fn, ts_code, name in BULL:
        path = os.path.join(TMP, fn)
        if not os.path.exists(path):
            print(f"[跳过] {name} 缺文件 {fn}")
            continue
        df = parse(path)
        if len(df) < 130:
            print(f"[数据不足] {name}: {len(df)} 根")
            continue
        try:
            r = engine.analyze(df, ts_code=ts_code, name=name)
        except Exception as e:
            print(f"[异常] {name}: {e}")
            continue
        results[name] = (ts_code, df, r)
        last = df.iloc[-1]
        extra = ""
        if r.base is not None and r.base.is_base:
            extra = f" 平台{ r.base.platform_days}日 保留{r.base.retain_ratio*100:.0f}% 质{r.base.score:.0f}"
        print(f"{name:<6} {last['close']:>8.2f}  {r.state:<22} {r.conclusion[:46] if r.conclusion else ''}{extra}")

    # ── 8月逐日 PRIMARY_BUY 扫描 ──
    print("\n" + "=" * 72)
    print("8月逐日扫描（截断至当日收盘）→ PRIMARY_BUY 信号")
    print("=" * 72)
    any_buy = False
    for name, (ts_code, df, _) in results.items():
        aug = df[df["trade_date"].str.startswith("202608")]["trade_date"].tolist()
        if not aug:
            continue
        buys, states = [], {}
        for d in aug:
            sub = df[df["trade_date"] <= d].reset_index(drop=True)
            if len(sub) < 130:
                continue
            r = engine.analyze(sub, ts_code=ts_code, name=name)
            is_buy = r.final_score is not None and r.final_score.is_primary_buy
            if is_buy or r.state == "PRIMARY_BUY":
                buys.append(d)
            if r.state not in states:
                states[r.state] = d
        tag = f"  ★买入信号 {buys}" if buys else ""
        stat_str = ", ".join(f"{s}@{d}" for s, d in sorted(states.items(), key=lambda x: x[1])[:4])
        print(f"{name:<6} 8月状态: {stat_str}{tag}")
        any_buy = any_buy or bool(buys)

    print("\n结论: " + ("存在 PRIMARY_BUY 信号" if any_buy else "8月无 PRIMARY_BUY 买入信号"))


if __name__ == "__main__":
    main()
