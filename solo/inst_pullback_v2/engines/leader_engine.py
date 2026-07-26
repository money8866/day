import os
import sys
import json
import sqlite3
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.indicators import ema, sma, slope, rsi, new_high_count, price_position, rolling_corr
from data.loader import DataLoader, load_config


@dataclass
class LeaderResult:
    ts_code: str
    name: str
    leader_score: float = 0.0
    cross_section_score: float = 0.0
    persistence_score: float = 0.0
    tenure_days: int = 0
    top3_ratio_20d: float = 0.0
    top3_ratio_60d: float = 0.0
    rank_stability: float = 0.0
    rank_momentum: float = 0.0
    history_weighted_score: float = 0.0
    ret_60d: float = 0.0
    ret_20d: float = 0.0
    amount_score: float = 0.0
    new_high_score: float = 0.0
    etf_corr_score: float = 0.0
    is_established: bool = False


class LeaderHistoryDB:
    DB_PATH = r"D:\mystock\cache_daily\leader_history_v3.db"

    def __init__(self):
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS leader_rankings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    theme_name TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    ts_code TEXT NOT NULL,
                    stock_name TEXT,
                    rank INTEGER NOT NULL,
                    cross_section_score REAL,
                    leader_score REAL,
                    ret_60d REAL,
                    ret_20d REAL,
                    amount_score REAL,
                    new_high_score REAL,
                    etf_corr_score REAL,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(theme_name, trade_date, ts_code)
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_lr_theme_date 
                ON leader_rankings(theme_name, trade_date)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_lr_code_date 
                ON leader_rankings(ts_code, trade_date)
            ''')
            conn.commit()

    def save_rankings(self, theme_name: str, trade_date: str, results: List[LeaderResult]):
        with sqlite3.connect(self.DB_PATH) as conn:
            for i, r in enumerate(results):
                conn.execute('''
                    INSERT OR REPLACE INTO leader_rankings 
                    (theme_name, trade_date, ts_code, stock_name, rank, cross_section_score, leader_score,
                     ret_60d, ret_20d, amount_score, new_high_score, etf_corr_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    theme_name, trade_date, r.ts_code, r.name, i + 1,
                    r.cross_section_score, r.leader_score,
                    r.ret_60d, r.ret_20d, r.amount_score, r.new_high_score, r.etf_corr_score
                ))
            conn.commit()

    def get_theme_history(self, theme_name: str, end_date: str, lookback_days: int = 120) -> pd.DataFrame:
        start_date = (pd.to_datetime(end_date) - timedelta(days=lookback_days)).strftime('%Y%m%d')
        with sqlite3.connect(self.DB_PATH) as conn:
            df = pd.read_sql_query('''
                SELECT theme_name, trade_date, ts_code, stock_name, rank, cross_section_score, leader_score
                FROM leader_rankings
                WHERE theme_name = ? AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date, rank
            ''', conn, params=(theme_name, start_date, end_date))
        if not df.empty:
            df['trade_date'] = df['trade_date'].astype(str)
        return df

    def get_stock_persistence(self, theme_name: str, ts_code: str, end_date: str, lookback_days: int = 120) -> Dict:
        df = self.get_theme_history(theme_name, end_date, lookback_days)
        if df.empty:
            return self._empty_persistence()

        stock_df = df[df['ts_code'] == ts_code].sort_values('trade_date')

        tenure = self._calc_tenure(df, ts_code, end_date)

        rank_stability = self._calc_rank_stability(stock_df)

        top3_ratio_20d = self._calc_topn_ratio(stock_df, 20, 3)
        top3_ratio_60d = self._calc_topn_ratio(stock_df, 60, 3)

        rank_momentum = self._calc_rank_momentum(stock_df)

        history_weighted = self._calc_history_weighted(stock_df)

        if stock_df.empty:
            return self._empty_persistence()

        return {
            'tenure_days': tenure,
            'rank_stability': rank_stability,
            'top3_ratio_20d': top3_ratio_20d,
            'top3_ratio_60d': top3_ratio_60d,
            'rank_momentum': rank_momentum,
            'history_weighted_score': history_weighted,
            'has_history': True,
            'total_days_in_top10': len(stock_df[stock_df['rank'] <= 10]),
            'total_days_in_top3': len(stock_df[stock_df['rank'] <= 3]),
            'best_rank': int(stock_df['rank'].min()) if not stock_df.empty else 99,
        }

    def _calc_tenure(self, df: pd.DataFrame, ts_code: str, end_date: str) -> int:
        stock_df = df[df['ts_code'] == ts_code].sort_values('trade_date')
        if stock_df.empty:
            return 0
        stock_df = stock_df[stock_df['rank'] <= 3]
        if stock_df.empty:
            return 0
        dates = sorted(stock_df['trade_date'].unique(), reverse=True)
        if not dates:
            return 0
        tenure = 0
        prev_date = pd.to_datetime(end_date)
        for d in dates:
            current = pd.to_datetime(d)
            if tenure == 0:
                tenure = 1
                prev_date = current
            else:
                diff = (prev_date - current).days
                if diff <= 5:
                    tenure += 1
                    prev_date = current
                else:
                    break
        return tenure

    def _calc_rank_stability(self, stock_df: pd.DataFrame) -> float:
        if stock_df.empty or len(stock_df) < 3:
            return 0.0
        ranks = stock_df['rank'].values
        if len(ranks) < 3:
            return 0.0
        std = float(np.std(ranks))
        if std > 20:
            return 0.0
        stability = 1.0 - (std / 20.0)
        return max(0.0, min(1.0, stability))

    def _calc_topn_ratio(self, stock_df: pd.DataFrame, window: int, n: int) -> float:
        if stock_df.empty:
            return 0.0
        recent = stock_df.tail(window)
        if recent.empty:
            return 0.0
        in_top = (recent['rank'] <= n).sum()
        return in_top / len(recent)

    def _calc_rank_momentum(self, stock_df: pd.DataFrame) -> float:
        if stock_df.empty or len(stock_df) < 5:
            return 0.0
        recent = stock_df.tail(10)
        if len(recent) < 5:
            return 0.0
        ranks = recent['rank'].values
        x = np.arange(len(ranks))
        try:
            slope_val = np.polyfit(x, ranks, 1)[0]
            normalized = -slope_val / 10.0
            return max(-1.0, min(1.0, normalized))
        except Exception:
            return 0.0

    def _calc_history_weighted(self, stock_df: pd.DataFrame) -> float:
        if stock_df.empty:
            return 0.0
        stock_df = stock_df.sort_values('trade_date')
        scores = stock_df['cross_section_score'].values
        n = len(scores)
        if n == 0:
            return 0.0
        weights = np.exp(np.linspace(-2, 0, n))
        weights = weights / weights.sum()
        return float(np.average(scores, weights=weights))

    def _empty_persistence(self):
        return {
            'tenure_days': 0,
            'rank_stability': 0.0,
            'top3_ratio_20d': 0.0,
            'top3_ratio_60d': 0.0,
            'rank_momentum': 0.0,
            'history_weighted_score': 0.0,
            'has_history': False,
            'total_days_in_top10': 0,
            'total_days_in_top3': 0,
            'best_rank': 99,
        }

    def get_all_stocks_persistence(self, theme_name: str, ts_codes: List[str], end_date: str) -> Dict[str, Dict]:
        df = self.get_theme_history(theme_name, end_date, 120)
        if df.empty:
            return {code: self._empty_persistence() for code in ts_codes}

        result = {}
        for code in ts_codes:
            stock_df = df[df['ts_code'] == code].sort_values('trade_date')
            result[code] = {
                'tenure_days': self._calc_tenure(df, code, end_date),
                'rank_stability': self._calc_rank_stability(stock_df),
                'top3_ratio_20d': self._calc_topn_ratio(stock_df, 20, 3),
                'top3_ratio_60d': self._calc_topn_ratio(stock_df, 60, 3),
                'rank_momentum': self._calc_rank_momentum(stock_df),
                'history_weighted_score': self._calc_history_weighted(stock_df),
                'has_history': not stock_df.empty,
                'total_days_in_top10': len(stock_df[stock_df['rank'] <= 10]) if not stock_df.empty else 0,
                'total_days_in_top3': len(stock_df[stock_df['rank'] <= 3]) if not stock_df.empty else 0,
                'best_rank': int(stock_df['rank'].min()) if not stock_df.empty else 99,
            }
        return result


class LeaderEngineV3:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['leader_engine']
        self.loader = DataLoader()
        self.history_db = LeaderHistoryDB()
        self._bulk_cache = {}

    def evaluate(self, theme_stocks: List[str], theme_name: str = "", etf_code: str = "",
                 trade_date=None) -> List[LeaderResult]:
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=120)).strftime('%Y%m%d')

        self._preload_bulk(theme_stocks, start_date, td)

        persis_map = self.history_db.get_all_stocks_persistence(theme_name, theme_stocks, td)

        results = []
        for code in theme_stocks:
            df = self._get_bulk(code)
            if df is None or len(df) < 60:
                continue
            name = self.loader.get_stock_name(code)
            close_hfq = df['close_hfq'].values if 'close_hfq' in df.columns else df['close'].values

            ret_60d = close_hfq[-1] / close_hfq[-min(60, len(close_hfq))] - 1
            ret_20d = close_hfq[-1] / close_hfq[-min(20, len(close_hfq))] - 1

            nh = 0
            if len(close_hfq) >= 60:
                nh_60 = pd.Series(close_hfq).rolling(60).max()
                nh = sum(1 for i in range(len(close_hfq) - 60, len(close_hfq)) if i >= 60 and close_hfq[i] >= nh_60.iloc[i - 1])
            nh_score = min(1.0, nh / 20) if nh > 0 else 0

            etf_corr = self._score_etf_corr_fast(code, etf_code, start_date, td) if etf_code else 0.5

            amount_score = self._score_amount_fast(df)

            w_cs = self.cfg.get('cross_section_weights', {
                'ret_60d': 0.20, 'ret_20d': 0.12, 'amount': 0.18,
                'new_high': 0.12, 'etf_corr': 0.10, 'inst_money': 0.10,
                'theme_purity': 0.10, 'market_recognition': 0.08
            })
            cross_section = (
                ret_60d * w_cs.get('ret_60d', 0.20) * 3 +
                ret_20d * w_cs.get('ret_20d', 0.12) * 3 +
                amount_score * w_cs.get('amount', 0.18) +
                nh_score * w_cs.get('new_high', 0.12) +
                etf_corr * w_cs.get('etf_corr', 0.10) +
                0.5 * w_cs.get('inst_money', 0.10) +
                0.7 * w_cs.get('theme_purity', 0.10) +
                min(1.0, max(0.3, ret_60d * 2)) * w_cs.get('market_recognition', 0.08)
            )

            p = persis_map.get(code, self.history_db._empty_persistence())

            w_p = self.cfg.get('persistence_weights', {
                'tenure': 0.25, 'rank_stability': 0.15, 'top3_ratio_20d': 0.18,
                'top3_ratio_60d': 0.12, 'rank_momentum': 0.12, 'history_weighted': 0.18
            })
            persistence = (
                min(1.0, p['tenure_days'] / 20) * w_p.get('tenure', 0.25) +
                p['rank_stability'] * w_p.get('rank_stability', 0.15) +
                p['top3_ratio_20d'] * w_p.get('top3_ratio_20d', 0.18) +
                p['top3_ratio_60d'] * w_p.get('top3_ratio_60d', 0.12) +
                max(0, p['rank_momentum']) * w_p.get('rank_momentum', 0.12) +
                p['history_weighted_score'] * w_p.get('history_weighted', 0.18)
            )

            if p['has_history'] and p['tenure_days'] >= 3:
                persistence = min(1.0, persistence + 0.05)

            cs_weight = self.cfg.get('cross_section_weight', 0.55)
            per_weight = self.cfg.get('persistence_weight', 0.45)

            leader_score = cross_section * cs_weight + persistence * per_weight

            results.append(LeaderResult(
                ts_code=code,
                name=name,
                leader_score=round(leader_score * 100, 1),
                cross_section_score=round(cross_section, 4),
                persistence_score=round(persistence, 4),
                tenure_days=p['tenure_days'],
                top3_ratio_20d=round(p['top3_ratio_20d'], 3),
                top3_ratio_60d=round(p['top3_ratio_60d'], 3),
                rank_stability=round(p['rank_stability'], 3),
                rank_momentum=round(p['rank_momentum'], 3),
                history_weighted_score=round(p['history_weighted_score'], 3),
                ret_60d=round(ret_60d, 4),
                ret_20d=round(ret_20d, 4),
                amount_score=round(amount_score, 4),
                new_high_score=round(nh_score, 4),
                etf_corr_score=round(etf_corr, 4),
                is_established=(p['tenure_days'] >= 5 and p['top3_ratio_60d'] >= 0.3),
            ))

        results.sort(key=lambda x: x.leader_score, reverse=True)
        top_n = self.cfg.get('top_n_per_theme', 3)
        top_results = results[:top_n]

        self.history_db.save_rankings(theme_name, td, results)

        return top_results

    def _preload_bulk(self, ts_codes, start_date, end_date):
        db_path = r"D:\mystock\cache_daily\stock_data.db"
        try:
            conn = sqlite3.connect(db_path)
            placeholders = ','.join(['?'] * len(ts_codes))
            query = f"""
                SELECT ts_code, trade_date, close_hfq, close, amount, vol, high, low, pct_chg
                FROM stk_factor_pro
                WHERE ts_code IN ({placeholders})
                AND trade_date BETWEEN ? AND ?
                ORDER BY ts_code, trade_date
            """
            params = list(ts_codes) + [str(start_date), str(end_date)]
            df = pd.read_sql_query(query, conn, params=params)
            conn.close()
            df['trade_date'] = df['trade_date'].astype(str)
            self._bulk_cache = {code: group.reset_index(drop=True) for code, group in df.groupby('ts_code')}
        except Exception:
            self._bulk_cache = {}

    def _get_bulk(self, code):
        return self._bulk_cache.get(code)

    def _score_amount_fast(self, df):
        amount = df['amount'].values if 'amount' in df.columns else None
        if amount is None or len(amount) < 10:
            return 0.5
        avg = amount[-10:].mean()
        if avg < 1e-6:
            return 0.3
        if avg > 5e9:
            return 1.0
        elif avg > 2e9:
            return 0.9
        elif avg > 1e9:
            return 0.8
        elif avg > 5e8:
            return 0.7
        elif avg > 2e8:
            return 0.5
        else:
            return 0.3

    def _score_etf_corr_fast(self, code, etf_code, start_date, end_date):
        if not etf_code:
            return 0.5
        stock_df = self._get_bulk(code)
        etf_df = self.loader.load_index_data(etf_code, start_date, end_date, silent=True)
        if stock_df is None or etf_df is None or etf_df.empty:
            return 0.5
        stock_close = stock_df.set_index('trade_date')['close']
        etf_close = etf_df.set_index('trade_date')['close']
        common = stock_close.index.intersection(etf_close.index)
        if len(common) < 20:
            return 0.5
        corr = stock_close.loc[common].pct_change().corr(etf_close.loc[common].pct_change())
        return min(1.0, max(0.0, corr)) if not pd.isna(corr) else 0.5