# -*- coding: utf-8 -*-
"""
中报猎手 × EGPT 回踩择时
=====================================================
对中报猎手(zhongbao_hunt_*.csv)选出的翻倍潜力股，叠加 EGPT
(Earnings Growth Pullback Timing) 回踩择时算法：

  EGPT 回踩形态（pullback_buy.analyze_shape）：
    洗盘(近10日回撤≥8%) → 放量首阳(涨幅≥3%+量比≥1.5) →
    缩量回踩不破实体(1-2日为最优买点窗口) → 回踩买点分(0-100)
    次日操作：✅次日可买入(回踩中&分≥60) / ⚠️次日观察等回踩(首阳)
              / ⚠️观察(回踩中低分) / ❌仅观察不买入(回踩完成)

  买点确认（EGPT enhanced 规则）：
    买点2(缩量回踩VWAP确认) > 买点1(放量突破VWAP+筹码峰) > 未突破
    ATR动态止损 = 现价 - 2×ATR14

  回测结论(EGPT自带)：回踩中1-2日为最优买点窗口(次日上涨率68%，
  分≥70次日+2.65%)；回踩≥3日无次日alpha，仅观察。
"""
import os
import sys
import argparse

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "multi_factor_picker"))
sys.path.insert(0, os.path.join(BASE_DIR, "multi_factor_picker", ".."))

from data_fetcher import DataFetcher
from enhanced_timing_analysis import _calc_vwap, _calc_atr, _calc_chip_concentration_peak
from pullback_buy import analyze_shape

from multi_factor_picker.main import load_config, get_token
import treasure_hunter as th

# 复用 EGPT 每日推送的 PushPlus 发送能力（消息大小自动降级）
from multi_factor_picker.push_washout_recovery import push_to_wechat  # noqa: E402

REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

# ── 主题热度配置（回测结论 20260818 + 网格搜索） ──
# 高热度(ETF20日≥10%)追高是负期望；网格搜索 T+5 胜率最高组合:
#   主题热度 5~15%(已启动未过热) + 主题白名单 + 扣非增速≥50%
#   → T+5 胜率 64.3% / T+5 +3.13% (42笔, 2023-2026四中报季)
THEME_MAP_FILE = os.path.join(REPORT_DIR, "theme_stock_map_latest_v2.json")
THEME_CFG_FILE = os.path.join(BASE_DIR, "theme_config.json")
THEME_WHITELIST = {"智能驾驶", "信创", "新能源车", "消费电子", "半导体", "创新药",
                   "机器人", "游戏", "建筑装饰", "传媒", "能源金属", "商业航天"}
HEAT_SWEET = (0.05, 0.15)     # 主题热度甜区: ETF近20日涨幅 5%~15%
HEAT_HOT = 0.15               # ≥15% 视为追高风险区
DTY_MIN = 50                  # 扣非增速≥50% 才可入选"主题优选"

STAGE_ORDER = {"回踩中": 0, "回踩完成": 1, "首阳确认": 2, "洗盘缩量": 3,
               "延续上涨": 4, "首阳后破位": 5}
OP_ORDER = {"✅ 次日可买入": 0, "⚠️ 次日观察等回踩": 1, "⚠️ 观察": 2,
            "❌ 仅观察不买入": 3, "❌ 等待首阳": 4}


# ════════════════════════════════════════════════════════════════
# 主题热度（复用回测脚本逻辑, ETF近20日涨幅=主题热度, 无前视）
# ════════════════════════════════════════════════════════════════
def load_theme_heat_map():
    """返回 (stock2theme, theme2etf, etf_series)"""
    import json
    from tail_backtest_tdx import parse_tdx_day_file as _pt, ts_code_to_tdx_file as _tt
    stock2theme, theme2etf, etf_series = {}, {}, {}
    try:
        with open(THEME_MAP_FILE, encoding='utf-8') as f:
            tm = json.load(f)
        for name, members in tm.get('themes', {}).items():
            for m in members:
                code = m.get('code', '')
                if code:
                    stock2theme.setdefault(code, []).append(name)
    except Exception as e:
        print(f"[警告] 主题映射加载失败: {e}")
    try:
        with open(THEME_CFG_FILE, encoding='utf-8') as f:
            cfg = json.load(f)
        for _, v in cfg.items():
            name = v.get('name_cn', '')
            if name and v.get('main_etf'):
                theme2etf[name] = v['main_etf']
    except Exception as e:
        print(f"[警告] theme_config 加载失败: {e}")
    for etf in set(theme2etf.values()):
        f = _tt(etf)
        df = _pt(f) if f and os.path.exists(f) else None
        if df is not None and len(df) > 200:
            etf_series[etf] = pd.Series(df['close'].values, index=df['trade_date'].values)
    print(f"  主题热度: 映射{len(stock2theme)}只 | ETF {len(etf_series)}只")
    return stock2theme, theme2etf, etf_series


def theme_heat_at(theme, date, theme2etf, etf_series):
    etf = theme2etf.get(theme)
    if not etf or etf not in etf_series:
        return None
    s = etf_series[etf]
    pos = s.index.searchsorted(date, side='right') - 1
    if pos < 20:
        return None
    c0, c20 = float(s.iloc[pos]), float(s.iloc[pos - 20])
    return c0 / c20 - 1.0 if c20 > 0 else None


def theme_status(theme, heat, dty=None):
    """主题状态: 主题优选(白名单+甜区5~15%+扣非≥50%) > 白名单 > 高热度(追高) > 冷门/无数据"""
    if not theme:
        return "冷门(无主题)"
    if heat is None or (isinstance(heat, float) and np.isnan(heat)):
        return f"{theme}(无热度数据)"
    if theme in THEME_WHITELIST and HEAT_SWEET[0] <= heat <= HEAT_SWEET[1]:
        if dty is not None and dty >= DTY_MIN:
            return f"{theme}(优选)"
        return f"{theme}(白名单)"
    if theme in THEME_WHITELIST:
        return f"{theme}(白名单)"
    if heat >= HEAT_HOT:
        return f"{theme}(高热度⚠️)"
    return f"{theme}(热度{heat * 100:.0f}%)"


def find_latest_zhongbao_csv() -> str:
    files = [f for f in os.listdir(REPORT_DIR)
             if f.startswith("zhongbao_hunt_") and f.endswith(".csv")]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(REPORT_DIR, files[0])


def buy_point_type(daily: pd.DataFrame, vwap: float, peak_high: float,
                   peak_low: float, price: float, ma20: float,
                   vols: np.ndarray, closes: np.ndarray) -> tuple:
    """EGPT 买点确认：买点2(回踩VWAP确认) > 买点1(放量突破) > 未突破"""
    vwap_bt = vwap is not None and price > vwap
    chip_bt = peak_low is not None and peak_high is not None and price > peak_high
    confirm = False
    if vwap_bt and chip_bt and ma20:
        above_ma20 = price > ma20
        vol_ratio = float(np.mean(vols[-5:])) / float(np.mean(vols[-20:])) if float(np.mean(vols[-20:])) > 0 else 99
        has_dipped = any(closes[-i] <= vwap * 1.02 for i in range(1, min(11, len(closes) + 1)))
        if above_ma20 and vol_ratio < 1.2 and has_dipped:
            confirm = True
    if vwap_bt and chip_bt and confirm:
        return "买点2(缩量回踩VWAP确认)", True
    if vwap_bt and chip_bt:
        return "买点1(放量突破VWAP+筹码峰)", False
    return "未突破", False


def _push_cell(r, key, fmt="{:.1f}"):
    v = r.get(key)
    if v is None or (isinstance(v, float) and np.isnan(v)) or v == "":
        return "--"
    if isinstance(v, str):
        return v
    try:
        return fmt.format(float(v))
    except Exception:
        return str(v)


def _push_bp_short(bp) -> str:
    s = str(bp)
    if "买点2" in s:
        return "买点2"
    if "买点1" in s:
        return "买点1"
    return "未突破"


def _push_table(lines, title, sub):
    """推送 Markdown 表格（股票/买点/回踩分/回踩天数/净利/扣非/主题/现价/止损）"""
    if len(sub) == 0:
        return
    lines.append(f"## {title}（{len(sub)} 只）")
    lines.append("")
    lines.append("| 股票 | 买点 | 回踩分 | 回踩日 | 净利 | 扣非 | 主题状态 | 现价 | 止损 |")
    lines.append("|------|:---:|:---:|:---:|:---:|:---:|------|:---:|:---:|")
    for _, r in sub.iterrows():
        name = f"{r['名称']}({str(r['代码']).replace('.SZ', '').replace('.SH', '')})"
        npg = r.get("净利增速")
        npg_s = f"{npg:+.0f}%" if isinstance(npg, (int, float)) and not (isinstance(npg, float) and np.isnan(npg)) else "--"
        dty = r.get("扣非增速")
        dty_s = f"{dty:+.0f}%" if isinstance(dty, (int, float)) and not (isinstance(dty, float) and np.isnan(dty)) else "--"
        th_s = str(r.get("主题状态", ""))
        lines.append(
            f"| {name} | {_push_bp_short(r.get('买点确认'))} | {_push_cell(r, '回踩买点分', '{:.0f}')} "
            f"| {_push_cell(r, '回踩天数', '{:.0f}')} | {npg_s} | {dty_s} | {th_s} "
            f"| {_push_cell(r, '现价', '{:.2f}')} | {_push_cell(r, 'ATR动态止损价', '{:.2f}')} |"
        )
    lines.append("")


def build_push_msg(out: pd.DataFrame, trade_date: str, pref, pref_watch, buy, watch, hot_bp) -> str:
    """构建中报猎手×EGPT 择时的微信推送消息（Markdown）"""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append("# 中报猎手×EGPT 回踩择时")
    lines.append(f"报告日期: {trade_date} | 推送时间: {now}")
    lines.append("")
    lines.append("> 口径：中报业绩正 + EGPT回踩择时。回测最优组合")
    lines.append("> (白名单主题×甜区热度5~15%×扣非≥50%×突破确认) T+5胜率72%/+5.2%。")
    lines.append("")

    _push_table(lines, "🎯 主题优选·确认买点", pref)
    if len(pref_watch):
        lines.append(f"> 主题优选内 {len(pref_watch)} 只未突破→降级观察（回测T+20 -6.5%拖累组合，不追）")
        lines.append("")
    _push_table(lines, "✅ 次日可买入", buy)
    _push_table(lines, "⚠️ 观察/等回踩（形态未确认需触发）", watch)
    if len(hot_bp):
        lines.append("## ⛔ 追高警示（买点1×高热度≥15%，回避）")
        lines.append("")
        lines.append("> 回测：买点1放量突破在甜区热度内 T+5 +5.2%/胜率72%；"
                     "高热度(≥15%)时 T+5 胜率仅15%，追高=接盘。")
        for _, r in hot_bp.iterrows():
            name = f"{r['名称']}({str(r['代码']).replace('.SZ', '').replace('.SH', '')})"
            lines.append(f"- {name} 主题:{r.get('主题状态', '')} 买点:{_push_bp_short(r.get('买点确认'))}")
        lines.append("")

    if len(pref) == 0 and len(pref_watch) == 0 and len(buy) == 0 and len(watch) == 0 and len(hot_bp) == 0:
        lines.append("> 今日无信号：名单内无满足'业绩正+回踩形态+主题优选'的标的，空仓等待。")
        lines.append("")

    lines.append("---")
    lines.append("> **买入纪律**：次日开盘承接，不追涨停/高开；止损=现价-2×ATR14；")
    lines.append("> 未突破仅观察不追；高热度(≥15%)回避；买点窗口=回踩1-2日。")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="择时数据截止日 YYYYMMDD（默认最近交易日）")
    ap.add_argument("--csv", default=None, help="中报猎手CSV路径（默认最新）")
    ap.add_argument("--push", action="store_true", help="运行后推送结果到微信(PushPlus)")
    args = ap.parse_args()

    trade_date = args.date or th.get_last_trade_date()
    csv_path = args.csv or find_latest_zhongbao_csv()
    if csv_path is None or not os.path.exists(csv_path):
        print(f"[错误] 未找到中报猎手CSV: {csv_path}")
        return

    print("━" * 70)
    print("  中报猎手 × EGPT 回踩择时")
    print(f"  中报名单: {os.path.basename(csv_path)}")
    print(f"  择时截止: {trade_date}")
    print("━" * 70)

    src = pd.read_csv(csv_path, encoding="utf-8-sig")
    if "ts_code" not in src.columns:
        print("[错误] CSV 缺少 ts_code 列")
        return
    print(f"  中报猎手标的: {len(src)} 只")

    config = load_config()
    fetcher = DataFetcher(get_token(config), config)
    start_date = (pd.Timestamp(trade_date) - pd.Timedelta(days=220)).strftime("%Y%m%d")

    # 主题热度映射
    print("  [加载主题热度]")
    stock2theme, theme2etf, etf_series = load_theme_heat_map()

    rows = []
    for i, (_, r) in enumerate(src.iterrows()):
        code = str(r["ts_code"]).strip()
        name = str(r.get("name", ""))
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  [{i+1}/{len(src)}] {name}({code})")

        try:
            daily = fetcher.get_daily_by_code(code, start_date=start_date, end_date=trade_date)
        except Exception:
            daily = None
        if daily is None or len(daily) < 30:
            rows.append({"代码": code, "名称": name, "形态阶段": "无形态", "次日操作": "--",
                         "回踩买点分": np.nan, "回踩天数": 0,
                         "主题": "", "主题热度%": np.nan, "主题状态": "冷门(无主题)"})
            continue

        daily = daily.sort_values("trade_date").reset_index(drop=True)
        closes = daily["close"].astype(float).values
        vols = daily["vol"].astype(float).values
        price = float(closes[-1])
        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else None
        vwap = _calc_vwap(daily, 20)
        atr = _calc_atr(daily, 14)
        peak_low, peak_high, peak_ratio = _calc_chip_concentration_peak(daily, 60)

        shape = analyze_shape(daily) or {}
        bp, confirm = buy_point_type(daily, vwap, peak_high, peak_low, price, ma20, vols, closes)
        dynamic_stop = round(price - 2.0 * atr, 2) if atr and atr > 0 else np.nan

        # 主题热度: 取股票所属主题中热度最高者
        themes = stock2theme.get(code, [])
        theme, heat = "", np.nan
        if themes:
            best_t, best_h = None, None
            for t in themes:
                h = theme_heat_at(t, trade_date, theme2etf, etf_series)
                if best_h is None or (h is not None and h > best_h):
                    best_h, best_t = h, t
            theme, heat = (best_t or ""), (best_h if best_h is not None else np.nan)

        # 扣非增速（主题优选门槛: ≥50%）— 中报猎手CSV列名 dt_netprofit_yoy
        dty_raw = r.get("dt_netprofit_yoy")
        if dty_raw is None:
            dty_raw = r.get("扣非增速")
        dty = None
        try:
            if dty_raw is not None and str(dty_raw) not in ("", "nan"):
                dty = float(dty_raw)
        except Exception:
            dty = None

        rows.append({
            "代码": code,
            "名称": name,
            "形态阶段": shape.get("stage", "无形态"),
            "次日操作": shape.get("decision", "--"),
            "回踩买点分": shape.get("pullback_score", np.nan),
            "首阳日期": shape.get("first_yang_date", ""),
            "首阳涨幅%": shape.get("first_yang_pct", ""),
            "首阳量比": shape.get("first_yang_vr", ""),
            "回踩天数": shape.get("pullback_days", 0),
            "回踩缩量比": shape.get("pullback_shrink", ""),
            "近10日最大回撤%": shape.get("max_dd10", ""),
            "买点确认": bp,
            "回踩确认": "✅ 是" if confirm else "否",
            "现价": round(price, 2),
            "VWAP": round(vwap, 2) if vwap else np.nan,
            "MA20": round(ma20, 2) if ma20 else np.nan,
            "筹码峰顶": round(peak_high, 2) if peak_high else np.nan,
            "ATR动态止损价": dynamic_stop,
            "主题": theme,
            "主题热度%": round(heat * 100, 1) if isinstance(heat, float) and not np.isnan(heat) else np.nan,
            "主题状态": theme_status(theme, heat, dty),
        })

    out = pd.DataFrame(rows)
    # 合并中报业绩（中报猎手CSV列名映射为展示列）
    merge_cols = [c for c in ["翻倍潜力分", "市值(亿)", "来源"] if c in src.columns]
    yoy_map = {"tr_yoy": "营收增速", "netprofit_yoy": "净利增速", "dt_netprofit_yoy": "扣非增速"}
    yoy_cols = [c for c in yoy_map if c in src.columns]
    if merge_cols or yoy_cols:
        rename = {"ts_code": "代码", **{c: yoy_map[c] for c in yoy_cols}}
        out = out.merge(src[["ts_code"] + merge_cols + yoy_cols].rename(columns=rename),
                        on="代码", how="left")
        out["翻倍潜力分"] = pd.to_numeric(out.get("翻倍潜力分"), errors="coerce")
        out["回踩买点分"] = pd.to_numeric(out.get("回踩买点分"), errors="coerce")

    # 排序：次日可买入 → 观察等回踩 → 观察 → 等待首阳 → 无形态；组内按回踩买点分
    out["_op"] = out["次日操作"].map(OP_ORDER).fillna(9)
    out["_st"] = out["形态阶段"].map(STAGE_ORDER).fillna(9)
    # 主题优选(白名单+甜区) 优先于 白名单 优先于 其他
    out["_tp"] = out["主题状态"].apply(lambda s: 0 if s.endswith("(优选)") else (1 if "白名单" in s else 2))
    out = out.sort_values(["_op", "_tp", "_st", "回踩买点分"], ascending=[True, True, True, False], na_position="last")
    out = out.drop(columns=["_op", "_st", "_tp"]).reset_index(drop=True)

    csv_out = os.path.join(REPORT_DIR, f"zhongbao_egpt_timing_{trade_date}.csv")
    out.to_csv(csv_out, index=False, encoding="utf-8-sig")
    print(f"\n完整CSV已保存: {csv_out}")

    def _fmt(v, fmt="{:.1f}"):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "--"
        if isinstance(v, str) and v == "":
            return "--"
        try:
            return fmt.format(float(v))
        except Exception:
            return str(v)

    def _show(sub, title):
        if len(sub) == 0:
            print(f"\n{title}: 无")
            return
        print("\n" + "━" * 70)
        print(f"  {title} ({len(sub)} 只)")
        print("━" * 70)
        for _, r in sub.iterrows():
            pb = _fmt(r.get("回踩买点分"))
            pot = _fmt(r.get("翻倍潜力分"))
            npg = r.get("净利增速")
            npg_s = f"净利{npg:+.0f}%" if isinstance(npg, (int, float)) and not (isinstance(npg, float) and np.isnan(npg)) else ""
            dty = r.get("扣非增速")
            dty_s = f"扣非{dty:+.0f}%" if isinstance(dty, (int, float)) and not (isinstance(dty, float) and np.isnan(dty)) else ""
            py = r.get("首阳日期", "")
            pd_ = r.get("回踩天数", 0)
            sl = _fmt(r.get("ATR动态止损价"), "{:.2f}")
            th = r.get("主题状态", "")
            print(f"  {r['名称']}({r['代码']}) {npg_s} {dty_s} | 潜力{pot} 回踩{pb} | 阶段{r['形态阶段']} "
                  f"回踩{pd_}日 首阳{py} 止损{sl}")
            print(f"     买点:{r['买点确认']} 回踩确认:{r['回踩确认']} 现价{_fmt(r.get('现价'), '{:.2f}')} "
                  f"VWAP{_fmt(r.get('VWAP'), '{:.2f}')} MA20{_fmt(r.get('MA20'), '{:.2f}')} "
                  f"筹码峰{_fmt(r.get('筹码峰顶'), '{:.2f}')}")
            if th:
                print(f"     主题: {th}  ETF近20日{_fmt(r.get('主题热度%'))}%")

    # 🎯 主题优选: 白名单×甜区×扣非≥50 + 可操作信号。
    # 回测(20260819): 优选组内 未突破 T+20 -6.5% 拖累组合(买点1 T+20 +6.4%)→ 未突破降级观察
    pref_all = out[(out["次日操作"].isin(["✅ 次日可买入", "⚠️ 次日观察等回踩"]))
                   & (out["主题状态"].str.endswith("(优选)", na=False))]
    pref = pref_all[pref_all["买点确认"].astype(str) != "未突破"]
    pref_watch = pref_all[pref_all["买点确认"].astype(str) == "未突破"]
    _show(pref, "🎯 主题优选（白名单×甜区×扣非≥50×突破确认）")
    if len(pref_watch):
        _show(pref_watch, "主题优选内未突破→降级观察(回测T+20 -6.5%)")

    buy = out[out["次日操作"] == "✅ 次日可买入"]
    watch = out[out["次日操作"].isin(["⚠️ 次日观察等回踩", "⚠️ 观察"])]
    rest = out[~out["次日操作"].isin(["✅ 次日可买入", "⚠️ 次日观察等回踩", "⚠️ 观察"])]

    _show(buy, "✅ 次日可买入（回踩中×分≥60，最优买点窗口）")
    _show(watch, "⚠️ 观察 / 等回踩（形态未确认，需触发）")
    _show(rest, "❌ 不买入 / 无形态")

    # 买点1 × 高热度(≥15%) 追高警示（回测: T+5胜率15.4%/均值-2.2% = 灾难）
    hot_bp = out[(out["买点确认"].astype(str).str.startswith("买点1", na=False))
                 & (out["主题状态"].astype(str).str.contains("高热度", na=False))]
    if len(hot_bp):
        print("\n" + "─" * 70)
        print(f"  ⚠️ 买点1(放量突破)×高热度(ETF20日≥15%) {len(hot_bp)} 只 = 追高风险，回测T+5胜率仅15%，回避")
        for _, r in hot_bp.iterrows():
            print(f"     {r['名称']}({r['代码']}) 主题:{r['主题状态']}")

    print("\n" + "─" * 70)
    print("  提示(买点优化回测20260819)：主题优选内买点1(放量突破) T+5 +5.2%/胜率72% 是核心alpha；")
    print("  次日开盘承接与收盘买入等价(胜率更高)；未突破仅观察不追；高热度(≥15%)回避；止损=现价-2×ATR。")

    # ── 微信推送（--push）：复用 EGPT 推送的 PushPlus 通道，消息留档 ──
    if args.push:
        try:
            msg = build_push_msg(out, trade_date, pref, pref_watch, buy, watch, hot_bp)
            push_file = os.path.join(REPORT_DIR, f"zhongbao_egpt_推送_{trade_date}.txt")
            with open(push_file, "w", encoding="utf-8") as f:
                f.write(msg)
            print(f"\n推送消息已留档: {push_file}")
            print(msg[:400] + ("..." if len(msg) > 400 else ""))
            ok = push_to_wechat(msg, title=f"中报猎手×EGPT 回踩择时 {trade_date}")
            print(f"微信推送: {'成功' if ok else '失败'}")
        except Exception as e:
            print(f"⚠️ 推送失败: {e}")


if __name__ == "__main__":
    main()
