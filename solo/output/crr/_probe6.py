# -*- coding: utf-8 -*-
import os, glob, sys
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
P = r"D:\mystock\cache_daily\parquet"

# 各 family 文件名样本
pats = {
    "income": "income_*.parquet",
    "cashflow": "cashflow_*.parquet",
    "balance": "balance_*.parquet",
    "fina_indicator": "fina_indicator_*.parquet",
}
for k, pat in pats.items():
    fs = glob.glob(os.path.join(P, pat))
    print(f"\n### {k}  count={len(fs)}")
    for f in fs[:5]:
        print("   ", os.path.basename(f))

# 具体代码匹配到的文件（点号版 + 下划线版）
for code in ("600000.SH", "600004.SH", "000002.SZ"):
    base, ex = code.split(".")
    print(f"\n### code={code}")
    for fam in ("income", "cashflow", "balance", "fina_indicator"):
        fs = glob.glob(os.path.join(P, f"{fam}_*{base}*"))
        fs = [f for f in fs if ex in os.path.basename(f)]
        print(f"   {fam}: n={len(fs)}")
        for f in fs[:6]:
            print("       ", os.path.basename(f))


def schema(pat, label):
    fs = glob.glob(os.path.join(P, pat))
    if not fs:
        print(f"\n### schema {label}: NO FILE")
        return
    f = fs[0]
    df = pd.read_parquet(f)
    print(f"\n### schema {label}: {os.path.basename(f)}  shape={df.shape}")
    print("   cols:", list(df.columns))
    with pd.option_context("display.max_columns", 40, "display.width", 200):
        print(df.head(3).to_string())


schema("income_600004.SH_*.parquet", "income-dot")
schema("income_600000_SH_*.parquet", "income-underline")
schema("cashflow_600000_SH_*.parquet", "cashflow-underline")
schema("balance_v*_600000_SH_*.parquet", "balance-underline")
schema("fina_indicator_600004.SH_*.parquet", "fina-dot")
