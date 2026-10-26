# -*- coding: utf-8 -*-
"""FinchX 全量接口矩阵：签名反射 + 系统化实测（含参数校验路径）"""
import inspect
import json
import time

from finchx import FinchX, FinchXError, SourceError

fx = FinchX(timeout=20.0)

MODULES = ["reference", "market", "fund_flow", "sector", "company", "financial",
           "regulation", "hotlist", "fupan", "news", "articles", "disclosure",
           "reports", "forum", "iwencai"]

# 每个接口的实测调用（无必填参数的用 ()；需要代码的用 600519/000001）
CALLS = {
    ("reference", "trading_calendar"): lambda: fx.reference.trading_calendar("2026-10-19", "2026-10-23"),
    ("market", "all_quotes"): lambda: fx.market.all_quotes(),
    ("market", "ranking"): lambda: fx.market.ranking(limit=5),
    ("market", "quote_snapshot"): lambda: fx.market.quote_snapshot("600519"),
    ("market", "ohlcv"): lambda: fx.market.ohlcv("600519", "2026-10-09", "2026-10-23"),
    ("market", "orderbook"): lambda: fx.market.orderbook("600519"),
    ("market", "equity_intraday"): lambda: fx.market.equity_intraday("600519"),
    ("market", "equity_intraday_5d"): lambda: fx.market.equity_intraday_5d("600519"),
    ("market", "index_intraday"): lambda: fx.market.index_intraday("000001"),
    ("market", "index_intraday_5d"): lambda: fx.market.index_intraday_5d("399006"),
    ("market", "em_breadth"): lambda: fx.market.em_breadth(),
    ("market", "agp_sentiment"): lambda: fx.market.agp_sentiment(),
    ("market", "limit_up_pool"): lambda: fx.market.limit_up_pool(),
    ("market", "limit_down_pool"): lambda: fx.market.limit_down_pool(),
    ("market", "broken_limit_pool"): lambda: fx.market.broken_limit_pool(),
    ("market", "yesterday_limit_up_pool"): lambda: fx.market.yesterday_limit_up_pool(),
    ("market", "strong_pool"): lambda: fx.market.strong_pool(),
    ("market", "consecutive_limit_up"): lambda: fx.market.consecutive_limit_up(),
    ("fund_flow", "stock"): lambda: fx.fund_flow.stock("600519", days=5),
    ("fund_flow", "dragon_tiger_list"): lambda: fx.fund_flow.dragon_tiger_list("2026-10-23"),
    ("sector", "tx_sectors"): lambda: fx.sector.tx_sectors("600519"),
    ("sector", "em_keywords"): lambda: fx.sector.em_keywords("600519"),
    ("sector", "tx_industry_comparison"): lambda: fx.sector.tx_industry_comparison("600519"),
    ("sector", "ths_concept_list"): lambda: fx.sector.ths_concept_list(),
    ("sector", "ths_concept_quote_snapshot"): lambda: fx.sector.ths_concept_quote_snapshot("300008"),
    ("sector", "ths_concept_ohlcv"): lambda: fx.sector.ths_concept_ohlcv("300008", "2026-10-09", "2026-10-23"),
    ("company", "info"): lambda: fx.company.info("600519"),
    ("company", "float_shareholders"): lambda: fx.company.float_shareholders("600519"),
    ("financial", "statements"): lambda: fx.financial.statements("600519", "income_statement", max_periods=2),
    ("regulation", "watchlist"): lambda: fx.regulation.watchlist(),
    ("regulation", "abnormal_records"): lambda: fx.regulation.abnormal_records(),
    ("regulation", "abnormal_prediction_history"): lambda: fx.regulation.abnormal_prediction_history(),
    ("regulation", "severe_predictions"): lambda: fx.regulation.severe_predictions(),
    ("regulation", "abnormal_counts"): lambda: fx.regulation.abnormal_counts(),
    ("regulation", "deviation"): lambda: fx.regulation.deviation("600519"),
    ("hotlist", "stocks"): lambda: fx.hotlist.stocks(category="rising", limit=5),
    ("hotlist", "sectors"): lambda: fx.hotlist.sectors(sector_type="concept", limit=5),
    ("hotlist", "convertible_bonds"): lambda: fx.hotlist.convertible_bonds(limit=5),
    ("hotlist", "etfs"): lambda: fx.hotlist.etfs(limit=5),
    ("news", "agp_list"): lambda: fx.news.agp_list(limit=5),
    ("news", "bd_list"): lambda: fx.news.bd_list(limit=5),
    ("news", "em_list"): lambda: fx.news.em_list("600519", limit=5),
    ("articles", "hot_content"): lambda: fx.articles.hot_content(limit=5),
    ("articles", "agp_topic_articles"): lambda: fx.articles.agp_topic_articles(limit=3),
    ("disclosure", "em_list"): lambda: fx.disclosure.em_list("600519", limit=5),
    ("reports", "fetch"): lambda: fx.reports.fetch("贵州茅台", limit=3),
    ("reports", "agp_fetch"): lambda: fx.reports.agp_fetch(limit=3),
    ("reports", "em_fetch"): lambda: fx.reports.em_fetch(limit=3),
    ("reports", "analysys_fetch"): lambda: fx.reports.analysys_fetch(limit=3),
}

# 需登录态/密钥的接口（按设计）
AUTH_REQUIRED = {
    ("forum", "tgb_replies"): "cookies",
    ("iwencai", "select"): "cookies",
    ("iwencai", "search"): "api_key",
    ("iwencai", "get_report"): "url",
    ("fupan", "jygs_replay"): "session",
    ("news", "em_get"): "url",
    ("articles", "ths_get"): "url",
    ("articles", "ths_list"): "topic_url",
    ("articles", "agp_get"): "url",
    ("disclosure", "em_get"): "url",
}

print("=" * 20, "全量签名反射", "=" * 20)
total_methods = 0
for mod in MODULES:
    api = getattr(fx, mod)
    for name in sorted(dir(api)):
        if name.startswith("_"):
            continue
        fn = getattr(api, name)
        if callable(fn):
            total_methods += 1
            doc = (inspect.getdoc(fn) or "").split("\n")[0][:40]
            try:
                sig = str(inspect.signature(fn))
            except Exception:
                sig = "?"
            print(f"fx.{mod}.{name}{sig}  # {doc}")
print(f"\n公开方法总数: {total_methods}")

print("\n" + "=" * 20, "系统化实测", "=" * 20)
results = []
for (mod, name), fn in CALLS.items():
    t0 = time.time()
    try:
        r = fn()
        dt = time.time() - t0
        if isinstance(r, list):
            n = len(r)
            sample = json.dumps(r[0], ensure_ascii=False, default=str)[:150] if r else "[]"
        elif isinstance(r, dict):
            n = len(r.get("reports", r)) if "reports" in r else len(r)
            sample = json.dumps(r, ensure_ascii=False, default=str)[:150]
        else:
            n, sample = 1, str(r)[:150]
        results.append((mod, name, "OK", round(dt, 2), n))
        print(f"[OK]   fx.{mod}.{name:<28} {dt:5.1f}s  n={n:<6} {sample[:110]}")
    except Exception as e:
        dt = time.time() - t0
        results.append((mod, name, type(e).__name__, round(dt, 2), str(e)[:80]))
        print(f"[FAIL] fx.{mod}.{name:<28} {dt:5.1f}s  {type(e).__name__}: {str(e)[:100]}")

print("\n" + "=" * 20, "参数校验路径抽查", "=" * 20)
checks = [
    ("market.ranking(criterion=bad)", lambda: fx.market.ranking(criterion="bad")),
    ("market.ohlcv(日期乱序)", lambda: fx.market.ohlcv("600519", "2026-10-23", "2026-10-09")),
    ("market.quote_snapshot(5位代码)", lambda: fx.market.quote_snapshot("60051")),
    ("market.quote_snapshot(kind=bad)", lambda: fx.market.quote_snapshot("600519", kind="bad")),
    ("hotlist.sectors(type=bad)", lambda: fx.hotlist.sectors(sector_type="bad")),
    ("hotlist.stocks(period=1h配trend)", lambda: fx.hotlist.stocks(category="trend", period="1h")),
    ("reports.fetch(空关键词)", lambda: fx.reports.fetch("")),
    ("fund_flow.dragon_tiger_list(非法日期)", lambda: fx.fund_flow.dragon_tiger_list("2026/10/23 12:00:00")),
    ("regulation.abnormal_records(type=bad)", lambda: fx.regulation.abnormal_records(event_type="bad")),
    ("FinchX(timeout=True)", lambda: FinchX(timeout=True)),
    ("FinchX(timeout=-1)", lambda: FinchX(timeout=-1)),
]
for label, fn in checks:
    try:
        fn()
        print(f"[!!] {label} → 未抛异常（校验缺失？）")
    except ValueError as e:
        print(f"[OK] {label} → ValueError: {str(e)[:60]}")
    except Exception as e:
        print(f"[??] {label} → {type(e).__name__}: {str(e)[:60]}")

ok = sum(1 for r in results if r[2] == "OK")
print(f"\n实测通过 {ok}/{len(results)}；失败 {len(results)-ok} 个：")
for mod, name, status, dt, msg in results:
    if status != "OK":
        print(f"  - fx.{mod}.{name}: {status} {msg}")
print(f"\n需登录态接口（按设计，未测）: {len(AUTH_REQUIRED)} 个")
