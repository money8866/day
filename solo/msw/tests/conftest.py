# -*- coding: utf-8 -*-
"""MSW 测试共享夹具：合成日线 + 假面板 + 配置。

合成数据设计原则（供 PIT 测试使用）：
  - 长期对数上行趋势（0.0009/日）+ 70 日锯齿（对数幅度 0.22 ≈ 峰谷 25%）
  - 锯齿幅度 > ZigZag 阈值 max(10%, 1.5×ATR%)，保证产生多个已确认波段
  - 日噪声 1.2% → ATR% ≈ 2% → 1.5×ATR% ≈ 3% < 10%，阈值由百分比项主导
  - 回撤半周期缩量（amount × 0.6），满足 C1 缩量条件
  - 全部为 PIT 纯函数输入：特征/波段/信号只允许回看
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from msw import load_config                      # noqa: E402
from msw.feature_engine import compute_features  # noqa: E402
from msw.swing_analyzer import SwingHistory      # noqa: E402


# ---------------------------------------------------------------- 合成日线

def make_bars(n: int = 900, seed: int = 7, start_price: float = 10.0,
              period: int = 70, amp: float = 0.22) -> pd.DataFrame:
    """合成单股日线（列与 data_adapter.load_stock 输出对齐，含 hfq_*）。

    返回列：trade_date/open/high/low/close/pre_close/pct_chg/vol/amount/
            total_mv/circ_mv/adj_factor/hfq_open/hfq_high/hfq_low/hfq_close
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    trend = 0.0009 * t
    phase = (t % period) / float(period)
    saw = np.where(phase < 0.5, amp * phase / 0.5,
                   amp * (1.0 - (phase - 0.5) / 0.5))
    noise = rng.normal(0.0, 0.012, n)
    close = np.exp(np.log(start_price) + trend + saw + noise)

    prev_close = np.concatenate([[start_price], close[:-1]])
    op = prev_close * (1.0 + rng.normal(0.0, 0.004, n))
    span = np.abs(rng.normal(0.014, 0.004, n))
    high = np.maximum.reduce([close, prev_close, op]) * (1.0 + span)
    low = np.minimum.reduce([close, prev_close, op]) * (1.0 - span)
    low = np.maximum(low, 1e-6)

    # 回撤半周期缩量：C1 的缩量条件（5 日均量 <= 0.75 × 20 日均量）
    down = (phase >= 0.5).astype(float)
    amount = (40_000_000.0 / 10.0        # 基准成交额 4000 万元 → 40000 千元
              * (1.0 - 0.35 * down)
              * (1.0 + rng.normal(0.0, 0.08, n))) / 10.0
    amount = np.maximum(amount, 1000.0)
    vol = amount * 1000.0 / close / 100.0        # 手（100 股）
    circ_mv = close * 1e8
    total_mv = circ_mv * 1.2

    dates = pd.bdate_range('2023-01-03', periods=n).strftime('%Y%m%d')
    df = pd.DataFrame({
        'trade_date': dates,
        'open': op, 'high': high, 'low': low, 'close': close,
        'pre_close': prev_close,
        'pct_chg': close / prev_close - 1.0,
        'vol': vol, 'amount': amount,
        'turnover_rate': rng.uniform(0.5, 4.0, n),
        'volume_ratio': rng.uniform(0.5, 2.0, n),
        'total_mv': total_mv, 'circ_mv': circ_mv,
        'adj_factor': np.ones(n),
    })
    for c in ('open', 'high', 'low', 'close'):
        df[f'hfq_{c}'] = df[c]          # adj_factor=1 → hfq 即原价
    return df


def make_feats(n: int = 900, seed: int = 7, cfg: dict = None) -> pd.DataFrame:
    """合成数据 + 全量特征（compute_features 纯函数）"""
    cfg = cfg or load_config()
    return compute_features(make_bars(n=n, seed=seed), cfg)


# ---------------------------------------------------------------- 假面板

class FakePanel:
    """MarketPanel 的最小替身：零收益基准 → RS = 个股区间收益。

    只实现 StockWalker 实际用到的接口：industry / bench_cum / ind_cum / breadth。
    """

    def __init__(self, dates, bench_ret: float = 0.0):
        idx = pd.Index([str(d) for d in dates])
        self.dates = list(idx)
        self.codes = []
        self.industry = {}
        self.bench_cum = {120: pd.Series(bench_ret, index=idx),
                          250: pd.Series(bench_ret, index=idx)}
        self.ind_cum = {}

    def breadth(self, date):
        return 1.0


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope='session')
def cfg():
    return load_config()


@pytest.fixture(scope='session')
def bars():
    return make_bars()


@pytest.fixture(scope='session')
def feats(cfg):
    return make_feats(cfg=cfg)


@pytest.fixture(scope='session')
def sh(feats, cfg):
    return SwingHistory(feats, cfg)


@pytest.fixture(scope='session')
def panel(feats):
    return FakePanel(feats['trade_date'])


def walker_on(feats_df, cfg, panel=None, start_idx=250, end_idx=None):
    """在给定特征上构造 StockWalker（uni_mask 全 True，无基本面序列）"""
    from msw.signal_engine import StockWalker
    sh = SwingHistory(feats_df, cfg)
    w = StockWalker('000001.SZ', feats_df, sh, cfg, panel=panel, name='测试股',
                    industry='测试', fina_series=None, start_idx=start_idx,
                    end_idx=end_idx, uni_mask=np.ones(len(feats_df), dtype=bool))
    return w, sh
