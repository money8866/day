import os
import sys
import sqlite3
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.indicators import ema, sma, slope, atr, adx, rsi, macd, new_high_count, price_position
from data.loader import DataLoader, load_config


@dataclass
class MarketState:
    STATE_CN_MAP = {
        "BULL_TREND": "牛市趋势",
        "BULL_PULLBACK": "牛市回调",
        "ROTATION": "板块轮动",
        "SIDEWAY": "横盘震荡",
        "RISK_OFF": "风险规避",
        "PANIC": "恐慌下跌",
        "UNKNOWN": "未知状态",
    }

    state: str = "UNKNOWN"
    score: float = 0.0
    trend_score: float = 0.0
    money_score: float = 0.0
    breadth_score: float = 0.0
    new_high_score: float = 0.0
    sentiment_score: float = 0.0
    details: Dict = field(default_factory=dict)

    def get_state_cn(self) -> str:
        return self.STATE_CN_MAP.get(self.state, "未知状态")


class MarketStateEngine:
    STATES = ["BULL_TREND", "BULL_PULLBACK", "ROTATION", "SIDEWAY", "RISK_OFF", "PANIC"]

    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['market_state']
        self.loader = DataLoader()

    def evaluate(self, trade_date=None) -> MarketState:
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=250)).strftime('%Y%m%d')

        result = MarketState()

        result.trend_score = self._calc_trend_score(start_date, td)
        result.money_score = self._calc_money_score(start_date, td)
        result.breadth_score = self._calc_breadth_score(td)
        result.new_high_score = self._calc_new_high_score(td)
        result.sentiment_score = self._calc_sentiment_score(td)

        w = self.cfg
        result.score = (
            result.trend_score * w['trend_score_weight'] +
            result.money_score * w['money_score_weight'] +
            result.breadth_score * w['breadth_weight'] +
            result.new_high_score * w['new_high_weight'] +
            result.sentiment_score * w['sentiment_weight']
        ) * 100

        result.score = min(100, max(0, result.score))
        result.state = self._classify_state(result.score, result.trend_score)
        result.details = {
            'trade_date': td,
            'trend_score': round(result.trend_score, 3),
            'money_score': round(result.money_score, 3),
            'breadth_score': round(result.breadth_score, 3),
            'new_high_score': round(result.new_high_score, 3),
            'sentiment_score': round(result.sentiment_score, 3),
        }
        return result

    def _calc_trend_score(self, start_date, end_date):
        indices = self.cfg.get('indices', ["000300.SH", "000852.SH", "399006.SZ", "000688.SH"])
        scores = []
        for idx in indices:
            df = self.loader.load_index_data(idx, start_date, end_date, silent=True)
            if df is None or df.empty or len(df) < 120:
                scores.append(0.5)
                continue
            close = df['close']
            ma20 = sma(close, 20)
            ma60 = sma(close, 60)
            ma120 = sma(close, 120)
            sl_20 = slope(ma20.dropna().reset_index(drop=True), min(5, len(ma20.dropna()) - 1))
            sl_60 = slope(ma60.dropna().reset_index(drop=True), min(5, len(ma60.dropna()) - 1))
            sl_20 = sl_20.iloc[-1] if sl_20 is not None and len(sl_20) > 0 else 0
            sl_60 = sl_60.iloc[-1] if sl_60 is not None and len(sl_60) > 0 else 0
            latest = close.iloc[-1]
            m20 = ma20.iloc[-1]
            m60 = ma60.iloc[-1]
            m120 = ma120.iloc[-1] if not pd.isna(ma120.iloc[-1]) else m60
            s = 0.3
            if latest > m20:
                s += 0.25
            if m20 > m60:
                s += 0.20
            if m60 > m120:
                s += 0.15
            if sl_20 > 0:
                s += 0.10
            if sl_60 > 0:
                s += 0.10
            scores.append(min(1.0, s))
        return np.mean(scores) if scores else 0.5

    def _calc_money_score(self, start_date, end_date):
        etf_pool = self.loader.get_etf_pool()
        etf_codes = list(etf_pool.keys())
        scores = []
        for code in etf_codes[:20]:
            df = self.loader.load_index_data(code, start_date, end_date, silent=True)
            if df is None or df.empty or len(df) < 20:
                continue
            if 'amount' not in df.columns:
                df['amount'] = df['close'] * df.get('vol', 0) * 100
            amount_ma20 = sma(df['amount'], 20)
            if amount_ma20.iloc[-1] < 1e-6:
                continue
            vol_ratio = df['amount'].iloc[-1] / amount_ma20.iloc[-1]
            ret_5d = df['close'].pct_change(5).iloc[-1]
            s = 0.5
            if vol_ratio > 1.2:
                s += 0.2
            elif vol_ratio > 1.0:
                s += 0.1
            if ret_5d > 0.02:
                s += 0.15
            elif ret_5d > -0.02:
                s += 0.05
            scores.append(min(1.0, s))
        return np.mean(scores) if scores else 0.5

    def _calc_breadth_score(self, trade_date):
        start_date = (pd.to_datetime(trade_date) - pd.Timedelta(days=120)).strftime('%Y%m%d')
        db_path = r"D:\mystock\cache_daily\stock_data.db"
        try:
            conn = sqlite3.connect(db_path)
            df = pd.read_sql_query(
                "SELECT ts_code, trade_date, close_qfq, ma_qfq_20, ma_qfq_60 FROM stk_factor_pro WHERE trade_date BETWEEN ? AND ? ORDER BY ts_code, trade_date",
                conn, params=(str(start_date), str(trade_date))
            )
            conn.close()
            if df.empty:
                return 0.5
            df['trade_date'] = df['trade_date'].astype(str)
            above_ma20 = 0
            above_ma60 = 0
            count = 0
            for code, group in df.groupby('ts_code'):
                if len(group) < 60:
                    continue
                count += 1
                last = group.iloc[-1]
                close_val = last['close_qfq']
                ma20_val = last.get('ma_qfq_20')
                ma60_val = last.get('ma_qfq_60')
                if pd.notna(ma20_val) and close_val > ma20_val:
                    above_ma20 += 1
                if pd.notna(ma60_val) and close_val > ma60_val:
                    above_ma60 += 1
                if count >= 500:
                    break
            if count == 0:
                return 0.5
            score = (above_ma20 / count * 0.6 + above_ma60 / count * 0.4)
            return score
        except Exception:
            return 0.5

    def _calc_new_high_score(self, trade_date):
        start_date = (pd.to_datetime(trade_date) - pd.Timedelta(days=60)).strftime('%Y%m%d')
        indices = self.cfg.get('indices', ["000300.SH", "000852.SH", "399006.SZ", "000688.SH"])
        scores = []
        for idx in indices:
            df = self.loader.load_index_data(idx, start_date, trade_date, silent=True)
            if df is None or df.empty or len(df) < 20:
                scores.append(0.5)
                continue
            close = df['close']
            nh = new_high_count(close, 20)
            ratio = nh.iloc[-1] / 20 if nh.iloc[-1] > 0 else 0
            scores.append(min(1.0, ratio * 5))
        return np.mean(scores) if scores else 0.5

    def _calc_sentiment_score(self, trade_date):
        score = 0.5
        try:
            start_date = (pd.to_datetime(trade_date) - pd.Timedelta(days=30)).strftime('%Y%m%d')
            df_300 = self.loader.load_index_data("000300.SH", start_date, trade_date, silent=True)
            if df_300 is not None and not df_300.empty and len(df_300) >= 5:
                ret_5d = df_300['close'].pct_change(5).iloc[-1]
                if ret_5d > 0.03:
                    score += 0.2
                elif ret_5d > 0:
                    score += 0.1
                elif ret_5d < -0.05:
                    score -= 0.2
                elif ret_5d < -0.02:
                    score -= 0.1
                vol_ratio = df_300['vol'].iloc[-5:].mean() / df_300['vol'].iloc[-20:].mean() if 'vol' in df_300.columns else 1.0
                if vol_ratio > 1.2:
                    score += 0.1
        except Exception:
            pass
        return min(1.0, max(0.0, score))

    def _classify_state(self, score, trend_score):
        if score >= 80:
            return "BULL_TREND"
        elif score >= 65:
            return "BULL_PULLBACK"
        elif score >= 50:
            return "ROTATION"
        elif score >= 35:
            return "SIDEWAY"
        elif score >= 20:
            return "RISK_OFF"
        else:
            return "PANIC"

    def is_trade_allowed(self, state: MarketState) -> bool:
        return state.state in self.cfg.get('allowed_states', ["BULL_TREND", "BULL_PULLBACK"])