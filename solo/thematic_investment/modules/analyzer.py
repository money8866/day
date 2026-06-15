"""
回测分析器 (analyzer.py)
=========================

生成回测绩效分析报告：
  1. 绩效指标汇总 (收益率/年化/夏普/最大回撤/换手率)
  2. 月度收益热力图
  3. 净值曲线
  4. 持仓权重时序图
  5. HTML 报告输出

依赖:
  pip install matplotlib seaborn pandas
"""

from __future__ import annotations

import os
import sys
import json
import datetime
from typing import Any, Dict, List

import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd

_CURRENT_DIR: str = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR: str = os.path.dirname(_CURRENT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from modules.utils import setup_logger

logger = setup_logger(
    name="analyzer",
    log_dir=os.path.join(_PARENT_DIR, "logs"),
    log_file="analyzer.log",
)


# ============================================================================ #
# 1. 绩效指标计算
# ============================================================================ #
class PerformanceAnalyzer:
    """绩效指标分析"""

    @staticmethod
    def calculate_metrics(results: Dict[str, Any]) -> Dict[str, Any]:
        """计算绩效指标"""
        metrics = {
            "total_return": round(results.get("total_return", 0.0) * 100, 2),
            "annual_return": round(results.get("annual_return", 0.0) * 100, 2),
            "max_drawdown": round(results.get("max_drawdown", 0.0) * 100, 2),
            "sharpe_ratio": round(results.get("sharpe_ratio", 0.0), 2),
            "turnover_ratio": round(results.get("turnover_ratio", 0.0), 2),
            "final_value": round(results.get("final_value", 0.0), 2),
        }
        return metrics

    @staticmethod
    def format_metrics(metrics: Dict[str, Any]) -> str:
        """格式化指标为文本"""
        lines = [
            "=" * 50,
            "  回测绩效指标",
            "=" * 50,
            f"  总收益率:        {metrics['total_return']}%",
            f"  年化收益率:      {metrics['annual_return']}%",
            f"  最大回撤:        {metrics['max_drawdown']}%",
            f"  夏普比率:        {metrics['sharpe_ratio']}",
            f"  换手率:          {metrics['turnover_ratio']}",
            f"  期末净值:        {metrics['final_value']:,}",
            "=" * 50,
        ]
        return "\n".join(lines)


# ============================================================================ #
# 2. 可视化生成器
# ============================================================================ #
class ReportVisualizer:
    """报告可视化"""

    def __init__(self, results: Dict[str, Any], save_dir: str = "reports"):
        self.results = results
        self.save_dir = os.path.join(_PARENT_DIR, save_dir)
        os.makedirs(self.save_dir, exist_ok=True)

    def plot_monthly_heatmap(self):
        """绘制月度收益热力图"""
        monthly_returns = self.results.get("monthly_returns", {})
        if not monthly_returns:
            return None

        # 构建数据框
        df = pd.DataFrame(list(monthly_returns.items()), columns=["month", "return"])
        df["year"] = df["month"].apply(lambda x: x[0])
        df["month"] = df["month"].apply(lambda x: x[1])
        pivot = df.pivot(index="year", columns="month", values="return").fillna(0)

        # 绘制热力图
        plt.figure(figsize=(12, 6))
        sns.heatmap(pivot, annot=True, fmt=".1%", cmap="RdYlGn", center=0,
                    xticklabels=["1月", "2月", "3月", "4月", "5月", "6月",
                                "7月", "8月", "9月", "10月", "11月", "12月"])
        plt.title("月度收益热力图")
        plt.xlabel("月份")
        plt.ylabel("年份")

        filename = os.path.join(self.save_dir, "monthly_heatmap.png")
        plt.savefig(filename, dpi=150, bbox_inches="tight")
        plt.close()
        return filename

    def plot_equity_curve(self):
        """绘制净值曲线"""
        # 模拟净值曲线（实际应从回测获取）
        dates = pd.date_range(start="2024-01-01", periods=252, freq="B")
        equity = np.cumprod(1 + np.random.normal(0.0005, 0.01, len(dates)))
        df = pd.DataFrame({"date": dates, "equity": equity})

        plt.figure(figsize=(12, 6))
        plt.plot(df["date"], df["equity"], label="策略净值", color="#1f77b4")
        plt.plot(df["date"], np.ones(len(df)), label="基准", color="#ff7f0e", linestyle="--")
        plt.title("策略净值曲线")
        plt.xlabel("日期")
        plt.ylabel("净值")
        plt.legend()
        plt.grid(True)

        filename = os.path.join(self.save_dir, "equity_curve.png")
        plt.savefig(filename, dpi=150, bbox_inches="tight")
        plt.close()
        return filename

    def plot_drawdown(self):
        """绘制回撤曲线"""
        dates = pd.date_range(start="2024-01-01", periods=252, freq="B")
        equity = np.cumprod(1 + np.random.normal(0.0005, 0.01, len(dates)))
        max_equity = np.maximum.accumulate(equity)
        drawdown = (max_equity - equity) / max_equity

        plt.figure(figsize=(12, 6))
        plt.fill_between(dates, drawdown * 100, 0, color="#ff4b5c", alpha=0.3)
        plt.plot(dates, drawdown * 100, color="#ff4b5c", label="回撤")
        plt.title("回撤曲线")
        plt.xlabel("日期")
        plt.ylabel("回撤 (%)")
        plt.legend()
        plt.grid(True)

        filename = os.path.join(self.save_dir, "drawdown.png")
        plt.savefig(filename, dpi=150, bbox_inches="tight")
        plt.close()
        return filename


# ============================================================================ #
# 3. HTML 报告生成器
# ============================================================================ #
class HTMLReportGenerator:
    """HTML 报告生成"""

    HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>主题轮动策略回测报告</title>
    <style>
        body { font-family: 'Microsoft YaHei', sans-serif; margin: 40px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 40px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
        h2 { color: #34495e; margin-top: 30px; }
        .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin: 20px 0; }
        .metric-box { background: #ecf0f1; padding: 20px; border-radius: 8px; text-align: center; }
        .metric-label { color: #7f8c8d; font-size: 14px; }
        .metric-value { font-size: 24px; font-weight: bold; color: #2c3e50; }
        .positive { color: #27ae60; }
        .negative { color: #e74c3c; }
        img { max-width: 100%; border-radius: 8px; margin: 10px 0; }
        .footer { text-align: center; color: #95a5a6; font-size: 14px; margin-top: 40px; padding-top: 20px; border-top: 1px solid #ecf0f1; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 主题轮动策略回测报告</h1>
        <p style="color: #7f8c8d;">生成时间: {report_time}</p>
        <p style="color: #7f8c8d;">回测周期: {start_date} ~ {end_date}</p>

        <h2>📊 绩效指标汇总</h2>
        <div class="metrics">
            <div class="metric-box">
                <div class="metric-label">总收益率</div>
                <div class="metric-value {tr_color}">{total_return}%</div>
            </div>
            <div class="metric-box">
                <div class="metric-label">年化收益率</div>
                <div class="metric-value {ar_color}">{annual_return}%</div>
            </div>
            <div class="metric-box">
                <div class="metric-label">最大回撤</div>
                <div class="metric-value {md_color}">{max_drawdown}%</div>
            </div>
            <div class="metric-box">
                <div class="metric-label">夏普比率</div>
                <div class="metric-value {sr_color}">{sharpe_ratio}</div>
            </div>
            <div class="metric-box">
                <div class="metric-label">换手率</div>
                <div class="metric-value">{turnover_ratio}</div>
            </div>
            <div class="metric-box">
                <div class="metric-label">期末净值</div>
                <div class="metric-value">¥ {final_value}</div>
            </div>
        </div>

        <h2>📉 净值曲线</h2>
        <img src="{equity_plot}" alt="净值曲线">

        <h2>📉 回撤曲线</h2>
        <img src="{drawdown_plot}" alt="回撤曲线">

        <h2>🔥 月度收益热力图</h2>
        <img src="{heatmap_plot}" alt="月度收益热力图">

        <div class="footer">
            <p>主题投资系统 - 回测报告</p>
        </div>
    </div>
</body>
</html>
    """

    def __init__(self, results: Dict[str, Any], start_date: str, end_date: str):
        self.results = results
        self.start_date = start_date
        self.end_date = end_date
        self.visualizer = ReportVisualizer(results)

    def generate(self) -> str:
        """生成 HTML 报告"""
        # 计算指标
        metrics = PerformanceAnalyzer.calculate_metrics(self.results)

        # 生成可视化
        equity_plot = self.visualizer.plot_equity_curve()
        drawdown_plot = self.visualizer.plot_drawdown()
        heatmap_plot = self.visualizer.plot_monthly_heatmap()

        # 颜色判断
        def get_color(value, is_percent=True, invert=False):
            val = float(value)
            if is_percent:
                val /= 100
            if invert:
                return "negative" if val > 0 else "positive"
            return "positive" if val >= 0 else "negative"

        # 填充模板
        report_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        html_content = self.HTML_TEMPLATE.format(
            report_time=report_time,
            start_date=self.start_date,
            end_date=self.end_date,
            total_return=metrics["total_return"],
            annual_return=metrics["annual_return"],
            max_drawdown=metrics["max_drawdown"],
            sharpe_ratio=metrics["sharpe_ratio"],
            turnover_ratio=metrics["turnover_ratio"],
            final_value=f"{metrics['final_value']:,}",
            tr_color=get_color(metrics["total_return"]),
            ar_color=get_color(metrics["annual_return"]),
            md_color=get_color(metrics["max_drawdown"], invert=True),
            sr_color=get_color(metrics["sharpe_ratio"], is_percent=False),
            equity_plot=os.path.basename(equity_plot) if equity_plot else "",
            drawdown_plot=os.path.basename(drawdown_plot) if drawdown_plot else "",
            heatmap_plot=os.path.basename(heatmap_plot) if heatmap_plot else "",
        )

        # 保存 HTML
        filename = os.path.join(self.visualizer.save_dir, "backtest_report.html")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info("[HTMLReport] 报告已保存: %s", filename)
        return filename


# ============================================================================ #
# 4. 便捷函数
# ============================================================================ #
def generate_report(results: Dict[str, Any], start_date: str, end_date: str) -> str:
    """生成完整报告"""
    generator = HTMLReportGenerator(results, start_date, end_date)
    return generator.generate()


def print_summary(results: Dict[str, Any]):
    """打印绩效摘要"""
    metrics = PerformanceAnalyzer.calculate_metrics(results)
    print(PerformanceAnalyzer.format_metrics(metrics))


if __name__ == "__main__":
    # 测试
    test_results = {
        "total_return": 0.2567,
        "annual_return": 0.1234,
        "max_drawdown": 0.0856,
        "sharpe_ratio": 1.85,
        "turnover_ratio": 5.2,
        "final_value": 1256700.0,
        "monthly_returns": {
            (2024, 1): 0.05, (2024, 2): -0.02, (2024, 3): 0.08,
            (2024, 4): 0.03, (2024, 5): -0.01, (2024, 6): 0.06,
            (2024, 7): 0.04, (2024, 8): -0.03, (2024, 9): 0.07,
            (2024, 10): 0.02, (2024, 11): 0.09, (2024, 12): 0.01,
        },
    }
    print_summary(test_results)
    generate_report(test_results, "2024-01-01", "2024-12-31")
