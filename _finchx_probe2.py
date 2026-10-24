# -*- coding: utf-8 -*-
"""FinchX 复测：修正参数后的失败项 + 与涨停研究相关的深度抽样"""
import json
import time

from finchx import FinchX

fx = FinchX(timeout=20.0)


def show(title, fn, n=300):
    t0 = time.time()
    try:
        rows = fn()
        dt = time.time() - t0
        if isinstance(rows, list):
            head = json.dumps(rows[0], ensure_ascii=False, default=str)[:n] if rows else "[]"
            print(f"[OK] {title} | {dt:.1f}s | list[{len(rows)}] | {head}")
        elif isinstance(rows, dict):
            print(f"[OK] {title} | {dt:.1f}s | {json.dumps(rows, ensure_ascii=False, default=str)[:n]}")
        else:
            print(f"[OK] {title} | {dt:.1f}s | {str(rows)[:n]}")
    except Exception as e:
        print(f"[FAIL] {title} | {time.time()-t0:.1f}s | {type(e).__name__}: {str(e)[:150]}")


# 修正参数重测
show("news.em_list(600519)", lambda: fx.news.em_list("600519"))
show("disclosure.em_list(600519)", lambda: fx.disclosure.em_list("600519"))
show("articles.ths_list(热门话题url?)", lambda: fx.articles.agp_topic_articles(limit=3))
for cat in ["popular", "most_searched", "most_traded", "limit_up", "strong"]:
    show(f"hotlist.stocks(category={cat})", lambda c=cat: fx.hotlist.stocks(category=c, limit=5))
show("hotlist.stocks(period=day)", lambda: fx.hotlist.stocks(period="day", limit=5))
show("news.bd_list(retry)", lambda: fx.news.bd_list(limit=5))

# 涨停/连板研究深抽样：一字板识别所需字段
print("--- 连板池全字段（海洋王 6连板）---")
rows = fx.market.consecutive_limit_up()
top = [r for r in rows if r.get("consecutive_limit_up_days", 0) >= 4]
print(json.dumps(top[:2], ensure_ascii=False, indent=1, default=str))

print("--- 昨日涨停池→今日溢价率抽样 ---")
y = fx.market.yesterday_limit_up_pool()
print("count:", len(y), "| sample:", json.dumps(y[0], ensure_ascii=False, default=str)[:500])

print("--- 龙虎榜（10-23）---")
show("fund_flow.dragon_tiger_list(2026-10-23)", lambda: fx.fund_flow.dragon_tiger_list("2026-10-23"), n=400)

print("--- 研报聚合返回结构 ---")
r = fx.reports.fetch("光模块", limit=3)
print("errors:", r.get("errors"), "| n_reports:", len(r.get("reports", [])))
print(json.dumps(r["reports"][0], ensure_ascii=False, default=str)[:400])

print("--- iwencai.search 需 api_key（设计如此）---")
try:
    fx.iwencai.search("涨停", api_key="x")
except Exception as e:
    print("iwencai.search →", type(e).__name__, str(e)[:120])
