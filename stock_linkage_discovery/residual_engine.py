"""市场/行业共同影响剔除（第二层）。

核心纪律：不能直接用原始涨跌幅相关系数 —— 大盘上涨时大量股票同涨不代表关联。

两套模型（严格因果滚动 OLS，第 t 日 beta 仅由 [t-L, t-1] 估计）：
    M1: R(i,t) = a + b1*R(market,t) + e(i,t)                  -> resid_mkt（主网络）
    M2: R(i,t) = a + b1*R(market,t) + b2*R(industry,t) + e(i,t) -> resid_mkt_ind（校验网络）
M2 只作为稳健性校验，防止行业因子过度剔除真实跨行业共同驱动。

同时提供原始收益相关与残差相关的对比，用于识别"市场共同驱动型伪联动"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .common import get_logger
from .data_layer import Slice

log = get_logger("residual_engine")


@dataclass
class ResidualResult:
    resid_mkt: np.ndarray
    resid_mkt_ind: np.ndarray
    beta_market: np.ndarray
    beta_industry: np.ndarray
    r2_market: np.ndarray
    quality: dict[str, Any] = field(default_factory=dict)


def _cum(a: np.ndarray) -> np.ndarray:
    return np.vstack([np.zeros((1,) + a.shape[1:], dtype=np.float64), np.cumsum(a, axis=0)])


def _rolling_ols(ret: np.ndarray, factors: list[np.ndarray], lookback: int,
                 min_obs: int, ridge: float) -> tuple[np.ndarray, np.ndarray]:
    """滚动因果 OLS 残差。factors 中每个元素为 (T,N) 或 (T,1)。

    返回 resid (T,N) 与最后一日 beta (N,K+1)。前 lookback 行无估计，填 NaN。
    """
    T, N = ret.shape
    K = len(factors)
    F = [np.broadcast_to(np.asarray(f, dtype=np.float64), (T, N)).copy() for f in factors]
    ok = np.isfinite(ret) & np.isfinite(F[0])
    for f in F[1:]:
        ok &= np.isfinite(f)
    y = np.where(ok, ret, 0.0).astype(np.float64)
    fs = [np.where(ok, f, 0.0).astype(np.float64) for f in F]
    cnt = ok.astype(np.float64)

    C = _cum(cnt)
    Cy = _cum(y)
    Cf = [_cum(f) for f in fs]
    Cyy = _cum(y * y)
    Cff = [[_cum(fa * fb) for fb in fs] for fa in fs]
    Cyf = [_cum(y * f) for f in fs]

    resid = np.full((T, N), np.nan, dtype=np.float32)
    beta_last = np.zeros((N, K + 1), dtype=np.float64)
    low = 0

    for t in range(lookback, T):
        a, b = t - lookback, t
        n = (C[b] - C[a])                                   # (N,)
        sy = (Cy[b] - Cy[a])
        sf = [(X[b] - X[a]) for X in Cf]
        A = np.zeros((N, K + 1, K + 1), dtype=np.float64)
        rhs = np.zeros((N, K + 1), dtype=np.float64)
        A[:, 0, 0] = n
        rhs[:, 0] = sy
        for p in range(K):
            A[:, 0, p + 1] = A[:, p + 1, 0] = sf[p]
            rhs[:, p + 1] = (Cyf[p][b] - Cyf[p][a])
            for q in range(K):
                A[:, p + 1, q + 1] = (Cff[p][q][b] - Cff[p][q][a])
        # 相对尺度 ridge：只用于防奇异，不能扭曲 beta 估计
        for p in range(K + 1):
            A[:, p, p] += ridge * np.maximum(np.abs(A[:, p, p]), 1e-8)
        valid_n = n >= min_obs
        if not valid_n.any():
            low += N
            resid[t] = np.where(np.isfinite(ret[t]), ret[t], 0.0)
            continue
        A_safe = np.where(valid_n[:, None, None], A, np.eye(K + 1)[None, :, :])
        try:
            beta = np.linalg.solve(A_safe, rhs[..., None])[..., 0]
        except np.linalg.LinAlgError:
            beta = np.zeros((N, K + 1))
            beta[:, 0] = sy / np.maximum(n, 1.0)
        beta[~valid_n] = 0.0
        beta[~valid_n, 0] = sy[~valid_n] / np.maximum(n[~valid_n], 1.0)
        low += int((~valid_n).sum())
        pred = beta[:, 0]
        for p in range(K):
            pred = pred + beta[:, p + 1] * np.nan_to_num(F[p][t])
        r = np.where(np.isfinite(ret[t]), ret[t], 0.0) - pred
        resid[t] = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        beta_last = beta
    return resid, beta_last


def compute_residuals(s: Slice, cfg: dict[str, Any]) -> ResidualResult:
    rcfg = cfg["residual"]
    L = int(rcfg.get("lookback", 60))
    minobs = int(rcfg.get("min_obs", 40))
    ridge = float(rcfg.get("ridge", 1e-4))
    clip = float(rcfg.get("clip_residual", 0.20))
    T, N = s.T, s.N
    if T < L + int(cfg["linkage"].get("long_window", 60)):
        log.warning("面板仅 %d 日，滚动残差有效窗口偏短（建议 >= %d）", T, L + 60)

    ret = np.asarray(s.ret, dtype=np.float64)
    mkt = np.asarray(s.market_ret, dtype=np.float64)[:, None]
    ind = np.asarray(s.industry_ret, dtype=np.float64)

    r1, b1 = _rolling_ols(ret, [mkt], L, minobs, ridge)
    r2, b2 = _rolling_ols(ret, [mkt, ind], L, minobs, ridge)
    r1 = np.clip(np.nan_to_num(r1, nan=0.0), -clip, clip).astype(np.float32)
    r2 = np.clip(np.nan_to_num(r2, nan=0.0), -clip, clip).astype(np.float32)

    tail = min(60, T)
    var_r = np.nanvar(ret[-tail:], axis=0)
    var_e = np.nanvar(r1[-tail:], axis=0)
    r2_market = np.nan_to_num(1.0 - var_e / np.where(var_r > 1e-12, var_r, np.nan), nan=0.0)

    quality = {
        "lookback": L, "min_obs": minobs, "usable_days": int(np.isfinite(r1).any(axis=1).sum()),
        "beta_market_mean": round(float(np.nanmean(b1[:, 1])), 4),
        "beta_industry_mean": round(float(np.nanmean(b2[:, 2])), 4) if b2.shape[1] > 2 else None,
        "market_explained_var_mean": round(float(np.nanmean(r2_market)), 4),
        "primary_network": rcfg.get("primary_network", "market"),
        "causal_beta": True,
        "no_future_data": True,
    }
    log.info("残差完成: beta_mkt=%.3f, 市场解释方差=%.1f%%, 有效日=%d",
             quality["beta_market_mean"], quality["market_explained_var_mean"] * 100,
             quality["usable_days"])
    return ResidualResult(resid_mkt=r1, resid_mkt_ind=r2,
                          beta_market=np.nan_to_num(b1[:, 1]), beta_industry=np.nan_to_num(b2[:, 2])
                          if b2.shape[1] > 2 else np.zeros(N),
                          r2_market=np.nan_to_num(r2_market), quality=quality)


def raw_vs_residual_corr(s: Slice, resid: np.ndarray, i: int, j: int, window: int = 20) -> dict[str, float]:
    """供调试/防伪联动：对比原始收益相关与剔除市场后的残差相关。"""
    from .common import safe_corr
    r = np.asarray(s.ret[-window:], dtype=np.float64)
    e = np.asarray(resid[-window:], dtype=np.float64)
    return {"raw_corr": safe_corr(r[:, i], r[:, j]),
            "resid_corr": safe_corr(e[:, i], e[:, j])}
