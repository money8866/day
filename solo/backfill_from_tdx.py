# -*- coding: utf-8 -*-
"""
从通达信本地日线数据补充历史K线到缓存
缓存格式与 tushare_quant.py 完全一致: d:\mystock\cache_daily\{ts_code}.csv

通达信日线文件:
  C:\new_tdx\vipdoc\sh\lday\shXXXXXX.day  (上海)
  C:\new_tdx\vipdoc\sz\lday\szXXXXXX.day  (深圳)

日线文件格式（每条32字节, little-endian）:
  bytes 0-3:  日期 (int32, YYYYMMDD)
  bytes 4-7:  开盘价 * 100 (int32)
  bytes 8-11: 最高价 * 100 (int32)
  bytes 12-15:最低价 * 100 (int32)
  bytes 16-19:收盘价 * 100 (int32)
  bytes 20-23:成交额 (float32, 单位:元)
  bytes 24-27:成交量 (int32, /100 得到股数)
  bytes 28-31:保留
"""
import os
import struct
import glob
import pandas as pd
import numpy as np
from datetime import datetime

# === 配置 ===
CACHE_DIR = r"d:\mystock\cache_daily"
TDX_DIR = r"C:\new_tdx"
START_DATE = "20250101"  # 补充从该日期开始的历史数据


def parse_tdx_day_file(filepath):
    """解析通达信 .day 文件，返回 DataFrame (按日期升序)"""
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            open_p = struct.unpack("<i", chunk[4:8])[0] / 100.0
            high_p = struct.unpack("<i", chunk[8:12])[0] / 100.0
            low_p = struct.unpack("<i", chunk[12:16])[0] / 100.0
            close_p = struct.unpack("<i", chunk[16:20])[0] / 100.0
            amount_yuan = struct.unpack("<f", chunk[20:24])[0]  # 成交额(元, float32)
            vol_shares = struct.unpack("<i", chunk[24:28])[0] / 100.0  # 成交量(/100=股数, int32)
            
            date_str = str(date_int)
            # 只保留指定日期之后的数据
            if date_str >= START_DATE:
                records.append({
                    "trade_date": date_str,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "vol": vol_shares,
                    "amount": amount_yuan,
                })
    
    if not records:
        return None
    
    df = pd.DataFrame(records)
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def tdx_code_to_ts_code(raw_code):
    """
    通达信 sh600000 -> 600000.SH, sz000001 -> 000001.SZ
    也支持 sh000300 -> 000300.SH (指数)
    """
    raw_code = raw_code.replace(".day", "")
    if raw_code.startswith("sh"):
        return raw_code[2:] + ".SH"
    elif raw_code.startswith("sz"):
        return raw_code[2:] + ".SZ"
    return None


def ts_code_to_tdx_file(ts_code):
    """ts_code (600000.SH) -> TDX day 文件路径"""
    sym, market = ts_code.split(".")
    if market == "SH":
        prefix = "sh"
        subdir = "sh"
    elif market == "SZ":
        prefix = "sz"
        subdir = "sz"
    else:
        return None
    return os.path.join(TDX_DIR, "vipdoc", subdir, "lday", f"{prefix}{sym}.day")


def calc_pct_chg(df):
    """计算 pct_chg（涨跌幅%）和 change"""
    if df.empty or "close" not in df.columns:
        return df
    df = df.copy()
    df["pre_close"] = df["close"].shift(1)
    df["pct_chg"] = (df["close"] / df["pre_close"] - 1) * 100
    df["change"] = df["close"] - df["pre_close"]
    # 首行没有前收盘价，pct_chg=0, change=0
    df.loc[df["pre_close"].isna(), ["pre_close", "pct_chg", "change"]] = [df["close"].iloc[0], 0.0, 0.0]
    return df


def tdx_amount_to_tushare(amount_yuan):
    """TDX金额(元) → tushare金额(千元)"""
    return round(amount_yuan / 1000, 3)


def main():
    print("=" * 60)
    print("通达信历史日线补充工具")
    print("=" * 60)
    print(f"缓存目录: {CACHE_DIR}")
    print(f"通达信目录: {TDX_DIR}")
    print(f"补充起始日期: {START_DATE}")
    print()
    
    # 收集所有现有缓存文件
    cache_files = glob.glob(os.path.join(CACHE_DIR, "*.csv"))
    print(f"发现 {len(cache_files)} 个缓存文件")
    
    # 统计
    updated_count = 0
    skipped_count = 0
    no_tdx_count = 0
    already_full_count = 0
    
    for cache_path in cache_files:
        fname = os.path.basename(cache_path)
        ts_code = fname.replace(".csv", "")
        
        # 跳过非股票格式的文件（如 000300.csv 无后缀）
        if "." not in ts_code:
            no_tdx_count += 1
            continue
        
        # 读取现有缓存
        try:
            df_cache = pd.read_csv(cache_path)
            df_cache["trade_date"] = df_cache["trade_date"].astype(str)
        except Exception as e:
            print(f"  ⚠ {ts_code}: 读取缓存失败: {e}")
            skipped_count += 1
            continue
        
        # 检查缓存是否已有 START_DATE 之前的数据
        cache_min_date = df_cache["trade_date"].min()
        if cache_min_date < START_DATE:
            # 已有完整历史数据，跳过
            already_full_count += 1
            continue
        
        # 查找通达信文件
        tdx_file = ts_code_to_tdx_file(ts_code)
        if not tdx_file or not os.path.exists(tdx_file):
            no_tdx_count += 1
            continue
        
        # 读取通达信数据
        df_tdx = parse_tdx_day_file(tdx_file)
        if df_tdx is None or df_tdx.empty:
            no_tdx_count += 1
            continue
        
        # 添加 ts_code 列
        df_tdx["ts_code"] = ts_code
        # 转换：TDX金额(元) → tushare金额(千元)
        df_tdx["amount"] = df_tdx["amount"].apply(tdx_amount_to_tushare)
        # vol四舍五入取整
        df_tdx["vol"] = df_tdx["vol"].round(2)
        
        # 合并：保留旧数据的增量更新（TDX数据是历史旧数据，放在前面以便concat后drop_duplicates保留后面的新数据）
        df_cache["ts_code"] = ts_code
        merged = pd.concat([df_tdx, df_cache], ignore_index=True)
        merged = merged.drop_duplicates(subset=["trade_date"], keep="last")
        merged = merged.sort_values("trade_date").reset_index(drop=True)
        
        # 计算 pct_chg 和 pre_close (必须在合并后、去重前计算，才能正确推算pre_close)
        merged = calc_pct_chg(merged)
        
        # 只保留 START_DATE 之后的数据（以防TDX数据更完整）
        merged = merged[merged["trade_date"] >= START_DATE].reset_index(drop=True)
        
        # 写入缓存（保留原始列顺序）
        cols = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"]
        existing_cols = [c for c in cols if c in merged.columns]
        merged[existing_cols].to_csv(cache_path, index=False)
        
        updated_count += 1
        
        # 显示进度
        old_min = cache_min_date
        new_min = merged["trade_date"].min()
        added = len(merged) - len(df_cache)
        print(f"  ✅ {ts_code}: 数据从 {old_min} 扩展到 {new_min}，新增 {added} 条")
    
    print()
    print("=" * 60)
    print(f"  已补充: {updated_count} 只")
    print(f"  已有完整数据: {already_full_count} 只")
    print(f"  无通达信数据: {no_tdx_count} 只")
    print(f"  跳过: {skipped_count} 只")
    print("=" * 60)


if __name__ == "__main__":
    main()
