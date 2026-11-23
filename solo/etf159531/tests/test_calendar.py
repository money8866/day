import pandas as pd
import pytest

from database import Database
from data_provider import TushareProvider


def _seed_calendar(db: Database) -> None:
    rows = []
    for d in pd.bdate_range("2026-11-16", "2026-12-31"):
        rows.append({"cal_date": d.strftime("%Y%m%d"), "is_open": 1,
                     "pretrade_date": None})
    db.upsert_calendar(pd.DataFrame(rows))


def test_calendar_queries(cfg, tmp_path):
    db = Database(tmp_path / "cal.db")
    _seed_calendar(db)
    p = TushareProvider(cfg, db)

    assert p.is_trade_date("20261120")
    assert not p.is_trade_date("20261121")   # 周六未登记为交易日
    assert p.next_trade_date("20261120") == "20261123"
    assert p.prev_trade_date("20261123") == "20261120"
    assert p.last_trade_date_on_or_before("20261122") == "20261120"
    days = p.trade_days("20261116", "20261120")
    assert days == ["20261116", "20261117", "20261118", "20261119", "20261120"]


def test_check_ready_requires_exact_date(cfg, tmp_path):
    db = Database(tmp_path / "ready.db")
    db.upsert_etf_daily(pd.DataFrame([{
        "ts_code": "159531.SZ", "trade_date": "20261120", "open": 1.0, "high": 1.1,
        "low": 0.9, "close": 1.0, "pre_close": 1.0, "pct_chg": 0.0, "vol": 100.0,
        "amount": 100.0, "adj_factor": 1.0, "close_adj": 1.0,
    }]))
    p = TushareProvider(cfg, db)
    ok, _ = p.check_ready("20261120")
    assert ok
    ok2, msg = p.check_ready("20261121")
    assert not ok2 and "尚未入库" in msg
