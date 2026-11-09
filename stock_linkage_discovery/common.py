"""公共工具：日志、计时、数值安全、as-of 防未来数据守卫。"""
from __future__ import annotations

import json
import logging
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

_LOG_CONFIGURED = False


def setup_logging(log_dir: str | Path, level: str = "INFO", asof: str | None = None) -> logging.Logger:
    global _LOG_CONFIGURED
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("linkage")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    root.propagate = False
    if not _LOG_CONFIGURED:
        fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
        _LOG_CONFIGURED = True
    if asof:
        fh = logging.FileHandler(Path(log_dir) / f"linkage_{asof}.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    return root


def get_logger(name: str = "linkage") -> logging.Logger:
    return logging.getLogger(name if name.startswith("linkage") else f"linkage.{name}")


@dataclass
class Timings:
    items: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def measure(self, label: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.items[label] = round(self.items.get(label, 0.0) + time.perf_counter() - t0, 4)

    def as_dict(self) -> dict[str, float]:
        return dict(self.items)

    @property
    def total(self) -> float:
        return round(sum(self.items.values()), 4)


def finite(x: Any, default: float = 0.0) -> float:
    """转 float 并保证有限值；NaN/Inf/None 一律回退，避免污染相关矩阵。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def clip01(x: float) -> float:
    return float(min(1.0, max(0.0, finite(x))))


def safe_corr(a: np.ndarray, b: np.ndarray, min_obs: int = 3) -> float:
    """皮尔逊相关，自动对齐有限值；样本不足或零方差返回 0。"""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < min_obs:
        return 0.0
    x, y = a[m], b[m]
    sx, sy = x.std(), y.std()
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    return float(np.clip(np.dot(x - x.mean(), y - y.mean()) / (len(x) * sx * sy), -1.0, 1.0))


def rankdata_axis0(x: np.ndarray) -> np.ndarray:
    """按列（每只股票的时间序列）求平均秩，用于批量 Spearman。"""
    x = np.asarray(x, dtype=np.float64)
    out = np.full(x.shape, np.nan)
    for j in range(x.shape[1]):
        col = x[:, j]
        m = np.isfinite(col)
        if not m.any():
            continue
        v = col[m]
        order = np.argsort(v, kind="stable")
        ranks = np.empty(len(v), dtype=np.float64)
        sv = v[order]
        i = 0
        while i < len(sv):
            k = i
            while k + 1 < len(sv) and sv[k + 1] == sv[i]:
                k += 1
            ranks[order[i:k + 1]] = (i + k) / 2.0 + 1.0
            i = k + 1
        out[m, j] = ranks
    return out


def zscore(x: np.ndarray, axis: int = 0) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    mu = np.nanmean(x, axis=axis, keepdims=True)
    sd = np.nanstd(x, axis=axis, keepdims=True)
    out = (x - mu) / np.where(sd < 1e-9, 1.0, sd)
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def assert_no_future(dates: list[str], asof: str, where: str) -> None:
    """严格 as-of 守卫：任何参与计算的交易日都不得晚于 asof。"""
    if dates and max(dates) > str(asof):
        raise AssertionError(
            f"[NO_FUTURE_DATA] {where} 检测到未来数据: max={max(dates)} > asof={asof}"
        )


def write_json(path: str | Path, obj: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=_json_default)
    return p


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def norm_code(code: str) -> str | None:
    """把 bj920110 / sh600000 / 600000.SH / 000001 统一成 Tushare ts_code。"""
    if code is None:
        return None
    s = str(code).strip().lower()
    if not s:
        return None
    if "." in s:
        num, suf = s.split(".", 1)
        if suf.upper() in ("SH", "SZ", "BJ", "CSI"):
            return f"{num.zfill(6)}.{suf.upper()}"
    if s[:2] in ("sh", "sz", "bj") and s[2:].isdigit():
        return f"{s[2:].zfill(6)}.{s[:2].upper()}"
    if s.isdigit() and len(s) == 6:
        if s[0] == "6" or s.startswith("9"):
            return f"{s}.SH"
        if s[0] in ("0", "2", "3"):
            return f"{s}.SZ"
        if s[0] in ("4", "8"):
            return f"{s}.BJ"
    return None
