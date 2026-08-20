# -*- coding: utf-8 -*-
"""
「猎尾V5」ND2 Snapshot Store
1. 14:50 保存完整快照(特征+评分+形态)
2. 次日盘后回填标签: Y_UP_2 / Y_CLOSE_2 / Y_DD_2 + 实际收益率
3. 供 ND2Engine 历史分桶统计使用

表结构:
  nd2_snapshot: 每日14:50候选快照(含正负样本)
  nd2_label:    次日实际表现标签
"""

import os
import json
import sqlite3
from datetime import datetime

from nd2_config import SNAPSHOT


class ND2SnapshotStore:

    def __init__(self, db_path=None):
        self.db_path = db_path or SNAPSHOT['db_path']
        self._ensure_dir()
        self._init_tables()

    def _ensure_dir(self):
        d = os.path.dirname(self.db_path)
        if d and not os.path.isdir(d):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass

    def _conn(self):
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _init_tables(self):
        try:
            conn = self._conn()
            conn.execute(f'''
                CREATE TABLE IF NOT EXISTS {SNAPSHOT['table']} (
                    signal_date     TEXT NOT NULL,
                    signal_time     TEXT,
                    ts_code         TEXT NOT NULL,
                    name            TEXT,
                    theme           TEXT,
                    pattern         TEXT,
                    final_score     INTEGER,
                    grade           TEXT,
                    trend_structure INTEGER,
                    pattern_quality INTEGER,
                    tail_flow       INTEGER,
                    strong_gene     INTEGER,
                    nd2_score       INTEGER,
                    theme_alpha     INTEGER,
                    market_alpha    INTEGER,
                    bonus           INTEGER,
                    risk_penalty    INTEGER,
                    p_up_2          REAL,
                    p_close_2       REAL,
                    p_dd_2          REAL,
                    prob_confidence REAL,
                    sample_size     INTEGER,
                    market_status   TEXT,
                    entry_price     REAL,
                    -- 14:50关键价格快照
                    open_px         REAL,
                    high_px         REAL,
                    low_px          REAL,
                    close_px        REAL,
                    pct_chg         REAL,
                    price_1420      REAL,
                    price_1430      REAL,
                    volume          REAL,
                    tail_volume     REAL,
                    tail_volume_ratio REAL,
                    tail_return     REAL,
                    close_position  REAL,
                    turnover        REAL,
                    detail_json     TEXT,
                    PRIMARY KEY (signal_date, ts_code)
                )
            ''')
            conn.execute(f'''
                CREATE TABLE IF NOT EXISTS {SNAPSHOT['label_table']} (
                    signal_date       TEXT NOT NULL,
                    ts_code           TEXT NOT NULL,
                    next_trade_date   TEXT,
                    entry_price       REAL,
                    next_open         REAL,
                    next_high         REAL,
                    next_low          REAL,
                    next_close        REAL,
                    next_high_return  REAL,
                    next_close_return REAL,
                    next_low_return   REAL,
                    Y_UP_2            INTEGER,
                    Y_CLOSE_2         INTEGER,
                    Y_DD_2            INTEGER,
                    updated_at        TEXT,
                    PRIMARY KEY (signal_date, ts_code)
                )
            ''')
            conn.execute(f'CREATE INDEX IF NOT EXISTS idx_nd2_snap_date ON {SNAPSHOT["table"]}(signal_date)')
            conn.execute(f'CREATE INDEX IF NOT EXISTS idx_nd2_label_date ON {SNAPSHOT["label_table"]}(signal_date)')
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠ ND2快照表初始化失败: {e}")

    # ══════════════════════════════════════════
    # 快照保存
    # ══════════════════════════════════════════
    def save_snapshot(self, signal_date, signals, market_status='正常市场'):
        """
        保存14:50候选快照(含正负样本,>=min_score_to_save)
        signals: ND2AlphaEngine.evaluate 输出列表
        """
        if not signals:
            return 0
        min_score = SNAPSHOT['min_score_to_save']
        max_n = SNAPSHOT['max_stocks_per_day']
        # 按分数降序取前N(保留高分正样本为主,同时保留60分以上负样本)
        sorted_signals = sorted(signals, key=lambda s: -s.get('final_score', 0))
        to_save = [s for s in sorted_signals if s.get('final_score', 0) >= min_score][:max_n]
        if not to_save:
            return 0

        now_str = datetime.now().strftime('%H:%M:%S')
        try:
            conn = self._conn()
            for s in to_save:
                d = s.get('detail', {})
                tf = d.get('tailflow', {})
                conn.execute(f'''
                    INSERT OR REPLACE INTO {SNAPSHOT['table']} (
                        signal_date, signal_time, ts_code, name, theme, pattern,
                        final_score, grade, trend_structure, pattern_quality, tail_flow,
                        strong_gene, nd2_score, theme_alpha, market_alpha, bonus, risk_penalty,
                        p_up_2, p_close_2, p_dd_2, prob_confidence, sample_size,
                        market_status, entry_price,
                        open_px, high_px, low_px, close_px, pct_chg,
                        price_1420, price_1430, volume, tail_volume, tail_volume_ratio,
                        tail_return, close_position, turnover, detail_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (
                    signal_date, now_str, s['ts_code'], s.get('name', ''), s.get('theme', ''),
                    s.get('pattern', ''), s.get('final_score', 0), s.get('grade', ''),
                    s.get('trend_structure', 0), s.get('pattern_quality', 0), s.get('tail_flow', 0),
                    s.get('strong_gene', 0), s.get('nd2_potential', 0), s.get('theme_alpha', 0),
                    s.get('market_alpha', 0), s.get('bonus', 0), s.get('risk_penalty', 0),
                    s.get('p_up_2'), s.get('p_close_2'), s.get('p_dd_2'),
                    s.get('probability_confidence'), s.get('sample_size', 0),
                    market_status, s.get('price', 0),
                    d.get('open'), d.get('high'), d.get('low'), d.get('price'), s.get('pct_chg', 0),
                    d.get('noon_price'), d.get('tail_base_price'), d.get('cur_vol'),
                    d.get('tail_inc_vol'), tf.get('tail_volume_ratio'),
                    tf.get('tail_return'), tf.get('close_position'),
                    d.get('turnover'),
                    json.dumps(s.get('detail', {}), ensure_ascii=False, default=str),
                ))
            conn.commit()
            conn.close()
            return len(to_save)
        except Exception as e:
            print(f"⚠ ND2快照保存失败: {e}")
            return 0

    # ══════════════════════════════════════════
    # 次日标签回填
    # ══════════════════════════════════════════
    def backfill_labels(self, fetch_daily_func, signal_date, next_date):
        """
        回填指定日期快照的次日标签
        fetch_daily_func(ts_code, start_date, end_date) -> DataFrame(trade_date, open, high, low, close)
            由调用方注入数据源(优先本地缓存 stock_cache.get_daily_cache)
        """
        try:
            conn = self._conn()
            rows = conn.execute(f'''
                SELECT ts_code, entry_price FROM {SNAPSHOT['table']}
                WHERE signal_date = ?
            ''', (signal_date,)).fetchall()
            if not rows:
                conn.close()
                return 0

            target_pct = SNAPSHOT['target_pct']
            dd_pct = SNAPSHOT['drawdown_pct']
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            filled = 0
            for ts_code, entry_price in rows:
                if not entry_price or entry_price <= 0:
                    continue
                df = fetch_daily_func(ts_code, next_date, next_date)
                if df is None or df.empty:
                    continue
                try:
                    row = df.iloc[0]
                    n_open = float(row.get('open', 0))
                    n_high = float(row.get('high', 0))
                    n_low = float(row.get('low', 0))
                    n_close = float(row.get('close', 0))
                except (TypeError, ValueError, IndexError):
                    continue
                if n_high <= 0 or n_close <= 0:
                    continue
                hi_ret = n_high / entry_price - 1
                cl_ret = n_close / entry_price - 1
                lo_ret = n_low / entry_price - 1 if n_low > 0 else 0
                conn.execute(f'''
                    INSERT OR REPLACE INTO {SNAPSHOT['label_table']} (
                        signal_date, ts_code, next_trade_date, entry_price,
                        next_open, next_high, next_low, next_close,
                        next_high_return, next_close_return, next_low_return,
                        Y_UP_2, Y_CLOSE_2, Y_DD_2, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (
                    signal_date, ts_code, next_date, entry_price,
                    n_open, n_high, n_low, n_close,
                    round(hi_ret, 5), round(cl_ret, 5), round(lo_ret, 5),
                    1 if hi_ret >= target_pct else 0,
                    1 if cl_ret >= target_pct else 0,
                    1 if lo_ret <= dd_pct else 0,
                    now_str,
                ))
                filled += 1
            conn.commit()
            conn.close()
            return filled
        except Exception as e:
            print(f"⚠ ND2标签回填失败: {e}")
            return 0

    def pending_backfill_dates(self, latest_done_date=None):
        """查询待回填的快照日期(signal_date有快照但label缺失,且信号日早于最新交易日)"""
        try:
            conn = self._conn()
            if latest_done_date:
                rows = conn.execute(f'''
                    SELECT DISTINCT signal_date FROM {SNAPSHOT['table']}
                    WHERE signal_date NOT IN (SELECT DISTINCT signal_date FROM {SNAPSHOT['label_table']})
                      AND signal_date < ?
                    ORDER BY signal_date
                ''', (latest_done_date,)).fetchall()
            else:
                rows = conn.execute(f'''
                    SELECT DISTINCT signal_date FROM {SNAPSHOT['table']}
                    WHERE signal_date NOT IN (SELECT DISTINCT signal_date FROM {SNAPSHOT['label_table']})
                    ORDER BY signal_date
                ''').fetchall()
            conn.close()
            return [r[0] for r in rows]
        except Exception:
            return []
