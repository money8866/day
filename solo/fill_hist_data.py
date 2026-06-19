#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
日线历史数据补足工具
- 补足目标股票最近1年（约252个交易日）的日线数据
- 使用 tushare pro.daily 接口批量拉取
- 缓存目录与主程序 tushare_quant.py 共享：cache_daily
- 配置文件：d:/mystock/config/.env（TUSHARE_TOKEN）

用法:
    python fill_hist_data.py --codes 002426.SZ,000988.SZ,002384.SZ
    python fill_hist_data.py --auto
    python fill_hist_data.py --codes-file my_stocks.txt
    python fill_hist_data.py --codes 002426.SZ --date 20260618
    python fill_hist_data.py --codes 002426.SZ --overwrite
"""

import os
import sys
import time
import json
import argparse
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

# =========================
# 路径 & 环境配置（与主程序 tushare_quant.py 完全一致）
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DATA_DIR = r"d:\mystock"

# 读取配置（与主程序一致的路径）
load_dotenv(r"d:/mystock/config/.env")

# 缓存目录：与主程序共享
CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")
os.makedirs(CACHE_DIR, exist_ok=True)

# =========================
# Tushare API 初始化
# =========================
try:
    import tushare as ts

    TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")

    if TUSHARE_TOKEN and TUSHARE_TOKEN.strip() and not TUSHARE_TOKEN.startswith("YOUR_"):
        ts.set_token(TUSHARE_TOKEN)
        pro = ts.pro_api()
        print(f"[初始化] Tushare Pro API 就绪 (token {len(TUSHARE_TOKEN)}位)")
    else:
        print("[错误] TUSHARE_TOKEN 未配置，请检查 d:/mystock/config/.env")
        sys.exit(1)
except ImportError:
    print("[错误] 请先安装 tushare: pip install tushare")
    sys.exit(1)
except Exception as e:
    print(f"[错误] Tushare 初始化失败: {e}")
    sys.exit(1)


# =========================
# 工具函数
# =========================
def get_latest_trade_date():
    """获取最近一个交易日（周末自动回退到周五）"""
    today = datetime.now()
    weekday = today.weekday()  # 0=周一, 4=周五, 5=周六, 6=周日
    if weekday >= 5:
        today = today - timedelta(days=weekday - 4)
    return today.strftime("%Y%m%d")


def get_start_date_one_year(latest_date):
    """获取1年前的日期"""
    dt = datetime.strptime(latest_date, "%Y%m%d") - timedelta(days=365)
    return dt.strftime("%Y%m%d")


def get_default_stock_list():
    """默认半导体/主题热门股票列表（与主程序关注的池对齐）"""
    return [
        # 半导体制造/设备
        "600703.SH",  # 三安光电
        "688012.SH",  # 中微公司
        # PCB/电子电路
        "002463.SZ",  # 沪电股份
        "002938.SZ",  # 鹏鼎控股
        "300852.SZ",  # 四会富仕
        "002426.SZ",  # 胜利精密
        "300602.SZ",  # 飞荣达
        "000988.SZ",  # 华工科技
        # AI终端/算力
        "300304.SZ",  # 云意电气
        "002364.SZ",  # 中恒电气
        "600345.SH",  # 长江通信
        "001309.SZ",  # 德明利
        # 半导体封测/存储
        "603936.SH",  # 博敏电子
        "688809.SH",  # 康代智能
        "688601.SH",  # 力芯微
        "600584.SH",  # 长电科技
        "603267.SH",  # 鸿远电子
        # 半导体材料
        "688733.SH",  # 壹石通
        "300179.SZ",  # 四方达
        "600888.SH",  # 新疆众和
        # 工业母机/机器人
        "300293.SZ",  # 蓝英装备
        "002903.SZ",  # 宇环数控
        "300607.SZ",  # 拓斯达
        "688090.SH",  # 瑞松科技
        # 光通信/光电子
        "300042.SZ",  # 朗科科技
        "688010.SH",  # 福光股份
        "000733.SZ",  # 振华科技
        # 被动元件
        "688785.SH",  # 恒运昌
        # 半导体EDA/IP
        "688206.SH",  # 概伦电子
    ]


def get_all_market_stocks():
    """从 Tushare 获取全市场A股股票列表（主板+创业板+科创板+北交所）

    Returns:
        list: [ts_code, ...]
    """
    try:
        df = pro.stock_basic(
            exchange='',
            list_status='L',
            fields='ts_code,symbol,name,market,list_date'
        )

        if df is None or df.empty:
            print("[错误] 获取股票列表失败，Tushare 返回空数据")
            return []

        total = len(df)
        print(f"  [全市场] 已从 Tushare 获取 {total} 只上市股票")

        # 按 ts_code 前缀筛选出A股（主板/创业板/科创板/北交所）
        valid_codes = []
        for _, row in df.iterrows():
            code = str(row['ts_code']).strip()
            symbol = str(row['symbol']).strip()

            # 主板: 600/601/603/605/000/001/002/003
            # 创业板: 300/301
            # 科创板: 688/689
            # 北交所: 43/83/87/88/92等
            if symbol.startswith(('600', '601', '603', '605',
                                   '000', '001', '002', '003',
                                   '300', '301',
                                   '688', '689',
                                   '4', '8', '9')) and '.' in code:
                valid_codes.append(code)

        # 去重保持顺序
        seen = set()
        final_codes = []
        for c in valid_codes:
            if c not in seen:
                seen.add(c)
                final_codes.append(c)

        print(f"  [全市场] 筛选后共 {len(final_codes)} 只A股")
        return final_codes

    except Exception as e:
        print(f"[错误] 获取全市场股票列表失败: {e}")
        print("       可能原因: Tushare token 权限不足（stock_basic 需要至少基础积分权限）")
        return []


def check_cache_complete(ts_code, target_date):
    """检查缓存是否完整（包含目标日期且至少有200天数据）"""
    cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
    if not os.path.exists(cache_file):
        return False, 0
    try:
        df = pd.read_csv(cache_file)
        df["trade_date"] = df["trade_date"].astype(str)
        if (df["trade_date"] == str(target_date)).any():
            return True, len(df)
        if len(df) < 200:
            return False, len(df)
        return True, len(df)
    except Exception:
        return False, 0


def fetch_and_save(ts_code, start_date, end_date, overwrite=False):
    """拉取并保存单个股票的日线数据

    Returns: (success: bool, days: int, message: str)
    """
    cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")

    if not overwrite:
        is_complete, days = check_cache_complete(ts_code, end_date)
        if is_complete:
            return True, days, "已完整缓存"

    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return False, 0, "空数据"

        df = df.sort_values("trade_date").reset_index(drop=True)
        df.to_csv(cache_file, index=False)
        return True, len(df), f"已保存 {len(df)} 行"
    except Exception as e:
        return False, 0, f"错误: {e}"


def fetch_batch_and_save(ts_codes, start_date, end_date, overwrite=False, sleep_between_batch=0.5):
    """批量拉取并保存（tushare 单次可批量拉取）"""
    if not ts_codes:
        return [], []

    # 1) 先过滤出需要拉取的
    to_fetch = []
    for c in ts_codes:
        if not overwrite:
            is_complete, _ = check_cache_complete(c, end_date)
            if is_complete:
                continue
        to_fetch.append(c)

    if not to_fetch:
        return ts_codes, []  # 全部已缓存

    # 2) 批量拉取（单次 API 可处理多个 ts_code）
    batch_codes_str = ",".join(to_fetch)

    success_codes = []
    failed_codes = []

    try:
        combined_df = pro.daily(ts_code=batch_codes_str, start_date=start_date, end_date=end_date)
        if combined_df is None or combined_df.empty:
            # 批量失败 → 回退逐只拉取
            for c in to_fetch:
                ok, days, msg = fetch_and_save(c, start_date, end_date, overwrite=True)
                if ok:
                    success_codes.append((c, days))
                else:
                    failed_codes.append((c, msg))
                time.sleep(sleep_between_batch)
            return success_codes, failed_codes

        # 按 ts_code 分组保存
        for code, group in combined_df.groupby("ts_code"):
            cache_file = os.path.join(CACHE_DIR, f"{code}.csv")
            group_sorted = group.sort_values("trade_date").reset_index(drop=True)
            group_sorted.to_csv(cache_file, index=False)
            success_codes.append((code, len(group_sorted)))

        # 检查哪些未拿到（可能不在交易所或代码有误）
        fetched_set = {c for c, _ in success_codes}
        for c in to_fetch:
            if c not in fetched_set:
                failed_codes.append((c, "批量拉取中无数据"))

    except Exception as e:
        # 批量失败 → 回退逐只拉取
        print(f"  [提示] 批量拉取异常({e})，回退逐只拉取...")
        for c in to_fetch:
            ok, days, msg = fetch_and_save(c, start_date, end_date, overwrite=True)
            if ok:
                success_codes.append((c, days))
            else:
                failed_codes.append((c, msg))
            time.sleep(sleep_between_batch)

    return success_codes, failed_codes


# =========================
# 主流程
# =========================
def main():
    parser = argparse.ArgumentParser(description="日线历史数据补足工具（1年）")
    parser.add_argument("--codes", type=str, default=None,
                        help="股票代码列表，逗号分隔（如: 002426.SZ,000988.SZ,002384.SZ）")
    parser.add_argument("--codes-file", type=str, default=None,
                        help="从文本文件读取，每行一个股票代码")
    parser.add_argument("--auto", action="store_true",
                        help="使用默认半导体主题热门股票列表")
    parser.add_argument("--all-market", action="store_true",
                        help="拉取全市场所有A股（约5000+只，从Tushare动态获取股票列表）")
    parser.add_argument("--date", type=str, default=None,
                        help="目标交易日（YYYYMMDD），默认自动识别最近交易日")
    parser.add_argument("--overwrite", action="store_true",
                        help="强制覆盖已有缓存")
    parser.add_argument("--sleep", type=float, default=0.3,
                        help="每次API调用之间的间隔秒数（默认0.3）")
    parser.add_argument("--batch-size", type=int, default=10,
                        help="每批同时拉取的股票数量（默认10）")

    args = parser.parse_args()

    # 1) 确定日期
    target_date = args.date or get_latest_trade_date()
    start_date = get_start_date_one_year(target_date)
    print(f"[配置] 时间范围: {start_date} ~ {target_date}")
    print(f"[配置] 缓存目录: {CACHE_DIR}")
    print(f"[配置] 模式: {'覆盖重写' if args.overwrite else '增量更新'}")

    # 2) 确定股票列表
    codes = []
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.codes_file:
        if os.path.exists(args.codes_file):
            with open(args.codes_file, "r", encoding="utf-8") as f:
                codes = [line.strip() for line in f
                         if line.strip() and not line.strip().startswith("#")]
        else:
            print(f"[错误] 文件不存在: {args.codes_file}")
            sys.exit(1)
    elif args.all_market:
        print(f"[配置] 拉取模式: 全市场A股")
        codes = get_all_market_stocks()
        if not codes:
            print("[错误] 无法获取全市场股票列表，请检查 Tushare token 权限")
            sys.exit(1)
    elif args.auto:
        codes = get_default_stock_list()
    else:
        print("[提示] 未指定股票，请使用 --codes 或 --auto 或 --all-market 或 --codes-file")
        print("       示例: python fill_hist_data.py --codes 002426.SZ,000988.SZ")
        print("       示例: python fill_hist_data.py --auto")
        print("       示例: python fill_hist_data.py --all-market --overwrite")
        sys.exit(1)

    # 自动补齐 .SH/.SZ（用户可能简写代码）
    processed_codes = []
    for c in codes:
        c = c.upper()
        if "." not in c:
            if c.startswith(("5", "6", "9")):
                c = c + ".SH"
            else:
                c = c + ".SZ"
        processed_codes.append(c)

    # 去重保持顺序
    seen = set()
    codes = []
    for c in processed_codes:
        if c not in seen:
            seen.add(c)
            codes.append(c)

    print(f"[配置] 股票数量: {len(codes)} 只")
    print("-" * 60)

    if not codes:
        print("[错误] 股票列表为空")
        sys.exit(1)

    # 3) 分批批量拉取
    success_count = 0
    skip_count = 0
    fail_count = 0
    start_time = time.time()

    print(f"\n{'='*60}")
    print(f"  开始拉取历史日线数据 ({len(codes)}只, {start_date}~{target_date})")
    print(f"  每批 {args.batch_size} 只批量拉取")
    print(f"{'='*60}\n")

    # 3a) 先检查已有缓存
    to_fetch = []
    for idx, ts_code in enumerate(codes, 1):
        if not args.overwrite:
            is_complete, days = check_cache_complete(ts_code, target_date)
            if is_complete:
                print(f"[{idx:>3}/{len(codes):<3}] {ts_code:<15} -> 已缓存({days}天), 跳过")
                skip_count += 1
                continue
        to_fetch.append((idx, ts_code))

    if not to_fetch:
        print(f"\n全部股票缓存已完整，无需拉取")
        print(f"  跳过: {skip_count} 只")
        print(f"  缓存目录: {CACHE_DIR}")
        print(f"{'='*60}\n")
        sys.exit(0)

    # 3b) 分批批量拉取
    batch_size = max(1, args.batch_size)
    total_batches = (len(to_fetch) + batch_size - 1) // batch_size

    for batch_i in range(total_batches):
        batch_start = batch_i * batch_size
        batch = to_fetch[batch_start:batch_start + batch_size]
        batch_codes = [c for _, c in batch]

        # 调用批量拉取
        batch_success, batch_failed = fetch_batch_and_save(
            batch_codes, start_date, target_date,
            overwrite=args.overwrite,
            sleep_between_batch=args.sleep
        )

        for c, days in batch_success:
            orig_idx = next((i for i, cc in batch if cc == c), 0)
            print(f"[{orig_idx:>3}/{len(codes):<3}] {c:<15} -> ✓ 已保存 {days} 行")
            success_count += 1

        for c, msg in batch_failed:
            orig_idx = next((i for i, cc in batch if cc == c), 0)
            print(f"[{orig_idx:>3}/{len(codes):<3}] {c:<15} -> ✗ {msg}")
            fail_count += 1

        # 批次间隔（避免触发API频率限制）
        if batch_i < total_batches - 1:
            time.sleep(args.sleep)

    # 4) 汇总报告
    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"  完成! 用时 {elapsed:.1f}秒")
    print(f"    成功拉取: {success_count} 只")
    print(f"    跳过(已缓存): {skip_count} 只")
    print(f"    失败: {fail_count} 只")
    print(f"    总计: {success_count + skip_count + fail_count} 只")
    print(f"  缓存目录: {CACHE_DIR}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
