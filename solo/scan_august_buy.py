# -*- coding: utf-8 -*-
"""扫描 2026-08 全部股票池：逐日截断跑引擎，寻找 PRIMARY_BUY 买入信号。"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rib.engine import RIBEngine

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
NEW_HEBAO = r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\3a520e23-c490-4364-886f-06de27e8a967.txt"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_hengbao_latest import parse_mcp_file


def main():
    engine = RIBEngine()
    print("=" * 70)
    print("2026-08 逐日信号扫描（截断至当日收盘判定）")
    print("=" * 70)

    for path, ts_code, name in FILES:
        if not os.path.exists(path):
            print(f"[跳过] {name} 缺少文件")
            continue
        df = parse_mcp_file(path)
        if name == "恒宝股份" and os.path.exists(NEW_HEBAO):
            df = pd.concat([df, parse_mcp_file(NEW_HEBAO)]).drop_duplicates("trade_date", keep="last")

        df = df.sort_values("trade_date").reset_index(drop=True)
        aug_days = df[df["trade_date"].str.startswith("202608")]["trade_date"].tolist()
        if not aug_days:
            print(f"[无8月数据] {name}")
            continue

        buy_days = []      # PRIMARY_BUY 信号日
        other_states = {}  # 其余状态 -> 首次出现日期
        for d in aug_days:
            sub = df[df["trade_date"] <= d].reset_index(drop=True)
            if len(sub) < 130:
                continue
            try:
                r = engine.analyze(sub, ts_code=ts_code, name=name)
            except Exception as e:
                print(f"[异常] {name} {d}: {e}")
                continue
            is_buy = r.final_score is not None and r.final_score.is_primary_buy
            if is_buy or r.state == "PRIMARY_BUY":
                buy_days.append(d)
            elif r.state not in other_states:
                other_states[r.state] = d

        print(f"\n{ts_code} {name}  (8月交易日 {len(aug_days)} 个)")
        print(f"  买入信号(PRIMARY_BUY): {buy_days if buy_days else '无'}")
        if other_states:
            print(f"  期间出现状态: " + ", ".join(f"{s}(首现{d})" for s, d in sorted(other_states.items(), key=lambda x: x[1])))

    print("\n" + "=" * 70)
    print("扫描完成")


if __name__ == "__main__":
    main()
