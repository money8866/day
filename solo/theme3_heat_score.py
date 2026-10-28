#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
theme3_heat_score.py
====================
基于 theme3_constituents_{date}.json（theme3_constituents_v2.py 产出）计算
各主题（一级目录 + 二级子主题）的热度评分 + 情绪阶段 + 操作建议。

热度模型（0-100）：
  涨停动能 30 分：今日涨停家数 / 最高连板高度 / 近10日涨停总次数
  资金热度 25 分：合计 5 日均成交额 / 单股成交中位数
  趋势强度 25 分：5日/10日涨跌幅中位数 + 趋势分均值
  上涨宽度 20 分：5日上涨占比 / 10日上涨占比

情绪阶段（按热度 + 连板结构 + 宽度综合判定）：
  高潮 / 主升 / 发酵 / 蓄势 / 退潮

一级目录口径：其下所有子主题成分股按 ts_code 去重（保留综合分最高的一条）
后再计算，避免跨主题重复计数；涨停/连板分母放宽 2 倍（股票池更大）。

输出：
  cache_backbone_tushare/theme3_heat_{date}.json
  cache_backbone_tushare/theme3_heat_{date}.csv
  cache_backbone_tushare/theme3_heat_{date}.html   （自包含可视化报告）

使用： python theme3_heat_score.py [date如20261027]   # 缺省取最新产出
"""
import os
import sys
import json
import csv
import glob

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")


def latest_constituents_path():
    files = sorted(glob.glob(os.path.join(CACHE_DIR, "theme3_constituents_20*.json")))
    return files[-1] if files else None


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else 0.0


def compute_heat(name, top_category, stocks, level="二级"):
    """对一组（去重后的）成分股计算热度/阶段/建议。"""
    n = len(stocks)
    if n == 0:
        return None

    chg5 = [s["change_5d_pct"] for s in stocks]
    chg10 = [s["change_10d_pct"] for s in stocks]
    amts = [s["avg_amount_5d"] for s in stocks if s["avg_amount_5d"] > 0]
    trends = [s["trend_score"] for s in stocks]

    # --- 涨停动能 (30) ---
    lb_today = [s for s in stocks if s["limit_up_days"] >= 1]      # 连板>=1 即今日涨停
    max_lb = max((s["limit_up_days"] for s in stocks), default=0)
    recent_lb_total = sum(s["recent_up_days"] for s in stocks)
    # 一级目录股票池更大，涨停/连板分母相应放宽
    k = 2.0 if level == "一级" else 1.0
    score_lb = (10 * clamp(len(lb_today) / (6 * k)) + 10 * clamp(max_lb / 4)
                + 10 * clamp(recent_lb_total / (25 * k)))

    # --- 资金热度 (25) ---
    total_amt = sum(amts)
    med_amt = median(amts)
    score_amt = 15 * clamp(total_amt / 3e10) + 10 * clamp(med_amt / 5e8)

    # --- 趋势强度 (25) ---
    med5, med10 = median(chg5), median(chg10)
    mean_trend = sum(trends) / n
    score_trend = (10 * clamp(med5 / 8) + 10 * clamp(med10 / 15)
                   + 5 * mean_trend / 100 * 1.0)

    # --- 上涨宽度 (20) ---
    w5 = sum(1 for v in chg5 if v > 0) / n
    w10 = sum(1 for v in chg10 if v > 0) / n
    score_width = 10 * w5 + 10 * w10

    heat = round(score_lb + score_amt + score_trend + score_width, 1)

    # --- 情绪阶段 + 操作建议 ---
    leaders = [s for s in stocks if s["role"] == "龙头"]
    advice_parts = []
    risks = []
    if heat >= 70:
        stage = "高潮"
        advice_parts.append("情绪高潮区，只做龙头不做跟风，逢分歧低吸、一致加速时兑现")
    elif heat >= 60:
        stage = "主升"
        advice_parts.append("主升期可参与：龙头持有、中军沿MA5低吸，补涨股只做首板或低吸")
    elif heat >= 45:
        stage = "发酵"
        advice_parts.append("发酵初期：轻仓试错龙头首板/低吸中军，等涨停潮确认再加仓")
    elif heat >= 30:
        stage = "蓄势"
        advice_parts.append("蓄势期：进观察池，等放量涨停启动信号再介入")
    else:
        stage = "退潮"
        advice_parts.append("热度退潮：回避追高，仅保留左侧逻辑硬的标的跟踪")

    if not lb_today and stage in ("主升", "发酵", "高潮"):
        risks.append("今日无涨停，缺催化，注意冲高回落")
    if total_amt > 2e10 and med5 < 0:
        risks.append(f"高量滞涨（5日均额{total_amt/1e8:.0f}亿但中位5日{med5:+.1f}%），分歧大防出货")
    if max_lb >= 4:
        advice_parts.append(f"空间板{max_lb}连板已打开高度，关注龙头卡位")
    if w5 >= 0.7:
        advice_parts.append(f"宽度好（5日上涨占比{w5*100:.0f}%），赚钱效应佳")
    elif w5 < 0.4 and heat >= 45:
        risks.append(f"宽度差（5日上涨占比{w5*100:.0f}%），涨的集中在少数股，谨慎接力")

    advice = "；".join(advice_parts) + ("。" if advice_parts else "")
    if risks:
        advice += " ⚠ " + "；".join(risks) + "。"

    role_rank = {"龙头": 0, "中军": 1, "补涨": 2}
    top_stocks = sorted(stocks, key=lambda s: (role_rank.get(s["role"], 3), -s["combined_score"]))[:5]

    return {
        "top_category": top_category,
        "theme_name": name,
        "level": level,
        "n_stocks": n,
        "heat": heat,
        "stage": stage,
        "breakdown": {
            "涨停动能": round(score_lb, 1),
            "资金热度": round(score_amt, 1),
            "趋势强度": round(score_trend, 1),
            "上涨宽度": round(score_width, 1),
        },
        "stats": {
            "今日涨停家数": len(lb_today),
            "最高连板": max_lb,
            "近10日涨停总次数": recent_lb_total,
            "5日均成交额_亿": round(total_amt / 1e8, 1),
            "中位5日涨跌幅%": round(med5, 2),
            "中位10日涨跌幅%": round(med10, 2),
            "5日上涨宽度%": round(w5 * 100),
            "10日上涨宽度%": round(w10 * 100),
            "龙头家数": len(leaders),
            "趋势分均值": round(mean_trend, 1),
        },
        "advice": advice,
        "top_stocks": [
            {"name": s["name"], "ts_code": s["ts_code"], "role": s["role"],
             "连板": s["limit_up_days"], "5日%": s["change_5d_pct"],
             "综合分": s["combined_score"],
             "desc": s.get("role_desc", "")}
            for s in top_stocks
        ],
    }


def compute_theme_heat(theme):
    return compute_heat(theme["theme_name"], theme["top_category"],
                        theme["stocks"], level="二级")


def dedup_stocks(stocks_iter):
    """按 ts_code 去重，保留综合分最高的一条。"""
    best = {}
    for s in stocks_iter:
        code = s["ts_code"]
        if code not in best or s["combined_score"] > best[code]["combined_score"]:
            best[code] = s
    return list(best.values())


def compute_category_heats(data):
    """一级目录：聚合其下所有子主题成分股（去重）后套同一热度模型。"""
    by_cat = {}
    for theme in data["themes"]:
        by_cat.setdefault(theme["top_category"], []).extend(theme["stocks"])

    rows = []
    for cat, stocks in by_cat.items():
        r = compute_heat(cat, cat, dedup_stocks(stocks), level="一级")
        if r:
            rows.append(r)
    rows.sort(key=lambda r: -r["heat"])
    return rows


STAGE_COLOR = {"高潮": "#d32f2f", "主升": "#e64a19", "发酵": "#f57c00",
               "蓄势": "#7b8794", "退潮": "#455a64"}


def gen_bars(rows):
    max_heat = max((r["heat"] for r in rows), default=100) or 100
    bars = []
    for r in rows:
        c = STAGE_COLOR[r["stage"]]
        w = r["heat"] / max_heat * 100
        lb_txt = f' · {r["stats"]["最高连板"]}板' if r["stats"]["最高连板"] else ""
        bars.append(f'''
<div class="row">
  <div class="row-head"><b>{r["theme_name"]}</b>
    <span class="tag" style="background:{c}">{r["stage"]}</span>
    <span class="sub">{r["n_stocks"]}只 · {r["stats"]["今日涨停家数"]}涨停{lb_txt} · {r["stats"]["5日均成交额_亿"]:.0f}亿</span></div>
  <div class="bar-bg"><div class="bar" style="width:{w:.0f}%;background:{c}"></div>
    <span class="heat-num">{r["heat"]:.0f}</span></div>
  <div class="advice">{r["advice"]}</div>
  <div class="stocks">关注: {(" / ".join(f'{s["name"]}({s["role"]}{"·"+str(s["连板"])+"板" if s["连板"] else ""} {s["5日%"]:+.1f}%)' for s in r["top_stocks"][:4]))}</div>
</div>''')
    return "\n".join(bars)


def gen_html(cat_rows, theme_rows, trade_date, market):
    rows_sorted = sorted(theme_rows, key=lambda r: -r["heat"])

    # 情绪周期分布（二级）
    dist = {}
    for r in theme_rows:
        dist[r["stage"]] = dist.get(r["stage"], 0) + 1

    stage_cards = "".join(
        f'<div class="card" style="border-top:4px solid {STAGE_COLOR[k]}">'
        f'<div class="card-num">{dist.get(k, 0)}</div><div class="card-label">{k}</div></div>'
        for k in ["高潮", "主升", "发酵", "蓄势", "退潮"]
    )

    mkt_cards = (
        f'<div class="card"><div class="card-num" style="color:#d32f2f">{market["今日涨停记录"]}</div>'
        f'<div class="card-label">主题内今日涨停(含重复)</div></div>'
        f'<div class="card"><div class="card-num" style="color:#d32f2f">{market["最高连板"]}</div>'
        f'<div class="card-label">全市场最高连板</div></div>'
        f'<div class="card"><div class="card-num">{market["总成交额_亿"]:.0f}亿</div>'
        f'<div class="card-label">成分股(去重{market["成分股数_去重"]})5日均额合计</div></div>'
        f'<div class="card"><div class="card-num">{market["主题数"]}</div>'
        f'<div class="card-label">覆盖子主题数</div></div>'
    )

    # 一级目录：热力卡片网格
    cat_cards = []
    for r in cat_rows:
        c = STAGE_COLOR[r["stage"]]
        cat_cards.append(f'''
<div class="cat-card" style="border-left:5px solid {c}">
  <div class="cat-name">{r["theme_name"]}<span class="tag" style="background:{c}">{r["stage"]}</span></div>
  <div class="cat-heat" style="color:{c}">{r["heat"]:.0f}</div>
  <div class="cat-sub">{r["n_stocks"]}只 · {r["stats"]["今日涨停家数"]}涨停 · {r["stats"]["最高连板"]}板高 · {r["stats"]["5日均成交额_亿"]:.0f}亿</div>
</div>''')
    cat_html = f'<div class="cat-grid">{"".join(cat_cards)}</div>'
    cat_bars_html = gen_bars(cat_rows)
    theme_bars_html = gen_bars(rows_sorted)

    return f'''<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>主题热度与操作建议 {trade_date}</title>
<style>
  @page {{ margin: 0; }}
  body {{ font-family: "Microsoft YaHei", sans-serif; font-size: 22px; margin: 0; background: #f6f7f9; color: #26313c; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px 60px; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin-top: 34px; border-left: 4px solid #d32f2f; padding-left: 8px; }}
  .sub-title {{ color: #7b8794; font-size: 13px; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 14px 0; }}
  .card {{ background: #fff; border-radius: 10px; padding: 12px 20px; min-width: 130px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
  .card-num {{ font-size: 26px; font-weight: 700; }} .card-label {{ font-size: 12px; color: #7b8794; margin-top: 2px; }}
  .cat-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; margin: 14px 0; }}
  .cat-card {{ background: #fff; border-radius: 10px; padding: 10px 14px; box-shadow: 0 1px 4px rgba(0,0,0,.05); }}
  .cat-name {{ font-size: 14px; font-weight: 700; }} .cat-heat {{ font-size: 24px; font-weight: 800; margin: 2px 0; }}
  .cat-sub {{ font-size: 11px; color: #7b8794; }}
  .row {{ background: #fff; border-radius: 10px; padding: 12px 16px; margin-bottom: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.05); }}
  .row-head b {{ font-size: 22px; }} .sub {{ color: #7b8794; font-size: 12px; margin-left: 8px; }}
  .tag {{ color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 8px; margin-left: 6px; vertical-align: middle; }}
  .bar-bg {{ position: relative; background: #eef1f4; border-radius: 6px; height: 16px; margin: 8px 0 6px; }}
  .bar {{ height: 16px; border-radius: 6px; opacity: .85; }}
  .heat-num {{ position: absolute; right: 8px; top: 0; font-size: 12px; font-weight: 700; line-height: 16px; color: #45505c; }}
  .advice {{ font-size: 22px; line-height: 1.55; }}
  .stocks {{ font-size: 22px; color: #52606d; margin-top: 4px; }}
</style></head><body><div class="wrap">
<h1>主题热度与操作建议 <span class="sub-title">交易日 {trade_date} · 数据源 theme3_constituents（Tushare K线+东财板块）</span></h1>
<h2>市场情绪总览</h2>
<div class="cards">{mkt_cards}</div>
<h2>情绪周期分布（{len(theme_rows)} 子主题）</h2>
<div class="cards">{stage_cards}</div>
<h2>一级目录热度（{len(cat_rows)} 个 · 股票去重口径）</h2>
{cat_html}
<h2>一级目录热度排行</h2>
{cat_bars_html}
<h2>子主题热度排行</h2>
{theme_bars_html}
<p class="sub-title" style="margin-top:16px">热度模型：涨停动能30 + 资金热度25 + 趋势强度25 + 上涨宽度20；一级目录按成分股去重聚合（涨停/连板分母放宽2倍）；完整数据见同日 CSV；操作建议由阶段 + 连板结构 + 宽度规则生成，仅供研究参考，不构成投资建议。</p>
</div></body></html>'''


def main():
    if len(sys.argv) > 1:
        date = sys.argv[1]
        src = os.path.join(CACHE_DIR, f"theme3_constituents_{date}.json")
    else:
        src = latest_constituents_path()
        if not src:
            print("未找到 theme3_constituents_*.json，请先运行 theme3_constituents_v2.py")
            sys.exit(1)
        date = os.path.basename(src).replace("theme3_constituents_", "").replace(".json", "")

    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)

    theme_rows = []
    for theme in data["themes"]:
        r = compute_theme_heat(theme)
        if r:
            theme_rows.append(r)
    theme_rows.sort(key=lambda r: -r["heat"])

    cat_rows = compute_category_heats(data)

    # 全市场口径：成分股先按代码去重再合计。同一股票可归属多个主题
    # （跨主题去重豁免 DNA 精确命中的股票），直接按主题相加会重复计算。
    seen_amt = {}
    for theme in data["themes"]:
        for s in theme.get("stocks", []):
            c = s.get("ts_code")
            if not c:
                continue
            a = s.get("avg_amount_5d", 0) or 0
            if a > seen_amt.get(c, 0):
                seen_amt[c] = a
    market = {
        "主题数": len(theme_rows),
        "今日涨停记录": sum(r["stats"]["今日涨停家数"] for r in theme_rows),
        "最高连板": max((r["stats"]["最高连板"] for r in theme_rows), default=0),
        "成分股数_去重": len(seen_amt),
        "总成交额_亿": round(sum(seen_amt.values()) / 1e8, 0),
    }

    out_json = os.path.join(CACHE_DIR, f"theme3_heat_{date}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"trade_date": date, "market": market,
                   "categories": cat_rows, "themes": theme_rows},
                  f, ensure_ascii=False, indent=2)
    print(f"✅ JSON: {out_json}")

    out_csv = os.path.join(CACHE_DIR, f"theme3_heat_{date}.csv")
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["层级", "主题/目录", "父目录", "热度", "阶段",
                    "涨停动能", "资金热度", "趋势强度", "上涨宽度",
                    "今日涨停", "最高连板", "近10日涨停次数", "5日均额(亿)",
                    "中位5日%", "中位10日%", "5日宽度%", "10日宽度%", "龙头家数", "操作建议"])
        for r in cat_rows + theme_rows:
            s = r["stats"]
            b = r["breakdown"]
            w.writerow([r["level"], r["theme_name"],
                        "" if r["level"] == "一级" else r["top_category"],
                        r["heat"], r["stage"],
                        b["涨停动能"], b["资金热度"], b["趋势强度"], b["上涨宽度"],
                        s["今日涨停家数"], s["最高连板"], s["近10日涨停总次数"],
                        s["5日均成交额_亿"], s["中位5日涨跌幅%"], s["中位10日涨跌幅%"],
                        s["5日上涨宽度%"], s["10日上涨宽度%"], s["龙头家数"], r["advice"]])
    print(f"✅ CSV : {out_csv}")

    html = gen_html(cat_rows, theme_rows, date, market)
    out_html = os.path.join(CACHE_DIR, f"theme3_heat_{date}.html")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML: {out_html}")

    # 控制台摘要
    print(f"\n=== {date} 市场概览 ===")
    print(f"今日涨停记录 {market['今日涨停记录']}, 最高连板 {market['最高连板']}板")
    print(f"\n=== 一级目录热度 ===")
    for r in cat_rows:
        s = r["stats"]
        print(f'{r["heat"]:>5.1f} [{r["stage"]}] {r["theme_name"]:<8} '
              f'{r["n_stocks"]:>4}只 涨停{s["今日涨停家数"]:>2} 最高{s["最高连板"]}板 '
              f'额{s["5日均成交额_亿"]:>5.0f}亿 宽度{s["5日上涨宽度%"]}%')
    print(f"\n=== 子主题热度 Top 10 ===")
    for r in theme_rows[:10]:
        s = r["stats"]
        print(f'{r["heat"]:>5.1f} [{r["stage"]}] {r["theme_name"]:<12} '
              f'涨停{s["今日涨停家数"]} 最高{s["最高连板"]}板 '
              f'额{s["5日均成交额_亿"]:.0f}亿 宽度{s["5日上涨宽度%"]}%')


if __name__ == "__main__":
    main()
