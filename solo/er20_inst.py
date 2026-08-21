# -*- coding: utf-8 -*-
"""
ER20 机构软加分模块（北向持股）
================================
用户偏好机构股，外资(北向)权重最大。作为 alpha 软加分项：
  er20_base += inst_adj   (0~+5，纯加分不误杀)

北向持股快照按季末日从 tushare hk_hold 批量拉取（非季末日该接口
只返回港股通数据，A 股全量仅在季末可得），本地 parquet 缓存。
生产版取扫描日最近已披露季末（如 20260820 → 20260630）；
回测版取扫描窗口前最近季末（2025H1 → 20250630，无前视）。
"""
import os
import pandas as pd

CACHE_DIR = r'D:\mystock\cache_daily'


def _read_token():
    token = os.environ.get('TUSHARE_TOKEN', '')
    if token:
        return token
    for p in (r'D:\mystock\config\.env', r'D:\mystock\solo\.env'):
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('TUSHARE_TOKEN='):
                        return line.split('=', 1)[1].strip().strip('"\'')
    return ''


def load_northbound_snapshot(trade_date, cache_dir=CACHE_DIR, force=False):
    """按季末 trade_date 拉取北向持股快照，返回 {ts_code: ratio(%)}
    缓存文件: northbound_{trade_date}.parquet
    """
    fp = os.path.join(cache_dir, f'northbound_{trade_date}.parquet')
    if os.path.exists(fp) and not force:
        try:
            df = pd.read_parquet(fp)
            return dict(zip(df['ts_code'], df['ratio']))
        except Exception:
            pass
    token = _read_token()
    if not token:
        raise RuntimeError('TUSHARE_TOKEN 未配置，无法拉取北向快照')
    import tushare as ts
    ts.set_token(token)
    pro = ts.pro_api()
    df = pro.hk_hold(trade_date=trade_date, fields='ts_code,trade_date,vol,ratio')
    if df is None or not len(df):
        raise RuntimeError(f'北向快照 {trade_date} 无数据')
    df = df[df['ts_code'].str.endswith(('.SH', '.SZ'))].copy()
    df['ratio'] = df['ratio'].astype(float).round(2)
    os.makedirs(cache_dir, exist_ok=True)
    df.to_parquet(fp, index=False)
    return dict(zip(df['ts_code'], df['ratio']))


def inst_adj_score(ratio):
    """北向持股比例(%) → 软加分 0~+5（外资权重最大）
    阈值参考 20250630 分位(25%=0.15 / 50%=0.57 / 75%=1.26)与绝对档位
    """
    if ratio is None or pd.isna(ratio) or ratio <= 0:
        return 0.0
    if ratio >= 3.0:
        return 5.0
    if ratio >= 1.5:
        return 4.0
    if ratio >= 0.8:
        return 3.0
    if ratio >= 0.4:
        return 2.0
    if ratio >= 0.15:
        return 1.0
    return 0.5
