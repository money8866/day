"""数据层：TuShare Pro 日线拉取、复权处理、完整性校验、增量入库。

硬性约束
- 只使用真实接口数据；权限不足 / 数据缺失直接报错，绝不用模拟值填补。
- 当日行情未发布时返回 DATA_NOT_READY，不把上一交易日数据当作当日数据。
- 增量更新至少回补最近 N 个自然日，写入用 UPSERT 去重。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from database import Database
from settings import get_logger, get_tushare_token

LOG = get_logger()

ETF_COLS_RAW = ["ts_code", "trade_date", "open", "high", "low", "close",
                "pre_close", "pct_chg", "vol", "amount"]


class DataError(RuntimeError):
    """数据层异常基类。"""


class DataPermissionError(DataError):
    """接口无权限 / Token 无效。"""


class DataNotReady(DataError):
    """目标交易日行情尚未发布。"""


class DataQualityError(DataError):
    """数据完整性校验失败（缺复权因子、价格异常等）。"""


@dataclass
class DataQuality:
    """一次数据校验的结果。"""
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_trade_days: list[str] = field(default_factory=list)
    zero_volume_days: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "missing_trade_days": list(self.missing_trade_days),
            "zero_volume_days": list(self.zero_volume_days),
        }


class RateLimiter:
    """线程安全的最小调用间隔限流器。"""

    def __init__(self, min_interval_sec: float = 0.2):
        self.min_interval = max(0.0, float(min_interval_sec))
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


def _fmt_date(d: str | datetime | None) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    s = str(d).replace("-", "").strip()
    return s[:8] if s else None


class TushareProvider:
    """封装 pro 接口：限流、重试、错误分类。"""

    def __init__(self, cfg: dict, db: Database, token: Optional[str] = None):
        self.cfg = cfg
        self.db = db
        data_cfg = cfg.get("data", {})
        self.etf_code = data_cfg.get("etf_code", "159531.SZ")
        self.index_code = data_cfg.get("index_code", "932000.CSI")
        self.max_retry = int(data_cfg.get("max_retry", 3))
        self.backoff = float(data_cfg.get("retry_backoff_sec", 15))
        self.backoff_factor = float(data_cfg.get("retry_backoff_factor", 2))
        self.adj_tolerance = float(data_cfg.get("adj_ratio_tolerance", 0.01))
        self.limiter = RateLimiter(data_cfg.get("tushare_min_interval_ms", 200) / 1000.0)
        self._pro = None
        self._token = token
        self._fund_info: Optional[dict] = None

    # ------------------------------------------------------------ 基础
    @property
    def pro(self):
        if self._pro is None:
            import tushare as ts

            if not self._token:
                self._token, source = get_tushare_token()
                LOG.info("TuShare Token 来源: %s", source)
            self._pro = ts.pro_api(self._token)
        return self._pro

    def _call(self, api: str, **kwargs):
        """带限流、指数退避重试的接口调用，并对错误分类。"""
        func = getattr(self.pro, api)
        last_exc: Optional[Exception] = None
        wait = self.backoff
        for attempt in range(1, self.max_retry + 1):
            try:
                self.limiter.wait()
                return func(**kwargs)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                low = msg.lower()
                if ("权限" in msg) or ("permission" in low) or ("积分" in msg) or \
                        ("token" in low and "invalid" in low):
                    raise DataPermissionError(
                        f"接口 {api} 调用失败（权限/Token 问题）：{msg}"
                    ) from exc
                last_exc = exc
                if attempt >= self.max_retry:
                    break
                LOG.warning("接口 %s 第 %d/%d 次失败：%s，%.1fs 后重试",
                            api, attempt, self.max_retry, msg, wait)
                time.sleep(wait)
                wait *= self.backoff_factor
        raise DataError(f"接口 {api} 重试 {self.max_retry} 次仍失败：{last_exc}")

    # ------------------------------------------------------------ 日历
    def update_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        df = self._call("trade_cal", exchange="SSE",
                        start_date=_fmt_date(start_date), end_date=_fmt_date(end_date))
        if df is None or df.empty:
            raise DataError(f"trade_cal 返回空数据：{start_date}~{end_date}")
        df = df.rename(columns={"cal_date": "cal_date"})
        df["cal_date"] = df["cal_date"].astype(str)
        self.db.upsert_calendar(df[["cal_date", "is_open", "pretrade_date"]])
        return df

    def ensure_calendar(self) -> pd.DataFrame:
        """保证日历覆盖到今年年底（缓存命中则不重复请求）。"""
        today = datetime.now()
        need_end = f"{today.year}1231"
        cal = self.db.calendar(end_date=need_end)
        if cal.empty or str(cal["cal_date"].max()) < need_end:
            start = f"{today.year - 1}0101"
            LOG.info("更新交易日历 %s ~ %s", start, need_end)
            self.update_calendar(start, need_end)
            cal = self.db.calendar()
        return cal

    def is_trade_date(self, date: str) -> bool:
        d = _fmt_date(date)
        row = self.db.calendar(start_date=d, end_date=d)
        if row.empty:
            self.ensure_calendar()
            row = self.db.calendar(start_date=d, end_date=d)
        return bool((not row.empty) and int(row.iloc[0]["is_open"]) == 1)

    def trade_days(self, start_date: str, end_date: str) -> list[str]:
        self.ensure_calendar()
        cal = self.db.calendar(start_date=_fmt_date(start_date),
                               end_date=_fmt_date(end_date))
        return cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist()

    def last_trade_date_on_or_before(self, date: str) -> Optional[str]:
        d = _fmt_date(date)
        cal = self.db.calendar(end_date=d)
        if cal.empty:
            self.ensure_calendar()
            cal = self.db.calendar(end_date=d)
        opened = cal.loc[cal["is_open"] == 1, "cal_date"]
        return str(opened.max()) if not opened.empty else None

    def next_trade_date(self, date: str) -> Optional[str]:
        d = _fmt_date(date)
        nxt = (datetime.strptime(d, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        cal = self.db.calendar(start_date=nxt)
        if cal.empty:
            self.ensure_calendar()
            cal = self.db.calendar(start_date=nxt)
        if cal.empty:
            return None
        opened = cal.loc[cal["is_open"] == 1, "cal_date"]
        return str(opened.min()) if not opened.empty else None

    def prev_trade_date(self, date: str) -> Optional[str]:
        d = _fmt_date(date)
        cal = self.db.calendar(end_date=d)
        opened = cal.loc[(cal["is_open"] == 1) & (cal["cal_date"] < d), "cal_date"]
        return str(opened.max()) if not opened.empty else None

    # ------------------------------------------------------------ 基础信息
    def fund_info(self) -> dict:
        if self._fund_info is not None:
            return self._fund_info
        df = self._call("fund_basic", ts_code=self.etf_code)
        if df is None or df.empty:
            raise DataError(f"fund_basic 未返回 {self.etf_code} 的基础信息，请核对代码")
        self._fund_info = df.iloc[0].to_dict()
        LOG.info("ETF 基础信息：%s %s 上市日=%s",
                 self._fund_info.get("ts_code"), self._fund_info.get("name"),
                 self._fund_info.get("list_date"))
        return self._fund_info

    def verify_index_code(self) -> dict:
        """通过 index_basic 校验中证2000指数代码（候选 932000.CSI）。"""
        df = self._call("index_basic", ts_code=self.index_code)
        if df is None or df.empty:
            raise DataError(
                f"index_basic 未返回 {self.index_code}；请确认中证2000指数代码后修改 config.yaml"
            )
        info = df.iloc[0].to_dict()
        LOG.info("指数校验通过：%s %s 发布日=%s",
                 info.get("ts_code"), info.get("name"), info.get("list_date"))
        return info

    # ------------------------------------------------------------ 行情
    def _fetch_etf_daily(self, start_date: str, end_date: str) -> pd.DataFrame:
        df = self._call("fund_daily", ts_code=self.etf_code,
                        start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return pd.DataFrame()
        keep = [c for c in ETF_COLS_RAW if c in df.columns]
        df = df[keep].copy()
        df["trade_date"] = df["trade_date"].astype(str)
        return df.sort_values("trade_date").reset_index(drop=True)

    def _fetch_fund_adj(self, start_date: str, end_date: str) -> pd.DataFrame:
        df = self._call("fund_adj", ts_code=self.etf_code,
                        start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df[["trade_date", "adj_factor"]].copy()
        df["trade_date"] = df["trade_date"].astype(str)
        return df.sort_values("trade_date").reset_index(drop=True)

    def update_etf(self, end_date: Optional[str] = None,
                   full: bool = False) -> pd.DataFrame:
        """全量或增量更新 ETF 日线 + 复权因子，并写入 close_adj。"""
        data_cfg = self.cfg.get("data", {})
        end_date = _fmt_date(end_date) or datetime.now().strftime("%Y%m%d")
        latest = self.db.latest_etf_date()

        if full or latest is None:
            start_cfg = data_cfg.get("start_date", "auto")
            if str(start_cfg).lower() == "auto":
                start_date = str(self.fund_info().get("list_date") or "20230918")
            else:
                start_date = _fmt_date(start_cfg)
            LOG.info("ETF 全量拉取 %s ~ %s", start_date, end_date)
        else:
            inc_days = int(data_cfg.get("incremental_days", 10))
            start_date = (datetime.strptime(latest, "%Y%m%d")
                          - timedelta(days=inc_days)).strftime("%Y%m%d")
            start_date = min(start_date, latest)
            LOG.info("ETF 增量更新 %s ~ %s（库内最新 %s）", start_date, end_date, latest)

        px = self._fetch_etf_daily(start_date, end_date)
        if px.empty:
            raise DataNotReady(f"fund_daily 在 {start_date}~{end_date} 无数据")

        adj = self._fetch_fund_adj(start_date, end_date)
        if adj.empty:
            raise DataQualityError(
                f"fund_adj 在 {start_date}~{end_date} 无复权因子，无法计算复权价格；"
                "请检查接口权限或该 ETF 是否尚未上市"
            )
        merged = px.merge(adj, on="trade_date", how="left", validate="one_to_one")
        missing_adj = merged.loc[merged["adj_factor"].isna(), "trade_date"].tolist()
        if missing_adj:
            raise DataQualityError(
                f"以下交易日缺少复权因子：{missing_adj[:10]}"
                f"{' 等' if len(missing_adj) > 10 else ''}"
            )
        merged["adj_factor"] = merged["adj_factor"].astype(float)
        merged["close_adj"] = merged["close"].astype(float) * merged["adj_factor"]

        # 合并库内历史做复权因子连续性校验（只警告，不修改数据）
        hist = self.db.load_etf_daily(end_date=end_date)
        if not hist.empty:
            chk = pd.concat([hist[["trade_date", "adj_factor"]], merged[["trade_date", "adj_factor"]]])
            chk = chk.drop_duplicates("trade_date", keep="last").sort_values("trade_date")
            ratio = chk["adj_factor"].pct_change().abs()
            bad = chk.loc[ratio > self.adj_tolerance, "trade_date"].tolist()
            if bad:
                LOG.warning("复权因子单日变动超过 %.2f%%：%s（若为分红/拆分属正常）",
                            self.adj_tolerance * 100, bad[:5])

        self.db.upsert_etf_daily(merged[["ts_code", "trade_date", "open", "high", "low",
                                         "close", "pre_close", "pct_chg", "vol", "amount",
                                         "adj_factor", "close_adj"]])
        LOG.info("ETF 写入 %d 行，最新交易日 %s", len(merged), merged["trade_date"].max())
        return merged

    def update_index(self, start_date: str, end_date: str) -> pd.DataFrame:
        df = self._call("index_daily", ts_code=self.index_code,
                        start_date=_fmt_date(start_date), end_date=_fmt_date(end_date))
        if df is None or df.empty:
            raise DataNotReady(f"index_daily 在 {start_date}~{end_date} 无数据")
        cols = ["ts_code", "trade_date", "open", "high", "low", "close",
                "pre_close", "pct_chg", "vol", "amount"]
        df = df[[c for c in cols if c in df.columns]].copy()
        df["trade_date"] = df["trade_date"].astype(str)
        df = df.sort_values("trade_date")
        self.db.upsert_index_daily(df)
        LOG.info("指数 %s 写入 %d 行，最新 %s", self.index_code, len(df),
                 df["trade_date"].max())
        return df

    def sync_index(self, end_date: Optional[str] = None) -> pd.DataFrame:
        """指数只需覆盖 ETF 区间；增量回补最近 30 个自然日。"""
        end_date = _fmt_date(end_date) or datetime.now().strftime("%Y%m%d")
        latest = self.db.latest_index_date()
        if latest is None:
            start = str(self.fund_info().get("list_date") or "20230918")
        else:
            start = (datetime.strptime(latest, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
        return self.update_index(start, end_date)

    # ------------------------------------------------------------ 校验
    def validate(self, etf_df: pd.DataFrame, target_date: Optional[str] = None) -> DataQuality:
        """价格、成交、重复、缺失交易日的完整性校验。"""
        q = DataQuality()
        if etf_df is None or etf_df.empty:
            q.add_error("ETF 行情为空")
            return q

        df = etf_df.copy()
        dup = df["trade_date"].duplicated().sum()
        if dup:
            q.add_error(f"存在 {int(dup)} 条重复交易日记录")

        # 价格合法性（使用未复权价）
        bad_price = df[
            (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (df["high"] < df["low"])
            | (df["close"] > df["high"] + 1e-9)
            | (df["close"] < df["low"] - 1e-9)
        ]
        if not bad_price.empty:
            q.add_error(f"异常价格记录 {len(bad_price)} 条：{bad_price['trade_date'].head(5).tolist()}")

        zero_vol = df.loc[df["vol"].fillna(0) <= 0, "trade_date"].astype(str).tolist()
        if zero_vol:
            q.zero_volume_days = zero_vol
            q.add_warning(f"零成交/停牌交易日 {len(zero_vol)} 天，已保留原值（不回填、不猜价）")

        if target_date:
            target = _fmt_date(target_date)
            if target not in set(df["trade_date"].astype(str)):
                q.add_error(f"目标交易日 {target} 无 ETF 行情（尚未发布或接口延迟）")

        # 与交易日历比对缺口
        start, end = df["trade_date"].min(), df["trade_date"].max()
        try:
            days = self.trade_days(start, end)
        except Exception as exc:  # 日历不可用时不让校验直接崩
            q.add_warning(f"交易日历校验跳过：{exc}")
            days = []
        have = set(df["trade_date"].astype(str))
        missing = [d for d in days if d not in have]
        # 上市首日之前 / 停牌造成的缺口单独提示，不判为致命错误
        if missing:
            q.missing_trade_days = missing
            q.add_warning(f"相对交易日历缺失 {len(missing)} 个交易日：{missing[:5]}")

        if df["adj_factor"].isna().any():
            q.add_error("复权因子存在空值")

        return q

    # ------------------------------------------------------------ 就绪判断
    def latest_available_date(self) -> Optional[str]:
        return self.db.latest_etf_date()

    def check_ready(self, target_date: str) -> tuple[bool, str]:
        """目标交易日行情是否已入库且通过基础校验。"""
        target = _fmt_date(target_date)
        cols = self.db.load_etf_daily(target, target)
        if cols.empty:
            return False, f"{target} 的 ETF 日线尚未入库（接口未更新或非交易日）"
        if float(cols.iloc[0]["close"] or 0) <= 0:
            return False, f"{target} 收盘价无效"
        return True, f"{target} 行情已就绪"
