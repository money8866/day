"""三花聚顶研究 - 面板构建（只读 stock_data.db，输出到 research/out/）

产出：
  panel.parquet       全市场逐日面板（自建前复权因子 + 涨停/触板识别 + 均量）
  basic.parquet       股票静态信息（名称/行业/上市日）
  trade_dates.csv     交易日历
  index.parquet       指数日线（基准/Regime 用）

涨停识别口径（不依赖 limit_list，cache 中无历史涨跌停表）：
  按板块设定涨停幅度 r ∈ {主板 10%, 创业板/科创板 20%, 北交所 30%}，
  另对主板附加 5% 候选以覆盖 ST（ST 主板限幅 5%）。
  判定 = close == round(pre_close*(1+r), 2) 且 pct_chg >= r*100 - 0.6。
  上市后前 10 个交易日不判定（新股无涨跌幅限制期）。
"""
import os
import sqlite3
import numpy as np
import pandas as pd

DB = r"D:\mystock\cache_daily\stock_data.db"
CD = r"D:\mystock\cache_daily"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)

START = "20210101"
END = "20260930"


def board_of(code):
    """板块判定：MAIN / GEM / STAR / BSE"""
    n = code.split(".")[0]
    if code.endswith(".BJ"):
        return "BSE"
    if n.startswith(("688", "689")):
        return "STAR"
    if n.startswith(("300", "301", "302")):
        return "GEM"
    if n.startswith("920"):
        return "BSE"
    return "MAIN"


def main():
    conn = sqlite3.connect(DB, timeout=120)
    print("读取 daily_cache ...")
    df = pd.read_sql_query(
        f"SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount "
        f"FROM daily_cache WHERE trade_date >= '{START}' AND trade_date <= '{END}' "
        f"ORDER BY ts_code, trade_date", conn)
    print("  行数:", len(df), "股票数:", df['ts_code'].nunique())
    conn.close()

    df['trade_date'] = df['trade_date'].astype(np.int32)
    df['ts_code'] = df['ts_code'].astype('category')

    g = df.groupby('ts_code', observed=True)

    # ── 上市后第几个交易日（0 基）
    df['seq'] = g.cumcount().astype(np.int16)

    # ── 自建前复权因子：f_t = prod_{s>t} (close_{s-1}/pre_close_s)
    ratio = (df['pre_close'] / g['close'].shift(1)).astype(np.float64)
    ratio = ratio.fillna(1.0).clip(0.3, 3.0)
    gc = ratio.groupby(df['ts_code'], observed=True).cumprod()
    glast = gc.groupby(df['ts_code'], observed=True).transform('last')
    adjf = (glast / gc).astype(np.float64)
    df['adjf'] = adjf

    for c in ('open', 'high', 'low', 'close'):
        df['a_' + c] = (df[c].astype(np.float64) * adjf).astype(np.float32)

    # ── 涨停识别
    boards = df['ts_code'].astype(str).map(board_of)
    df['board'] = boards.astype('category')
    r_main = np.where(boards.values == 'MAIN', 0.10,
                      np.where(boards.values == 'BSE', 0.30, 0.20))
    lim = np.round(df['pre_close'].values.astype(np.float64) * (1.0 + r_main), 2)
    df['limit_price'] = lim
    df['limit_pct'] = (r_main * 100).astype(np.float32)
    valid = df['seq'].values >= 10
    df['is_limit_up'] = (valid & (np.abs(df['close'].values - lim) < 0.005)
                         & (df['pct_chg'].values >= r_main * 100 - 0.6))
    # 触板（盘中触及涨停价但未收在涨停）
    df['touch_limit'] = (valid & (np.abs(df['high'].values - lim) < 0.005))
    df['broken_limit'] = df['touch_limit'] & (~df['is_limit_up'])
    # 一字板 / 开过板
    df['one_word'] = df['is_limit_up'] & (np.abs(df['open'].values - lim) < 0.005) \
        & (np.abs(df['low'].values - lim) < 0.005)
    df['opened_board'] = df['is_limit_up'] & (df['low'].values < lim - 0.005)
    # 主板 ST 候选（5% 限幅）——仅用于首板 lookback 的保守排他
    lim5 = np.round(df['pre_close'].values.astype(np.float64) * 1.05, 2)
    df['is_limit_up_5'] = ((df['board'].values == 'MAIN') & valid
                           & (np.abs(df['close'].values - lim5) < 0.005)
                           & (df['pct_chg'].values >= 4.4))

    # ── 均线 / 均量（基于前复权价）
    for w in (5, 10, 20, 60):
        df['ma' + str(w)] = g['a_close'].transform(
            lambda s, w=w: s.rolling(w, min_periods=w).mean()).astype(np.float32)
    df['vol_ma20'] = g['vol'].transform(
        lambda s: s.rolling(20, min_periods=20).mean()).astype(np.float32)
    df['vol_ma5'] = g['vol'].transform(
        lambda s: s.rolling(5, min_periods=5).mean()).astype(np.float32)
    df['vr20'] = (df['vol'] / df['vol_ma20']).astype(np.float32)
    # 前 20 日累计涨幅（用于首板前的趋势背景）
    df['ret20_prev'] = g['a_close'].transform(
        lambda s: s / s.shift(20) - 1.0).astype(np.float32)

    for c in ('open', 'high', 'low', 'close', 'pre_close', 'vol', 'amount', 'pct_chg'):
        df[c] = df[c].astype(np.float32)
    df['trade_date'] = df['trade_date'].astype(np.int32)

    keep = ['ts_code', 'trade_date', 'seq', 'board', 'open', 'high', 'low', 'close', 'pre_close',
            'pct_chg', 'vol', 'amount', 'adjf', 'a_open', 'a_high', 'a_low', 'a_close',
            'limit_price', 'limit_pct', 'is_limit_up', 'touch_limit', 'broken_limit',
            'one_word', 'opened_board', 'is_limit_up_5', 'ma5', 'ma10', 'ma20', 'ma60',
            'vol_ma5', 'vol_ma20', 'vr20', 'ret20_prev']
    df = df[keep]
    p = os.path.join(OUT, "panel.parquet")
    df.to_parquet(p, index=False, compression='zstd')
    print("已写", p, os.path.getsize(p) / 1e6, "MB", len(df), "行")

    # ── 静态信息
    sb = pd.read_csv(os.path.join(CD, "stock_basic.csv"), dtype=str)
    sb['is_st_name'] = sb['name'].fillna('').str.contains('ST', case=True)
    sb['is_delisted'] = sb['name'].fillna('').str.contains('退', case=True)
    sb.to_parquet(os.path.join(OUT, "basic.parquet"), index=False)
    print("已写 basic.parquet", len(sb))

    # ── 交易日历
    td = np.sort(df['trade_date'].unique())
    pd.DataFrame({'trade_date': td}).to_csv(os.path.join(OUT, "trade_dates.csv"), index=False)
    print("交易日数:", len(td), td[0], td[-1])

    # ── 指数
    conn = sqlite3.connect(DB, timeout=60)
    idx = pd.read_sql_query(
        "SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount "
        f"FROM index_daily_cache WHERE trade_date >= '{START}' AND trade_date <= '{END}'", conn)
    conn.close()
    idx.to_parquet(os.path.join(OUT, "index.parquet"), index=False)
    print("已写 index.parquet", len(idx), idx['ts_code'].nunique())


if __name__ == '__main__':
    main()
