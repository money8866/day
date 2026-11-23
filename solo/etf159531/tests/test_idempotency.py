import copy
from pathlib import Path

import pandas as pd
import pytest

import main as app
from database import Database
from data_provider import DataQuality
from conftest import make_ohlcv


class FakeProvider:
    """离线测试用 Provider：不访问 TuShare。"""

    def __init__(self, db: Database):
        self.db = db

    def ensure_calendar(self):
        return None

    def is_trade_date(self, d):
        return True

    def next_trade_date(self, d):
        return None

    def verify_index_code(self):
        return {}

    def update_etf(self, end_date=None, full=False):
        return None

    def sync_index(self, end_date=None):
        return None

    def check_ready(self, target):
        return True, f"{target} 行情已就绪（测试桩）"

    def validate(self, etf, target_date=None):
        return DataQuality().as_dict()


def _prepare_env(cfg, tmp_path):
    c = copy.deepcopy(cfg)
    c["project"] = dict(c["project"],
                        db_path=str(tmp_path / "idem.db"),
                        report_dir=str(tmp_path / "reports"),
                        log_dir=str(tmp_path / "logs"))
    c["data"] = dict(c["data"], min_history_days=100)
    c["account"] = dict(c.get("account", {}), dir=str(tmp_path / "acc"))
    c["report"] = dict(c.get("report", {}), html=False)

    df = make_ohlcv(n=320, seed=31)
    db = Database(tmp_path / "idem.db")
    db.upsert_etf_daily(df)
    db.upsert_calendar(pd.DataFrame({
        "cal_date": df["trade_date"], "is_open": 1, "pretrade_date": None,
    }))
    return c, db, df


def test_repeat_run_is_idempotent(cfg, tmp_path):
    c, db, df = _prepare_env(cfg, tmp_path)
    provider = FakeProvider(db)
    target = str(df["trade_date"].iloc[-1])

    code1, ctx1 = app.run_daily(c, date=target, allow_fetch=False,
                                provider=provider, db=db)
    code2, ctx2 = app.run_daily(c, date=target, allow_fetch=False,
                                provider=provider, db=db)
    assert code1 == 0 and code2 == 0

    n_sig = db.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert n_sig == 1
    n_runs = db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert n_runs == 2
    assert ctx1["caps"]["final_target"] == pytest.approx(ctx2["caps"]["final_target"])

    ev1 = db.load_oversold_events()
    app.run_daily(c, date=target, allow_fetch=False, provider=provider, db=db)
    ev2 = db.load_oversold_events()
    assert len(ev1) == len(ev2)
    if len(ev1):
        assert ev1["event_id"].tolist() == ev2["event_id"].tolist()
        assert ev1["state"].tolist() == ev2["state"].tolist()


def test_non_trading_day_skipped(cfg, tmp_path, monkeypatch):
    c, db, df = _prepare_env(cfg, tmp_path)
    provider = FakeProvider(db)
    monkeypatch.setattr(provider, "is_trade_date", lambda d: False)
    code, ctx = app.run_daily(c, date=str(df["trade_date"].iloc[-1]),
                              allow_fetch=False, provider=provider, db=db)
    assert code == 0 and ctx is None
    n_sig = db.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert n_sig == 0
    last = db.list_runs(1)[0]
    assert last["status"] == "SKIPPED_NON_TRADING"


def test_data_not_ready_does_not_write_signal(cfg, tmp_path):
    c, db, df = _prepare_env(cfg, tmp_path)
    provider = FakeProvider(db)
    target = str(df["trade_date"].iloc[-1])

    def not_ready(t):
        return False, "行情未更新"

    provider.check_ready = not_ready
    code, ctx = app.run_daily(c, date=target, allow_fetch=False,
                              provider=provider, db=db)
    assert code == 2
    n_sig = db.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert n_sig == 0
    last = db.list_runs(1)[0]
    assert last["status"] == "DATA_NOT_READY"
    # 展示型报告仍生成，但标记为不可执行
    assert ctx is not None and ctx["meta"]["executable"] is False


def test_report_files_written(cfg, tmp_path):
    c, db, df = _prepare_env(cfg, tmp_path)
    provider = FakeProvider(db)
    target = str(df["trade_date"].iloc[-1])
    code, ctx = app.run_daily(c, date=target, allow_fetch=False,
                              provider=provider, db=db)
    assert code == 0
    base = Path(c["project"]["report_dir"]) / target
    assert (base / f"report_{target}.md").exists()
    assert (base / f"report_{target}.json").exists()
