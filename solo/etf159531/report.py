"""报告生成：Markdown / JSON / 可选 HTML。

报告内容严格区分「市场信号模式」与「账户模式」，
市场信号模式下不输出不可计算的交易金额与账户回撤。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


def _pct(v, digits: int = 2, sign: bool = False) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if sign:
        return f"{float(v) * 100:+.{digits}f}%"
    return f"{float(v) * 100:.{digits}f}%"


def _num(v, digits: int = 4) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{float(v):.{digits}f}"


def _bool_cn(v) -> str:
    return "是" if v else "否"


def render_markdown(d: dict) -> str:
    meta = d.get("meta", {})
    m = d.get("market", {})
    trend = d.get("trend", {})
    os_ = d.get("oversold", {})
    crisis = d.get("crisis", {})
    caps = d.get("caps", {})
    acc = d.get("account", {})
    trade = d.get("trade", {})
    account_mode = acc.get("mode") == "account"

    L: list[str] = []
    L.append("# 159531 每日动态仓位报告")
    L.append("")
    L.append(f"日期：{meta.get('trade_date')}（数据日期 {meta.get('data_date')}，"
             f"生成时间 {meta.get('generated_at')}，程序版本 {meta.get('version')}）")
    if not meta.get("executable", True):
        L.append("")
        L.append(f"> ⚠ 本次运行状态：{meta.get('status')}。以下内容仅供展示，**不构成可执行仓位指令**。")
    if not account_mode:
        L.append("")
        L.append("> 账户信息未提供：本报告为**仅市场信号模式**，不计算账户回撤与交易金额。")
    L.append("")

    L.append("【市场行情】")
    L.append(f"ETF价格：{_num(m.get('close'), 3)}（未复权收盘）")
    L.append(f"当日涨跌幅：{_pct(m.get('pct_chg'), 2, True)}")
    for k in ("ma5", "ma10", "ma20", "ma60", "ma120", "ma250"):
        L.append(f"{k.upper()}：{_num(m.get(k), 4)}")
    L.append(f"RSI6：{_num(m.get('rsi6'), 2)}")
    L.append(f"RSI14：{_num(m.get('rsi14'), 2)}")
    L.append(f"VOL20：{_pct(m.get('vol20'))}")
    L.append(f"VOL60：{_pct(m.get('vol60'))}")
    L.append(f"有效波动率：{_pct(m.get('effective_vol'))}")
    L.append(f"近20日收益：{_pct(m.get('ret20'), 2, True)}　"
             f"近60日收益：{_pct(m.get('ret60'), 2, True)}　"
             f"近120日收益：{_pct(m.get('ret120'), 2, True)}")
    L.append("")

    L.append("【趋势分析】")
    L.append(f"当前趋势等级：{trend.get('trend_level')} 条均线确认在上（{trend.get('dirs_str', '')}）")
    L.append(f"趋势基础仓位：{_pct(trend.get('base_weight'), 0)}")
    L.append(f"相比昨日变化：{trend.get('change_reason')}")
    L.append(f"确认站上的均线：{('、'.join(trend.get('confirmed_mas', [])) or '无')}")
    L.append("")

    L.append("【超卖分析】")
    L.append(f"是否极端超卖：{_bool_cn(os_.get('state') in ('OVERSOLD_WATCH', 'RECOVERY_1', 'RECOVERY_2'))}"
             f"（状态 {os_.get('state')}）")
    L.append(f"是否产生反弹确认：{_bool_cn(bool(os_.get('activated')))}")
    L.append(f"战术加仓建议：{_pct(os_.get('weight'), 0)}"
             + (f"（事件 {os_.get('event_id')}，触发日 {os_.get('trigger_date')}，"
                f"触发价 {_num(os_.get('trigger_price'), 3)}，事件最低收盘 "
                f"{_num(os_.get('lowest_close'), 3)}）" if os_.get('event_id') else ""))
    if os_.get("exit_reason"):
        L.append(f"事件退出原因：{os_.get('exit_reason')}")
    L.append(f"冷却期截止：{os_.get('cooldown_until') or '—'}")
    L.append("")

    L.append("【风险管理】")
    L.append(f"是否进入危机：{_bool_cn(crisis.get('active'))}")
    L.append(f"危机说明：{crisis.get('reason')}")
    if crisis.get("active"):
        L.append(f"危机中的试探仓：{_pct(crisis.get('probe_weight'), 0)}"
                 f"（{'已激活' if crisis.get('probe_active') else '未激活'}）")
    if account_mode:
        L.append(f"账户当前回撤：{_pct(acc.get('drawdown'))}")
        L.append(f"回撤熔断状态：{acc.get('breaker_stage_cn')}")
    else:
        L.append("账户当前回撤：未提供账户数据，无法计算")
    L.append(f"波动率仓位上限：{_pct(caps.get('vol_cap'), 0)}")
    if account_mode:
        L.append(f"账户回撤仓位上限：{_pct(caps.get('dd_cap'), 0)}")
    L.append(f"危机开关上限：{_pct(caps.get('crisis_cap'), 0)}")
    L.append("")

    L.append("【最终交易建议】")
    if account_mode:
        L.append(f"当前实际仓位：{_pct(acc.get('current_weight'))}")
    L.append(f"最终目标仓位：{_pct(caps.get('final_target'), 0)}")
    if account_mode:
        L.append(f"建议增减仓：{_pct(trade.get('delta_weight'), 1, True)}")
        if trade.get("action") == "HOLD":
            L.append(f"预计交易份额：0（{trade.get('note')}）")
            L.append("预计交易金额：0 元")
        else:
            L.append(f"预计交易份额：{trade.get('exec_shares', 0):+.0f} 份")
            L.append(f"预计交易金额：约 {trade.get('est_amount', 0):,.0f} 元")
        L.append(f"最早执行日期：{trade.get('exec_date')}"
                 f"（信号于 {meta.get('trade_date')} 收盘后生成，最早下一交易日执行）")
    else:
        L.append("建议增减仓：需账户数据，当前无法计算")
        L.append("预计交易份额：需账户数据")
        L.append("预计交易金额：需账户数据")
        L.append(f"最早执行日期：{meta.get('next_trade_date')}")
    L.append("")

    L.append("【仓位上限逐项】")
    L.append(f"策略组合（趋势 {_pct(trend.get('base_weight'), 0)} + 超卖 {_pct(os_.get('weight'), 0)}）"
             f"＝ {_pct(caps.get('raw_weight'), 0)}")
    L.append(f"波动率上限：{_pct(caps.get('vol_cap'), 0)}")
    L.append(f"危机上限：{_pct(caps.get('crisis_cap'), 0)}")
    L.append(f"账户回撤上限：{_pct(caps.get('dd_cap'), 0) if account_mode else '不适用（未提供账户）'}")
    L.append(f"系统上限：{_pct(caps.get('max_position'), 0)}")
    L.append(f"→ 最终仓位由「{caps.get('binding')}」决定，为 {_pct(caps.get('final_target'), 0)}")
    L.append("")

    L.append("【信号解释】")
    for r in d.get("reasons", []):
        L.append(f"- {r}")
    L.append("")

    L.append("【风险提示】")
    for r in d.get("disclaimers", []):
        L.append(f"- {r}")
    L.append("")
    return "\n".join(L)


def save_json(d: dict, out_dir: Path, trade_date: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report_{trade_date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2, default=str)
    return path


def save_markdown(text: str, out_dir: Path, trade_date: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report_{trade_date}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _setup_cjk_font() -> None:
    """优先使用系统已安装的中文字体，避免图中中文变方框。"""
    try:
        from matplotlib import font_manager, rcParams

        installed = {f.name for f in font_manager.fontManager.ttflist}
        for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                     "Source Han Sans SC", "PingFang SC", "WenQuanYi Zen Hei"):
            if name in installed:
                rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
                rcParams["axes.unicode_minus"] = False
                return
    except Exception:
        return


def _chart_base64(d: dict, features: Optional[pd.DataFrame]) -> Optional[str]:
    """生成简化走势图（ETF 收盘 + 目标仓位），失败则跳过。"""
    try:
        import base64
        import io

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        _setup_cjk_font()

        if features is None or features.empty:
            return None
        tail = features.tail(180)
        if tail.empty:
            return None
        fig, ax = plt.subplots(figsize=(10, 4.2), dpi=120)
        ax.plot(pd.to_datetime(tail["trade_date"]), tail["close"], color="#1f3a93", lw=1.6,
                label="ETF 收盘（未复权）")
        if "market_final" in tail.columns:
            ax2 = ax.twinx()
            ax2.fill_between(pd.to_datetime(tail["trade_date"]),
                             tail["market_final"] * 100, color="#2e86de", alpha=0.18)
            ax2.plot(pd.to_datetime(tail["trade_date"]), tail["market_final"] * 100,
                     color="#2e86de", lw=1.2, label="模型仓位(%)")
            ax2.set_ylim(0, 70)
            ax2.set_ylabel("目标仓位 (%)")
        ax.set_ylabel("价格")
        ax.grid(alpha=0.25)
        ax.legend(loc="upper left")
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def save_html(d: dict, text_md: str, out_dir: Path, trade_date: str,
              features: Optional[pd.DataFrame] = None) -> Path:
    """轻量 HTML 报告（卡片式、移动端友好）。"""
    meta = d.get("meta", {})
    caps = d.get("caps", {})
    trend = d.get("trend", {})
    os_ = d.get("oversold", {})
    crisis = d.get("crisis", {})
    acc = d.get("account", {})
    trade = d.get("trade", {})
    account_mode = acc.get("mode") == "account"

    def row(k, v):
        return f"<tr><th>{k}</th><td>{v}</td></tr>"

    chart = _chart_base64(d, features)
    chart_html = (f'<div class="card"><h2>近 180 日走势与模型仓位</h2>'
                  f'<img src="data:image/png;base64,{chart}" alt="走势图"></div>'
                  if chart else "")

    reasons = "".join(f"<li>{r}</li>" for r in d.get("reasons", []))
    disclaimers = "".join(f"<li>{r}</li>" for r in d.get("disclaimers", []))

    acc_rows = ""
    if account_mode:
        acc_rows = (row("账户当前回撤", _pct(acc.get("drawdown"))) +
                    row("当前实际仓位", _pct(acc.get("current_weight"))) +
                    row("熔断状态", acc.get("breaker_stage_cn", "—")))
    trade_rows = ""
    if account_mode:
        trade_rows = (row("建议动作", trade.get("action", "—")) +
                      row("预计交易份额", f"{trade.get('exec_shares', 0):+.0f} 份") +
                      row("预计交易金额", f"{trade.get('est_amount', 0):,.0f} 元"))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>159531 每日动态仓位报告 {meta.get('trade_date')}</title>
<style>
  body {{ margin:0; background:#eef2f7; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         color:#1f2937; font-size:22px; line-height:1.8; -webkit-text-size-adjust:100%; }}
  .wrap {{ max-width: 760px; margin: 0 auto; padding: 18px; }}
  .hero {{ background: linear-gradient(135deg,#1f3a93,#2e86de); color:#fff; border-radius:16px;
           padding:22px 24px; margin-bottom:16px; }}
  .hero h1 {{ font-size:26px; margin:0 0 8px; }}
  .hero p {{ margin:4px 0; font-size:18px; opacity:.95; }}
  .card {{ background:#fff; border-radius:16px; padding:18px 22px; margin-bottom:16px;
           box-shadow:0 2px 10px rgba(31,58,147,.06); overflow:hidden; }}
  h2 {{ font-size:24px; margin:8px 0 12px; padding-left:12px; border-left:6px solid #2e86de; }}
  table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
  th, td {{ font-size:18px; padding:8px 10px; border-bottom:1px solid #eef1f6;
            word-break:break-word; text-align:left; vertical-align:top; }}
  th {{ width:42%; color:#4b5563; font-weight:600; }}
  .badge {{ display:inline-block; padding:2px 12px; border-radius:999px; font-size:17px;
            background:#e8f1ff; color:#1f3a93; }}
  .warn {{ background:#fff4e5; color:#8a4b00; border-radius:12px; padding:12px 16px;
           font-size:18px; margin-bottom:14px; }}
  ul {{ padding-left:22px; margin:8px 0; }}
  li {{ margin:6px 0; }}
  .ft {{ text-align:center; color:#6b7280; font-size:16px; padding:14px 0 22px; }}
  img {{ width:100%; height:auto; border-radius:10px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>159531 每日动态仓位报告</h1>
    <p>交易日 {meta.get('trade_date')} ｜ 数据日期 {meta.get('data_date')}</p>
    <p>生成时间 {meta.get('generated_at')} ｜ 版本 {meta.get('version')}</p>
    <p>最终目标仓位 <span class="badge">{_pct(caps.get('final_target'), 0)}</span></p>
  </div>
  {'' if meta.get('executable', True) else f'<div class="warn">状态：{meta.get("status")}，仅供展示，不构成可执行指令。</div>'}
  {'' if account_mode else '<div class="warn">仅市场信号模式：未提供账户数据，不计算账户回撤与交易金额。</div>'}
  {chart_html}
  <div class="card">
    <h2>市场与信号</h2>
    <table>
      {row("ETF 收盘价", _num(d.get('market',{}).get('close'),3))}
      {row("当日涨跌幅", _pct(d.get('market',{}).get('pct_chg'),2,True))}
      {row("趋势等级 / 基础仓位", f"{trend.get('trend_level')} 条 / {_pct(trend.get('base_weight'),0)}")}
      {row("均线方向", trend.get('dirs_str',''))}
      {row("RSI6 / RSI14", f"{_num(d.get('market',{}).get('rsi6'),2)} / {_num(d.get('market',{}).get('rsi14'),2)}")}
      {row("VOL20 / VOL60", f"{_pct(d.get('market',{}).get('vol20'))} / {_pct(d.get('market',{}).get('vol60'))}")}
      {row("超卖状态", f"{os_.get('state')}（战术 {_pct(os_.get('weight'),0)}）")}
      {row("危机开关", _bool_cn(crisis.get('active')))}
    </table>
  </div>
  <div class="card">
    <h2>仓位上限</h2>
    <table>
      {row("策略组合", _pct(caps.get('raw_weight'),0))}
      {row("波动率上限", _pct(caps.get('vol_cap'),0))}
      {row("危机上限", _pct(caps.get('crisis_cap'),0))}
      {row("账户回撤上限", _pct(caps.get('dd_cap'),0) if account_mode else "不适用")}
      {row("最终仓位 / 决定因素", f"{_pct(caps.get('final_target'),0)} ｜ {caps.get('binding')}")}
      {acc_rows}
      {trade_rows}
    </table>
  </div>
  <div class="card">
    <h2>信号解释</h2>
    <ul>{reasons}</ul>
  </div>
  <div class="card">
    <h2>风险提示</h2>
    <ul>{disclaimers}</ul>
  </div>
  <div class="ft">本报告由程序自动生成，仅用于决策辅助，不构成投资建议，不会自动下单。</div>
</div>
</body>
</html>"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report_{trade_date}.html"
    path.write_text(html, encoding="utf-8")
    return path


def save_report(d: dict, cfg: dict, features: Optional[pd.DataFrame] = None) -> dict:
    """保存 Markdown / JSON / HTML，返回文件路径字典。"""
    from settings import resolve_path

    report_cfg = cfg.get("report", {})
    base = resolve_path(cfg, cfg.get("project", {}).get("report_dir", "reports"))
    trade_date = d["meta"]["trade_date"]
    out_dir = base / trade_date
    md = render_markdown(d)
    paths = {
        "markdown": str(save_markdown(md, out_dir, trade_date)),
        "json": str(save_json(d, out_dir, trade_date)),
    }
    if report_cfg.get("html", True):
        paths["html"] = str(save_html(d, md, out_dir, trade_date, features))
    return paths
