"""核心算法单元测试。

⚠️ 本文件所有数据均为合成数据，只用于断言算法行为。
按项目纪律：合成数据不得进入日报、不得冒充实盘结果（runtime.allow_synthetic_in_report=false）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stock_linkage_discovery import cluster_engine, feature_engine, linkage_engine  # noqa: E402
from stock_linkage_discovery import residual_engine, snapshot_store  # noqa: E402
from stock_linkage_discovery.common import assert_no_future, norm_code, safe_corr  # noqa: E402
from stock_linkage_discovery.config import load_config  # noqa: E402
from stock_linkage_discovery.data_layer import Slice  # noqa: E402


def _cfg(tmp: str | None = None) -> dict:
    cfg = load_config()
    if tmp:
        cfg["paths"] = dict(cfg["paths"])
        cfg["paths"]["linkage_db"] = str(Path(tmp) / "linkage.db")
        cfg["paths"]["output_dir"] = str(Path(tmp) / "out")
        cfg["paths"]["log_dir"] = str(Path(tmp) / "logs")
    cfg["linkage"] = dict(cfg["linkage"])
    cfg["linkage"]["max_candidates_per_stock"] = 12
    cfg["linkage"]["permutation_benchmarks"] = 24  # 与生产一致，保证经验 p 值有足够分辨率
    cfg["cluster"] = dict(cfg["cluster"])
    cfg["cluster"]["min_size"] = 4
    return cfg


def _synthetic(T: int = 150, N: int = 160, seed: int = 7) -> Slice:
    rng = np.random.default_rng(seed)
    market = rng.normal(0, 0.010, T).astype(np.float32)
    noise = rng.normal(0, 0.012, (T, N)).astype(np.float32)
    common = rng.normal(0, 0.020, T).astype(np.float32)
    ret = (market[:, None] + noise).astype(np.float32)
    ret[:, :6] = market[:, None] + common[:, None] + noise[:, :6] * 0.25
    close = 10.0 * np.cumprod(1.0 + ret, axis=0)
    amount = np.abs(rng.normal(30000, 6000, (T, N))).astype(np.float32)
    amount[:, :6] = amount[:, :6] * (1.0 + 0.5 * np.abs(common[:, None]))
    dates = [f"D{i:04d}" for i in range(T)]
    codes = [f"{600000 + i:06d}.SH" for i in range(N)]
    industry = np.asarray(["电子" if i < 6 else f"行业{i % 5}" for i in range(N)])
    zeros = np.zeros((T, N), dtype=np.float32)
    return Slice(
        asof=dates[-1], dates=dates, codes=codes, industry=industry,
        names=np.asarray([f"股票{i}" for i in range(N)]),
        ret=ret, close=close.astype(np.float32), close_raw=close.astype(np.float32),
        high=(close * 1.01).astype(np.float32), low=(close * 0.99).astype(np.float32),
        open=(close * 0.999).astype(np.float32), amount=amount,
        vol=amount / 10, turnover=np.abs(rng.normal(2, 0.5, (T, N))).astype(np.float32),
        circ_mv=np.full((T, N), 500000, dtype=np.float32),
        pct_chg=(ret * 100).astype(np.float32),
        valid=np.ones((T, N), dtype=bool),
        market_ret=market, industry_ret=np.broadcast_to(market[:, None], (T, N)).copy(),
        market_source="SYNTHETIC",
    )


def test_common_utils() -> None:
    assert norm_code("sh600000") == "600000.SH"
    assert norm_code("000001.SZ") == "000001.SZ"
    assert norm_code("bj920110") == "920110.BJ"
    assert abs(safe_corr(np.array([1., 2., 3.]), np.array([2., 4., 6.])) - 1.0) < 1e-9
    try:
        assert_no_future(["20261101", "20261110"], "20261106", "unit")
        raise AssertionError("应当拒绝未来数据")
    except AssertionError as e:
        assert "NO_FUTURE_DATA" in str(e)


def test_bh_fdr() -> None:
    p = np.array([0.001, 0.002, 0.02, 0.4, 0.9])
    q = linkage_engine.bh_fdr(p)
    assert np.all(np.diff(q) >= -1e-9), "q 值应单调不减"
    assert q[0] <= 0.01 and q[-1] <= 1.0


def test_residual_removes_common_market_factor() -> None:
    """两只独立股票因共同市场因子而高度相关，剔除市场后相关必须显著下降。"""
    s = _synthetic(T=150, N=8)
    rng = np.random.default_rng(11)
    s.ret = (s.market_ret[:, None] + rng.normal(0, 0.003, (150, 8))).astype(np.float32)
    s.industry_ret = np.broadcast_to(s.market_ret[:, None], (150, 8)).copy()
    raw = safe_corr(s.ret[-40:, 6], s.ret[-40:, 7])
    rr = residual_engine.compute_residuals(s, _cfg())
    resid = safe_corr(rr.resid_mkt[-40:, 6], rr.resid_mkt[-40:, 7])
    assert raw > 0.75, f"共同市场因子应制造高相关，实际 {raw:.3f}"
    assert abs(resid) < abs(raw) * 0.5, f"残差相关应大幅下降: raw={raw:.3f} resid={resid:.3f}"


def test_residual_is_causal() -> None:
    """改动最后一天数据不应影响此前任何一天的残差（严格因果）。"""
    s = _synthetic(T=150, N=6)
    cfg = _cfg()
    a = residual_engine.compute_residuals(s, cfg).resid_mkt.copy()
    s2 = _synthetic(T=150, N=6)
    s2.ret = s.ret.copy()
    s2.ret[-1, :] = 0.2
    b = residual_engine.compute_residuals(s2, cfg).resid_mkt
    assert np.allclose(a[:-1], b[:-1], atol=1e-7), "历史残差不得被未来数据改变"


def test_prescreen_recovers_planted_cluster() -> None:
    """预筛应把植入的 6 只同步股大量召回，同时把候选对压到全组合的很小比例。"""
    s = _synthetic()
    cfg = _cfg()
    rr = residual_engine.compute_residuals(s, cfg)
    s.resid_mkt, s.resid_mkt_ind = rr.resid_mkt, rr.resid_mkt_ind
    fs = feature_engine.compute_features(s, cfg)
    X = np.asarray(rr.resid_mkt[-60:], dtype=np.float32)
    C20 = linkage_engine._corr_matrix(X[-20:])
    C10 = linkage_engine._corr_matrix(X[-10:])
    C60 = linkage_engine._corr_matrix(X)
    ii, jj, info = linkage_engine.prescreen(s, fs, rr.resid_mkt, cfg, C20, C10, C60)
    pairs = set(zip(ii.tolist(), jj.tolist()))
    planted = {(i, j) for i in range(6) for j in range(i + 1, 6)}
    hit = len(pairs & planted) / len(planted)
    assert hit >= 0.8, f"植入群体召回率过低: {hit:.2f}"
    assert info["candidate_ratio"] < 0.5, "预筛未起到压缩计算量的作用"
    assert C20[0, 1] > 0.8, "植入联动的残差相关应很高"


def test_end_to_end_small(tmp: str) -> None:
    """小规模端到端：残差 -> 特征 -> 网络 -> 群体，验证全链路可运行且无异常。"""
    s = _synthetic()
    cfg = _cfg(tmp)
    rr = residual_engine.compute_residuals(s, cfg)
    s.resid_mkt, s.resid_mkt_ind = rr.resid_mkt, rr.resid_mkt_ind
    fs = feature_engine.compute_features(s, cfg)
    net = linkage_engine.build_network(s, fs, rr, cfg)
    assert set(net.graphs) == {"market", "market_industry"}
    assert len(net.edges) > 0, "植入群体应产生有效关联边"
    planted = {f"{600000 + i:06d}.SH" for i in range(6)}
    found = set(net.edges["a"]) | set(net.edges["b"])
    assert len(planted & found) >= 4, f"植入群体未被发现: {planted & found}"
    hist = cluster_engine.History(available=False)
    cs = cluster_engine.discover_clusters(net, s, fs, cfg, hist)
    assert cs.stats["valid_clusters"] >= 1
    assert cs.flags["BASELINE_SNAPSHOT"] is True
    assert all(c["state"] == "BASELINE_FIRST_SNAPSHOT" for c in cs.clusters), \
        "首日基线不应硬判新联动"
    assert all(0 <= c["linkage_score"] <= 100 for c in cs.clusters)


def test_snapshot_roundtrip(tmp: str) -> None:
    """快照写入/读取与增量跳过。"""
    cfg = _cfg(tmp)
    snapshot_store.init_db(cfg)
    snapshot_store.start_run(cfg, "D0149")
    s = _synthetic()
    fs = feature_engine.FeatureSet(s=s)
    net = linkage_engine.NetworkResult(asof="D0149", edges=pd.DataFrame(),
                                       graphs={"market": __import__("networkx").Graph(),
                                               "market_industry": __import__("networkx").Graph()},
                                       stats={}, codes=s.codes)
    cs = cluster_engine.ClusterSet(asof="D0149")
    obs = pd.DataFrame()
    snapshot_store.save_snapshot(cfg, "D0149", s, fs, net, cs, obs, {"total": 0.1}, {})
    assert snapshot_store.has_snapshot(cfg, "D0149")
    snapshot_store.delete_snapshot(cfg, "D0149")
    assert not snapshot_store.has_snapshot(cfg, "D0149")


def run_tests() -> int:
    passed, failed = 0, 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn in sorted(globals().items()):
            if not name.startswith("test_") or not callable(fn):
                continue
            try:
                if name in ("test_end_to_end_small", "test_snapshot_roundtrip"):
                    fn(tmp)
                else:
                    fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as e:
                import traceback
                print(f"  FAIL  {name}: {e}")
                traceback.print_exc()
                failed += 1
    print(f"\n=== TESTS: {passed} passed, {failed} failed ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_tests())
