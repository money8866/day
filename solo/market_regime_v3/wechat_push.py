"""WeChat Push - 微信推送模块

通过 PushPlus 推送市场状态报告到微信。
支持 markdown 模板，自动生成简洁版概览。
"""

import os
import requests
from typing import Dict, Optional
from dotenv import load_dotenv

# 加载配置文件（与 tushare_quant.py 一致）
_ENV_PATH = os.getenv("ENV_FILE", "d:/mystock/config/.env")
if os.path.exists(_ENV_PATH):
    load_dotenv(_ENV_PATH)
elif os.path.exists("d:/mystock/config/.env"):
    load_dotenv("d:/mystock/config/.env")


def send_pushplus(content: str, token: str = None, title: str = None) -> bool:
    """通过 PushPlus 推送微信消息（支持markdown）"""
    if token is None:
        token = os.getenv("PUSHPLUS", os.getenv("PUSHPLUS_TOKEN", ""))
    if not token:
        print("  ⚠ PushPlus token 未配置（请设置环境变量 PUSHPLUS）")
        return False
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": title or "Market Regime Report",
        "content": content,
        "template": "markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        result = resp.json()
        if result.get('code') == 200:
            print("  ✅ PushPlus 推送成功")
            return True
        print(f"  ⚠ PushPlus 发送失败: {result.get('msg', '未知错误')}")
        return False
    except Exception as e:
        print(f"  ⚠ PushPlus 异常: {e}")
        return False


def build_summary(report_dict: Dict) -> str:
    """从报告字典构建简洁版微信推送内容"""
    r = report_dict
    meta = r.get("meta", {})
    overview = r.get("overview", {})
    tb = r.get("theme_beta", {})
    rc = r.get("risk_control", {})
    ts = r.get("trading_style", {})
    risks = r.get("risk_warnings", [])
    pb_list = r.get("pullback_qualified", [])  # 符合回调条件的个股列表

    trade_date = meta.get("trade_date", "")

    lines = []
    lines.append(f"# 市场状态报告 — {trade_date}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 综合概览
    lines.append("## 综合概览")
    lines.append("")
    lines.append(f"- **Market Score**: {overview.get('market_score', 0):.0f}/100")
    lines.append(f"- **Market Regime**: {overview.get('regime', '')}（{overview.get('regime_cn', '')}）")
    lines.append(f"- **热度**: {overview.get('heat_score', 0):.0f}/100 {overview.get('heat_level', '')}")
    lines.append(f"- **风格**: {overview.get('dominant_style', '')} → {ts.get('label', '波段操作')}")
    lines.append(f"- **建议仓位**: {overview.get('exposure', 0):.0f}%")
    lines.append("")

    # 市场评分
    lines.append(f"**评分**: {overview.get('market_score', 0):.0f}分 | "
                 f"指数{overview.get('index_score', 0):.0f} "
                 f"宽度{overview.get('breadth_score', 0):.0f} "
                 f"情绪{overview.get('sentiment_score', 0):.0f}")
    lines.append("")

    # 指数强度简表
    idx = r.get("index_strength", {})
    idx_rows = idx.get("table_rows", [])
    if idx_rows:
        lines.append("| 指数 | 分 | 趋 | 动 | MA | MACD |")
        lines.append("|------|----|----|----|----|------|")
        for row in idx_rows[:5]:
            if isinstance(row, dict):
                lines.append(
                    f"| {row.get('name','')[:2]} | {row.get('score',0):.0f} | "
                    f"{row.get('trend',0):.0f} | {row.get('momentum',0):.0f} | "
                    f"{row.get('ma_alignment',0):.0f} | {row.get('macd',0):.0f} |"
                )
        lines.append("")

    # 主题资金分配（精简）
    if tb.get("allocations"):
        for tname, alloc in sorted(tb.get("allocations", {}).items(),
                                    key=lambda x: x[1], reverse=True):
            score = tb.get("theme_scores", {}).get(tname, 0)
            bar = "▬" * int(alloc * 10)
            lines.append(f"**{tname}** {alloc*100:.0f}% {bar} ({score:.0f}分)")
    lines.append("")

    # ======================================================
    # 核心：符合回踩条件的龙头标的 + 入场逻辑
    # ======================================================
    if pb_list:
        lines.append("## 符合回踩条件的标的")
        lines.append("")
        for i, item in enumerate(pb_list):
            name = item.get("name", "")
            code = item.get("ts_code", "")
            theme = item.get("theme", "")
            score = item.get("leader_score", 0)
            ret_60d = item.get("ret_60d", 0)
            drawdown = item.get("drawdown", 0)
            quality = item.get("quality_score", 0)
            pullback_ma = item.get("pullback_ma", "")
            ref_price = item.get("ref_price", 0)
            stop_loss = item.get("stop_loss", 0)
            take_profit = item.get("take_profit", 0)
            atr_val = item.get("atr", 0)
            is_first = item.get("is_first_pullback", False)

            lines.append(f"**{i+1}. {name}（{code}）** 主题：{theme}")
            lines.append(f"  龙头评分: {score:.0f}分 | 60日涨幅: {ret_60d*100:.0f}% | "
                         f"回撤: {drawdown*100:.1f}%")
            lines.append(f"  回踩: {pullback_ma} | 首次回调: {'是' if is_first else '否'} | "
                         f"质量分: {quality:.2f}")
            lines.append(f"  ── 入场逻辑 ──")
            lines.append(f"  低吸参考价: **{ref_price:.2f}**（{pullback_ma}附近）")
            lines.append(f"  防守止损: **{stop_loss:.2f}**（{stop_loss/ref_price-1:.1%}）")
            if take_profit > ref_price:
                lines.append(f"  目标止盈: **{take_profit:.2f}**（+{take_profit/ref_price-1:.1%}）")
            lines.append(f"  ATR: {atr_val:.2f}")
            lines.append("")
    else:
        lines.append("## 符合条件的标的")
        lines.append("当前无符合回踩条件的龙头")
        lines.append("")

    # 风控概要
    lines.append("## 风控")
    lines.append(f"- 安全状态: {'✅' if rc.get('is_safe', True) else '⚠️ 有风险'}")
    lines.append(f"- 止损: {rc.get('stop_loss_atr', 2.0)}×ATR | "
                 f"止盈: {rc.get('take_profit_atr', 3.0)}×ATR | "
                 f"单票上限: {rc.get('max_per_position_pct', 0.15)*100:.0f}%")

    # 风险提示
    if risks:
        danger_risks = [r for r in risks if r.get("severity") == "danger"]
        if danger_risks:
            for r_item in danger_risks[:2]:
                lines.append(f"🔴 {r_item.get('type','')}: {r_item.get('detail','')}")

    lines.append("")
    lines.append("---")
    lines.append(f"*Market Regime V3 · {trade_date} 自动推送*")

    return "\n".join(lines)
