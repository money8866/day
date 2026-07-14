# -*- coding: utf-8 -*-
"""
主题涨跌概率预测系统 - 主入口

用法:
    python -m theme_forecast.main                  # 全部主题
    python -m theme_forecast.main --top 10         # 只看概率最高的10个
    python -m theme_forecast.main --theme 光通信    # 只看指定主题
    python -m theme_forecast.main --min-stocks 10  # 只看成份股>=10的主题
"""
import sys
import os
import argparse
import time
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from theme_forecast import data_loader as dl
from theme_forecast.factors import momentum, synergy, sentiment, flow
from theme_forecast.predictor import fuse_probability, format_report, save_report


# ETF→主题映射（从 mainline_engine/config.yaml 提取关键映射）
ETF_THEME_MAP = {
    "159516.SZ": "半导体设备",
    "159732.SZ": "消费电子与AI终端",
    "159995.SZ": "消费电子与AI终端",
    "512480.SH": "半导体制造",
    "512760.SH": "半导体制造",
    "159825.SZ": "农业",
    "159997.SZ": "家电家居",
    "515030.SH": "新能源汽车链",
    "515050.SH": "5G通信",
    "515170.SH": "基建地产链",
    "515790.SH": "发电与电源设备",
    "512660.SH": "军工",
    "512800.SH": "基建地产链",
    "512880.SH": "券商",
    "512980.SH": "传媒",
    "516160.SH": "商超零售",
    "516510.SH": "人形机器人",
    "516950.SH": "基建地产链",
    "588200.SH": "券商",
    "159870.SZ": "化工链",
    "562500.SH": "半导体设备",
    "159998.SZ": "大农业",
    "512010.SH": "医药产业链",
    "512170.SH": "医药产业链",
    "512690.SH": "白酒",
    "515220.SH": "煤炭",
    "516150.SH": "稀土",
    "516970.SH": "基建地产链",
    "515210.SH": "钢铁",
    "515880.SH": "光通信",
    "159992.SZ": "创新药",
    "563000.SH": "人形机器人",
}


def run_prediction(theme_name: str, theme_stocks: list, trade_date: str,
                   market_index=None, moneyflow=None, limit_list=None,
                   limit_step=None, daily_basic=None, north_hold=None) -> dict:
    """运行单主题预测"""
    codes = [s["code"] for s in theme_stocks]
    n_stocks = len(codes)

    if n_stocks < 3:
        return None

    # 加载K线（60自然日≈40交易日）
    end = trade_date
    from datetime import datetime, timedelta
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
    klines = dl.load_klines(codes, start, end)

    if len(klines) < 3:
        return None

    # 计算各层因子
    mom = momentum.calc_all_momentum(klines, market_index)
    syn = synergy.calc_all_synergy(klines)
    sent = sentiment.calc_all_sentiment(codes, limit_list, limit_step, daily_basic, trade_date, klines)

    # 资金流：找对应的ETF
    etf_code = None
    for ec, tn in ETF_THEME_MAP.items():
        if tn == theme_name or theme_name in tn or tn in theme_name:
            etf_code = ec
            break

    etf_share_df = None
    etf_daily_df = None
    if etf_code:
        etf_share_df = dl.load_etf_share(etf_code)
        etf_daily_df = dl.load_etf_daily(etf_code)

    fl = flow.calc_all_flow(etf_share_df, etf_daily_df, codes, moneyflow, north_hold, klines)

    # 合并所有因子
    all_factors = {}
    all_factors.update(mom)
    all_factors.update(syn)
    all_factors.update(sent)
    all_factors.update(fl)

    # 移除非因子字段
    all_factors.pop("theme_index_close", None)

    # 融合概率
    prediction = fuse_probability(all_factors)
    prediction["theme_name"] = theme_name
    prediction["stock_count"] = n_stocks
    prediction["trade_date"] = trade_date
    prediction["etf_code"] = etf_code

    return prediction


def main():
    parser = argparse.ArgumentParser(description="主题涨跌概率预测")
    parser.add_argument("--top", type=int, default=0, help="只看概率最高的N个主题")
    parser.add_argument("--theme", type=str, default="", help="只看指定主题")
    parser.add_argument("--min-stocks", type=int, default=5, help="最少成份股数")
    parser.add_argument("--output", type=str, default="", help="输出JSON路径")
    args = parser.parse_args()

    start_time = time.time()

    # 1. 加载主题映射
    print("[1/5] 加载主题成份股映射...")
    themes = dl.load_theme_stocks()
    print(f"  共 {len(themes)} 个主题")

    # 2. 获取交易日
    trade_date = dl.get_last_trade_date()
    print(f"[2/5] 交易日: {trade_date}")

    # 3. 加载全市场截面数据（一次性）
    print("[3/5] 加载全市场截面数据...")
    market_index = dl.load_index_daily("000001.SH", n_days=90)
    print(f"  大盘指数: {len(market_index)} 条")

    moneyflow = dl.load_daily_moneyflow(trade_date)
    print(f"  资金流: {len(moneyflow) if moneyflow is not None else 0} 条")

    limit_list = dl.load_limit_list(trade_date)
    print(f"  涨停: {len(limit_list) if limit_list is not None else 0} 条")

    limit_step = dl.load_limit_step(trade_date)
    print(f"  炸板: {len(limit_step) if limit_step is not None else 0} 条")

    daily_basic = dl.load_daily_basic(trade_date)
    print(f"  daily_basic: {len(daily_basic) if daily_basic is not None else 0} 条")

    north_hold = dl.load_north_hold(trade_date)
    print(f"  北向: {len(north_hold) if north_hold is not None else 0} 条")

    # 4. 逐主题预测
    print(f"[4/5] 计算主题涨跌概率...")

    target_themes = {}
    for name, stocks in themes.items():
        if args.theme and args.theme not in name:
            continue
        if len(stocks) < args.min_stocks:
            continue
        target_themes[name] = stocks

    print(f"  待计算: {len(target_themes)} 个主题")

    all_results = []
    for i, (name, stocks) in enumerate(target_themes.items(), 1):
        try:
            result = run_prediction(name, stocks, trade_date, market_index,
                                    moneyflow, limit_list, limit_step,
                                    daily_basic, north_hold)
            if result:
                all_results.append(result)
                prob = result["probability"]
                fp5 = result.get("future_probs", {}).get("5d", {}).get("prob", 0)
                print(f"  [{i}/{len(target_themes)}] {name:<22} 当前{prob}% | 未来5日{fp5}%")
        except Exception as e:
            print(f"  [{i}/{len(target_themes)}] {name} 失败: {e}")

    # 5. 排序输出（按未来5日上涨概率降序）
    print(f"[5/5] 排序输出...")
    def sort_key(x):
        fp = x.get("future_probs", {}).get("5d", {})
        return -fp.get("prob", x.get("probability", 50))
    all_results.sort(key=sort_key)

    if args.top > 0:
        all_results = all_results[:args.top]

    elapsed = time.time() - start_time
    print(f"\n耗时: {elapsed:.1f}s")
    print(f"{'='*60}")

    # 打印报告
    for result in all_results:
        theme_name = result["theme_name"]
        report = format_report(theme_name, None, result, result["stock_count"])
        print(report)
        print()

    # 汇总表（核心：未来上涨概率）
    print(f"{'='*70}")
    print(f"  汇总（按未来5日上涨概率降序）")
    print(f"{'='*70}")
    print(f"{'#':>3} {'主题':<22} {'当前':>5} {'3日':>5} {'5日':>5} {'10日':>5} {'预期收益':>8} {'置信':<4}")
    print("-" * 70)
    for i, r in enumerate(all_results, 1):
        fp = r.get("future_probs", {})
        p3 = fp.get("3d", {}).get("prob", 0)
        p5 = fp.get("5d", {}).get("prob", 0)
        p10 = fp.get("10d", {}).get("prob", 0)
        ret5 = fp.get("5d", {}).get("avg_ret", 0)
        conf = fp.get("5d", {}).get("confidence", "")
        conf_label = {"high": "高", "medium": "中", "low": "低"}.get(conf, "")
        print(f"{i:>3} {r['theme_name']:<22} {r['probability']:>4}% {p3:>4}% {p5:>4}% {p10:>4}% {ret5:>+7.2f}% {conf_label:<4}")

    # 保存JSON
    output_path = args.output or str(PROJECT_ROOT / "theme_forecast" / "output" / f"theme_forecast_{trade_date}.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    save_report(all_results, output_path)
    print(f"\n报告已保存: {output_path}")


if __name__ == "__main__":
    main()
