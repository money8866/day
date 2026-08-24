# -*- coding: utf-8 -*-
"""Bull中报股池 全量103只：20260821 状态 + 8月逐日 PRIMARY_BUY 扫描。
数据源: 项目本地日K线缓存（通达信 .day 文件，C:\new_tdx，不复权），不联网。"""
from __future__ import annotations

import os
import struct
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rib.engine import RIBEngine

TDX_DIR = r"C:\new_tdx"


def tdx_file(code6: str) -> str:
    market = "sh" if code6.startswith("6") else "sz"
    return os.path.join(TDX_DIR, "vipdoc", market, "lday", f"{market}{code6}.day")


def parse_tdx(fp: str) -> pd.DataFrame:
    """解析通达信 .day 文件 -> DataFrame(trade_date/open/high/low/close/vol/amount)"""
    if not os.path.exists(fp):
        return pd.DataFrame()
    recs = []
    with open(fp, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            recs.append({
                "trade_date": str(date_int),
                "open": struct.unpack("<i", chunk[4:8])[0] / 100.0,
                "high": struct.unpack("<i", chunk[8:12])[0] / 100.0,
                "low": struct.unpack("<i", chunk[12:16])[0] / 100.0,
                "close": struct.unpack("<i", chunk[16:20])[0] / 100.0,
                "vol": struct.unpack("<i", chunk[24:28])[0] / 100.0,
                "amount": struct.unpack("<f", chunk[20:24])[0],
            })
    if not recs:
        return pd.DataFrame()
    return pd.DataFrame(recs).sort_values("trade_date").reset_index(drop=True)


def to_tscode(code6: str) -> str:
    return code6 + (".SH" if code6.startswith("6") else ".SZ")


def main():
    pool = pd.read_csv(r"d:\mystock\solo\bull103.csv", dtype={"代码6": str})
    engine = RIBEngine()

    print("═" * 76)
    print("Bull中报股池 全量103只 — 本地缓存(通达信) 20260821 状态 + 8月逐日信号")
    print("═" * 76)

    rows = []
    fail = []
    for i, (_, s) in enumerate(pool.iterrows(), 1):
        code6, name = s["代码6"], s["名称"]
        df = parse_tdx(tdx_file(code6))
        if len(df) < 130:
            fail.append(name)
            print(f"[{i:>3}] {name:<6} 数据不足({len(df)}根) 跳过")
            continue
        try:
            r = engine.analyze(df, ts_code=to_tscode(code6), name=name)
        except Exception as e:
            fail.append(name)
            print(f"[{i:>3}] {name:<6} 异常: {e}")
            continue

        last = df.iloc[-1]
        extra = ""
        if r.base is not None and r.base.is_base:
            extra = f" 平台{r.base.platform_days}日 保留{r.base.retain_ratio*100:.0f}% 质{r.base.score:.0f}"

        # ── 8月逐日 PRIMARY_BUY 扫描 ──
        aug = df[df["trade_date"].str.startswith("202608")]["trade_date"].tolist()
        buys, states = [], {}
        for d in aug:
            sub = df[df["trade_date"] <= d].reset_index(drop=True)
            if len(sub) < 130:
                continue
            try:
                r2 = engine.analyze(sub, ts_code=to_tscode(code6), name=name)
            except Exception:
                continue
            if (r2.final_score is not None and r2.final_score.is_primary_buy) or r2.state == "PRIMARY_BUY":
                buys.append(d)
            states[r2.state] = d

        tag = f"  ★8月买入 {buys}" if buys else ""
        rows.append((code6, name, last["close"], r.state, extra, buys))
        print(f"[{i:>3}] {name:<6} {last['close']:>8.2f}  {r.state:<24} {r.conclusion[:40] if r.conclusion else ''}{extra}{tag}")

    # ── 汇总 ──
    print("\n" + "═" * 76)
    print("汇总")
    print("═" * 76)
    if rows:
        import collections
        dist = collections.Counter(r[3] for r in rows)
        print("状态分布:")
        for st, n in sorted(dist.items(), key=lambda x: -x[1]):
            print(f"  {st:<24} {n}")
        buys_all = [r for r in rows if r[5]]
        if buys_all:
            print(f"\n8月 PRIMARY_BUY 信号: {len(buys_all)} 只")
            for _, name, close, st, extra, buys in buys_all:
                print(f"  {name:<6} 信号日 {buys}")
        else:
            print("\n8月全池无 PRIMARY_BUY 买入信号")

        non_down = [r for r in rows if r[3] not in ("DOWNTREND",)]
        if non_down:
            print(f"\n非 DOWNTREND 状态 ({len(non_down)} 只):")
            for code6, name, close, st, extra, buys in non_down:
                print(f"  {name:<6} {st:<24} {extra}")
    if fail:
        print(f"\n数据缺失 {len(fail)} 只: {', '.join(fail)}")

    print("\n结论: Bull103 全池扫描完成（数据源: 本地通达信缓存）")


if __name__ == "__main__":
    main()
