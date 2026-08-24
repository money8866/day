# -*- coding: utf-8 -*-
"""真实A股数据测试：解析 MCP 拉取的K线并运行 RIB 引擎。"""
from __future__ import annotations

import glob
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rib.engine import RIBEngine

# MCP 输出临时文件 -> (ts_code, name)
FILES = [
    (r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\dbf000b2-60de-4c07-9a7b-bf74a94e84e9.txt", "002594.SZ", "比亚迪"),
    (r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\d5a9acb4-ed3b-4667-b6a4-64d2bd95b69d.txt", "600519.SH", "贵州茅台"),
    (r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\dc1af3a1-54ee-41b1-aa83-08a2f0451f82.txt", "300750.SZ", "宁德时代"),
    (r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\2116c8b0-fb09-4422-a900-ab352be6548e.txt", "000001.SZ", "平安银行"),
    (r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\4ba8a739-c509-4779-ab9c-ab51f23ba4de.txt", "603986.SH", "兆易创新"),
    (r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\50599b85-5a43-4f5d-9483-41114883ed4f.txt", "300313.SZ", "天山生物"),
    (r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\57717718-522b-4a93-9b9f-faedb0dfc352.txt", "002104.SZ", "恒宝股份"),
    (r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\7c143713-ef82-4637-bf1a-8c8289a9e6dc.txt", "300189.SZ", "神农种业"),
]

# 截止日期（YYYYMMDD），传命令行参数指定，如: python test_real_data.py 20260821
END_DATE = sys.argv[1] if len(sys.argv) > 1 else ""


def parse_mcp_file(path: str) -> pd.DataFrame:
    """解析 MCP 返回的临时文件为 DataFrame。"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    # 去掉前缀，解析外层 JSON 数组
    if content.startswith("The MCP server responded with:"):
        content = content[len("The MCP server responded with:"):].strip()
    outer = json.loads(content)
    inner_text = outer[0]["text"]
    data = json.loads(inner_text)["data"]
    items = data["item"]
    records = []
    for it in items:
        from datetime import datetime, timezone, timedelta

        dt = datetime.fromtimestamp(it["date_ms"] / 1000, tz=timezone(timedelta(hours=8)))
        records.append({
            "trade_date": dt.strftime("%Y%m%d"),
            "open": it["open_price"],
            "high": it["high_price"],
            "low": it["low_price"],
            "close": it["close_price"],
            "vol": it["volume"],
            "amount": it["turnover"],
        })
    df = pd.DataFrame(records).sort_values("trade_date").reset_index(drop=True)
    return df


def main():
    engine = RIBEngine()
    for path, ts_code, name in FILES:
        if not os.path.exists(path):
            print(f"[跳过] 缺少文件 {path}")
            continue
        try:
            df = parse_mcp_file(path)
        except Exception as e:
            print(f"[解析失败] {ts_code}: {e}")
            continue

        if END_DATE:
            df = df[df["trade_date"] <= END_DATE].reset_index(drop=True)

        if len(df) < 130:
            print(f"[数据不足] {ts_code} {name}: {len(df)} 根")
            continue

        last = df.iloc[-1]
        print(f"\n{'='*60}")
        print(f"{ts_code} {name}  共{len(df)}根K线  最新收盘 {last['close']:.2f}")
        print(f"区间: {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}")
        print(f"{'='*60}")

        try:
            result = engine.analyze(df, ts_code=ts_code, name=name)
        except Exception as e:
            print(f"[引擎异常] {ts_code}: {e}")
            continue

        print(f"State: {result.state}")
        print(f"Is valid: {result.is_valid}")
        if result.final_score:
            fs = result.final_score
            print(f"final_score: {fs.total:.1f} ({fs.grade})  is_primary_buy={fs.is_primary_buy}")
            failed = [k for k, v in (fs.passed_checks or {}).items() if not v]
            print(f"Q检查未过: {failed if failed else '无，全部通过'}")
        if result.conclusion:
            print(f"Conclusion: {result.conclusion[:300]}")
        print(f"State sequence: {result.state_sequence}")


if __name__ == "__main__":
    main()
