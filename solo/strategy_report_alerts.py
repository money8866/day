#!/usr/bin/env python3
"""
研报跟踪个股选股策略 —— 多头排列 + 缩量调整 预警

从 stock_reports.db 中提取近期获研报覆盖的个股，
接入 Tushare 日线数据，筛选出：
  ① 多头排列（MA5 > MA10 > MA20 > MA60）
  ② 缩量调整（价格回踩不破 MA20 + 量能萎缩）
的标的，输出预警信号。
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ── Tushare 初始化 ────────────────────────────────────────────────
# 复用已有项目的 Tushare 配置
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from tushare_quant import pro, TRADE_DATE, CACHE_DIR, batch_prefetch_hist_data
except ImportError:
    # 兜底：独立初始化
    import tushare as ts
    TOKEN = os.getenv("TUSHARE_TOKEN")
    if not TOKEN:
        print("[ERROR] 请设置环境变量 TUSHARE_TOKEN")
        sys.exit(1)
    pro = ts.pro_api(TOKEN)
    TODAY = datetime.now().strftime("%Y%m%d")
    TRADE_DATE = TODAY
    CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_daily")


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_reports.db")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_reports")


def normalize_code(code: str) -> str | None:
    """6位股票代码 -> Tushare ts_code（同 breakout_backtest.py）"""
    code = str(code).strip()
    if "." in code:
        return code
    code = code.zfill(6)
    if code.startswith(("60", "68", "11", "13")):
        return f"{code}.SH"
    elif code.startswith(("0", "3", "12")):
        return f"{code}.SZ"
    # 北交所跳过（8/4/9开头）
    return None


def get_stock_codes_from_db(db_path: str, min_reports: int = 1, max_days: int = 30) -> list[dict]:
    """从数据库读取近期有研报覆盖的个股"""
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT stock_code, stock_name,
               COUNT(*) AS report_count,
               MAX(publish_date) AS last_report,
               GROUP_CONCAT(DISTINCT org_name) AS institutions
        FROM reports
        WHERE publish_date >= date('now', ?)
        GROUP BY stock_code, stock_name
        HAVING report_count >= ?
        ORDER BY last_report DESC
    """, (f"-{max_days} days", min_reports)).fetchall()
    conn.close()
    result = []
    for r in rows:
        ts_code = normalize_code(r[0])
        if ts_code:
            result.append({
                "code": str(r[0]).zfill(6),
                "name": r[1],
                "ts_code": ts_code,
                "report_count": r[2],
                "last_report": r[3],
                "institutions": r[4] or "",
            })
    return result


def fetch_daily_data(stocks: list[dict], lookback_days: int = 90) -> dict[str, pd.DataFrame]:
    """
    批量获取日线数据
    返回 {ts_code: DataFrame} 字典
    """
    # 先批量预取
    ts_codes = [s["ts_code"] for s in stocks]
    start_date = (datetime.now() - timedelta(days=lookback_days + 30)).strftime("%Y%m%d")

    try:
        batch_prefetch_hist_data(ts_codes, start_date=start_date)
    except Exception as e:
        print(f"  [WARN] 批量预取失败: {e}，改用逐只查询")

    # 从缓存/API 逐一读取
    result = {}
    for s in stocks:
        ts_code = s["ts_code"]
        try:
            # 优先从缓存文件读取
            cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
            if os.path.exists(cache_file):
                df = pd.read_csv(cache_file)
                df["trade_date"] = df["trade_date"].astype(str)
                df = df.sort_values("trade_date").reset_index(drop=True)
                # 仅保留最新 lookback_days 天
                cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
                df = df[df["trade_date"] >= cutoff].copy()
            else:
                # 直接调用 API
                # V2: 优先 daily_cache 表
                df = None
                try:
                    from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
                    _, _max_date = get_daily_cache_range(ts_code)
                    if _max_date is not None and str(_max_date) >= str(TRADE_DATE):
                        df = get_daily_cache(ts_code, start_date, TRADE_DATE)
                        if df is not None and not df.empty:
                            df['trade_date'] = df['trade_date'].astype(str)
                except Exception:
                    pass
                if df is None or df.empty:
                    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=TRADE_DATE)
                    if df is not None and not df.empty:
                        try:
                            from stock_cache import batch_insert_daily_cache
                            batch_insert_daily_cache(df)
                        except Exception:
                            pass
                if df is not None and not df.empty:
                    df = df.sort_values("trade_date").reset_index(drop=True)

            if df is not None and not df.empty and len(df) >= 60:
                result[ts_code] = df
            else:
                print(f"  [SKIP] {s['name']}({ts_code}) 数据不足 ({len(df) if df is not None else 0}行)")
        except Exception as e:
            print(f"  [WARN] {s['name']}({ts_code}) 获取失败: {e}")

        time.sleep(0.1)  # 频率控制

    return result


def check_bullish_alignment(df: pd.DataFrame) -> tuple[bool, dict]:
    """
    检查多头排列条件
    返回 (是否满足, 指标字典)
    """
    close = df["close"].values
    ma5 = pd.Series(close).rolling(5).mean().values
    ma10 = pd.Series(close).rolling(10).mean().values
    ma20 = pd.Series(close).rolling(20).mean().values
    ma60 = pd.Series(close).rolling(60).mean().values

    # 最新值
    m5, m10, m20, m60 = ma5[-1], ma10[-1], ma20[-1], ma60[-1]

    # 多头排列判定
    aligned = all([
        m5 > m10 > m20 > m60,           # 严格排序
        m5 > m10 > m20,                 # 短中期明确
        m20 > m60,                      # 中期趋势向上
        close[-1] > m20,                # 价格在 MA20 之上
    ])

    metrics = {
        "ma5": round(m5, 2),
        "ma10": round(m10, 2),
        "ma20": round(m20, 2),
        "ma60": round(m60, 2),
        "close": round(close[-1], 2),
        "aligned": aligned,
    }
    return aligned, metrics


def check_volume_shrink(df: pd.DataFrame) -> tuple[bool, dict]:
    """
    检查缩量调整条件
    ① 价格从近期高点回撤 ≥3% 且 ≤15%（调整而非下跌）
    ② 收盘仍在 MA20 之上
    ③ 近3日均量 < 前10日均量 * 0.8（缩量 20%+）
    """
    close = df["close"].values
    vol = df["vol"].values
    ma20 = pd.Series(close).rolling(20).mean().values

    # 近期高点（10天）
    recent_high = max(close[-10:])
    high_idx = list(close[-10:]).index(recent_high)
    pct_from_high = (close[-1] - recent_high) / recent_high * 100

    # 仍在 MA20 之上
    above_ma20 = close[-1] > ma20[-1]

    # 量比：近3日均量 / 前10日均量
    avg_vol_3 = np.mean(vol[-3:])
    avg_vol_10_before = np.mean(vol[-13:-3]) if len(vol) >= 13 else np.mean(vol[:-3])
    vol_ratio = avg_vol_3 / avg_vol_10_before if avg_vol_10_before > 0 else 999

    # 连续缩量天数
    shrink_days = 0
    for i in range(-1, -min(6, len(vol)), -1):
        if vol[i] <= vol[i - 1]:
            shrink_days += 1
        else:
            break

    is_pullback = -15 <= pct_from_high <= -3  # 调整3%-15%
    is_shrinking = vol_ratio < 0.8  # 缩量20%以上

    triggered = above_ma20 and is_pullback and is_shrinking

    metrics = {
        "high_10d": round(recent_high, 2),
        "pct_from_high": round(pct_from_high, 1),
        "above_ma20": above_ma20,
        "avg_vol_3": round(float(avg_vol_3), 0),
        "avg_vol_10_before": round(float(avg_vol_10_before), 0),
        "vol_ratio": round(vol_ratio, 2),
        "shrink_days": shrink_days,
        "is_pullback": is_pullback,
        "is_shrinking": is_shrinking,
        "triggered": triggered,
    }
    return triggered, metrics


def calculate_score(metrics: dict, vol_metrics: dict) -> int:
    """综合评分（满分100）"""
    score = 0
    reasons = []

    # ① 多头排列强度（40分）
    ma_gap_5_20 = (metrics["ma5"] / metrics["ma20"] - 1) * 100
    if ma_gap_5_20 > 5:
        score += 40
        reasons.append("多头排列强(+40)")
    elif ma_gap_5_20 > 3:
        score += 30
        reasons.append("多头排列较强(+30)")
    elif ma_gap_5_20 > 1:
        score += 20
        reasons.append("多头排列一般(+20)")
    else:
        score += 10
        reasons.append("多头排列偏弱(+10)")

    # ② 缩量程度（30分）
    vr = vol_metrics["vol_ratio"]
    if vr < 0.4:
        score += 30
        reasons.append("极度缩量(+30)")
    elif vr < 0.6:
        score += 24
        reasons.append("显著缩量(+24)")
    elif vr < 0.8:
        score += 18
        reasons.append("适度缩量(+18)")
    else:
        score += 5
        reasons.append("缩量不明显(+5)")

    # ③ 回踩恰到好处（20分）
    pct = abs(vol_metrics["pct_from_high"])
    if 5 <= pct <= 10:
        score += 20
        reasons.append("回踩适中(+20)")
    elif 3 <= pct < 5:
        score += 15
        reasons.append("轻微回踩(+15)")
    elif 10 < pct <= 15:
        score += 10
        reasons.append("回踩偏深(+10)")
    else:
        score += 5
        reasons.append("回踩幅度一般(+5)")

    # ④ 连续缩量天数加分（10分）
    sd = vol_metrics["shrink_days"]
    if sd >= 4:
        score += 10
        reasons.append(f"连续缩量{sd}天(+10)")
    elif sd >= 2:
        score += 6
        reasons.append(f"连续缩量{sd}天(+6)")

    return min(score, 100), reasons


def run_strategy(db_path: str = DB_PATH, min_reports: int = 1,
                 max_days: int = 30, min_score: int = 60,
                 output_file: str = "") -> list[dict]:
    """主策略入口"""
    print("═══ 研报跟踪 · 多头排列+缩量调整 预警 ═══\n")
    print(f"[INFO] 读取研报数据库...")
    stocks = get_stock_codes_from_db(db_path, min_reports=min_reports, max_days=max_days)
    print(f"[INFO] 近期有研报覆盖的个股: {len(stocks)} 只（排除北交所后）")

    if not stocks:
        print("[INFO] 无数据，退出")
        return []

    print(f"[INFO] 批量预取日线数据...")
    daily_data = fetch_daily_data(stocks, lookback_days=90)
    print(f"[INFO] 成功获取数据: {len(daily_data)} 只\n")

    results = []
    for s in stocks:
        ts_code = s["ts_code"]
        if ts_code not in daily_data:
            continue
        df = daily_data[ts_code]

        # 检查多头排列
        aligned, metrics = check_bullish_alignment(df)
        if not aligned:
            continue

        # 检查缩量调整
        vol_triggered, vol_metrics = check_volume_shrink(df)
        if not vol_triggered:
            # 仅多头排列但不满足缩量条件也记入（标记非预警）
            results.append({
                **s, "score": 0, "reasons": ["多头排列✓ 缩量调整×"], "alert": False,
                **metrics, **vol_metrics,
            })
            continue

        # 综合评分
        score, reasons = calculate_score(metrics, vol_metrics)
        if score < min_score:
            continue

        results.append({
            **s, "score": score, "reasons": reasons, "alert": True,
            **metrics, **vol_metrics,
        })

    # 按分数排序
    results.sort(key=lambda x: x["score"], reverse=True)

    # 输出
    alerts = [r for r in results if r["alert"]]
    only_aligned = [r for r in results if not r["alert"]]

    print(f"\n{'='*60}")
    print(f"  预警信号: {len(alerts)} 只")
    print(f"  仅多头排列（缩量未达标）: {len(only_aligned)} 只")
    print(f"  总覆盖: {len(stocks)} 只 | 满足多头: {len(results)} 只\n")

    # 输出预警详情
    if alerts:
        print("━" * 70)
        print(f"{'评分':<4} {'代码':<10} {'名称':<8} {'现价':<8} {'回撤':<6} {'量比':<6} {'缩量天':<6} {'原因'}")
        print("━" * 70)
        for r in alerts:
            reason_str = " | ".join(r["reasons"][:2])
            print(f"{r['score']:<4} {r['code']:<10} {r['name']:<8} "
                  f"{r['close']:<8} {r['pct_from_high']:<6} {r['vol_ratio']:<6} "
                  f"{r['shrink_days']:<6} {reason_str}")
        print()

        # 输出详细信息
        print("═══ 详细分析 ═══\n")
        for r in alerts:
            print(f"── {r['name']}({r['code']}) 评分 {r['score']} ──")
            print(f"  研报覆盖: {r['institutions']}")
            print(f"  均线: MA5={r['ma5']}  MA10={r['ma10']}  MA20={r['ma20']}  MA60={r['ma60']}")
            print(f"  价格: {r['close']}（MA20上方: {'是' if r['above_ma20'] else '否'}）")
            print(f"  回撤: {r['pct_from_high']}%（10日高={r['high_10d']}）")
            print(f"  量能: 近3日均量={r['avg_vol_3']:.0f} / 前10日均量={r['avg_vol_10_before']:.0f}（量比={r['vol_ratio']}）")
            print(f"  评分依据: {' | '.join(r['reasons'])}")
            print()

    # 保存到文件
    lines = []
    lines.append("# 研报跟踪 · 多头排列+缩量调整 预警报告\n")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"- 研报覆盖个股: {len(stocks)} 只")
    lines.append(f"- 满足多头排列: {len(results)} 只")
    lines.append(f"- 预警信号: **{len(alerts)} 只**\n")

    if alerts:
        lines.append("## 预警信号\n")
        lines.append("| 评分 | 代码 | 名称 | 现价 | 回撤% | 量比 | 缩量天数 | MA5 | MA10 | MA20 | 说明 |")
        lines.append("|------|------|------|------|--------|------|----------|-----|------|------|------|")
        for r in alerts:
            reasons = " ".join(r["reasons"][:2])
            lines.append(
                f"| {r['score']} | {r['code']} | {r['name']} | {r['close']} | "
                f"{r['pct_from_high']}% | {r['vol_ratio']} | {r['shrink_days']}天 | "
                f"{r['ma5']} | {r['ma10']} | {r['ma20']} | {reasons} |"
            )
        lines.append("")

        lines.append("## 详细分析\n")
        for r in alerts:
            lines.append(f"### {r['name']}（{r['code']}）— 评分 {r['score']}\n")
            lines.append(f"- **研报覆盖**: {r['institutions']}")
            lines.append(f"- **均线**: MA5={r['ma5']}, MA10={r['ma10']}, MA20={r['ma20']}, MA60={r['ma60']} — 多头排列确认")
            lines.append(f"- **价格位置**: {r['close']}，从10日高点 {r['high_10d']} 回撤 {r['pct_from_high']}%")
            lines.append(f"- **量能变化**: 近3日均量 {r['avg_vol_3']:.0f}，前10日均量 {r['avg_vol_10_before']:.0f}，量比 {r['vol_ratio']}")
            lines.append(f"- **连续缩量**: {r['shrink_days']} 天")
            lines.append(f"- **评分依据**: {' | '.join(r['reasons'])}")
            lines.append("")

    if only_aligned:
        lines.append(f"\n## 仅多头排列（缩量未达标，{len(only_aligned)}只）\n")
        lines.append("| 代码 | 名称 | 现价 | 回撤% | 量比 | MA5 | MA10 | MA20 |")
        lines.append("|------|------|------|--------|------|-----|------|------|")
        for r in sorted(only_aligned, key=lambda x: x["close"], reverse=True)[:20]:
            lines.append(
                f"| {r['code']} | {r['name']} | {r['close']} | "
                f"{r['pct_from_high']}% | {r['vol_ratio']} | "
                f"{r['ma5']} | {r['ma10']} | {r['ma20']} |"
            )

    report = "\n".join(lines)

    if output_file:
        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[INFO] 报告已保存: {output_file}")
    else:
        print(report)

    return alerts


def main():
    parser = argparse.ArgumentParser(
        description="研报跟踪选股策略：多头排列+缩量调整预警",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python strategy_report_alerts.py                          # 默认运行\n"
            "  python strategy_report_alerts.py --min-score 70           # 提高评分阈值\n"
            "  python strategy_report_alerts.py --days 60                # 回溯60天内的研报股\n"
            "  python strategy_report_alerts.py -o alert.md              # 输出到文件\n"
        ),
    )
    parser.add_argument("--db", default=DB_PATH, help="研报数据库路径")
    parser.add_argument("--min-reports", type=int, default=1, help="最少研报覆盖数（默认1）")
    parser.add_argument("--days", type=int, default=30, help="回溯研报天数（默认30）")
    parser.add_argument("--min-score", type=int, default=60, help="最低评分（默认60）")
    parser.add_argument("--output", "-o", default="", help="输出报告路径")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"[ERROR] 数据库不存在: {args.db}")
        print("请先运行 python fetch_stock_reports.py")
        sys.exit(1)

    alerts = run_strategy(
        db_path=args.db,
        min_reports=args.min_reports,
        max_days=args.days,
        min_score=args.min_score,
        output_file=args.output,
    )
    return alerts


if __name__ == "__main__":
    main()
