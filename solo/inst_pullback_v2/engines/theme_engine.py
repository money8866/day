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
from data.indicators import ema, sma, slope, rsi, new_high_count, price_position
from data.loader import DataLoader, load_config

THEME_CONFIG_PATH = r"D:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json"


def load_etf_mapping() -> Dict[str, str]:
    """从 theme_config.json 加载主题→主ETF的映射"""
    mapping = {}
    try:
        if os.path.exists(THEME_CONFIG_PATH):
            with open(THEME_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            for key, val in cfg.items():
                name_cn = val.get('name_cn', '')
                main_etf = val.get('main_etf', '')
                if name_cn and main_etf:
                    # 去掉 .SH/.SZ 后缀，与 DataLoader.load_index_data 格式统一
                    clean_etf = main_etf.replace('.SH', '').replace('.SZ', '')
                    mapping[name_cn] = clean_etf
    except Exception as e:
        print(f"  [ThemeEngine] 加载 theme_config.json 失败: {e}")
    return mapping


@dataclass
class ThemeResult:
    name: str
    rank: int = 0
    composite_score: float = 0.0
    trend_score: float = 0.0
    money_score: float = 0.0
    duration_score: float = 0.0
    etf_trend_score: float = 0.0
    leader_strength_score: float = 0.0
    momentum_intensity: float = 0.0
    etf_code: str = ""
    stock_count: int = 0
    theme_score: float = 0.0
    leader_stocks: List = field(default_factory=list)
    details: Dict = field(default_factory=dict)


class InstitutionThemeEngine:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['theme_engine']
        self.loader = DataLoader()
        self._bulk_cache = {}
        self._theme_score_cache = {}
        self.etf_map = load_etf_mapping()

    def evaluate(self, trade_date=None) -> List[ThemeResult]:
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=250)).strftime('%Y%m%d')

        theme_map = self.loader.load_theme_stock_map()
        if not theme_map:
            print("  [ThemeEngine] 无法加载主题映射")
            return []

        all_stock_codes = set()
        for theme_name, stocks in theme_map.items():
            if isinstance(stocks, list):
                for s in stocks:
                    if isinstance(s, dict) and 'code' in s:
                        all_stock_codes.add(s['code'])
                    elif isinstance(s, str):
                        all_stock_codes.add(s)

        self._preload_bulk(list(all_stock_codes), start_date, td)

        results = []
        for theme_name, raw_stocks in theme_map.items():
            codes = []
            for s in raw_stocks:
                if isinstance(s, dict) and 'code' in s:
                    codes.append(s['code'])
                elif isinstance(s, str):
                    codes.append(s)

            if not codes:
                continue

            etf_code = self.etf_map.get(theme_name, "")

            trend_score = self._calc_theme_trend(codes)
            money_score = self._calc_theme_money(codes, td)
            duration_score = self._calc_theme_duration(codes, td)
            etf_trend = self._calc_etf_trend(etf_code, start_date, td) if etf_code else 0.5
            leader_strength = self._calc_leader_strength(codes)
            momentum_intensity = self._calc_momentum_intensity(codes)

            theme_score = self._calc_theme_score(theme_name)

            w = self.cfg
            composite = (
                theme_score * w.get('theme_score_weight', 0.30) +
                trend_score * w.get('trend_weight', 0.25) +
                money_score * w.get('money_weight', 0.20) +
                duration_score * w.get('duration_weight', 0.10) +
                etf_trend * w.get('etf_trend_weight', 0.10) +
                leader_strength * w.get('leader_strength_weight', 0.05) +
                momentum_intensity * w.get('momentum_weight', 0.15)
            )

            results.append(ThemeResult(
                name=theme_name,
                composite_score=round(composite, 4),
                trend_score=round(trend_score, 4),
                money_score=round(money_score, 4),
                duration_score=round(duration_score, 4),
                etf_trend_score=round(etf_trend, 4),
                leader_strength_score=round(leader_strength, 4),
                momentum_intensity=round(momentum_intensity, 4),
                etf_code=etf_code,
                stock_count=len(codes),
                theme_score=round(theme_score, 4),
            ))

        results.sort(key=lambda x: x.composite_score, reverse=True)
        top_n = self.cfg.get('top_n', 5)
        results = results[:top_n]
        for i, r in enumerate(results):
            r.rank = i + 1

        return results

    def _preload_bulk(self, ts_codes, start_date, end_date):
        if not ts_codes:
            return
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
        except Exception as e:
            print(f"  [ThemeEngine] bulk预加载失败: {e}")
            self._bulk_cache = {}

    def _get_bulk(self, code):
        return self._bulk_cache.get(code)

    def _calc_theme_trend(self, codes: List[str]) -> float:
        scores = []
        for code in codes[:30]:
            df = self._get_bulk(code)
            if df is None or len(df) < 60:
                continue
            close = df['close_hfq'].values if 'close_hfq' in df.columns else df['close'].values
            ma20 = pd.Series(close).rolling(20).mean()
            ma60 = pd.Series(close).rolling(60).mean()
            if pd.isna(ma20.iloc[-1]) or pd.isna(ma60.iloc[-1]):
                continue
            s = 0.5
            if close[-1] > ma20.iloc[-1]:
                s += 0.15
            if ma20.iloc[-1] > ma60.iloc[-1]:
                s += 0.15
            ret_20d = close[-1] / close[-min(20, len(close))] - 1
            if ret_20d > 0.05:
                s += 0.1
            elif ret_20d > 0:
                s += 0.05
            scores.append(min(1.0, s))
        return np.mean(scores) if scores else 0.5

    def _calc_theme_money(self, codes: List[str], trade_date: str) -> float:
        scores = []
        for code in codes[:30]:
            df = self._get_bulk(code)
            if df is None or len(df) < 20:
                continue
            if 'amount' not in df.columns:
                continue
            recent_amount = df['amount'].tail(10)
            prev_amount = df['amount'].iloc[-20:-10] if len(df) >= 20 else df['amount'].head(10)
            if prev_amount.mean() < 1e-6:
                continue
            vol_ratio = recent_amount.mean() / prev_amount.mean()
            s = 0.5
            if vol_ratio > 1.5:
                s += 0.3
            elif vol_ratio > 1.2:
                s += 0.2
            elif vol_ratio > 1.0:
                s += 0.1
            scores.append(min(1.0, s))
        return np.mean(scores) if scores else 0.5

    def _calc_momentum_intensity(self, codes: List[str]) -> float:
        """计算主题的动量爆发强度 - 涨停/大涨出现频率"""
        scores = []
        for code in codes[:30]:
            df = self._get_bulk(code)
            if df is None or len(df) < 20:
                continue
            if 'pct_chg' not in df.columns:
                continue
            recent = df['pct_chg'].tail(20).values
            has_limit = sum(1 for x in recent if x >= 9.5)
            has_big = sum(1 for x in recent if x >= 5)
            s = 0.0
            # 涨停加分（核心）
            if has_limit >= 3:
                s += 0.6
            elif has_limit >= 1:
                s += 0.4
            # 大涨加分（辅助）
            if has_big >= 5:
                s += 0.2
            elif has_big >= 2:
                s += 0.1
            scores.append(min(1.0, s))
        return np.mean(scores) if scores else 0.0

    def _calc_theme_duration(self, codes: List[str], trade_date: str) -> float:
        score = 0.5
        new_high_count_total = 0
        valid_stocks = 0
        for code in codes[:30]:
            df = self._get_bulk(code)
            if df is None or len(df) < 60:
                continue
            close = df['close'].values
            if len(close) < 60:
                continue
            recent_high = max(close[-60:])
            if recent_high > 0:
                if close[-1] >= recent_high * 0.95:
                    new_high_count_total += 1
            valid_stocks += 1
        if valid_stocks > 0:
            nh_ratio = new_high_count_total / valid_stocks
            if nh_ratio > 0.5:
                score = 1.0
            elif nh_ratio > 0.3:
                score = 0.8
            elif nh_ratio > 0.15:
                score = 0.6
        return score

    def _calc_etf_trend(self, etf_code: str, start_date: str, end_date: str) -> float:
        if not etf_code:
            return 0.5
        df = self.loader.load_index_data(etf_code, start_date, end_date, silent=True)
        if df is None or df.empty or len(df) < 20:
            return 0.5
        close = df['close']
        ma20 = sma(close, 20)
        ma60 = sma(close, 60)
        s = 0.5
        if close.iloc[-1] > ma20.iloc[-1]:
            s += 0.15
        if ma20.iloc[-1] > ma60.iloc[-1]:
            s += 0.15
        ret_20d = close.iloc[-1] / close.iloc[-min(20, len(close))] - 1
        if ret_20d > 0.05:
            s += 0.1
        elif ret_20d > 0:
            s += 0.05
        nh = new_high_count(close, 20)
        if nh is not None and len(nh) > 0 and nh.iloc[-1] > 0:
            s += 0.1
        return min(1.0, s)

    def _calc_leader_strength(self, codes: List[str]) -> float:
        rets = []
        for code in codes[:30]:
            df = self._get_bulk(code)
            if df is None or len(df) < 60:
                continue
            close = df['close_hfq'].values if 'close_hfq' in df.columns else df['close'].values
            ret_60d = close[-1] / close[-min(60, len(close))] - 1
            rets.append(ret_60d)
        if not rets:
            return 0.5
        top_ret = sorted(rets, reverse=True)[:max(3, len(rets) // 10)]
        avg_top = np.mean(top_ret)
        if avg_top > 1.0:
            return 1.0
        elif avg_top > 0.5:
            return 0.8
        elif avg_top > 0.3:
            return 0.6
        elif avg_top > 0.1:
            return 0.4
        return 0.2

    def _calc_theme_score(self, theme_name: str) -> float:
        if theme_name in self._theme_score_cache:
            return self._theme_score_cache[theme_name]

        theme_score_path = r"D:\mystock\cache_daily\theme_score.json"
        try:
            if os.path.exists(theme_score_path):
                with open(theme_score_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                scores = data.get('scores', {})
                if theme_name in scores:
                    score = float(scores[theme_name]) / 100.0
                    score = min(1.0, max(0.0, score))
                    self._theme_score_cache[theme_name] = score
                    return score
        except Exception:
            pass

        hot_theme_path = r"D:\mystock\cache_daily\hot_theme.json"
        try:
            if os.path.exists(hot_theme_path):
                with open(hot_theme_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                themes = data.get('themes', data) if isinstance(data, dict) else []
                if isinstance(themes, list):
                    for t in themes:
                        name = t.get('name', t.get('theme_name', ''))
                        score = t.get('score', t.get('hot_score', 50))
                        if name == theme_name:
                            s = float(score) / 100.0
                            s = min(1.0, max(0.0, s))
                            self._theme_score_cache[theme_name] = s
                            return s
                elif isinstance(themes, dict):
                    for name, info in themes.items():
                        if isinstance(info, dict):
                            score = info.get('score', 50)
                        else:
                            score = float(info) if info else 50
                        if name == theme_name:
                            s = float(score) / 100.0
                            s = min(1.0, max(0.0, s))
                            self._theme_score_cache[theme_name] = s
                            return s
        except Exception:
            pass

        return 0.5