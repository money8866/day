# -*- coding: utf-8 -*-
"""
Pattern Database — 历史回撤模式数据库

管理3张核心表：
  1. pattern_history: 每日回撤候选标的的全量特征 + 未来收益标签
  2. daily_feature_snapshot: 每日全市场候选股票的截面因子快照
  3. factor_performance: 因子IC、Rank IC、胜率、分层收益统计

设计原则：
  - 只记录，不预测（数据由每日运行管线按需写入）
  - 写入幂等（INSERT OR REPLACE）
  - 查询索引优化（按 trade_date、ts_code、market_regime 等）
"""

import os
import sys
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import stock_cache as sc

# 数据库路径 — 与主缓存 DB 同目录
PATTERN_DB_PATH = os.path.join(os.path.dirname(sc.DB_PATH) if sc.DB_PATH else r"D:\mystock\cache_daily",
                               "pattern_data.db")


# ──────────────────────────────────────────────
# 数据库连接
# ──────────────────────────────────────────────

@contextmanager
def get_conn(max_retries=3, retry_delay=0.5):
    for attempt in range(max_retries):
        conn = sqlite3.connect(PATTERN_DB_PATH, timeout=10.0)
        try:
            yield conn
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if 'database is locked' in str(e) and attempt < max_retries - 1:
                conn.close()
                import time
                time.sleep(retry_delay * (attempt + 1))
                continue
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ──────────────────────────────────────────────
# 初始化建表
# ──────────────────────────────────────────────

INIT_SQL = """
-- 表1: 历史回撤模式
CREATE TABLE IF NOT EXISTS pattern_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 股票标识
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,

    -- 市场状态
    market_regime TEXT,
    market_score REAL,
    risk_appetite REAL,
    heat_score REAL,

    -- 主题
    theme TEXT,
    theme_rank INTEGER,
    theme_strength REAL,

    -- 个股特征
    pattern_type TEXT DEFAULT 'PULLBACK_ALPHA',  -- V6.2: PULLBACK/BREAKOUT/ROTATION/REBOUND/PRE_ROTATE
    entry_type TEXT DEFAULT 'pullback',  -- pullback / leader / cross_sectional
    leader_rank INTEGER,
    alpha_rank INTEGER,
    cross_sectional_rank INTEGER,       -- 截面排序排名
    ret_60d REAL,
    max_drawdown REAL,
    pullback_ma TEXT,
    dist_to_ma REAL,
    atr REAL,
    turnover_rate REAL,
    amount REAL,

    -- 资金
    smart_money_score REAL,
    moneyflow REAL,
    volume_change REAL,

    -- 结果标签（未来收益）
    future_5_return REAL,
    future_10_return REAL,
    future_20_return REAL,
    future_max_drawdown REAL,
    holding_days INTEGER,
    success_flag INTEGER,  -- 1=成功（未来10日涨跌幅>0）, 0=失败

    UNIQUE(ts_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_pattern_trade_date ON pattern_history(trade_date);
CREATE INDEX IF NOT EXISTS idx_pattern_ts_code ON pattern_history(ts_code);
CREATE INDEX IF NOT EXISTS idx_pattern_regime ON pattern_history(market_regime);
CREATE INDEX IF NOT EXISTS idx_pattern_theme ON pattern_history(theme);

-- 表2: 每日因子快照
CREATE TABLE IF NOT EXISTS daily_feature_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,

    -- 动量因子
    mom_5d REAL,
    mom_20d REAL,
    mom_60d REAL,
    mom_accel REAL,

    -- 量价因子
    volume_ratio REAL,
    turnover_rate REAL,
    amount REAL,
    volume_price_score REAL,

    -- 趋势因子
    ma5 REAL,
    ma10 REAL,
    ma20 REAL,
    ma60 REAL,
    dist_ma20 REAL,
    trend_quality REAL,

    -- 资金因子
    net_inflow_5d REAL,
    large_order_intensity REAL,
    smart_money_score REAL,

    -- 风险因子
    volatility_20d REAL,
    max_drawdown_20d REAL,
    atr REAL,

    -- 基本面
    market_cap REAL,
    pe REAL,
    profit_growth REAL,

    UNIQUE(ts_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_date ON daily_feature_snapshot(trade_date);
CREATE INDEX IF NOT EXISTS idx_snapshot_code ON daily_feature_snapshot(ts_code);

-- 表3: 因子表现统计
CREATE TABLE IF NOT EXISTS factor_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_name TEXT NOT NULL,
    calc_date TEXT NOT NULL,
    lookback_days INTEGER DEFAULT 60,

    ic REAL,
    rank_ic REAL,
    win_rate REAL,
    top_quartile_return REAL,
    bottom_quartile_return REAL,
    long_short_return REAL,

    UNIQUE(factor_name, calc_date, lookback_days)
);

CREATE INDEX IF NOT EXISTS idx_factor_name ON factor_performance(factor_name);
CREATE INDEX IF NOT EXISTS idx_factor_date ON factor_performance(calc_date);
"""


def init_db():
    """初始化数据库（建表 + 迁移旧表）"""
    with get_conn() as conn:
        conn.executescript(INIT_SQL)
        # 迁移: 添加 entry_type 列（若不存在）
        try:
            conn.execute("ALTER TABLE pattern_history ADD COLUMN entry_type TEXT DEFAULT 'pullback'")
        except sqlite3.OperationalError:
            pass  # 列已存在
        try:
            conn.execute("ALTER TABLE pattern_history ADD COLUMN cross_sectional_rank INTEGER")
        except sqlite3.OperationalError:
            pass
        # V6.2: 添加 pattern_type 列（若不存在）
        try:
            conn.execute("ALTER TABLE pattern_history ADD COLUMN pattern_type TEXT DEFAULT 'PULLBACK_ALPHA'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pattern_type ON pattern_history(pattern_type)")
        except sqlite3.OperationalError:
            pass
    print(f"[PatternDB] 数据库初始化完成: {PATTERN_DB_PATH}")


# ──────────────────────────────────────────────
# 写入操作
# ──────────────────────────────────────────────

def save_pattern_record(record: Dict):
    """写入一条回撤模式记录（幂等插入）"""
    sql = """
    INSERT OR REPLACE INTO pattern_history (
        ts_code, trade_date,
        market_regime, market_score, risk_appetite, heat_score,
        theme, theme_rank, theme_strength,
        pattern_type, entry_type, leader_rank, alpha_rank, cross_sectional_rank,
        ret_60d, max_drawdown, pullback_ma, dist_to_ma,
        atr, turnover_rate, amount,
        smart_money_score, moneyflow, volume_change,
        future_5_return, future_10_return, future_20_return,
        future_max_drawdown, holding_days, success_flag
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    with get_conn() as conn:
        conn.execute(sql, (
            record.get('ts_code'),
            record.get('trade_date'),
            record.get('market_regime'),
            record.get('market_score'),
            record.get('risk_appetite'),
            record.get('heat_score'),
            record.get('theme'),
            record.get('theme_rank'),
            record.get('theme_strength'),
            record.get('pattern_type', 'PULLBACK_ALPHA'),
            record.get('entry_type', 'pullback'),
            record.get('leader_rank'),
            record.get('alpha_rank'),
            record.get('cross_sectional_rank'),
            record.get('ret_60d'),
            record.get('max_drawdown'),
            record.get('pullback_ma'),
            record.get('dist_to_ma'),
            record.get('atr'),
            record.get('turnover_rate'),
            record.get('amount'),
            record.get('smart_money_score'),
            record.get('moneyflow'),
            record.get('volume_change'),
            record.get('future_5_return'),
            record.get('future_10_return'),
            record.get('future_20_return'),
            record.get('future_max_drawdown'),
            record.get('holding_days'),
            record.get('success_flag'),
        ))


def batch_save_pattern_records(records: List[Dict]):
    """批量写入回撤模式记录"""
    if not records:
        return
    sql = """
    INSERT OR REPLACE INTO pattern_history (
        ts_code, trade_date,
        market_regime, market_score, risk_appetite, heat_score,
        theme, theme_rank, theme_strength,
        pattern_type, entry_type, leader_rank, alpha_rank, cross_sectional_rank,
        ret_60d, max_drawdown, pullback_ma, dist_to_ma,
        atr, turnover_rate, amount,
        smart_money_score, moneyflow, volume_change,
        future_5_return, future_10_return, future_20_return,
        future_max_drawdown, holding_days, success_flag
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    rows = [(
        r.get('ts_code'), r.get('trade_date'),
        r.get('market_regime'), r.get('market_score'), r.get('risk_appetite'), r.get('heat_score'),
        r.get('theme'), r.get('theme_rank'), r.get('theme_strength'),
        r.get('pattern_type', 'PULLBACK_ALPHA'), r.get('entry_type', 'pullback'),
        r.get('leader_rank'), r.get('alpha_rank'), r.get('cross_sectional_rank'),
        r.get('ret_60d'), r.get('max_drawdown'), r.get('pullback_ma'), r.get('dist_to_ma'),
        r.get('atr'), r.get('turnover_rate'), r.get('amount'),
        r.get('smart_money_score'), r.get('moneyflow'), r.get('volume_change'),
        r.get('future_5_return'), r.get('future_10_return'), r.get('future_20_return'),
        r.get('future_max_drawdown'), r.get('holding_days'), r.get('success_flag'),
    ) for r in records]
    with get_conn() as conn:
        conn.executemany(sql, rows)


def save_snapshot_records(records: List[Dict]):
    """批量写入每日因子快照"""
    if not records:
        return
    sql = """
    INSERT OR REPLACE INTO daily_feature_snapshot (
        ts_code, trade_date,
        mom_5d, mom_20d, mom_60d, mom_accel,
        volume_ratio, turnover_rate, amount, volume_price_score,
        ma5, ma10, ma20, ma60, dist_ma20, trend_quality,
        net_inflow_5d, large_order_intensity, smart_money_score,
        volatility_20d, max_drawdown_20d, atr,
        market_cap, pe, profit_growth
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    rows = [(
        r.get('ts_code'), r.get('trade_date'),
        r.get('mom_5d'), r.get('mom_20d'), r.get('mom_60d'), r.get('mom_accel'),
        r.get('volume_ratio'), r.get('turnover_rate'), r.get('amount'), r.get('volume_price_score'),
        r.get('ma5'), r.get('ma10'), r.get('ma20'), r.get('ma60'),
        r.get('dist_ma20'), r.get('trend_quality'),
        r.get('net_inflow_5d'), r.get('large_order_intensity'), r.get('smart_money_score'),
        r.get('volatility_20d'), r.get('max_drawdown_20d'), r.get('atr'),
        r.get('market_cap'), r.get('pe'), r.get('profit_growth'),
    ) for r in records]
    with get_conn() as conn:
        conn.executemany(sql, rows)


def save_factor_performance(record: Dict):
    """写入因子表现统计"""
    sql = """
    INSERT OR REPLACE INTO factor_performance (
        factor_name, calc_date, lookback_days,
        ic, rank_ic, win_rate,
        top_quartile_return, bottom_quartile_return, long_short_return
    ) VALUES (?,?,?,?,?,?,?,?,?)
    """
    with get_conn() as conn:
        conn.execute(sql, (
            record.get('factor_name'),
            record.get('calc_date'),
            record.get('lookback_days', 60),
            record.get('ic'),
            record.get('rank_ic'),
            record.get('win_rate'),
            record.get('top_quartile_return'),
            record.get('bottom_quartile_return'),
            record.get('long_short_return'),
        ))


# ──────────────────────────────────────────────
# 查询操作 - 模式匹配
# ──────────────────────────────────────────────

def query_similar_patterns(
    market_regime: str = None,
    pullback_ma: str = None,
    theme: str = None,
    pattern_type: str = None,     # V6.2 分桶过滤
    drawdown_min: float = None,
    drawdown_max: float = None,
    ret_60d_min: float = None,
    min_samples: int = 5,
    max_results: int = 500,
) -> pd.DataFrame:
    """查询历史相似模式

    Args:
        market_regime: 市场状态过滤
        pullback_ma: 回踩均线类型过滤
        theme: 主题过滤（部分匹配）
        pattern_type: V6.2 模式类型过滤 (PULLBACK_ALPHA/BREAKOUT_ALPHA/etc)
        drawdown_min: 最小回撤幅度
        drawdown_max: 最大回撤幅度
        ret_60d_min: 最小60日涨幅
        min_samples: 最低样本数
        max_results: 最大返回数

    Returns:
        DataFrame with pattern_history columns
    """
    conditions = ["1=1"]
    params = []

    if market_regime:
        conditions.append("market_regime = ?")
        params.append(market_regime)
    if pullback_ma:
        conditions.append("pullback_ma = ?")
        params.append(pullback_ma)
    if theme:
        conditions.append("theme LIKE ?")
        params.append(f"%{theme}%")
    if pattern_type:
        conditions.append("pattern_type = ?")
        params.append(pattern_type)
    if drawdown_min is not None:
        conditions.append("max_drawdown >= ?")
        params.append(drawdown_min)
    if drawdown_max is not None:
        conditions.append("max_drawdown <= ?")
        params.append(drawdown_max)
    if ret_60d_min is not None:
        conditions.append("ret_60d >= ?")
        params.append(ret_60d_min)

    sql = f"""
    SELECT * FROM pattern_history
    WHERE {' AND '.join(conditions)}
    ORDER BY trade_date DESC
    LIMIT {max_results}
    """
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    return df


def query_pattern_stats(
    market_regime: str = None,
    pullback_ma: str = None,
    theme: str = None,
    pattern_type: str = None,     # V6.2 分桶过滤
    drawdown_min: float = None,
    drawdown_max: float = None,
    ret_60d_min: float = None,
    min_samples: int = 5,
) -> Dict:
    """查询历史模式的汇总统计

    返回:
        {
            'n_samples': int,
            'win_probability': float,
            'avg_return_5d': float,
            'avg_return_10d': float,
            'avg_return_20d': float,
            'median_return_10d': float,
            'avg_max_drawdown': float,
            'avg_holding_days': float,
            'avg_win_return': float,
            'avg_loss_return': float,
            # === V6.1 Confidence fields ===
            'confidence': float,            # 综合置信度 0.0~1.0
            'sample_size_confidence': float, # 样本量置信度
            'recency_score': float,          # 时效性评分 0.0~1.0
            'match_quality': float,          # 匹配质量 0.0~1.0
            'recency_weighted_win_rate': float,  # 时效加权胜率
        }
    """
    df = query_similar_patterns(
        market_regime=market_regime,
        pullback_ma=pullback_ma,
        theme=theme,
        pattern_type=pattern_type,
        drawdown_min=drawdown_min,
        drawdown_max=drawdown_max,
        ret_60d_min=ret_60d_min,
        min_samples=min_samples,
    )
    result = {
        'n_samples': 0,
        'win_probability': 0.5,
        'avg_return_5d': 0.0,
        'avg_return_10d': 0.0,
        'avg_return_20d': 0.0,
        'median_return_10d': 0.0,
        'avg_max_drawdown': 0.0,
        'avg_holding_days': 0,
        'avg_win_return': 0.0,
        'avg_loss_return': 0.0,
        # V6.1 Confidence fields
        'confidence': 0.0,
        'sample_size_confidence': 0.0,
        'recency_score': 0.0,
        'match_quality': 1.0,
        'recency_weighted_win_rate': 0.5,
    }

    if df is None or df.empty or len(df) < min_samples:
        return result

    n = len(df)
    result['n_samples'] = n

    # ── 胜率 ──
    success = df[df['success_flag'] == 1]
    failure = df[df['success_flag'] == 0]
    raw_win_rate = float(len(success)) / float(n) if n > 0 else 0.5
    result['win_probability'] = raw_win_rate

    # 平均收益
    for period in ['5', '10', '20']:
        col = f'future_{period}_return'
        if col in df.columns:
            result[f'avg_return_{period}d'] = float(df[col].mean())
    if 'future_10_return' in df.columns:
        result['median_return_10d'] = float(df['future_10_return'].median())

    # 最大回撤
    if 'future_max_drawdown' in df.columns:
        result['avg_max_drawdown'] = float(df['future_max_drawdown'].mean())

    # 持有天数
    if 'holding_days' in df.columns:
        result['avg_holding_days'] = float(df['holding_days'].mean())

    # 成功/失败平均收益
    if not success.empty and 'future_10_return' in success.columns:
        result['avg_win_return'] = float(success['future_10_return'].mean())
    if not failure.empty and 'future_10_return' in failure.columns:
        result['avg_loss_return'] = float(failure['future_10_return'].mean())

    # ════════════════════════════════════════════
    # V6.1 Confidence 计算
    # ════════════════════════════════════════════

    # 1. 样本量置信度: sigmoid(n/20), 20个样本接近饱和
    sample_conf = 1.0 / (1.0 + np.exp(-(n - 10) / 4.0))
    result['sample_size_confidence'] = round(sample_conf, 4)

    # 2. 时效性评分: 最近样本权重高
    if 'trade_date' in df.columns:
        dates = pd.to_datetime(df['trade_date'].astype(str))
        latest_date = dates.max()
        days_ago = (latest_date - dates).dt.days.values.astype(float)
        # 指数衰减: 30天内权重1.0, 半年0.5, 一年0.2
        recency_weights = np.exp(-days_ago / 90.0)
        result['recency_score'] = round(float(recency_weights.mean()), 4)

        # 3. 时效加权胜率
        success_flags = df['success_flag'].values.astype(float)
        weighted_win = np.sum(recency_weights * success_flags) / np.sum(recency_weights) if np.sum(recency_weights) > 0 else raw_win_rate
        result['recency_weighted_win_rate'] = round(float(weighted_win), 4)
    else:
        result['recency_score'] = 0.5
        result['recency_weighted_win_rate'] = raw_win_rate

    # 4. 综合置信度: 0.40×样本量 + 0.35×时效性 + 0.25×匹配质量(默认1.0)
    result['confidence'] = round(
        0.40 * result['sample_size_confidence'] +
        0.35 * result['recency_score'] +
        0.25 * result['match_quality'],
        4
    )

    return result


# ──────────────────────────────────────────────
# 标签回填（在future_date更新记录）
# ──────────────────────────────────────────────

def backfill_labels(trade_date: str, lookahead: int = 20):
    """回填未来收益标签

    在 T日 + lookahead 天后调用，读取T日记录的股票在
    [T+1, T+lookahead] 区间的实际收益，更新到 pattern_history。

    通常由每日管线在收盘后自动触发。
    """
    # 查询 trade_date 有记录但 future_10_return 为 NULL 的股票
    sql = """
    SELECT ts_code, trade_date FROM pattern_history
    WHERE trade_date = ? AND future_10_return IS NULL
    """
    with get_conn() as conn:
        pending = pd.read_sql_query(sql, conn, params=[trade_date])

    if pending is None or pending.empty:
        return 0

    end_date = (pd.to_datetime(trade_date) + timedelta(days=lookahead + 10)).strftime('%Y%m%d')
    updated = 0

    for _, row in pending.iterrows():
        code = row['ts_code']
        rec_date = row['trade_date']
        try:
            df = sc.cached_stk_factor_pro(code, rec_date, end_date, silent=True)
            if df is None or df.empty:
                continue

            df = df.sort_values('trade_date').reset_index(drop=True)
            close_col = 'close_hfq' if 'close_hfq' in df.columns else 'close'
            prices = df[close_col].values

            # 找到 rec_date 的位置
            date_idx = None
            for i, d in enumerate(df['trade_date'].astype(str)):
                if d == rec_date:
                    date_idx = i
                    break

            if date_idx is None or date_idx >= len(prices) - 1:
                continue

            entry_price = prices[date_idx]

            # 计算未来收益
            future_5 = (prices[min(date_idx + 5, len(prices)-1)] / entry_price - 1) if date_idx + 5 < len(prices) else None
            future_10 = (prices[min(date_idx + 10, len(prices)-1)] / entry_price - 1) if date_idx + 10 < len(prices) else None
            future_20 = (prices[min(date_idx + 20, len(prices)-1)] / entry_price - 1) if date_idx + 20 < len(prices) else None

            # 最大回撤（从 entry 往后）
            future_prices = prices[date_idx+1:]
            if len(future_prices) > 0:
                peak = np.maximum.accumulate(future_prices)
                drawdowns = (future_prices - peak) / peak
                future_max_dd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0
            else:
                future_max_dd = 0.0

            # 持有天数（到达最高收益的天数）
            price_series = prices[date_idx:]
            if len(price_series) > 1:
                max_price_idx = np.argmax(price_series)
                holding = int(max_price_idx)
            else:
                holding = 0

            success = 1 if future_10 is not None and future_10 > 0 else 0

            update_sql = """
            UPDATE pattern_history SET
                future_5_return = ?, future_10_return = ?, future_20_return = ?,
                future_max_drawdown = ?, holding_days = ?, success_flag = ?
            WHERE ts_code = ? AND trade_date = ?
            """
            with get_conn() as conn2:
                conn2.execute(update_sql, (
                    future_5, future_10, future_20,
                    future_max_dd, holding, success,
                    code, rec_date
                ))
            updated += 1
        except Exception:
            continue

    return updated


# ──────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────

def get_db_size_mb() -> float:
    """获取数据库文件大小（MB）"""
    try:
        return os.path.getsize(PATTERN_DB_PATH) / (1024 * 1024)
    except OSError:
        return 0.0


def get_record_count() -> Dict[str, int]:
    """获取各表的记录数"""
    counts = {}
    with get_conn() as conn:
        for table in ['pattern_history', 'daily_feature_snapshot', 'factor_performance']:
            try:
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = cursor.fetchone()[0]
            except Exception:
                counts[table] = 0
    return counts


# 启动时自动建表
init_db()

if __name__ == '__main__':
    print(f"[PatternDB] 数据库路径: {PATTERN_DB_PATH}")
    print(f"[PatternDB] 文件大小: {get_db_size_mb():.2f} MB")
    print(f"[PatternDB] 记录数: {get_record_count()}")
