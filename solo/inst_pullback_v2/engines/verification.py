import os
import sys
import sqlite3
import json
import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.loader import DataLoader, load_config


@dataclass
class VerificationRecord:
    ts_code: str
    name: str
    theme: str
    entry_date: str
    alpha: float
    market_state: str
    market_score: float
    theme_strength: float
    leader_score: float
    pullback_quality: float
    etf_resonance_score: float
    chip_stability: float
    fund_flow_recovery: float
    trend_health_score: float
    ret_5d: float = None
    ret_10d: float = None
    ret_20d: float = None
    ret_40d: float = None
    max_profit: float = None
    max_drawdown: float = None
    hit_10pct: int = 0
    hit_20pct: int = 0
    hit_30pct: int = 0


class VerificationEngine:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['verification']
        self.loader = DataLoader()
        self.db_path = self.cfg.get('db_path', 'D:/mystock/cache_daily/verification_v2.db')
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS verification_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_code TEXT NOT NULL,
                    name TEXT,
                    theme TEXT,
                    entry_date TEXT NOT NULL,
                    alpha REAL,
                    market_state TEXT,
                    market_score REAL,
                    theme_strength REAL,
                    leader_score REAL,
                    pullback_quality REAL,
                    etf_resonance_score REAL,
                    chip_stability REAL,
                    fund_flow_recovery REAL,
                    trend_health_score REAL,
                    ret_5d REAL,
                    ret_10d REAL,
                    ret_20d REAL,
                    ret_40d REAL,
                    max_profit REAL,
                    max_drawdown REAL,
                    hit_10pct INTEGER DEFAULT 0,
                    hit_20pct INTEGER DEFAULT 0,
                    hit_30pct INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now', 'localtime')),
                    UNIQUE(ts_code, entry_date)
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS factor_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_date TEXT NOT NULL,
                    factor_name TEXT NOT NULL,
                    importance REAL,
                    win_rate REAL,
                    avg_return REAL,
                    sharpe REAL,
                    analysis_type TEXT,
                    details TEXT,
                    created_at TEXT DEFAULT (datetime('now', 'localtime'))
                )
            ''')
            conn.commit()

    def save_record(self, record: VerificationRecord):
        with sqlite3.connect(self.db_path) as conn:
            cols = [
                'ts_code', 'name', 'theme', 'entry_date', 'alpha',
                'market_state', 'market_score', 'theme_strength', 'leader_score',
                'pullback_quality', 'etf_resonance_score', 'chip_stability',
                'fund_flow_recovery', 'trend_health_score'
            ]
            values = [getattr(record, c) for c in cols]
            placeholders = ','.join(['?'] * len(cols))
            sql = f'INSERT OR REPLACE INTO verification_records ({",".join(cols)}) VALUES ({placeholders})'
            conn.execute(sql, values)
            conn.commit()

    def save_batch(self, records: List[VerificationRecord]):
        for r in records:
            self.save_record(r)

    def update_forward_returns(self, entry_date: str):
        forward_days = self.cfg.get('forward_days', [5, 10, 20, 40])
        max_days = max(forward_days)

        with sqlite3.connect(self.db_path) as conn:
            records = conn.execute(
                'SELECT id, ts_code, entry_date FROM verification_records WHERE entry_date = ? AND ret_5d IS NULL',
                (entry_date,)
            ).fetchall()

            for rec_id, ts_code, ed in records:
                end_date = (pd.to_datetime(ed) + pd.Timedelta(days=max_days + 10)).strftime('%Y%m%d')
                df = self.loader.load_stk_factor(ts_code, ed, end_date, silent=True)
                if df is None or df.empty or len(df) < 2:
                    continue

                close = df['close'].values
                entry_idx = 0
                for i, d in enumerate(df['trade_date'].values):
                    if str(d) >= ed:
                        entry_idx = i
                        break

                entry_price = close[entry_idx]
                rets = {}
                for fd in forward_days:
                    if entry_idx + fd < len(close):
                        rets[f'ret_{fd}d'] = close[entry_idx + fd] / entry_price - 1

                remaining = close[entry_idx:]
                mfe = max(remaining) / entry_price - 1
                mae = min(remaining) / entry_price - 1

                hit_10 = 1 if any(p / entry_price - 1 >= 0.10 for p in remaining) else 0
                hit_20 = 1 if any(p / entry_price - 1 >= 0.20 for p in remaining) else 0
                hit_30 = 1 if any(p / entry_price - 1 >= 0.30 for p in remaining) else 0

                updates = {
                    'ret_5d': rets.get('ret_5d'),
                    'ret_10d': rets.get('ret_10d'),
                    'ret_20d': rets.get('ret_20d'),
                    'ret_40d': rets.get('ret_40d'),
                    'max_profit': mfe,
                    'max_drawdown': mae,
                    'hit_10pct': hit_10,
                    'hit_20pct': hit_20,
                    'hit_30pct': hit_30,
                }

                set_clause = ', '.join([f'{k} = ?' for k, v in updates.items() if v is not None])
                values = [v for v in updates.values() if v is not None] + [rec_id]
                if set_clause:
                    conn.execute(f'UPDATE verification_records SET {set_clause} WHERE id = ?', values)
            conn.commit()

    def backfill_all_pending(self):
        with sqlite3.connect(self.db_path) as conn:
            dates = conn.execute(
                'SELECT DISTINCT entry_date FROM verification_records WHERE ret_5d IS NULL ORDER BY entry_date'
            ).fetchall()
        for (d,) in dates:
            try:
                self.update_forward_returns(d)
            except Exception as e:
                print(f"[Verification] 回填 {d} 失败: {e}")

    def generate_weekly_report(self, report_date=None):
        if report_date is None:
            report_date = datetime.now().strftime('%Y%m%d')

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                "SELECT * FROM verification_records WHERE ret_5d IS NOT NULL",
                conn
            )

        if df.empty:
            return "暂无验证数据"

        lines = []
        lines.append("# 策略验证周报")
        lines.append(f"报告日期: {report_date}")
        lines.append(f"总记录数: {len(df)}")
        lines.append("")

        lines.append("## 一、整体表现")
        metrics = ['ret_5d', 'ret_10d', 'ret_20d', 'ret_40d']
        for m in metrics:
            if m in df.columns:
                valid = df[m].dropna()
                if len(valid) > 0:
                    win_rate = (valid > 0).mean()
                    avg_ret = valid.mean()
                    sr = valid.mean() / (valid.std() + 1e-10) * np.sqrt(252 / int(m.split('_')[1].replace('d', '')))
                    lines.append(f"- {m}: 胜率 {win_rate:.1%}, 平均收益 {avg_ret:.2%}, 夏普 {sr:.2f}")

        lines.append("")
        lines.append("## 二、因子重要性分析")
        factor_cols = ['theme_strength', 'leader_score', 'pullback_quality',
                       'etf_resonance_score', 'chip_stability', 'fund_flow_recovery', 'trend_health_score']
        available = [c for c in factor_cols if c in df.columns]
        if available:
            for col in available:
                valid = df[[col, 'ret_20d']].dropna()
                if len(valid) > 10:
                    corr = valid[col].corr(valid['ret_20d'])
                    high = valid[valid[col] > valid[col].median()]['ret_20d'].mean()
                    low = valid[valid[col] <= valid[col].median()]['ret_20d'].mean()
                    lines.append(f"- {col}: 相关系数 {corr:.3f}, 高分组 {high:.3%}, 低分组 {low:.3%}")

        lines.append("")
        lines.append("## 三、市场状态分层")
        if 'market_state' in df.columns and 'ret_20d' in df.columns:
            for state in df['market_state'].unique():
                subset = df[df['market_state'] == state]['ret_20d'].dropna()
                if len(subset) > 0:
                    lines.append(f"- {state}: 样本 {len(subset)}, 胜率 {(subset > 0).mean():.1%}, 平均 {subset.mean():.2%}")

        lines.append("")
        lines.append("## 四、目标达成率")
        for target in ['hit_10pct', 'hit_20pct', 'hit_30pct']:
            if target in df.columns:
                rate = df[target].mean()
                lines.append(f"- {target}: {rate:.1%}")

        lines.append("")
        lines.append("## 五、建议")
        lines.append("- 持续淘汰无效因子，保留真正有统计优势的规则")
        lines.append("- 关注不同市场状态下的策略表现差异")
        lines.append("- 定期校准因子阈值，基于数据驱动优化")

        return "\n".join(lines)

    def get_factor_importance(self):
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                "SELECT * FROM verification_records WHERE ret_20d IS NOT NULL",
                conn
            )
        if df.empty:
            return {}

        factor_cols = ['theme_strength', 'leader_score', 'pullback_quality',
                       'etf_resonance_score', 'chip_stability', 'fund_flow_recovery', 'trend_health_score']
        importance = {}
        for col in factor_cols:
            if col in df.columns:
                valid = df[[col, 'ret_20d']].dropna()
                if len(valid) > 10:
                    corr = abs(valid[col].corr(valid['ret_20d']))
                    high = valid[valid[col] > valid[col].median()]['ret_20d'].mean()
                    low = valid[valid[col] <= valid[col].median()]['ret_20d'].mean()
                    importance[col] = {
                        'correlation': round(corr, 4),
                        'high_group_return': round(high, 4),
                        'low_group_return': round(low, 4),
                        'spread': round(high - low, 4),
                    }
        return importance

    def save_factor_analysis(self, report_date, importance):
        with sqlite3.connect(self.db_path) as conn:
            for factor_name, data in importance.items():
                conn.execute(
                    'INSERT INTO factor_analysis (report_date, factor_name, importance, win_rate, avg_return, analysis_type, details) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (report_date, factor_name, data.get('correlation', 0),
                     data.get('spread', 0), data.get('high_group_return', 0),
                     'correlation', json.dumps(data, ensure_ascii=False))
                )
            conn.commit()