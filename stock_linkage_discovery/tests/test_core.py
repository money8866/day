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
from stock_linkage_discovery import residual_engine, snapshot_store, wave2_engine  # noqa: E402
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


def test_momentum_magnitude() -> None:
    """涨幅幅度标签、位置分层与群体启动确认（合成数据）。"""
    from stock_linkage_discovery import momentum_engine

    mcfg = momentum_engine.load_config()
    assert momentum_engine.member_tag(0.06, 0.10, 0.15, mcfg) == "MOVED"
    assert momentum_engine.member_tag(0.01, 0.02, 0.65, mcfg) == "EXTENDED"
    assert momentum_engine.member_tag(0.01, 0.02, 0.05, mcfg) == "FLAT"
    assert momentum_engine.member_tag(-0.08, -0.02, 0.00, mcfg) == "PULLED_BACK"
    assert momentum_engine.cluster_state(0.08, 0.10, 0.0, mcfg) == "EXTENDED"
    assert momentum_engine.cluster_state(-0.15, 0.00, 0.0, mcfg) == "DEEP_PULLBACK"
    assert momentum_engine.cluster_state(-0.05, 0.05, 0.0, mcfg) == "PULLBACK"
    assert momentum_engine.cluster_state(0.025, 0.10, 0.0, mcfg) == "EARLY_MOVE"
    assert momentum_engine.cluster_state(0.01, 0.10, 0.0, mcfg) == "FLAT"

    s = _synthetic()
    n = s.N
    fs = feature_engine.FeatureSet(s=s)
    for w in (5, 10, 20):
        fs.latest[f"ret_{w}"] = np.zeros(n, dtype=np.float64)
    fs.latest["ret_5"][:4] = 0.07
    fs.latest["ret_5"][0] = 0.12            # 领涨领先其余成员 5pct
    fs.latest["ret_10"][:4] = 0.12
    fs.latest["ret_20"][:4] = 0.20
    rows = []
    for k in range(6):
        rows.append({"cluster_id": "C1" if k < 4 else "C2", "ts_code": s.codes[k],
                     "role": "LEADER" if k == 0 else "FOLLOWER", "stock_name": f"T{k}",
                     "industry": "测试", "excess_mkt_5": 0.0, "resid_corr_20": 0.6,
                     "amount_ratio": 1.2, "trend": "多头", "is_new_member": False})
    cs = cluster_engine.ClusterSet(asof=s.asof)
    cs.members = pd.DataFrame(rows)
    cs.clusters = [
        {"cluster_id": "C1", "leader": s.codes[0], "grade": "A", "n_members": 4,
         "rank": 1, "priority": 70.0},
        {"cluster_id": "C2", "leader": s.codes[4], "grade": "B", "n_members": 2,
         "rank": 0, "priority": 50.0},
    ]
    momentum_engine.attach(cs, s, fs, mcfg)
    c1, c2 = cs.clusters[0], cs.clusters[1]
    assert c1["move_confirmed"] is True and c1["moved_stocks"] == 4
    assert c1["leader_follower_gap_5"] >= 0.03
    assert c1["move_state"] == "EXTENDED" and c1["move_healthy"] is False
    assert c2["move_confirmed"] is False and c2["move_healthy"] is True
    assert "move_5" in cs.members.columns and "move_tag" in cs.members.columns
    assert cs.stats["move_confirmed_clusters"] == 1
    assert cs.stats["move_extended_clusters"] == 1

    # 默认口径不改评级；显式开启严格过滤时，未确认的 S/A 群体降级
    cfg = _cfg()
    cfg["_momentum"] = dict(mcfg)
    assert cluster_engine.apply_momentum(cs, cfg).clusters[0]["grade"] == "A"
    c1["move_confirmed"] = False
    strict = dict(mcfg)
    strict["strict_filter"] = True
    cfg["_momentum"] = strict
    cs2 = cluster_engine.apply_momentum(cs, cfg)
    assert cs2.clusters[0]["grade"] == "B" and cs2.clusters[0]["move_demoted"] is True


def _wave2_data(seed: int = 7, breakdown: bool = False):
    """合成：D035~D040 放量第一波，D041~D046 量能仍高，D047 起连续缩量横盘。"""
    rng = np.random.default_rng(seed)
    T, N = 70, 5
    R = np.zeros((T, N), dtype=np.float64)
    R[:35] = rng.normal(0, 0.004, (35, N))
    R[35:41] = rng.normal(0.010, 0.003, (6, N))
    R[41:] = rng.normal(0, 0.001, (T - 41, N))
    A = np.ones((T, N)) * 100.0
    A[35:41] = 260.0          # 第一波放量
    A[41:47] = 200.0          # 回落但仍高于 20 日均额，不算缩量
    A[47:53] = 45.0           # 连续缩量平台
    if breakdown:
        R[45] = -0.12          # 等待期内先破位
    dates = [f"D{i:03d}" for i in range(T)]
    return dates, R, A


def _bars(dates, R, A, wcfg):
    return wave2_engine.bars_from_matrices(dates, "D040", R, A, A + 0.05, A - 0.05, wcfg)


def test_wave2_shrink_entry_and_causality() -> None:
    """二波判定：只在持续缩量平台出现买点；改动未来数据不得改变已选出的入场日。"""
    wcfg = wave2_engine.load_config()
    dates, R, A = _wave2_data()
    b = _bars(dates, R, A, wcfg)
    assert b is not None and b.t0_pos == 40

    res = wave2_engine.scan(b, wcfg, upto=50)   # 等待窗口末端 T0+10
    assert res["status"] == "WAVE2_READY", res["status"]
    assert res["entry_date"] == "D048", f"应在连续 2 日缩量后入场，实际 {res['entry_date']}"
    assert res["wait_days"] == 8
    assert res["first_ready_detail"]["first_wave_peak_vr"] >= wcfg["first_wave_vr"]
    assert res["first_ready_detail"]["vr"] <= wcfg["shrink_abs"]
    # 第一波刚结束、量能仍高时不能判买点
    assert wave2_engine.pass_core(b, 42, wcfg)[0] is False

    # 严格 as-of：只修改 upto 之后的行情，入场日与当日全部指标都不能变
    R2, A2 = R.copy(), A.copy()
    R2[51:] = 0.09
    A2[51:] = 900.0
    b2 = _bars(dates, R2, A2, wcfg)
    res2 = wave2_engine.scan(b2, wcfg, upto=50)
    assert res2["entry_date"] == res["entry_date"]
    assert res2["first_ready_detail"] == res["first_ready_detail"]
    assert res2["metrics"] == res["metrics"]
    # 超过最大等待日后，窗口关闭（入场日仍保留供追溯，但不重开计时）
    assert wave2_engine.scan(b, wcfg, upto=52)["status"] == "WAVE2_EXPIRED"

    # 等待期内先破位 -> 结构破坏，不再补票
    _, Rb, Ab = _wave2_data(breakdown=True)
    bb = _bars(dates, Rb, Ab, wcfg)
    rb = wave2_engine.scan(bb, wcfg, upto=50)
    assert rb["status"] == "WAVE2_BROKEN" and rb["entry_date"] is None

    # 超时未出现缩量 -> EXPIRED
    Ano = np.ones_like(A) * 100.0
    bno = _bars(dates, R, Ano, wcfg)
    rno = wave2_engine.scan(bno, wcfg, upto=60)
    assert rno["status"] == "WAVE2_EXPIRED" and rno["entry_date"] is None


def test_wave2_attach_keeps_grade() -> None:
    """实时标注只写状态字段，不改评级 / 成员 / 策略信号。"""
    wcfg = wave2_engine.load_config()
    dates, R, A = _wave2_data()
    N = R.shape[1]
    codes = [f"{600000 + i:06d}.SH" for i in range(N)]
    s = Slice(
        asof="D050", dates=dates[:51], codes=codes,
        industry=np.asarray(["测试"] * N), names=np.asarray(codes),
        ret=R[:51].astype(np.float32), close=np.cumprod(1 + R[:51], axis=0).astype(np.float32) * 10,
        close_raw=np.ones((51, N), dtype=np.float32) * 10,
        high=(A[:51] + 0.05).astype(np.float32), low=(A[:51] - 0.05).astype(np.float32),
        open=np.ones((51, N), dtype=np.float32) * 10,
        amount=A[:51].astype(np.float32), vol=A[:51].astype(np.float32),
        turnover=np.ones((51, N), dtype=np.float32),
        circ_mv=np.ones((51, N), dtype=np.float32) * 500000,
        pct_chg=(R[:51] * 100).astype(np.float32),
        valid=np.ones((51, N), dtype=bool),
        market_ret=np.zeros(51, dtype=np.float32),
        industry_ret=np.zeros((51, N), dtype=np.float32), market_source="SYNTHETIC",
    )
    fs = feature_engine.FeatureSet(s=s)
    cs = cluster_engine.ClusterSet(asof="D050")
    cs.members = pd.DataFrame([{"cluster_id": "C1", "ts_code": c} for c in codes])
    cs.clusters = [{"cluster_id": "C1", "grade": "S", "state": "EMERGING_CLUSTER",
                    "signals": ["EMERGING_CLUSTER"], "birth_date": "D040",
                    "asof": "D050", "leader": codes[0], "n_members": N, "rank": 1}]
    wave2_engine.attach(cs, s, fs, wcfg)
    c = cs.clusters[0]
    assert c["grade"] == "S" and c["state"] == "EMERGING_CLUSTER"
    assert c["wave2_status"] in wave2_engine.STATUSES
    assert c["wave2_status"] == "WAVE2_READY", c["wave2_status"]
    assert c["wave2_entry_date"] == "D048"
    assert len(cs.members) == N and cs.stats["wave2_ready_clusters"] == 1


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
