# -*- coding: utf-8 -*-
"""FinchX 3.1.1 接口实测：连通性 + 签名反射 + 返回结构抽样"""
import inspect
import json
import time
import traceback

from finchx import FinchX

fx = FinchX(timeout=20.0)


def show(title, fn):
    t0 = time.time()
    try:
        rows = fn()
        dt = time.time() - t0
        if isinstance(rows, list):
            head = json.dumps(rows[0], ensure_ascii=False, default=str)[:400] if rows else "[]"
            print(f"[OK] {title} | {dt:.1f}s | list[{len(rows)}] | {head}")
        elif isinstance(rows, dict):
            keys = list(rows.keys())
            snippet = json.dumps(rows, ensure_ascii=False, default=str)[:400]
            print(f"[OK] {title} | {dt:.1f}s | dict keys={keys} | {snippet}")
        else:
            print(f"[OK] {title} | {dt:.1f}s | {type(rows).__name__}: {str(rows)[:200]}")
    except Exception as e:
        print(f"[FAIL] {title} | {time.time()-t0:.1f}s | {type(e).__name__}: {str(e)[:200]}")


print("=" * 30, "市场行情", "=" * 30)
show("market.ranking(limit=5)", lambda: fx.market.ranking(limit=5))
show("market.em_breadth", lambda: fx.market.em_breadth())
show("market.limit_up_pool", lambda: fx.market.limit_up_pool())
show("market.limit_down_pool", lambda: fx.market.limit_down_pool())
show("market.broken_limit_pool", lambda: fx.market.broken_limit_pool())
show("market.yesterday_limit_up_pool", lambda: fx.market.yesterday_limit_up_pool())
show("market.strong_pool", lambda: fx.market.strong_pool())
show("market.consecutive_limit_up", lambda: fx.market.consecutive_limit_up())
show("market.quote_snapshot(600519)", lambda: fx.market.quote_snapshot("600519"))
show("market.quote_snapshot(000001,index)", lambda: fx.market.quote_snapshot("000001", kind="index"))
show("market.ohlcv(600519)", lambda: fx.market.ohlcv("600519", "2026-10-09", "2026-10-23"))
show("market.orderbook(600519)", lambda: fx.market.orderbook("600519"))
show("market.equity_intraday(600519)", lambda: fx.market.equity_intraday("600519"))
show("market.all_quotes (len only)", lambda: fx.market.all_quotes())

print("=" * 30, "资金流/板块", "=" * 30)
show("fund_flow.stock(600519, days=5)", lambda: fx.fund_flow.stock("600519", days=5))
show("sector.ths_concept_list (len only)", lambda: fx.sector.ths_concept_list())
show("sector.tx_sectors(600519)", lambda: fx.sector.tx_sectors("600519"))

print("=" * 30, "公司/财务", "=" * 30)
show("company.info(600519) keys", lambda: list(fx.company.info("600519").keys()))
show("financial.statements(600519)", lambda: fx.financial.statements("600519", "income_statement", max_periods=2))

print("=" * 30, "监管/日历", "=" * 30)
show("regulation.watchlist (len only)", lambda: fx.regulation.watchlist())
show("reference.trading_calendar", lambda: fx.reference.trading_calendar("2026-10-19", "2026-10-23"))

print("=" * 30, "热榜/新闻/研报/问财（签名反射 + 调用试探）", "=" * 30)
for mod_name in ["hotlist", "news", "articles", "disclosure", "reports", "forum", "iwencai", "fupan"]:
    mod = getattr(fx, mod_name)
    for m in sorted(dir(mod)):
        if m.startswith("_"):
            continue
        fn = getattr(mod, m)
        if callable(fn):
            try:
                sig = str(inspect.signature(fn))
            except Exception:
                sig = "?"
            print(f"SIG fx.{mod_name}.{m}{sig}")

# 无参/易调用接口逐个试探
for label, fn in [
    ("hotlist.stocks", lambda: fx.hotlist.stocks()),
    ("hotlist.sectors", lambda: fx.hotlist.sectors()),
    ("hotlist.convertible_bonds", lambda: fx.hotlist.convertible_bonds()),
    ("hotlist.etfs", lambda: fx.hotlist.etfs()),
    ("news.agp_list", lambda: fx.news.agp_list()),
    ("news.bd_list", lambda: fx.news.bd_list()),
    ("news.em_list", lambda: fx.news.em_list()),
    ("articles.hot_content", lambda: fx.articles.hot_content()),
    ("disclosure.em_list", lambda: fx.disclosure.em_list()),
    ("reports.fetch('贵州茅台')", lambda: fx.reports.fetch("贵州茅台")),
    ("iwencai.select('涨停')", lambda: fx.iwencai.select("涨停")),
    ("fupan.jygs_replay", lambda: fx.fupan.jygs_replay()),
]:
    show(label, fn)
