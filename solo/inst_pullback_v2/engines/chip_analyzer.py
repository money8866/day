import os
import sys
import sqlite3
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.indicators import ema, sma, slope
from data.loader import DataLoader, load_config


@dataclass
class ChipResult:
    ts_code: str
    name: str
    is_stable: bool = False
    stability_score: float = 0.0
    centroid_shift: float = 0.0
    profit_ratio: float = 0.0
    concentration: float = 0.0
    chip_peak: float = 0.0
    avg_cost: float = 0.0
    details: Dict = field(default_factory=dict)


class ChipAnalyzer:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['chip_analysis']
        self.loader = DataLoader()

    def analyze(self, ts_code: str, trade_date=None) -> Optional[ChipResult]:
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        lookback = self.config['general']['lookback_days']
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=lookback)).strftime('%Y%m%d')

        df = self.loader.load_stk_factor(ts_code, start_date, td, silent=True)
        if df is None or df.empty or len(df) < 60:
            return None

        result = ChipResult(
            ts_code=ts_code,
            name=self.loader.get_stock_name(ts_code),
        )

        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        vol = df['vol'].values if 'vol' in df.columns else np.ones(len(close))

        recent = 60
        recent_close = close[-recent:]
        recent_high = high[-recent:]
        recent_low = low[-recent:]
        recent_vol = vol[-recent:]

        avg_cost = np.average((recent_high + recent_low + recent_close) / 3, weights=recent_vol)
        result.avg_cost = round(float(avg_cost), 2)

        result.chip_peak = round(float(np.median(recent_close)), 2)

        price_range = (recent_high.max() - recent_low.min())
        if price_range > 0:
            result.concentration = 1.0 - (price_range / recent_close[-1])
        else:
            result.concentration = 1.0

        above_avg = (recent_close[-1] > avg_cost)
        above_ratio = float(np.sum(recent_close > avg_cost) / len(recent_close))
        result.profit_ratio = above_ratio

        centroid_recent = np.average((recent_high[-20:] + recent_low[-20:] + recent_close[-20:]) / 3,
                                     weights=recent_vol[-20:])
        centroid_prev = np.average((recent_high[-40:-20] + recent_low[-40:-20] + recent_close[-40:-20]) / 3,
                                   weights=recent_vol[-40:-20])
        result.centroid_shift = float((centroid_recent - centroid_prev) / centroid_prev) if centroid_prev > 0 else 0

        is_stable = True
        if result.centroid_shift < -self.cfg.get('centroid_drop_max', 0.03):
            is_stable = False
        if result.profit_ratio < self.cfg.get('profit_ratio_min', 0.55):
            is_stable = False
        if result.concentration < self.cfg.get('concentration_threshold', 0.6):
            is_stable = False

        result.is_stable = is_stable
        result.stability_score = self._calc_stability(result)

        result.details = {
            'avg_cost': result.avg_cost,
            'chip_peak': result.chip_peak,
            'concentration': round(result.concentration, 4),
            'profit_ratio': round(result.profit_ratio, 4),
            'centroid_shift': round(result.centroid_shift, 4),
        }

        return result

    def _calc_stability(self, result):
        score = 0.5
        if result.centroid_shift > -0.01:
            score += 0.2
        elif result.centroid_shift > -0.03:
            score += 0.1
        if result.profit_ratio > 0.70:
            score += 0.15
        elif result.profit_ratio > 0.55:
            score += 0.08
        if result.concentration > 0.8:
            score += 0.15
        elif result.concentration > 0.6:
            score += 0.07
        return min(1.0, score)


class ETFResonance:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['etf_resonance']
        self.loader = DataLoader()

    def evaluate(self, ts_code: str, etf_code: str, trade_date=None) -> Optional[Dict]:
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=120)).strftime('%Y%m%d')

        if not etf_code:
            return {'is_resonant': False, 'score': 0, 'reason': 'no_etf'}

        etf_df = self.loader.load_index_data(etf_code, start_date, td, silent=True)
        if etf_df is None or etf_df.empty or len(etf_df) < 20:
            return {'is_resonant': False, 'score': 0, 'reason': 'no_etf_data'}

        etf_close = etf_df['close']
        etf_ma20 = sma(etf_close, 20)
        etf_ma60 = sma(etf_close, 60)

        trend_score = 0
        if etf_close.iloc[-1] > etf_ma20.iloc[-1]:
            trend_score += 30
        if etf_ma20.iloc[-1] > etf_ma60.iloc[-1]:
            trend_score += 25
        ret_20d = etf_close.iloc[-1] / etf_close.iloc[-min(20, len(etf_close))] - 1
        if ret_20d > 0.05:
            trend_score += 20
        elif ret_20d > 0:
            trend_score += 10
        sl_20 = slope(etf_ma20.dropna().reset_index(drop=True), 5)
        if sl_20 is not None and len(sl_20) > 0 and sl_20.iloc[-1] > 0:
            trend_score += 15

        nh_window = self.cfg.get('new_high_window', 10)
        nh = 0
        for i in range(len(etf_close) - nh_window, len(etf_close)):
            if i >= nh_window:
                if etf_close.iloc[i] >= etf_close.iloc[i - nh_window:i].max():
                    nh += 1
        if nh >= self.cfg.get('new_high_min_count', 1):
            trend_score += 10

        is_resonant = trend_score >= self.cfg.get('trend_score_min', 75)

        return {
            'is_resonant': is_resonant,
            'score': trend_score,
            'etf_ma20_up': etf_close.iloc[-1] > etf_ma20.iloc[-1],
            'etf_ma60_up': etf_ma20.iloc[-1] > etf_ma60.iloc[-1],
            'etf_ret_20d': round(ret_20d, 4),
            'new_high_recent': nh,
        }


class FundFlow:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['fund_flow']
        self.loader = DataLoader()

    def evaluate(self, ts_code: str, trade_date=None) -> Optional[Dict]:
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        flow_window = self.cfg.get('flow_window', 3)

        recent_dates = self.loader.get_recent_trade_dates(flow_window + 2)

        mf_df = self.loader.load_moneyflow_multi(recent_dates)
        if mf_df is None or mf_df.empty:
            return {'is_recovering': True, 'score': 0.5, 'reason': 'no_data'}

        stock_mf = mf_df[mf_df['ts_code'] == ts_code].sort_values('trade_date')
        if stock_mf.empty or len(stock_mf) < 2:
            return {'is_recovering': True, 'score': 0.5, 'reason': 'no_data'}

        if 'net_mf_amount' in stock_mf.columns:
            net_flows = stock_mf.tail(flow_window)['net_mf_amount'].tolist()
        else:
            buy_cols = [c for c in stock_mf.columns if c.startswith('buy_') and 'amount' in c]
            sell_cols = [c for c in stock_mf.columns if c.startswith('sell_') and 'amount' in c]
            if not buy_cols or not sell_cols:
                return {'is_recovering': True, 'score': 0.5, 'reason': 'no_moneyflow_fields'}
            recent = stock_mf.tail(flow_window)
            net_flows = []
            for _, row in recent.iterrows():
                buy = sum(float(row[c]) for c in buy_cols if pd.notna(row[c]))
                sell = sum(float(row[c]) for c in sell_cols if pd.notna(row[c]))
                net_flows.append(buy - sell)

        if len(net_flows) < 2:
            return {'is_recovering': True, 'score': 0.5, 'reason': 'insufficient_data'}

        is_recovering = False
        if self.cfg.get('require_turn_positive', True):
            if net_flows[-1] > 0 and sum(net_flows) > 0:
                is_recovering = True
        else:
            if net_flows[-1] > net_flows[-2]:
                is_recovering = True

        score = 0.5
        if is_recovering:
            score = 0.8
        if len(net_flows) >= 3 and all(n > 0 for n in net_flows[-3:]):
            score = 1.0

        return {
            'is_recovering': is_recovering,
            'score': score,
            'net_flows': net_flows,
        }


class TrendHealth:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['trend_health']
        self.loader = DataLoader()

    def evaluate(self, ts_code: str, trade_date=None) -> Optional[Dict]:
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        lookback = self.config['general']['lookback_days']
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=lookback)).strftime('%Y%m%d')

        df = self.loader.load_stk_factor(ts_code, start_date, td, silent=True)
        if df is None or df.empty or len(df) < 120:
            return {'is_healthy': False, 'score': 0, 'reason': 'insufficient_data'}

        last = df.iloc[-1]
        close = df['close_qfq']

        # EMA对齐 — 使用 stk_factor_pro 预计算字段（前复权）
        ema20_val = last.get('ema_qfq_20') or ema(close, 20).iloc[-1]
        ema60_val = last.get('ema_qfq_60') or ema(close, 60).iloc[-1]
        # EMA120 无预计算字段，保持自算
        ema120_val = ema(close, 120).iloc[-1]

        alignment = (ema20_val > ema60_val and ema60_val > ema120_val)
        if not alignment and self.cfg.get('require_bullish_alignment', True):
            return {'is_healthy': False, 'score': 0, 'reason': 'ema_not_aligned'}

        health_score = 0.5
        if alignment:
            health_score += 0.2

        # MACD — 使用 macd_dif_qfq / macd_dea_qfq
        macd_dif = last.get('macd_dif_qfq')
        macd_dea = last.get('macd_dea_qfq')
        macd_score = 0
        if pd.notna(macd_dif) and pd.notna(macd_dea):
            if macd_dif > macd_dea:
                macd_score += 0.1
            if macd_dif > 0:
                macd_score += 0.05
            if abs(macd_dif / close.iloc[-1]) < 0.05:
                macd_score += 0.05
        health_score += macd_score

        # ADX — 使用 dmi_adx_qfq
        adx_val = last.get('dmi_adx_qfq')
        if pd.notna(adx_val):
            if adx_val > 25 and adx_val < 50:
                health_score += 0.05
            elif adx_val >= 50:
                health_score -= 0.05

        # ATR — 使用 atr_qfq
        atr_val = last.get('atr_qfq')
        if pd.notna(atr_val) and len(close) >= 20:
            if atr_val / close.iloc[-1] < 0.05:
                health_score += 0.05

        is_healthy = health_score >= 0.5

        return {
            'is_healthy': is_healthy,
            'score': round(min(1.0, health_score), 4),
            'ema_aligned': alignment,
            'ema20': round(float(ema20_val), 2),
            'ema60': round(float(ema60_val), 2),
            'ema120': round(float(ema120_val), 2),
            'adx': round(float(adx_val), 2) if pd.notna(adx_val) else None,
            'macd_score': round(macd_score, 3),
        }


class ThemeLifecycleFilter:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['theme_lifecycle']
        self.loader = DataLoader()

    def evaluate(self, theme_name: str, theme_stocks: List[str], trade_date=None) -> Dict:
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=120)).strftime('%Y%m%d')

        momentum_ratios = []
        for code in theme_stocks[:50]:
            df = self.loader.load_stk_factor(code, start_date, td, silent=True)
            if df is None or df.empty or len(df) < 60:
                continue
            close = df['close']
            ret_20d = close.iloc[-1] / close.iloc[-min(20, len(close))] - 1
            ret_60d = close.iloc[-1] / close.iloc[-min(60, len(close))] - 1
            momentum_ratios.append(ret_20d / abs(ret_60d) + 1e-10 if ret_60d != 0 else 0)

        avg_momentum = np.mean(momentum_ratios) if momentum_ratios else 0.5

        if avg_momentum > 0.5:
            stage = "EXPANSION"
        elif avg_momentum > 0.2:
            stage = "EARLY_MATURE"
        elif avg_momentum > 0:
            stage = "BIRTH"
        elif avg_momentum > -0.3:
            stage = "LATE"
        else:
            stage = "DEATH"

        allowed = self.cfg.get('allowed_stages', ["BIRTH", "EXPANSION", "EARLY_MATURE"])
        is_allowed = stage in allowed

        return {
            'is_allowed': is_allowed,
            'stage': stage,
            'momentum': round(avg_momentum, 4),
        }


class RiskFilter:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['risk_filter']
        self.loader = DataLoader()

    def evaluate(self, ts_code: str, trade_date=None) -> Dict:
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=60)).strftime('%Y%m%d')

        issues = []
        name = self.loader.get_stock_name(ts_code)

        if self.cfg.get('exclude_st', True):
            if 'ST' in name.upper() or '*ST' in name.upper():
                issues.append('ST股票')

        if self.cfg.get('exclude_delist', True):
            if '退' in name:
                issues.append('退市风险')

        db_path = r"D:\mystock\cache_daily\stock_data.db"
        df = None
        try:
            conn = sqlite3.connect(db_path)
            df = pd.read_sql_query(
                "SELECT ts_code, trade_date, amount, pct_chg, pe_ttm, pb FROM stk_factor_pro WHERE ts_code = ? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                conn, params=(ts_code, str(start_date), str(td))
            )
            conn.close()
        except Exception:
            pass

        if df is not None and not df.empty and len(df) >= 10:
            df['trade_date'] = df['trade_date'].astype(str)

            if 'amount' in df.columns:
                valid_amount = df['amount'].dropna()
                if len(valid_amount) >= 5:
                    avg_amount = valid_amount.tail(20).mean()
                    min_amount = self.cfg.get('min_amount', 200000)
                    if avg_amount < min_amount:
                        issues.append(f'流动性不足(日均成交{avg_amount/1e5:.1f}亿)')

            if 'pct_chg' in df.columns:
                recent = df['pct_chg'].tail(30)
                consec_down = 0
                max_consec = 0
                for v in recent.values:
                    try:
                        vf = float(v)
                    except (ValueError, TypeError):
                        continue
                    if vf < -9.5:
                        consec_down += 1
                        max_consec = max(max_consec, consec_down)
                    else:
                        consec_down = 0
                if max_consec > self.cfg.get('max_consecutive_limit_down', 1):
                    issues.append(f'近期连续跌停({max_consec}天)')

            if self.cfg.get('exclude_high_goodwill', True):
                if 'pe_ttm' in df.columns and 'pb' in df.columns:
                    pe = df['pe_ttm'].iloc[-1]
                    pb = df['pb'].iloc[-1]
                    try:
                        pe_f = float(pe)
                        pb_f = float(pb)
                        if pe_f < 0 and pb_f > 10:
                            issues.append('疑似商誉异常')
                    except (ValueError, TypeError):
                        pass

        is_clean = len(issues) == 0
        score = 1.0 if is_clean else max(0, 1.0 - len(issues) * 0.3)

        return {
            'is_clean': is_clean,
            'issues': issues,
            'score': score,
        }