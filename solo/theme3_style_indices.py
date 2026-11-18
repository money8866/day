# -*- coding: utf-8 -*-
"""
theme3_style_indices.py
=======================
基于 theme3 主题分类体系，构建「成长指数」与「防御指数」的可回测原型。

两层口径互相验证：
  口径A（目录级）：按 14 个一级目录切分，8 成长 / 4 防御 / 2 剔除
  口径B（主题级）：按 74 个子主题精细归类，60 成长 / 9 防御 / 5 中性

指数编制：
  - 股票池 = 该风格下所有子主题成分股，按 ts_code 去重
  - 日收益 = 池内当日有效股票 pct_chg 的等权算术平均（当日复盘值）
  - 净值   = 基准日 20260720 = 1000，累乘 (1 + r)
  - 基准   = 沪深300 / 中证2000 / 创业板指

输出：cache_backbone_tushare/theme3_style_index_{DATE}.{csv,json,html}
"""
import os
import io
import re
import json
import math
import sqlite3
from collections import defaultdict

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache_backbone_tushare")
DB = os.path.join(CACHE, "cache.db")
DATE = "20261116"
BASE_DATE = "20260720"

# ---------------------------------------------------------------- 风格映射
# 口径A：一级目录级
CAT_GROWTH = ["AI", "人形机器人", "低空经济", "商业航天", "半导体",
              "新能源", "新能源汽车", "物理AI"]
CAT_DEFENSIVE = ["消费", "生物医药", "资源", "金融"]
CAT_EXCLUDED = ["军工", "化工"]          # 主题驱动 / 纯周期，单列

# 口径B：子主题级精细归类
THEME_GROWTH = [
    # AI 全链
    "AI应用", "AI文化娱乐", "AI模型与AI Agent", "AI算力芯片", "光模块与CPO",
    "数据中心网络", "数据中心散热", "高速铜连接", "数据中心瓶颈硬件链", "AI终端",
    # 机器人 / 低空 / 航天
    "人形机器人整机与集成", "伺服电机与运动控制", "执行器总成", "灵巧手",
    "精密减速器", "行星滚柱丝杠",
    "低空基础设施", "低空数据与控制", "低空运营服务", "低空飞行器制造",
    "卫星制造与发射", "卫星应用", "卫星运营",
    # 半导体全链
    "先进封装", "功率半导体", "半导体材料", "半导体设备", "存储芯片",
    "晶圆代工与IDM", "芯片设计",
    # 新能源 / 新能源车
    "光伏产业链", "固态电池", "新型储能", "电力链", "电网数字化", "风电产业链",
    "充换电与能源补给", "动力系统", "新能源汽车整车制造",
    "新能源汽车智能化", "新能源汽车材料链",
    # 军工（主题成长）
    "军工", "军工电子与信息化", "核聚变", "船舶制造",
    # 物理AI 全链
    "传感器", "具身智能大模型", "数字孪生与工业仿真", "机器视觉与3D感知",
    "边缘计算与AI芯片",
    # 混合目录中的成长子主题
    "情绪消费成长链", "智能驾驶", "消费电子",
    "CXO周期修复链", "创新医药主线", "医疗AI智能化", "合成生物", "脑机接口",
    "稀土永磁", "金融科技",
]
THEME_DEFENSIVE = [
    "家电家装", "必选消费红利链", "白酒",
    "中药", "医疗器械",
    "煤炭链", "贵金属与黄金",
    "保险", "银行",
]
THEME_NEUTRAL = ["有色资源", "钢铁", "基础化工", "券商", "多元金融"]

STYLE_CN = {"growth": "成长指数", "defensive": "防御指数"}
COLOR = {"成长指数": "#d32f2f", "防御指数": "#1565c0", "成长/防御比价": "#8e24aa",
         "沪深300": "#78909c", "中证2000": "#ef6c00", "创业板指": "#00897b"}


# ---------------------------------------------------------------- 数据读取
def load_stock_panel():
    """SQLite 中的个股日线拼成面板（收盘价 / 涨跌幅 / 成交额）。"""
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT key, data FROM cache_data WHERE key LIKE 'tsc_daily_kline_%'"
    ).fetchall()
    con.close()
    pat = re.compile(r"^tsc_daily_kline_(\d{6}\.\w+)_(\d{8})_(\d{8})_(\d{8})$")
    frames = []
    for key, data in rows:
        m = pat.match(key)
        if not m or m.group(4) < "20261028":      # 只取覆盖到 10 月底以后的新批次
            continue
        try:
            df = pd.read_csv(io.StringIO(data), usecols=["ts_code", "trade_date", "close", "pct_chg", "amount"])
        except Exception:
            continue
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
    panel["trade_date"] = panel["trade_date"].astype(str)
    panel["pct_chg"] = panel["pct_chg"].clip(-11, 11) / 100.0
    return panel


def load_constituents(date=DATE):
    path = os.path.join(CACHE, f"theme3_constituents_{date}.json")
    data = json.load(open(path, encoding="utf-8"))
    by_theme = {}
    best = {}
    for t in data["themes"]:
        codes = [s["ts_code"] for s in t["stocks"]]
        by_theme[t["theme_name"]] = {"cat": t["top_category"], "codes": codes}
        for s in t["stocks"]:
            c = s["ts_code"]
            if c not in best or s.get("combined_score", 0) > best[c][1]:
                best[c] = (t["theme_name"], s.get("combined_score", 0))
    return data, by_theme, {c: v[0] for c, v in best.items()}


def load_benchmark(index_code="000300.SH"):
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT key, data FROM cache_data WHERE key LIKE ?",
        (f"tsc_index_kline_ts_code_{index_code}_%",),
    ).fetchall()
    con.close()
    frames = []
    for _, data in rows:
        try:
            df = pd.read_csv(io.StringIO(data), usecols=["trade_date", "pct_chg"])
        except Exception:
            continue
        frames.append(df)
    if not frames:
        return pd.Series(dtype=float)
    out = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date")
    out["trade_date"] = out["trade_date"].astype(str)
    out["pct_chg"] = out["pct_chg"] / 100.0
    return out.set_index("trade_date")["pct_chg"]


# ---------------------------------------------------------------- 指数编制
def build_style_returns(panel, dates, code_set):
    """等权风格日收益：池内当日有数据股票的涨跌幅均值。"""
    sub = panel[panel["ts_code"].isin(code_set)]
    g = sub.groupby("trade_date")["pct_chg"].agg(["mean", "count"])
    s = g["mean"].reindex(dates)
    return s.fillna(0.0), g["count"].reindex(dates).fillna(0).astype(int)


def nav_from_ret(ret, base=1000.0):
    return base * (1.0 + ret).cumprod()


def max_drawdown(nav):
    peak = nav.cummax()
    return float((nav / peak - 1.0).min())


def perf_stats(ret, nav, n_year=252):
    r = ret.dropna()
    total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    ann = float((1 + total) ** (n_year / max(len(r), 1)) - 1)
    vol = float(r.std(ddof=1) * math.sqrt(n_year))
    mdd = max_drawdown(nav)
    sharpe = ann / vol if vol > 0 else 0.0
    calmar = ann / abs(mdd) if mdd < 0 else 0.0
    win = float((r > 0).mean())
    return {"累计收益": total, "年化收益": ann, "年化波动": vol,
            "最大回撤": mdd, "夏普比率": sharpe, "卡玛比率": calmar,
            "日胜率": win}


def rotation_strategy(g_ret, d_ret, win=20):
    """20 日动量轮动：昨日信号决定今日持仓，避免前视。"""
    mom_g = g_ret.rolling(win).mean()
    mom_d = d_ret.rolling(win).mean()
    sig = (mom_g > mom_d).astype(float).shift(1).fillna(0.0)
    strat = sig * g_ret + (1 - sig) * d_ret
    return nav_from_ret(strat), int((sig.diff().fillna(0) != 0).sum())


# ---------------------------------------------------------------- HTML / SVG
def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def line_chart(series_dict, dates, title, height=380, width=980, log_scale=False):
    """纯 SVG 折线图，无外部依赖。"""
    pad_l, pad_r, pad_t, pad_b = 62, 20, 34, 74
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b
    all_v = [float(v) for s in series_dict.values() for v in s if pd.notna(v)]
    lo, hi = min(all_v), max(all_v)
    if hi == lo:
        hi = lo + 1
    span = hi - lo
    lo -= span * 0.06
    hi += span * 0.06

    def xy(i, v):
        x = pad_l + iw * i / max(len(dates) - 1, 1)
        y = pad_t + ih * (1 - (v - lo) / (hi - lo))
        return x, y

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px;font-family:inherit">',
             f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>',
             f'<text x="{pad_l}" y="20" font-size="14" font-weight="700" fill="#263238">{esc(title)}</text>']
    # 网格 + y 轴
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = pad_t + ih * (1 - k / 4)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+iw}" y2="{y:.1f}" stroke="#eceff1" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" font-size="11" fill="#78909c" text-anchor="end">{v:,.0f}</text>')
    # x 轴日期
    step = max(len(dates) // 9, 1)
    for i in range(0, len(dates), step):
        x, _ = xy(i, lo)
        parts.append(f'<text x="{x:.1f}" y="{pad_t+ih+20}" font-size="10" fill="#78909c" text-anchor="middle">{dates[i][4:6]}/{dates[i][6:]}</text>')
    # 曲线
    for name, s in series_dict.items():
        pts = " ".join(f"{xy(i, float(v))[0]:.1f},{xy(i, float(v))[1]:.1f}"
                       for i, v in enumerate(s) if pd.notna(v))
        c = COLOR.get(name, "#455a64")
        dash = ' stroke-dasharray="5,4"' if "比价" in name else ""
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="2"{dash} stroke-linejoin="round"/>')
    # 图例
    lx = pad_l
    for name in series_dict:
        c = COLOR.get(name, "#455a64")
        parts.append(f'<rect x="{lx}" y="{height-32}" width="13" height="3" fill="{c}"/>')
        parts.append(f'<text x="{lx+18}" y="{height-27}" font-size="11" fill="#455a64">{esc(name)}</text>')
        lx += max(80, len(name) * 13 + 40)
    parts.append("</svg>")
    return "".join(parts)


def fmt_pct(v):
    return f"{v*100:+.2f}%"


def metrics_table(stats_dict):
    keys = list(next(iter(stats_dict.values())).keys())
    rows = []
    for k in keys:
        cells = []
        for name, st in stats_dict.items():
            v = st[k]
            if "收益" in k or "回撤" in k or "胜率" in k or "波动" in k:
                cells.append(f'<td style="padding:7px;border-bottom:1px solid #eceff1;text-align:right">{fmt_pct(v)}</td>')
            else:
                cells.append(f'<td style="padding:7px;border-bottom:1px solid #eceff1;text-align:right">{v:.2f}</td>')
        rows.append(f'<tr><td style="padding:7px;border-bottom:1px solid #eceff1;font-weight:600">{esc(k)}</td>{"".join(cells)}</tr>')
    names = "".join(f'<th style="padding:7px;text-align:right;color:#455a64">{esc(n)}</th>' for n in stats_dict)
    return (f'<table style="border-collapse:collapse;width:100%;font-size:13px">'
            f'<tr style="background:#f5f7f9"><th style="padding:7px;text-align:left">指标</th>{names}</tr>'
            + "".join(rows) + "</table>")


def gen_html(res, dates, mapping_desc):
    g, d = res["growth"], res["defensive"]
    navs = {"成长指数": g["nav"], "防御指数": d["nav"],
            "沪深300": res["hs300"], "中证2000": res["zz2000"], "创业板指": res["cyb"]}
    ratio = g["nav"] / d["nav"]
    chart1 = line_chart(navs, dates, f"风格指数净值（基准日 {BASE_DATE} = 1000）")
    chart2 = line_chart({"成长/防御比价": ratio}, dates, "成长/防御比价（上行=成长占优）")
    rot_nav, trades = res["rotation"], res["trades"]

    stats = {"成长指数": g["stats"], "防御指数": d["stats"],
             "轮动策略": perf_stats(res["rot_ret"], rot_nav)}
    tbl = metrics_table(stats)
    spread = g["stats"]["累计收益"] - d["stats"]["累计收益"]
    winner = "成长" if spread > 0 else "防御"

    def card(label, val, color="#263238"):
        return (f'<div style="flex:1;min-width:130px;background:#f8fafb;border:1px solid #e8edf0;'
                f'border-radius:8px;padding:12px 14px"><div style="font-size:12px;color:#78909c">{label}</div>'
                f'<div style="font-size:22px;font-weight:800;color:{color};margin-top:4px">{val}</div></div>')

    cards = "".join([
        card("成长指数 累计", fmt_pct(g["stats"]["累计收益"]), COLOR["成长指数"]),
        card("防御指数 累计", fmt_pct(d["stats"]["累计收益"]), COLOR["防御指数"]),
        card("风格价差 (成长-防御)", fmt_pct(spread), "#8e24aa"),
        card("两指数日收益相关性", f'{res["corr"]:.3f}', "#455a64"),
    ])

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>theme3 成长/防御指数原型 · {DATE}</title>
<style>body{{font-family:-apple-system,"Microsoft YaHei",Arial,sans-serif;color:#263238;
background:#f4f6f8;margin:0;padding:24px}} .wrap{{max-width:1060px;margin:0 auto;background:#fff;
border-radius:12px;padding:28px 32px;box-shadow:0 2px 12px rgba(0,0,0,.05)}} h1{{font-size:24px;margin:0 0 6px}}
h2{{font-size:17px;margin:28px 0 12px;padding-left:10px;border-left:4px solid #d32f2f}}
.sub{{color:#78909c;font-size:12px;margin-bottom:20px}} .cards{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}
.note{{background:#fff8e1;border-left:3px solid #ffb300;padding:10px 14px;font-size:12px;color:#6d4c41;line-height:1.7;margin:14px 0}}
table.map{{font-size:12px}} .tagG{{color:#d32f2f;font-weight:700}} .tagD{{color:#1565c0;font-weight:700}}
</style></head><body><div class="wrap">
<h1>theme3 成长指数 / 防御指数 · 可回测原型</h1>
<div class="sub">交易日 {DATE} · 回测区间 {dates[0]} ~ {dates[-1]}（{len(dates)} 个交易日）·
成分股快照 {DATE} · 数据源 SQLite 缓存</div>

<h2>一、核心结果</h2>
<div class="cards">{cards}</div>
<p style="font-size:13px;line-height:1.8">区间内 <b>{winner}</b>风格占优，累计收益差 <b>{fmt_pct(spread)}</b>。
两指数日收益相关性 <b>{res['corr']:.3f}</b>
（{'较低，风格切换对组合分散有实际意义' if res['corr'] < 0.75 else '偏高，两者同涨同跌为主'}）。
成长指数成分 {g['n_stocks']} 只 / {g['n_themes']} 个主题，防御指数成分 {d['n_stocks']} 只 / {d['n_themes']} 个主题。</p>

<h2>二、净值曲线</h2>
{chart1}
{chart2}

<h2>三、绩效指标</h2>
{tbl}
<p style="font-size:12px;color:#78909c">注：年化指标由 {len(dates)} 个交易日外推，样本偏短仅作方向性参考；
无风险利率按 0 计。轮动策略 = 20 日动量判定，昨日信号今日持仓，共触发 {trades} 次调仓。</p>

<h2>四、风格轮动策略净值</h2>
{line_chart({"轮动策略": rot_nav, "成长指数": g["nav"], "防御指数": d["nav"]}, dates, "20日动量轮动 vs 单一风格")}

<h2>五、分类映射</h2>
<div style="font-size:13px;line-height:1.9">{mapping_desc}</div>

<div class="note"><b>局限与说明：</b>
① 成分股使用 {DATE} 快照回溯全区间，存在成分变更的前视偏差，正式指数应按期重采样成分；
② 回测区间仅 {len(dates)} 个交易日（约 4 个月），未跨越完整牛熊，夏普/回撤结论不稳健；
③ 等权日再平衡未计交易成本与涨跌停冲击；
④ 该指数是「主题风格跟踪工具」，不是可直接交易的产品，需用 ETF/期货等可交易标的映射后才能落地。</div>
</div></body></html>"""
    return html


# ---------------------------------------------------------------- main
def main():
    panel = load_stock_panel()
    dates = sorted(panel["trade_date"].unique())
    dates = [d for d in dates if d >= BASE_DATE]
    print(f"[数据] K线面板 {panel.ts_code.nunique()} 只 / {len(dates)} 个交易日 {dates[0]}~{dates[-1]}")

    raw, by_theme, dedup_theme = load_constituents()
    theme_names = set(by_theme)
    print(f"[数据] 成分股 {len(dedup_theme)} 只 / {len(theme_names)} 个主题")

    # 口径校验：两套映射是否覆盖全部主题
    mapped = set(THEME_GROWTH) | set(THEME_DEFENSIVE) | set(THEME_NEUTRAL)
    print(f"[校验] 口径B 映射 {len(set(THEME_GROWTH))} 成长 / {len(set(THEME_DEFENSIVE))} 防御 / "
          f"{len(set(THEME_NEUTRAL))} 中性，未覆盖: {sorted(theme_names - mapped)}")

    def make_set(themes=None, cats=None):
        s = set()
        for name, info in by_theme.items():
            if (themes and name in themes) or (cats and info["cat"] in cats):
                s.update(info["codes"])
        return s

    sets = {
        "growth_A": make_set(cats=CAT_GROWTH),
        "defensive_A": make_set(cats=CAT_DEFENSIVE),
        "growth_B": make_set(themes=set(THEME_GROWTH)),
        "defensive_B": make_set(themes=set(THEME_DEFENSIVE)),
    }
    theme_count = {
        "growth_A": sum(1 for t in theme_names if by_theme[t]["cat"] in CAT_GROWTH),
        "defensive_A": sum(1 for t in theme_names if by_theme[t]["cat"] in CAT_DEFENSIVE),
        "growth_B": len(set(THEME_GROWTH) & theme_names),
        "defensive_B": len(set(THEME_DEFENSIVE) & theme_names),
    }

    rets, navs, counts = {}, {}, {}
    for k, codes in sets.items():
        r, cnt = build_style_returns(panel, dates, codes)
        rets[k], counts[k] = r, cnt
        navs[k] = nav_from_ret(r)
        cum = navs[k].iloc[-1] / navs[k].iloc[0] - 1
        print(f"[指数] {k:<13} 股票 {len(codes):>4} 只 主题 {theme_count[k]:>2} 个  "
              f"累计 {cum:+.2%}  日均池 {cnt.mean():.0f} 只")

    # 一致性：两种口径的成长/防御收益相关性
    corr_ab_g = rets["growth_A"].corr(rets["growth_B"])
    corr_ab_d = rets["defensive_A"].corr(rets["defensive_B"])
    print(f"[校验] 两口径相关性 成长 {corr_ab_g:.3f} / 防御 {corr_ab_d:.3f}")

    # 主结果用口径B（精细）
    g, d = rets["growth_B"], rets["defensive_B"]
    hs = load_benchmark("000300.SH").reindex(dates).fillna(0.0)
    zz = load_benchmark("932000.CSI").reindex(dates).fillna(0.0)
    cy = load_benchmark("399006.SZ").reindex(dates).fillna(0.0)
    rot_nav, trades = rotation_strategy(g, d)
    rot_ret = rot_nav.pct_change().fillna(0.0)

    res = {
        "growth": {"ret": g, "nav": navs["growth_B"],
                   "stats": perf_stats(g, navs["growth_B"]),
                   "n_stocks": len(sets["growth_B"]), "n_themes": theme_count["growth_B"]},
        "defensive": {"ret": d, "nav": navs["defensive_B"],
                      "stats": perf_stats(d, navs["defensive_B"]),
                      "n_stocks": len(sets["defensive_B"]), "n_themes": theme_count["defensive_B"]},
        "hs300": nav_from_ret(hs), "zz2000": nav_from_ret(zz), "cyb": nav_from_ret(cy),
        "corr": float(g.corr(d)), "corr_ab_growth": float(corr_ab_g),
        "corr_ab_defensive": float(corr_ab_d),
        "rotation": rot_nav, "rot_ret": rot_ret, "trades": trades,
    }

    # CSV 明细
    out = pd.DataFrame({
        "trade_date": dates,
        "成长指数_口径A": navs["growth_A"].values, "防御指数_口径A": navs["defensive_A"].values,
        "成长指数_口径B": navs["growth_B"].values, "防御指数_口径B": navs["defensive_B"].values,
        "成长日收益": g.values, "防御日收益": d.values,
        "沪深300": res["hs300"].values, "中证2000": res["zz2000"].values,
        "创业板指": res["cyb"].values, "轮动策略": rot_nav.values,
    })
    csv_path = os.path.join(CACHE, f"theme3_style_index_{DATE}.csv")
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # JSON 指标
    summary = {
        "trade_date": DATE, "backtest": [dates[0], dates[-1]], "n_days": len(dates),
        "growth": res["growth"]["stats"], "defensive": res["defensive"]["stats"],
        "corr_growth_defensive": res["corr"],
        "corr_two_methods": {"growth": res["corr_ab_growth"], "defensive": res["corr_ab_defensive"]},
        "rotation": {**perf_stats(rot_ret, rot_nav), "trades": trades},
        "counts": {"growth_stocks": res["growth"]["n_stocks"],
                   "defensive_stocks": res["defensive"]["n_stocks"],
                   "growth_themes": res["growth"]["n_themes"],
                   "defensive_themes": res["defensive"]["n_themes"]},
        "mapping": {"growth_themes": sorted(set(THEME_GROWTH) & theme_names),
                    "defensive_themes": sorted(set(THEME_DEFENSIVE) & theme_names),
                    "neutral_themes": sorted(set(THEME_NEUTRAL) & theme_names),
                    "excluded_categories": CAT_EXCLUDED},
    }
    json_path = os.path.join(CACHE, f"theme3_style_index_{DATE}.json")
    json.dump(summary, open(json_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # HTML
    mapping_desc = (
        f'<p><span class="tagG">成长（口径B，{len(set(THEME_GROWTH)&theme_names)} 个主题）</span>：'
        + " / ".join(sorted(set(THEME_GROWTH) & theme_names)) + "</p>"
        f'<p><span class="tagD">防御（口径B，{len(set(THEME_DEFENSIVE)&theme_names)} 个主题）</span>：'
        + " / ".join(sorted(set(THEME_DEFENSIVE) & theme_names)) + "</p>"
        f'<p>中性：{" / ".join(sorted(set(THEME_NEUTRAL) & theme_names))}；'
        f'剔除目录：{" / ".join(CAT_EXCLUDED)}（主题驱动/纯周期，风格不纯）</p>'
        f'<p style="color:#78909c">口径A（目录级）成长 = {" / ".join(CAT_GROWTH)}；'
        f'防御 = {" / ".join(CAT_DEFENSIVE)}。两口径日收益相关性：'
        f'成长 {corr_ab_g:.3f}、防御 {corr_ab_d:.3f}，'
        f'{"互为印证" if min(corr_ab_g,corr_ab_d)>0.8 else "差异较大，需检查边界主题"}。</p>'
    )
    html_path = os.path.join(CACHE, f"theme3_style_index_{DATE}.html")
    open(html_path, "w", encoding="utf-8").write(gen_html(res, dates, mapping_desc))

    print(f"\n[输出] {csv_path}")
    print(f"[输出] {json_path}")
    print(f"[输出] {html_path}")
    print("\n=== 主结果（口径B）===")
    for k in ["growth", "defensive"]:
        st = res[k]["stats"]
        print(f"{STYLE_CN['growth' if k=='growth' else 'defensive']}: "
              f"累计 {fmt_pct(st['累计收益'])} 年化 {fmt_pct(st['年化收益'])} "
              f"波动 {fmt_pct(st['年化波动'])} 回撤 {fmt_pct(st['最大回撤'])} 夏普 {st['夏普比率']:.2f}")
    rs = summary["rotation"]
    print(f"轮动策略: 累计 {fmt_pct(rs['累计收益'])} 回撤 {fmt_pct(rs['最大回撤'])} "
          f"夏普 {rs['夏普比率']:.2f} 调仓 {rs['trades']} 次")


if __name__ == "__main__":
    main()
