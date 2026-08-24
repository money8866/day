# -*- coding: utf-8 -*-
"""恒宝股份 20260824 最新数据验证：合并历史 + 新K线后运行 RIB 引擎。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rib.engine import RIBEngine


def parse_mcp_file(path: str) -> pd.DataFrame:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if content.startswith("The MCP server responded with:"):
        content = content[len("The MCP server responded with:"):].strip()
    outer = json.loads(content)
    inner_text = outer[0]["text"]
    data = json.loads(inner_text)["data"]
    items = data["item"]
    records = []
    tz = timezone(timedelta(hours=8))
    for it in items:
        dt = datetime.fromtimestamp(it["date_ms"] / 1000, tz=tz)
        records.append({
            "trade_date": dt.strftime("%Y%m%d"),
            "open": it["open_price"],
            "high": it["high_price"],
            "low": it["low_price"],
            "close": it["close_price"],
            "vol": it["volume"],
            "amount": it["turnover"],
        })
    return pd.DataFrame(records)


def main():
    OLD = r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\57717718-522b-4a93-9b9f-faedb0dfc352.txt"  # 历史(截止20260821)
    NEW = r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\3a520e23-c490-4364-886f-06de27e8a967.txt"  # 2026年至今(含20260824)

    df_old = parse_mcp_file(OLD)
    df_new = parse_mcp_file(NEW)
    df = pd.concat([df_old, df_new]).drop_duplicates("trade_date", keep="last").sort_values("trade_date").reset_index(drop=True)

    print(f"共{len(df)}根K线  区间: {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}")
    last = df.iloc[-1]
    prev = df.iloc[-2]
    print(f"20260824: 开{last['open']} 高{last['high']} 低{last['low']} 收{last['close']}  量{last['vol']:.0f}")
    print(f"涨跌幅: {(last['close']/prev['close']-1)*100:.2f}%  量能/前日: {last['vol']/prev['vol']:.2f}")
    print(f"20260821收盘: {prev['close']}  第一波高点(12.00): 今日最高{'突破' if last['high']>=12.00 else '未突破'}")

    engine = RIBEngine()
    result = engine.analyze(df, ts_code="002104.SZ", name="恒宝股份")
    print(f"\nState: {result.state}")
    print(f"Is valid: {result.is_valid}")
    if result.final_score:
        fs = result.final_score
        print(f"final_score: {fs.total:.1f} ({fs.grade})  is_primary_buy={fs.is_primary_buy}")
    if result.conclusion:
        print(f"Conclusion: {result.conclusion[:300]}")
    print(f"State sequence: {result.state_sequence}")


if __name__ == "__main__":
    main()
