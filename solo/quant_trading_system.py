#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
幻方量化交易系统 v1.0
========================
四大模块:
  1. 大势判断 (MarketRegimeJudge)  — V8六状态: 主升加速/震荡轮动/顶部分歧/冰点反弹/主跌退潮
  2. 主题轮动 (ThemeRotationEngine) — 震荡轮动期做轮动/主升加速期做主线
  3. 个股选股 (StockSelector) — 主板5日线斜率/双创底部抬高+长阳
  4. 择时信号 (EntryTiming) — 低吸买入,盈亏比3:1,明确止损

数据源:
  - 回测模式: 通达信本地数据 (etf_alpha_ranking/tdx_reader.py)
  - 每日运行: Tushare + stock_cache 缓存

用法:
  python quant_trading_system.py                        # 每日运行
  python quant_trading_system.py --backtest             # 回测(默认近1年)
  python quant_trading_system.py --backtest --start 20240101 --end 20260722
  python quant_trading_system.py --date 20260722        # 指定日期分析
"""

import sys
import os
import json
import time
import argparse
import warnings
from datetime import datetime, timedelta

# 对接 market_analysis V8 大盘分析模块
SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
if SOLO_DIR not in sys.path:
    sys.path.insert(0, SOLO_DIR)
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# ============================================================
# 路径与环境配置
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "etf_alpha_ranking"))

CACHE_DIR = r"D:\mystock\cache_daily"
os.makedirs(CACHE_DIR, exist_ok=True)

# ============================================================
# 数据源抽象层
# ============================================================

class DataSource:
    """统一数据源接口 — 优先读CSV缓存，回测/每日共用"""

    def __init__(self, mode: str = "tushare", tdx_root: str = r"D:\zd_tdx\vipdoc"):
        self.mode = mode
        self.tdx_root = tdx_root
        self._tdx_reader = None
        self._pro = None
        self._cache_dir = r"D:\mystock\cache_daily"

    def _init_tdx(self):
        if self._tdx_reader is None and self.mode == "tdx" and os.path.exists(self.tdx_root):
            from etf_alpha_ranking.tdx_reader import TDXReader
            self._tdx_reader = TDXReader(tdx_root=self.tdx_root)

    def _init_tushare(self):
        if self._pro is None:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(BASE_DIR), "config", ".env"))
            import tushare as ts
            ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
            self._pro = ts.pro_api()

    def _read_cache_csv(self, code: str, start_date: str, end_date: str, min_rows: int = 20) -> Optional[pd.DataFrame]:
        """从 cache_daily 读取CSV缓存"""
        fpath = os.path.join(self._cache_dir, f"{code}.csv")
        if not os.path.exists(fpath):
            return None
        try:
            df = pd.read_csv(fpath)
            df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
            df = df.sort_values("trade_date").reset_index(drop=True)
            mask = (df["trade_date"] >= pd.Timestamp(start_date)) & (df["trade_date"] <= pd.Timestamp(end_date))
            result = df[mask]
            if len(result) >= min_rows:
                return result.reset_index(drop=True)
        except Exception:
            pass
        return None

    def load_index_daily(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """加载指数日线数据 (优先缓存CSV)"""
        df = self._read_cache_csv(ts_code, start_date, end_date, min_rows=60)
        if df is not None:
            return df
        if self._tdx_reader is not None:
            return self._tdx_reader.load_index(ts_code, start_date, end_date)
        self._init_tushare()
        try:
            df = self._pro.index_daily(
                ts_code=ts_code, start_date=start_date, end_date=end_date,
                fields="ts_code,trade_date,open,close,high,low,vol,amount"
            )
            if df is not None and not df.empty:
                df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
                df = df.sort_values("trade_date").reset_index(drop=True)
            return df
        except Exception:
            return None

    def load_etf_daily(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """加载ETF日线数据 (优先缓存CSV)"""
        df = self._read_cache_csv(ts_code, start_date, end_date, min_rows=20)
        if df is not None:
            return df
        if self._tdx_reader is not None:
            return self._tdx_reader.load_daily_price(ts_code, start_date, end_date)
        self._init_tushare()
        try:
            df = self._pro.fund_daily(
                ts_code=ts_code, start_date=start_date, end_date=end_date,
                fields="ts_code,trade_date,open,close,high,low,vol,amount"
            )
            if df is not None and not df.empty:
                df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
                df = df.sort_values("trade_date").reset_index(drop=True)
            return df
        except Exception:
            return None

    def load_stock_daily(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """加载个股日线数据 (优先缓存CSV，不够再补API)"""
        df = self._read_cache_csv(ts_code, start_date, end_date, min_rows=60)
        if df is not None:
            return df
        if self._tdx_reader is not None:
            return self._tdx_reader.load_daily_price(ts_code, start_date, end_date)
        self._init_tushare()
        try:
            import stock_cache as sc
            df = sc.cached_daily(ts_code, start_date, end_date, pro=self._pro)
            if df is not None and not df.empty:
                df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
                df = df.sort_values("trade_date").reset_index(drop=True)
            return df
        except Exception:
            return None

    def get_trade_calendar(self, start_date: str, end_date: str) -> List[str]:
        """获取交易日列表 (优先从Tushare获取，缓存仅作为备用)"""
        self._init_tushare()
        try:
            cal = self._pro.trade_cal(exchange="", start_date=start_date, end_date=end_date)
            if cal is not None and not cal.empty:
                open_days = cal[cal["is_open"] == 1]["cal_date"].tolist()
                return [str(d) for d in sorted(open_days)]
        except Exception:
            pass
        sh_df = self._read_cache_csv("000001.SH", start_date, end_date, min_rows=5)
        if sh_df is not None and len(sh_df) > 0:
            return sorted(sh_df["trade_date"].dt.strftime("%Y%m%d").unique().tolist())
        if self._tdx_reader is not None:
            try:
                df = self._tdx_reader.load_index("000001.SH", start_date, end_date)
                if df is not None and not df.empty:
                    return sorted(df["trade_date"].dt.strftime("%Y%m%d").unique().tolist())
            except Exception:
                pass
        date_range = pd.date_range(start=start_date, end_date=end_date, freq="B")
        return [d.strftime("%Y%m%d") for d in date_range]


# ============================================================
# ETF池配置 (复用 etf_mainline_strategy_tushare.py)
# ============================================================

ETF_POOL = {
    "半导体": "512480", "芯片": "159995", "半导体设备": "159516",
    "人工智能": "159819", "软件": "515230", "通信": "515880",
    "消费电子": "159732", "金融科技": "159851", "游戏": "159869",
    "新能源": "516160", "光伏": "515790", "储能": "159566",
    "电池": "159755", "新能源车": "515030", "创新药": "159992",
    "医疗器械": "159883", "医药": "512010", "军工": "512660",
    "航空航天": "159227", "机器人": "562500", "有色金属": "516650",
    "化工": "159870", "煤炭": "515220", "钢铁": "515210",
    "电力": "159611", "电网设备": "561380", "消费": "159928",
    "食品饮料": "159736", "酒": "512690", "家电": "159996",
    "证券": "512880", "银行": "512800", "红利": "515180",
    "工业母机": "159667", "科创半导体": "588170",
}

SH_INDEX = "000001.SH"
HS300_INDEX = "000300.SH"
ZZ2000_INDEX = "000852.SH"


def _etf_code_to_ts(code: str) -> str:
    if code.startswith("5") or code.startswith("6"):
        return f"{code}.SH"
    return f"{code}.SZ"


# ============================================================
# Module 1: 大势判断 (Market Regime Judge)
# ============================================================

@dataclass
class MarketRegimeResult:
    regime: str = "震荡轮动期"
    regime_score: float = 50.0
    position_pct: float = 0.0
    position_range: str = "0%"
    trend_score: float = 50.0
    sentiment_score: float = 50.0
    detail: Dict = field(default_factory=dict)


class MarketRegimeJudge:
    """
    大势判断引擎 (V8 算法)
    
    状态分类 (6种):
    - 主升加速期: 趋势>=75, 情绪>=70, 趋势与情绪共振
    - 震荡轮动期: 趋势50-75, 结构性行情
    - 顶部分歧期: 趋势>=60 但情绪<40, 趋势在但情绪骤降
    - 冰点反弹期: 趋势30-45, 情绪<25, 试探性建仓
    - 主跌退潮期: 趋势<35, 情绪<30, 空仓等待
    - (默认): 趋势40-50, 偏弱震荡
    
    仓位滞回: 升仓需超过阈值+3分, 降仓需低于阈值-3分, 单日仓位变化上限±15%
    """

    def __init__(self):
        self.last_regime = "震荡轮动期"
        self.last_score = 50.0
        self.last_position = 40.0  # 前一日仓位, 用于滞回

    def judge(self,
              sh_df: pd.DataFrame,
              hs300_df: pd.DataFrame,
              zz2000_df: pd.DataFrame,
              trade_date: str = None,
              ) -> MarketRegimeResult:
        """
        调用 market_analysis.py V8 算法判断市场状态
        包含: 真实市场广度(涨跌家数)、TOP3主题趋势分、仓位滞回、趋势确认
        """
        try:
            import market_analysis as ma

            # 确定交易日: 优先用传入参数, 其次从sh_df推断
            if not trade_date and sh_df is not None and len(sh_df) > 0:
                if "trade_date" in sh_df.columns:
                    raw_date = str(sh_df["trade_date"].values[-1])
                    if "T" in raw_date or "-" in raw_date:
                        trade_date = raw_date[:10].replace("-", "")
                    else:
                        trade_date = raw_date

            # 调用 V8 分析流程 — 直接用tushare拉取最新数据, 绕过缓存
            start_dt = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")

            # 拉取全市场涨跌家数 (用于BREADTH_SCORE)
            daily_df = ma.pro.daily(trade_date=trade_date)
            up_count = int((daily_df["pct_chg"] > 0).sum()) if daily_df is not None and not daily_df.empty else 0
            down_count = int((daily_df["pct_chg"] < 0).sum()) if daily_df is not None and not daily_df.empty else 0
            total_count = up_count + down_count
            total_amount = float(daily_df["amount"].sum() / 100000) if daily_df is not None and not daily_df.empty else 0  # 千元→亿元

            # 涨停数据
            try:
                zt_df = ma.pro.limit_list_ths(trade_date=trade_date, limit_type="涨停池")
                zt_count = len(zt_df) if zt_df is not None else 0
            except:
                zt_count = 0
            zhaban_rate = 0.0

            overview = {
                'up_count': up_count, 'down_count': down_count,
                'total_amount': total_amount, 'zt_count': zt_count, 'zb_rate': zhaban_rate,
            }

            indices = {
                "上证指数": "000001.SH",
                "沪深300": "000300.SH",
                "中证2000": "932000.CSI"
            }

            results = []
            start_dt = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
            print(f"[V8] trade_date={trade_date} up={up_count} down={down_count} total={total_count}")
            for name, code in indices.items():
                # 直接用 tushare 拉取, 绕过 market_analysis 缓存
                df = ma.pro.index_daily(ts_code=code, start_date=start_dt, end_date=trade_date)
                if df is None or df.empty:
                    continue
                df = df.sort_values('trade_date').reset_index(drop=True)
                if df is None or df.empty:
                    continue
                latest = df.iloc[-1]
                up_count = overview.get('up_count', 0)
                total_count = overview.get('up_count', 0) + overview.get('down_count', 0)
                trend_score, trend_status, trend_detail = ma.calc_trend_score(df, up_count, total_count)
                zt_count = overview.get('zt_count', 0)
                zhaban_rate = overview.get('zb_rate', 0)
                sentiment_score, sentiment_status = ma.calc_sentiment_score(
                    df, zt_count, zhaban_rate, overview.get('total_amount', 0))
                results.append({
                    "name": name, "code": code,
                    "trend_score": trend_score, "trend_status": trend_status,
                    "sentiment_score": sentiment_score, "sentiment_status": sentiment_status,
                    "close": latest['close'], "pct_chg": latest.get('pct_chg', 0),
                    "trend_detail": trend_detail,
                })

            if not results:
                return self._fallback_judge(sh_df, hs300_df, zz2000_df)

            # TOP3 主题趋势分
            theme_top3_scores = ma.get_top3_theme_scores(trade_date)

            # 市场趋势总评分
            trend_score, index_trend, theme_trend = ma.calculate_market_trend_score(
                results, theme_top3_scores, trade_date)

            prev_position = ma._get_prev_position(trade_date)
            prev_trend_score = ma._get_prev_trend_score(trade_date)
            avg_sentiment = sum(r['sentiment_score'] for r in results) / len(results)

            zhaban_rate = overview.get('zb_rate', 0)
            market_status, position_range, position = ma.get_market_status_and_position(
                trend_score, prev_position=prev_position, sentiment_score=avg_sentiment)

            market_regime, regime_reason = ma.classify_market_regime(
                trend_score, avg_sentiment, zhaban_rate=zhaban_rate,
                prev_trend_score=prev_trend_score)

            # V8.1 三重仓位过滤器
            recent_scores = ma._get_recent_trend_scores(trade_date, days=10)
            ma20_slope, ma20_down = ma._calc_ma20_slope(results)
            position, filter_reasons = ma._apply_position_filters(
                position, trend_score, market_regime,
                prev_trend_score, recent_scores,
                ma20_slope, ma20_down, results
            )

            # 如果仓位被过滤器压低，同步更新市场状态
            if filter_reasons:
                if position <= 10:
                    if market_regime not in ("主跌退潮期", "冰点反弹期"):
                        market_regime = "冰点反弹期"
                elif position <= 25:
                    if market_regime in ("主升加速期",):
                        market_regime = "震荡轮动期"
                filter_note = " | [V8.1过滤] " + " | ".join(filter_reasons)
                regime_reason += filter_note

            portfolio_structure = ma.suggest_portfolio_structure(market_regime)

            # 映射到交易系统的状态
            regime_mapped = self._map_regime(market_regime, trend_score)

            # 指数详情
            sh_s = next((r['trend_score'] for r in results if r['name'] == '上证指数'), 50)
            hs300_s = next((r['trend_score'] for r in results if r['name'] == '沪深300'), 50)
            zz2000_s = next((r['trend_score'] for r in results if r['name'] == '中证2000'), 50)

            return MarketRegimeResult(
                regime=regime_mapped,
                regime_score=round(trend_score, 2),
                position_pct=float(position),
                position_range=position_range,
                trend_score=round(trend_score, 2),
                sentiment_score=round(avg_sentiment, 2),
                detail={
                    "sh_score": round(sh_s, 2),
                    "hs300_score": round(hs300_s, 2),
                    "zz2000_score": round(zz2000_s, 2),
                    "regime_reason": regime_reason,
                    "structure": portfolio_structure,
                    "index_trend": round(index_trend, 2),
                    "theme_trend": round(theme_trend, 2),
                    "market_regime": market_regime,
                },
            )

        except Exception as e:
            print(f"[V8对接] 调用market_analysis失败: {e}, 使用内置算法")
            return self._fallback_judge(sh_df, hs300_df, zz2000_df)

    def _map_regime(self, ma_regime: str, trend_score: float) -> str:
        """将 market_analysis 的状态映射到交易系统状态"""
        # 直接使用 V8 状态名，但根据仓位决定是否交易
        if ma_regime in ("主跌退潮期",):
            return "主跌退潮期"
        if ma_regime in ("顶部分歧期",):
            return "顶部分歧期"
        if ma_regime in ("主升加速期",):
            return "主升加速期"
        if ma_regime in ("震荡轮动期", "冰点反弹期"):
            return ma_regime
        return ma_regime

    def _fallback_judge(self, sh_df, hs300_df, zz2000_df) -> MarketRegimeResult:
        """降级方案: 使用内置简化算法 (当market_analysis不可用时)"""
        sh_score = self._calc_v8_trend_score(sh_df)
        hs300_score = self._calc_v8_trend_score(hs300_df)
        zz2000_score = self._calc_v8_trend_score(zz2000_df)
        trend_score = sh_score * 0.50 + hs300_score * 0.30 + zz2000_score * 0.20
        sentiment = (self._calc_v8_sentiment(sh_df) + self._calc_v8_sentiment(hs300_df) + self._calc_v8_sentiment(zz2000_df)) / 3.0
        trend_score = max(0, min(100, trend_score))
        regime, regime_reason = self._classify_regime_v8(trend_score, sentiment)
        position, position_range = self._get_position_v8(trend_score)
        if regime == "顶部分歧期":
            position = 25.0
            position_range = "20~30%"
        return MarketRegimeResult(
            regime=regime, regime_score=round(trend_score, 2),
            position_pct=position, position_range=position_range,
            trend_score=round(trend_score, 2), sentiment_score=round(sentiment, 2),
            detail={"sh_score": round(sh_score, 2), "hs300_score": round(hs300_score, 2),
                    "zz2000_score": round(zz2000_score, 2), "regime_reason": regime_reason},
        )

    def _calc_v8_trend_score(self, df: pd.DataFrame) -> float:
        """
        V8 趋势分 = MA_SCORE(50) + INDEX_SCORE(30) + BREADTH(20)
        MA_SCORE: 均线排列 + 动量加速检测
        INDEX_SCORE: 价格站上均线层级
        BREADTH: 量能/波动率替代市场广度
        """
        if df is None or len(df) < 20:
            return 50.0

        close = df["close"].values
        n = len(close)

        ma5 = np.mean(close[-5:]) if n >= 5 else close[-1]
        ma10 = np.mean(close[-10:]) if n >= 10 else close[-1]
        ma20 = np.mean(close[-20:]) if n >= 20 else close[-1]

        # MA5斜率 (5日动量加速)
        ma5_slope = 0.0
        if n >= 10:
            ma5_prev = np.mean(close[-10:-5])
            if ma5_prev > 0:
                ma5_slope = (ma5 - ma5_prev) / ma5_prev * 100

        ma20_slope = 0.0
        if n >= 40:
            ma20_prev = np.mean(close[-40:-20])
            if ma20_prev > 0:
                ma20_slope = (ma20 - ma20_prev) / ma20_prev * 100

        # --- MA_SCORE (50分) ---
        if ma5 > ma10 > ma20:
            if ma5_slope > 2 and ma20_slope > 0.5:
                ma_score = 50  # 加速上涨
            elif ma5_slope < 0.5:
                ma_score = 40  # 多头减速
            else:
                ma_score = 45  # 多头平稳
        elif ma5 > ma10:
            ma_score = 38
        elif ma5 > ma20:
            ma_score = 25
        elif ma5 < ma10 < ma20:
            if ma5_slope > 0:
                ma_score = 22  # 空头企稳
            else:
                ma_score = 12  # 空头加速
        else:
            ma_score = 18

        # --- INDEX_SCORE (30分) ---
        if close[-1] > ma20:
            index_score = 30
        elif close[-1] > ma10:
            index_score = 20
        elif close[-1] > ma5:
            index_score = 10
        else:
            index_score = 0

        # --- BREADTH (20分) -- 用量能比值替代市场广度 ---
        vol = df["vol"].values if "vol" in df.columns else None
        breadth_score = 10
        if vol is not None and n >= 20:
            vol5 = np.mean(vol[-5:])
            vol20 = np.mean(vol[-20:])
            if vol20 > 0:
                vr = vol5 / vol20
                if vr > 1.3:
                    breadth_score = 20
                elif vr > 1.1:
                    breadth_score = 16
                elif vr > 0.9:
                    breadth_score = 12
                elif vr > 0.7:
                    breadth_score = 8
                else:
                    breadth_score = 4

        return ma_score + index_score + breadth_score

    def _calc_v8_sentiment(self, df: pd.DataFrame) -> float:
        """
        V8 情绪分 = 方向(25) + 量能(20) + 振幅(15) + 连涨跌(20) = 80分 (缩放到100)
        """
        if df is None or len(df) < 20:
            return 50.0

        close = df["close"].values
        n = len(close)
        pct_chg = df["pct_chg"].values[-1] if "pct_chg" in df.columns else 0

        # 1. 涨跌方向 (25分)
        if pct_chg >= 2:
            dir_score = 25
        elif pct_chg >= 1:
            dir_score = 18
        elif pct_chg >= 0:
            dir_score = 10
        elif pct_chg >= -1:
            dir_score = 5
        elif pct_chg >= -2:
            dir_score = 0
        else:
            dir_score = -15

        # 2. 量能变化 (20分)
        vol = df["vol"].values if "vol" in df.columns else [1]*n
        vol5 = np.mean(vol[-5:])
        vol20 = np.mean(vol[-20:])
        vol_ratio = vol5 / vol20 if vol20 > 0 else 1.0
        vol_score = 10 + (vol_ratio - 1) * 15
        if pct_chg < -1 and vol_ratio > 1.2:
            vol_score -= 8
        elif pct_chg > 1 and vol_ratio > 1.2:
            vol_score += 4
        vol_score = max(0, min(20, vol_score))

        # 3. 振幅 (15分)
        high = df["high"].values if "high" in df.columns else close
        low = df["low"].values if "low" in df.columns else close
        amp = (high[-1] - low[-1]) / low[-1] * 100 if low[-1] > 0 else 2
        if pct_chg < 0:
            amp_score = max(0, 7.5 - amp * 0.5)
        else:
            amp_score = min(15, 7.5 + amp * 0.4)

        # 4. 连涨连跌 (20分)
        up_streak = 0
        for i in range(1, min(6, n)):
            if close[-i] > close[-i-1]:
                up_streak += 1
            else:
                break
        down_streak = 0
        for i in range(1, min(6, n)):
            if close[-i] < close[-i-1]:
                down_streak += 1
            else:
                break
        streak_score = 10 + up_streak * 2.5 - down_streak * 3
        streak_score = max(0, min(20, streak_score))

        # 总分缩放到100
        return (dir_score + vol_score + amp_score + streak_score) / 80.0 * 100

    def _classify_regime_v8(self, trend_score: float, sentiment: float) -> Tuple[str, str]:
        """V8 6状态分类 + 趋势确认"""
        prev_score = self.last_score

        # 趋势确认: 前日极低分(<30)反弹时需确认
        if prev_score < 30:
            if trend_score >= 50:
                return "冰点反弹期", f"前日趋势分{prev_score:.0f}极低，今日反弹至{trend_score:.0f}但仍需确认，试探性建仓严格止损"
            elif trend_score >= 40:
                return "冰点反弹期", f"前日趋势分{prev_score:.0f}，今日反弹至{trend_score:.0f}，情绪修复中"

        if trend_score >= 75 and sentiment >= 70:
            return "主升加速期", "趋势与情绪共振，重仓主线龙头"
        if trend_score >= 60 and sentiment < 35:
            return "顶部分歧期", "趋势在但情绪骤降，减仓兑现"
        if 50 <= trend_score < 75:
            return "震荡轮动期", "结构性行情为主，高抛低吸"
        if 30 <= trend_score < 45 and sentiment < 25:
            return "冰点反弹期", "情绪冰点，试探性建仓超跌反弹"
        if trend_score < 35 and sentiment < 30:
            return "主跌退潮期", "趋势与情绪双弱，空仓等待"
        if trend_score >= 45:
            return "震荡轮动期", "结构性行情为主"
        return "主跌退潮期", "市场偏弱，空仓或极轻仓"

    def _get_position_v8(self, trend_score: float) -> Tuple[float, str]:
        """V8 仓位计算 (带滞回)"""
        tiers = [
            (80, 75, "70~80%"),
            (70, 60, "55~70%"),
            (60, 50, "40~60%"),
            (50, 40, "30~50%"),
            (40, 25, "20~30%"),
            (35, 10, "5~15%"),
            (0,  0,  "0~5%"),
        ]

        # 找当前档位
        tier_idx = 0
        for i, (th, pos, pr) in enumerate(tiers):
            if trend_score >= th:
                tier_idx = i
                break

        raw_position = tiers[tier_idx][1]

        # 滞回: 非极端区域避免频繁切换
        if 30 < trend_score < 80 and self.last_position > 0:
            prev_tier_idx = 0
            for i, (th, pos, pr) in enumerate(tiers):
                if self.last_position >= pos - 5:
                    prev_tier_idx = i
                    break
            if abs(tier_idx - prev_tier_idx) <= 1:
                threshold = tiers[min(tier_idx, prev_tier_idx)][0]
                if abs(trend_score - threshold) <= 3:
                    tier_idx = prev_tier_idx
                    raw_position = tiers[tier_idx][1]

        # 单日仓位变化上限 ±15%
        if self.last_position > 0:
            if raw_position > self.last_position + 15:
                for i, (th, pos, pr) in enumerate(tiers):
                    if pos <= self.last_position + 15:
                        tier_idx = i
                        raw_position = tiers[tier_idx][1]
                        break

        self.last_position = raw_position
        self.last_score = trend_score

        return raw_position, tiers[tier_idx][2]

    def _suggest_structure(self, regime: str) -> Dict:
        """V8 持仓结构建议"""
        structure_map = {
            "主升加速期": {
                "集中度": "集中3-5只", "持有周期": "3-5日",
                "止损": "5-8%", "选股偏好": "追强主线龙头",
            },
            "震荡轮动期": {
                "集中度": "分散5-8只", "持有周期": "T+1~T+2",
                "止损": "3%", "选股偏好": "低吸回流",
            },
            "顶部分歧期": {
                "集中度": "减至2-3只", "持有周期": "T+1",
                "止损": "2-3%", "选股偏好": "防御/低位补涨",
            },
            "主跌退潮期": {
                "集中度": "空仓或1-2只", "持有周期": "不参与",
                "止损": "不适用", "选股偏好": "管住手",
            },
            "冰点反弹期": {
                "集中度": "试探2-3只", "持有周期": "1-3日",
                "止损": "3-5%", "选股偏好": "超跌+缩量到位",
            },
        }
        return structure_map.get(regime, {
            "集中度": "3-5只", "持有周期": "短线",
            "止损": "3-5%", "选股偏好": "均衡",
        })


# ============================================================
# Module 2: 主题轮动引擎 (Theme Rotation Engine)
# ============================================================

@dataclass
class ThemeRankResult:
    name: str
    code: str
    ts_code: str
    total_score: float
    momentum: float
    vol_score: float
    risk_adj: float
    rel_strength: float
    etf_df: Optional[pd.DataFrame] = None


class ThemeRotationEngine:
    """
    主题轮动引擎
    - 震荡轮动期: 选TOP3主题做轮动 (分散)
    - 主升加速期: 选TOP1主题做主线 (集中)
    """

    def __init__(self, data_source: DataSource):
        self.ds = data_source
        self.etf_ts_codes = {code: _etf_code_to_ts(code) for code in ETF_POOL.values()}

    def rank_etfs(self, trade_date: str) -> List[ThemeRankResult]:
        """多因子动量评分排名ETF"""
        end_date = trade_date
        start_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")

        rankings = []
        for name, code in ETF_POOL.items():
            ts_code = self.etf_ts_codes[code]
            df = self.ds.load_etf_daily(ts_code, start_date, end_date)
            if df is None or len(df) < 25:
                continue

            close = df["close"].values
            n = len(close)

            mom_20d = (close[-1] / close[-21] - 1) * 100 if n >= 21 else 0

            vol = df["vol"].values if "vol" in df.columns else np.ones(n)
            recent_vol = np.mean(vol[-5:]) if n >= 5 else 0
            prev_vol = np.mean(vol[-20:-5]) if n >= 20 else 0
            vol_ratio = recent_vol / (prev_vol + 1e-6)
            vol_score = min(max(vol_ratio * 50, 0), 100)

            daily_returns = np.diff(np.log(close)) * 100
            if len(daily_returns) >= 20:
                volatility = np.std(daily_returns[-20:]) * np.sqrt(252)
                if volatility > 0:
                    sharpe_like = mom_20d / volatility
                    risk_adj = max(0, min(100, 50 + (sharpe_like - 0.5) * 50))
                else:
                    risk_adj = 50
            else:
                risk_adj = 50

            rel_score = 50

            mom_score = max(0, min(100, 50 + mom_20d * 2))
            total_score = mom_score * 0.40 + vol_score * 0.25 + risk_adj * 0.20 + rel_score * 0.15

            rankings.append(ThemeRankResult(
                name=name, code=code, ts_code=ts_code,
                total_score=round(total_score, 2),
                momentum=round(mom_20d, 2),
                vol_score=round(vol_score, 2),
                risk_adj=round(risk_adj, 2),
                rel_strength=round(rel_score, 2),
                etf_df=df,
            ))

        rankings.sort(key=lambda x: x.total_score, reverse=True)
        return rankings

    def get_top_themes(self, trade_date: str, regime: str) -> List[ThemeRankResult]:
        """
        根据市场状态选择主题
        - 主升加速期: 选TOP1做主线
        - 震荡轮动期: 选TOP3做轮动
        """
        rankings = self.rank_etfs(trade_date)
        if regime == "主升加速期":
            return rankings[:1]
        elif regime == "震荡轮动期":
            return rankings[:3]
        else:
            return []

    # ETF名称 → 主题名称映射 (一个ETF对应多个主题)
    ETF_TO_THEMES = {
        "半导体": ["半导体制造", "半导体设备", "半导体材料", "AI芯片", "先进封装", "功率半导体"],
        "芯片": ["半导体制造", "半导体设备", "半导体材料", "AI芯片", "先进封装", "功率半导体"],
        "半导体设备": ["半导体设备", "半导体制造", "半导体材料"],
        "科创半导体": ["半导体制造", "半导体设备", "AI芯片", "先进封装"],
        "人工智能": ["AI应用与模型", "AI算力基建", "AI芯片", "软件与IT服务"],
        "软件": ["软件与IT服务", "AI应用与模型"],
        "通信": ["光通信", "AI算力基建", "电信运营商"],
        "消费电子": ["消费电子与AI终端", "品牌消费电子", "光学光电子"],
        "金融科技": ["金融科技", "AI应用与模型"],
        "游戏": ["AI文娱内容", "AI应用与模型"],
        "新能源": ["新能源汽车链", "新型储能", "发电与电源设备"],
        "光伏": ["发电与电源设备", "新型储能", "新能源汽车链"],
        "储能": ["新型储能", "发电与电源设备", "固态电池"],
        "电池": ["固态电池", "新能源汽车链", "新型储能", "能源金属"],
        "新能源车": ["新能源汽车链", "固态电池", "智能驾驶", "汽车零部件"],
        "创新药": ["创新药", "医药产业链", "医疗服务"],
        "医疗器械": ["医疗器械", "医药产业链", "医疗服务"],
        "医药": ["医药产业链", "创新药", "医疗服务", "中药"],
        "军工": ["军工", "商业航天", "低空经济"],
        "航空航天": ["军工", "商业航天", "低空经济", "航空运输"],
        "机器人": ["人形机器人", "工业母机与自动化", "工业智能"],
        "有色金属": ["工业金属", "小金属", "贵金属", "能源金属"],
        "化工": ["化工链", "化工新材料", "化学纤维", "钾肥磷化工"],
        "煤炭": ["煤炭链", "红利公用事业"],
        "钢铁": ["钢铁", "基建地产链", "金属制品"],
        "电力": ["发电与电源设备", "红利公用事业", "电网智能化"],
        "电网设备": ["电网智能化", "特高压", "充电桩", "发电与电源设备"],
        "消费": ["消费白马", "必选消费红利链", "商超零售链", "家电家居链"],
        "食品饮料": ["必选消费红利链", "餐饮食品链", "消费白马"],
        "酒": ["消费白马", "必选消费红利链", "餐饮食品链"],
        "家电": ["家电家居链", "消费白马"],
        "证券": ["券商", "多元金融", "金融科技"],
        "银行": ["银行", "多元金融", "红利公用事业"],
        "红利": ["红利公用事业", "必选消费红利链", "电信运营商"],
        "工业母机": ["工业母机与自动化", "工业智能", "工程机械与重型装备"],
    }

    def get_theme_constituents(self, etf_name: str) -> List[Dict]:
        """
        获取ETF对应的主题成份股列表
        优先从 theme_stock_map_latest.json 读取主题成份股，
        并用 etf_constituents_all.json 的ETF成份股作为补充
        """
        result = []
        seen_codes = set()

        # 1. 从 theme_stock_map_latest.json 读取主题成份股
        map_file = os.path.join(CACHE_DIR, "theme_stock_map_latest.json")
        theme_data = {}
        if os.path.exists(map_file):
            try:
                with open(map_file, "r", encoding="utf-8") as f:
                    theme_data = json.load(f).get("themes", {})
            except Exception:
                pass

        matched_themes = self.ETF_TO_THEMES.get(etf_name, [etf_name])
        for theme_name in matched_themes:
            if theme_name in theme_data:
                for stock in theme_data[theme_name]:
                    code = stock.get("code", "")
                    if code and code not in seen_codes:
                        seen_codes.add(code)
                        result.append(stock)

        # 2. 从 etf_constituents_all.json 补充ETF成份股
        cons_file = os.path.join(CACHE_DIR, "etf_constituents_all.json")
        if os.path.exists(cons_file):
            try:
                with open(cons_file, "r", encoding="utf-8") as f:
                    cons_data = json.load(f)

                etf_code = ETF_POOL.get(etf_name, "")
                if etf_code:
                    ts_code = _etf_code_to_ts(etf_code)
                    etf_info = cons_data.get(ts_code)
                    if etf_info and isinstance(etf_info, dict):
                        cons_list = etf_info.get("constituents", [])
                        for con in cons_list:
                            con_code = con.get("con_code", "")
                            if con_code and con_code not in seen_codes:
                                seen_codes.add(con_code)
                                result.append({
                                    "code": con_code,
                                    "name": con.get("con_name", ""),
                                    "weight": con.get("weight", 0),
                                })
            except Exception:
                pass

        return result


# ============================================================
# Module 3: 个股选股 (Stock Selector)
# ============================================================

@dataclass
class StockPickResult:
    ts_code: str
    name: str
    score: float
    board: str  # "主板" or "双创"
    ma5_slope: float = 0
    bottom_rising: bool = False
    long_yang_count: int = 0
    close: float = 0
    ma5: float = 0
    ma10: float = 0
    ma20: float = 0
    atr: float = 0


class StockSelector:
    """
    个股选择器
    - 主板: 沿5日均线上行斜率大
    - 双创: 底部不断抬高 + 经常出现8%以上长阳
    """

    def __init__(self, data_source: DataSource):
        self.ds = data_source

    def select_stocks(self,
                      candidates: List[Dict],
                      trade_date: str,
                      top_n: int = 10) -> List[StockPickResult]:
        """从候选股票池中选出符合条件的个股"""
        results = []
        for stock in candidates:
            code = stock.get("code", "")
            name = stock.get("name", "")
            if not code:
                continue

            board = self._classify_board(code)
            result = self._evaluate_stock(code, name, board, trade_date)
            if result is not None:
                results.append(result)

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_n]

    def _classify_board(self, ts_code: str) -> str:
        if ts_code.endswith(".SH") and ts_code.startswith("6"):
            return "主板"
        elif ts_code.endswith(".SZ") and (ts_code.startswith("000") or ts_code.startswith("002")):
            return "主板"
        elif ts_code.endswith(".SZ") and ts_code.startswith("300"):
            return "双创"
        elif ts_code.endswith(".SH") and ts_code.startswith("688"):
            return "双创"
        elif ts_code.endswith(".BJ"):
            return "双创"
        return "主板"

    def _evaluate_stock(self, ts_code: str, name: str, board: str,
                        trade_date: str) -> Optional[StockPickResult]:
        """评估单只股票"""
        # 北交所股票直接跳过
        if ts_code.endswith(".BJ") or ".BJ" in ts_code:
            return None
        start_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=250)).strftime("%Y%m%d")
        df = self.ds.load_stock_daily(ts_code, start_date, trade_date)
        if df is None or len(df) < 60:
            return None

        close = df["close"].values
        high = df["high"].values if "high" in df.columns else close
        low = df["low"].values if "low" in df.columns else close
        vol = df["vol"].values if "vol" in df.columns else np.ones(len(close))
        n = len(close)

        # 基本过滤: ST, 流动性, 价格
        if close[-1] < 3 or close[-1] > 300:
            return None
        if "amount" in df.columns:
            avg_amount = df["amount"].values[-20:].mean()
            if avg_amount < 3000:
                return None

        # 计算均线
        ma5 = np.mean(close[-5:]) if n >= 5 else close[-1]
        ma10 = np.mean(close[-10:]) if n >= 10 else close[-1]
        ma20 = np.mean(close[-20:]) if n >= 20 else close[-1]
        ma60 = np.mean(close[-60:]) if n >= 60 else close[-1]

        # 基础趋势过滤: close > MA20 (90%以上股票站上20日线才算趋势)
        if close[-1] <= ma20:
            return None

        # ATR计算
        tr = np.maximum(high[-20:] - low[-20:],
                        np.maximum(np.abs(high[-20:] - np.roll(close[-20:], 1)),
                                   np.abs(low[-20:] - np.roll(close[-20:], 1))))
        tr[0] = high[-20] - low[-20] if n >= 20 else 0
        atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)

        if board == "主板":
            result = self._eval_mainboard(close, high, low, vol, n, ma5, ma10, ma20, atr)
        else:
            result = self._eval_shuangchuang(close, high, low, vol, n, ma5, ma10, ma20, atr)

        if result is None:
            return None

        result["ts_code"] = ts_code
        result["name"] = name
        result["board"] = board
        result["close"] = close[-1]
        result["ma5"] = ma5
        result["ma10"] = ma10
        result["ma20"] = ma20
        result["atr"] = atr

        return StockPickResult(**result)

    def _eval_mainboard(self, close, high, low, vol, n, ma5, ma10, ma20, atr) -> Optional[Dict]:
        """
        主板选股: 沿5日均线上行斜率大

        条件:
        1. close > MA5 (价格在5日线上方)
        2. MA5斜率 > 0.3%/天 (5日线持续上行)
        3. 5日涨幅 > 0 (短期动量)
        4. 量价配合 (上涨放量)
        """
        if n < 30:
            return None

        # 5日线斜率
        ma5_vals = np.array([np.mean(close[max(0, i - 4):i + 1]) for i in range(n - 5, n)])
        if len(ma5_vals) < 5:
            return None
        x = np.arange(len(ma5_vals))
        slope, _ = np.polyfit(x, ma5_vals, 1)
        ma5_slope_pct = slope / ma5_vals[-1] * 100 if ma5_vals[-1] > 0 else 0

        if ma5_slope_pct < 0.2:
            return None

        # 价格在5日线附近或上方
        dist_to_ma5 = (close[-1] / ma5 - 1) * 100
        if dist_to_ma5 < -2:
            return None

        # 5日涨幅
        ret5 = (close[-1] / close[-6] - 1) * 100 if n >= 6 else 0
        if ret5 < -3:
            return None

        # 量价配合
        vol5 = np.mean(vol[-5:]) if n >= 5 else 0
        vol20 = np.mean(vol[-20:]) if n >= 20 else 0
        vol_ratio = vol5 / (vol20 + 1e-6) if vol20 > 0 else 1

        # 计算关键阳线
        yang_count = sum(1 for i in range(1, min(21, n)) if close[-i] > close[-i - 1])

        score = (
            min(ma5_slope_pct * 15, 40) +
            min(ret5 * 2, 20) +
            min(vol_ratio * 8, 15) +
            min(yang_count * 1.5, 15) +
            max(0, 10 - abs(dist_to_ma5) * 5)
        )
        score = max(0, min(100, score))

        if score < 45:
            return None

        return {
            "score": round(score, 2),
            "ma5_slope": round(ma5_slope_pct, 2),
            "bottom_rising": False,
            "long_yang_count": 0,
        }

    def _eval_shuangchuang(self, close, high, low, vol, n, ma5, ma10, ma20, atr) -> Optional[Dict]:
        """
        双创选股: 底部不断抬高 + 经常出现8%以上长阳

        条件:
        1. 最近3个低点逐步抬高 (底部抬升)
        2. 近60个交易日内至少有2天涨幅>8% (长阳活跃)
        3. 量能放大
        4. 趋势向上
        """
        if n < 60:
            return None

        # 底部不断抬高: 找最近3个局部低点 (argmin代替==精确浮点比较)
        lows = []
        window = 10
        for i in range(n - window, n - 1):
            lb = max(0, i - window // 2)
            rb = min(n, i + window // 2 + 1)
            segment = low[lb:rb]
            if len(segment) >= 3:
                min_idx = np.argmin(segment)
                if i == lb + min_idx:
                    lows.append(low[i])
        if len(lows) >= 3:
            lows = lows[-3:]
        else:
            g = sorted(low[-40:])
            if len(g) >= 3:
                lows = g[:3]
            else:
                lows = []

        bottom_rising = False
        if len(lows) >= 3:
            bottom_rising = lows[-1] > lows[-2] > lows[-3]

        # 长阳统计: 近60个交易日中涨幅>8%的天数
        long_yang_count = 0
        for i in range(1, min(61, n)):
            if close[-i] > 0 and close[-i - 1] > 0:
                chg = (close[-i] / close[-i - 1] - 1) * 100
                if chg >= 8:
                    long_yang_count += 1

        if long_yang_count < 2:
            return None

        # 量能
        vol5 = np.mean(vol[-5:]) if n >= 5 else 0
        vol20 = np.mean(vol[-20:]) if n >= 20 else 0
        vol_ratio = vol5 / (vol20 + 1e-6) if vol20 > 0 else 1

        # 5日线斜率
        ma5_vals = np.array([np.mean(close[max(0, i - 4):i + 1]) for i in range(n - 5, n)])
        if len(ma5_vals) >= 5:
            x = np.arange(len(ma5_vals))
            slope, _ = np.polyfit(x, ma5_vals, 1)
            ma5_slope_pct = slope / ma5_vals[-1] * 100 if ma5_vals[-1] > 0 else 0
        else:
            ma5_slope_pct = 0

        # 20日涨幅
        ret20 = (close[-1] / close[-21] - 1) * 100 if n >= 21 else 0

        score = (
            min(long_yang_count * 8, 35) +
            min(ret20 * 1.5, 25) +
            min(vol_ratio * 8, 15) +
            min(ma5_slope_pct * 10, 15) +
            (10 if close[-1] > ma20 else 5) +
            (10 if bottom_rising else 0)
        )
        score = max(0, min(100, score))

        if score < 40:
            return None

        return {
            "score": round(score, 2),
            "bottom_rising": bottom_rising,
            "long_yang_count": long_yang_count,
            "ma5_slope": round(ma5_slope_pct, 2),
        }


# ============================================================
# Module 4: 择时信号 (Entry Timing)
# ============================================================

@dataclass
class BuySignal:
    ts_code: str
    name: str
    board: str
    theme: str = ""
    stock_score: float = 0
    entry_price: float = 0
    stop_loss: float = 0
    take_profit: float = 0
    risk_reward_ratio: float = 0
    signal_strength: str = "弱"  # "强" / "中" / "弱"
    signal_reason: str = ""
    atr: float = 0
    current_price: float = 0
    ma5: float = 0
    ma10: float = 0
    ma20: float = 0


class EntryTiming:
    """
    择时引擎
    - 低吸买入信号
    - 盈亏比 >= 3:1
    - 明确止损价格
    """

    def check_buy_signal(self, ts_code: str, name: str, board: str,
                         trade_date: str, ds: DataSource) -> Optional[BuySignal]:
        """检查低吸买入信号"""
        start_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")
        df = ds.load_stock_daily(ts_code, start_date, trade_date)
        if df is None or len(df) < 30:
            return None

        close = df["close"].values
        high = df["high"].values if "high" in df.columns else close
        low = df["low"].values if "low" in df.columns else close
        vol = df["vol"].values if "vol" in df.columns else np.ones(len(close))
        n = len(close)

        ma5 = np.mean(close[-5:]) if n >= 5 else close[-1]
        ma10 = np.mean(close[-10:]) if n >= 10 else close[-1]
        ma20 = np.mean(close[-20:]) if n >= 20 else close[-1]

        # ATR
        tr = np.maximum(high[-20:] - low[-20:],
                        np.maximum(np.abs(high[-20:] - np.roll(close[-20:], 1)),
                                   np.abs(low[-20:] - np.roll(close[-20:], 1))))
        tr[0] = high[-20] - low[-20] if n >= 20 else 0
        atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)

        current_price = close[-1]

        # ========================
        # 低吸条件检测
        # ========================
        reasons = []

        # 条件1: 价格回调到5日线/10日线附近 (距离 < 2%)
        dist_to_ma5 = (current_price / ma5 - 1) * 100
        dist_to_ma10 = (current_price / ma10 - 1) * 100
        near_ma = (abs(dist_to_ma5) <= 2.5) or (abs(dist_to_ma10) <= 2.5)
        if near_ma:
            reasons.append(f"回调至均线(MA5距{dist_to_ma5:+.1f}%, MA10距{dist_to_ma10:+.1f}%)")

        # 条件2: 缩量后放量
        vol5 = np.mean(vol[-5:]) if n >= 5 else 0
        vol10 = np.mean(vol[-10:-5]) if n >= 10 else 0
        vol20 = np.mean(vol[-20:-5]) if n >= 20 else 0
        vol_shrink = vol10 < vol20 * 0.85 if vol20 > 0 else False
        vol_expand = vol5 > vol10 * 1.1 if vol10 > 0 else False
        if vol_shrink and vol_expand:
            reasons.append("缩量后放量企稳")

        # 条件3: KDJ J值低位
        kdj_j = self._calc_kdj_j(close, high, low, n)
        if kdj_j is not None and kdj_j < 30:
            reasons.append(f"KDJ低位(J={kdj_j:.1f})")

        # 条件4: MACD 柱状线收窄或即将翻红
        macd_status = self._calc_macd_status(close, n)
        if macd_status in ("收缩", "金叉"):
            reasons.append(f"MACD{macd_status}")

        # 条件5: 价格在MA20上方 (中期趋势向上)
        if current_price > ma20:
            reasons.append("MA20上方")
        else:
            reasons.append("跌破MA20(注意风险)")

        # 条件6: 近3日有下影线 (支撑确认)
        has_shadow = False
        for i in range(1, min(4, n)):
            body = abs(close[-i] - (df["open"].values[-i] if "open" in df.columns else close[-i - 1]))
            lower_shadow = min(close[-i], (df["open"].values[-i] if "open" in df.columns else close[-i - 1])) - low[-i]
            if lower_shadow > body * 0.5 and lower_shadow > 0:
                has_shadow = True
                break
        if has_shadow:
            reasons.append("下影线支撑")

        if len(reasons) < 2:
            return None

        # ========================
        # 止损与止盈计算 (盈亏比3:1)
        # ========================
        stop_loss = self._calc_stop_loss(current_price, atr, low, ma10, ma20, n)
        take_profit = current_price + 3 * (current_price - stop_loss)

        risk = current_price - stop_loss
        reward = take_profit - current_price
        rr_ratio = reward / risk if risk > 0 else 0

        if rr_ratio < 2.8:
            return None

        # 信号强度
        signal_len = len(reasons)
        if signal_len >= 4:
            signal_strength = "强"
        elif signal_len >= 3:
            signal_strength = "中"
        else:
            signal_strength = "弱"

        # 股票评分(基于信号条件)
        stock_score = min(signal_len * 18 +
                          (10 if current_price > ma20 else 0) +
                          (10 if near_ma else 0) +
                          (8 if vol_shrink and vol_expand else 0), 100)

        return BuySignal(
            ts_code=ts_code,
            name=name,
            board=board,
            stock_score=round(stock_score, 2),
            entry_price=round(current_price, 2),
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            risk_reward_ratio=round(rr_ratio, 2),
            signal_strength=signal_strength,
            signal_reason="; ".join(reasons),
            atr=round(atr, 2),
            current_price=round(current_price, 2),
            ma5=round(ma5, 2),
            ma10=round(ma10, 2),
            ma20=round(ma20, 2),
        )

    def _calc_stop_loss(self, price: float, atr: float, low: np.ndarray,
                        ma10: float, ma20: float, n: int) -> float:
        """计算止损价格"""
        atr_stop = price - 2.0 * atr
        ma10_stop = ma10 * 0.98
        ma20_stop = ma20 * 0.97

        recent_low = np.min(low[-10:]) if n >= 10 else low[-1]
        recent_low_stop = recent_low * 0.99

        candidates = [atr_stop, ma10_stop, ma20_stop, recent_low_stop]
        candidates = [c for c in candidates if c > 0 and c < price * 0.95]

        if candidates:
            return max(candidates)
        return price * 0.93

    def _calc_kdj_j(self, close: np.ndarray, high: np.ndarray, low: np.ndarray, n: int) -> Optional[float]:
        """计算KDJ J值"""
        period = 9
        if n < period + 5:
            return None
        low_n = np.min(low[-period:])
        high_n = np.max(high[-period:])
        if high_n == low_n:
            return 50.0
        rsv = (close[-1] - low_n) / (high_n - low_n) * 100
        k = rsv * 1 / 3 + 50 * 2 / 3
        d = k * 1 / 3 + 50 * 2 / 3
        j = 3 * k - 2 * d
        return j

    def _calc_macd_status(self, close: np.ndarray, n: int) -> str:
        """判断MACD状态"""
        if n < 30:
            return "未知"
        ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
        ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
        dif = ema12 - ema26
        dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
        macd_hist = 2 * (dif - dea)

        if macd_hist[-1] > macd_hist[-2] and macd_hist[-2] < 0:
            return "收缩"
        if dif[-1] > dea[-1] and dif[-2] <= dea[-2]:
            return "金叉"
        if dif[-1] > dea[-1]:
            return "多头"
        return "空头"


# ============================================================
# Module 5: 回测引擎 (Backtest Engine)
# ============================================================

@dataclass
class Trade:
    ts_code: str
    name: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    exit_reason: str
    pnl_pct: float
    shares: int = 0
    stop_loss: float = 0
    take_profit: float = 0


@dataclass
class BacktestResult:
    total_return: float = 0
    annual_return: float = 0
    sharpe_ratio: float = 0
    max_drawdown: float = 0
    win_rate: float = 0
    total_trades: int = 0
    avg_return: float = 0
    equity_curve: List[float] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    dates: List[str] = field(default_factory=list)


class BacktestEngine:
    """
    回测引擎
    使用通达信数据源进行历史回测
    """

    def __init__(self, ds: DataSource, initial_capital: float = 1_000_000):
        self.ds = ds
        self.initial_capital = initial_capital
        self.regime_judge = MarketRegimeJudge()
        self.theme_engine = ThemeRotationEngine(ds)
        self.stock_selector = StockSelector(ds)
        self.entry_timing = EntryTiming()

        self.max_positions = 5
        self.commission = 0.0003
        self.slippage = 0.001
        self.max_hold_days = 20

        self.cooldown_codes = {}  # 冷却期: {ts_code: 剩余冷却天数}
        self.cooldown_days = 10   # 止损后冷却10个交易日
        self.max_per_theme = 2    # 同一主题最多持有2只
        self.consecutive_stops = 0    # 连续止损计数
        self.trade_pause_until = 0    # 暂停开仓直到第N天(索引)

    def run(self, start_date: str, end_date: str) -> BacktestResult:
        """运行回测"""
        trade_dates = self.ds.get_trade_calendar(start_date, end_date)
        print(f"[回测] 交易日范围: {trade_dates[0]} ~ {trade_dates[-1]}, 共{len(trade_dates)}天")

        cash = self.initial_capital
        positions: Dict[str, Dict] = {}
        all_trades: List[Trade] = []
        equity_curve = []
        dates = []

        scan_interval = 5  # 每5个交易日扫描一次信号
        cached_signals = []  # 缓存的买入信号
        last_scan_idx = -1

        total_active = len([d for d in trade_dates[60:]])
        processed = 0

        for i, date_str in enumerate(trade_dates):
            if i < 60:
                equity_curve.append(self.initial_capital)
                dates.append(date_str)
                continue

            processed += 1
            if processed % 10 == 0:
                print(f"  [{date_str}] 进度 {processed}/{total_active} 持仓{len(positions)}只", flush=True)

            # 检查持仓退出
            closed = []
            for code, pos in list(positions.items()):
                exit_result = self._check_exit(code, pos, date_str)
                if exit_result:
                    pnl = exit_result["pnl"]
                    cash += pos["capital"] + pnl
                    trade = Trade(
                        ts_code=code,
                        name=pos["name"],
                        entry_date=pos["entry_date"],
                        entry_price=pos["entry_price"],
                        exit_date=date_str,
                        exit_price=exit_result["price"],
                        exit_reason=exit_result["reason"],
                        pnl_pct=round(pnl / pos["capital"] * 100, 2),
                        shares=pos["shares"],
                        stop_loss=pos["stop_loss"],
                        take_profit=pos["take_profit"],
                    )
                    all_trades.append(trade)
                    closed.append(code)
                    # 止损触发 → 加入冷却期 + 连续止损计数
                    if exit_result["reason"] == "止损":
                        self.cooldown_codes[code] = self.cooldown_days
                        self.consecutive_stops += 1
                        if self.consecutive_stops >= 3:
                            self.trade_pause_until = i + 5  # 暂停5天
                    if trade.pnl_pct > 0:
                        self.consecutive_stops = 0  # 盈利重置计数

            for code in closed:
                del positions[code]

            # 大势判断
            regime_result = self._judge_regime_for_date(date_str)
            if regime_result is None:
                equity_curve.append(cash + sum(p.get("capital", 0) for p in positions.values()))
                dates.append(date_str)
                continue

            # 仅主跌退潮期清仓; 顶部分歧期降仓但不强制清仓
            if regime_result.regime == "主跌退潮期":
                for code, pos in list(positions.items()):
                    exit_price = self._get_close_price(code, date_str)
                    if exit_price:
                        pnl = (exit_price - pos["entry_price"]) * pos["shares"]
                        cash += pos["capital"] + pnl
                        all_trades.append(Trade(
                            ts_code=code, name=pos["name"],
                            entry_date=pos["entry_date"], entry_price=pos["entry_price"],
                            exit_date=date_str, exit_price=exit_price,
                            exit_reason="市场转空", pnl_pct=round(pnl / pos["capital"] * 100, 2),
                            shares=pos["shares"], stop_loss=pos["stop_loss"],
                            take_profit=pos["take_profit"],
                        ))
                positions.clear()
                equity_curve.append(cash)
                dates.append(date_str)
                continue

            if regime_result.regime == "顶部分歧期":
                # 顶部分歧: 降仓不清仓, 不开新仓, 让已有持仓走自己的止盈止损
                # 如果持仓超过降仓目标, 平掉超出的部分
                target_positions = 2  # 顶部分歧期最多保留2只
                if len(positions) > target_positions:
                    # 按持仓时间排序, 优先平掉近期开仓的
                    sorted_pos = sorted(positions.items(), key=lambda x: -x[1]["hold_days"])
                    for code, pos in sorted_pos[target_positions:]:
                        exit_price = self._get_close_price(code, date_str)
                        if exit_price:
                            pnl = (exit_price - pos["entry_price"]) * pos["shares"]
                            cash += pos["capital"] + pnl
                            all_trades.append(Trade(
                                ts_code=code, name=pos["name"],
                                entry_date=pos["entry_date"], entry_price=pos["entry_price"],
                                exit_date=date_str, exit_price=exit_price,
                                exit_reason="顶部分歧减仓", pnl_pct=round(pnl / pos["capital"] * 100, 2),
                                shares=pos["shares"], stop_loss=pos["stop_loss"],
                                take_profit=pos["take_profit"],
                            ))
                            del positions[code]

            # 建仓/加仓 (每 scan_interval 天扫描一次)
            if self.trade_pause_until > 0 and i < self.trade_pause_until:
                pass  # 连续止损暂停期，跳过开仓
            elif regime_result.regime == "顶部分歧期":
                pass  # 顶部分歧期不开新仓
            elif len(positions) < self.max_positions and regime_result.position_pct > 0:
                if i - last_scan_idx >= scan_interval or not cached_signals:
                    cached_signals = self._generate_signals_for_date(date_str, regime_result)
                    last_scan_idx = i

                available_slots = self.max_positions - len(positions)
                max_new = min(available_slots, int(regime_result.position_pct / 100 * self.max_positions))

                for signal in cached_signals[:max_new]:
                    if signal.ts_code in positions:
                        continue
                    if signal.signal_strength == "弱":
                        continue
                    # 冷却期过滤
                    if signal.ts_code in self.cooldown_codes:
                        continue
                    # 短期趋势过滤: 近5日涨幅>-2%
                    if self._check_short_trend(signal.ts_code, date_str) is False:
                        continue
                    # 主题集中度限制: 同一主题最多持有2只
                    if signal.theme:
                        theme_count = sum(1 for p in positions.values() if p.get("theme") == signal.theme)
                        if theme_count >= self.max_per_theme:
                            continue

                    position_capital = cash * (regime_result.position_pct / 100) / max(1, max_new)
                    entry_price = signal.entry_price * (1 + self.slippage)
                    shares = int(position_capital / entry_price / 100) * 100
                    if shares < 100:
                        continue

                    cost = shares * entry_price
                    fee = cost * self.commission
                    cash -= cost + fee

                    positions[signal.ts_code] = {
                        "name": signal.name,
                        "theme": signal.theme,
                        "entry_date": date_str,
                        "entry_price": entry_price,
                        "shares": shares,
                        "capital": cost,
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit,
                        "hold_days": 0,
                    }

            # 更新持仓天数
            for code in positions:
                positions[code]["hold_days"] += 1

            # 更新冷却期
            expired_cooldowns = []
            for code in self.cooldown_codes:
                self.cooldown_codes[code] -= 1
                if self.cooldown_codes[code] <= 0:
                    expired_cooldowns.append(code)
            for code in expired_cooldowns:
                del self.cooldown_codes[code]

            # 计算当日权益
            pos_value = 0
            for code, pos in positions.items():
                close_price = self._get_close_price(code, date_str)
                if close_price:
                    pos_value += pos["shares"] * close_price
                else:
                    pos_value += pos["capital"]

            equity_curve.append(cash + pos_value)
            dates.append(date_str)

            if (i + 1) % 50 == 0:
                nav = equity_curve[-1]
                ret = (nav / self.initial_capital - 1) * 100
                print(f"  [{date_str}] NAV={nav:.0f} 收益={ret:+.2f}% 持仓={len(positions)}只")

        # 强制平仓
        final_date = trade_dates[-1]
        for code, pos in list(positions.items()):
            exit_price = self._get_close_price(code, final_date) or pos["entry_price"]
            pnl = (exit_price - pos["entry_price"]) * pos["shares"]
            all_trades.append(Trade(
                ts_code=code, name=pos["name"],
                entry_date=pos["entry_date"], entry_price=pos["entry_price"],
                exit_date=final_date, exit_price=exit_price,
                exit_reason="回测结束", pnl_pct=round(pnl / pos["capital"] * 100, 2),
                shares=pos["shares"], stop_loss=pos["stop_loss"],
                take_profit=pos["take_profit"],
            ))
        positions.clear()

        return self._compute_metrics(equity_curve, all_trades, dates)

    def _judge_regime_for_date(self, date_str: str) -> Optional[MarketRegimeResult]:
        """对指定日期判断大势"""
        start_date = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
        sh_df = self.ds.load_index_daily(SH_INDEX, start_date, date_str)
        hs300_df = self.ds.load_index_daily(HS300_INDEX, start_date, date_str)
        zz2000_df = self.ds.load_index_daily(ZZ2000_INDEX, start_date, date_str)

        if sh_df is None or len(sh_df) < 60:
            return None

        return self.regime_judge.judge(sh_df, hs300_df if hs300_df is not None and len(hs300_df) >= 60 else sh_df, zz2000_df if zz2000_df is not None and len(zz2000_df) >= 60 else sh_df, trade_date=date_str)

    def _generate_signals_for_date(self, date_str: str,
                                   regime_result: MarketRegimeResult) -> List[BuySignal]:
        """为指定日期生成买入信号"""
        top_themes = self.theme_engine.get_top_themes(date_str, regime_result.regime)
        if not top_themes:
            return []

        all_candidates = []
        code_to_theme = {}
        for theme in top_themes:
            constituents = self.theme_engine.get_theme_constituents(theme.name)
            for c in constituents:
                code = c.get("code", "")
                if code and ".BJ" not in code:
                    code_to_theme[code] = theme.name
                    all_candidates.append(c)

        if not all_candidates:
            return []

        picks = self.stock_selector.select_stocks(all_candidates, date_str, top_n=15)
        if not picks:
            return []

        signals = []
        for pick in picks:
            signal = self.entry_timing.check_buy_signal(
                pick.ts_code, pick.name, pick.board, date_str, self.ds
            )
            if signal is not None:
                signal.stock_score = pick.score
                signal.theme = code_to_theme.get(pick.ts_code, "")
                signals.append(signal)

        signals.sort(key=lambda s: s.stock_score, reverse=True)
        return signals

    def _check_exit(self, code: str, pos: Dict, date_str: str) -> Optional[Dict]:
        """检查持仓退出条件 (含移动止盈)"""
        start_date = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
        df = self.ds.load_stock_daily(code, start_date, date_str)
        if df is None or len(df) < 2:
            return None

        close = df["close"].values
        high = df["high"].values if "high" in df.columns else close
        low = df["low"].values if "low" in df.columns else close
        current_price = close[-1]
        n = len(close)

        # 计算当前盈亏比
        profit_pct = (current_price / pos["entry_price"] - 1) * 100

        # 移动止盈: 盈利超5%后用最高价的回撤做止损
        if "max_price" not in pos:
            pos["max_price"] = pos["entry_price"]
        if current_price > pos["max_price"]:
            pos["max_price"] = current_price

        trailing_stop = pos["max_price"] * 0.92  # 从最高点回撤8%止盈

        # 止损检查 (原始止损 或 移动止盈, 取较高者)
        effective_stop = pos["stop_loss"]
        if profit_pct >= 5:
            effective_stop = max(pos["stop_loss"], trailing_stop)

        if current_price <= effective_stop:
            reason = "移动止盈" if profit_pct >= 5 and trailing_stop > pos["stop_loss"] else "止损"
            return {"price": current_price, "pnl": (current_price - pos["entry_price"]) * pos["shares"], "reason": reason}

        # 止盈检查
        if current_price >= pos["take_profit"]:
            return {"price": current_price, "pnl": (current_price - pos["entry_price"]) * pos["shares"], "reason": "止盈"}

        # 持有天数超限
        if pos["hold_days"] >= self.max_hold_days:
            return {"price": current_price, "pnl": (current_price - pos["entry_price"]) * pos["shares"], "reason": f"超期({pos['hold_days']}天)"}

        # 跌破MA20 (仅当亏损时使用, 盈利的用移动止盈保护)
        ma20 = np.mean(close[-20:]) if n >= 20 else close[-1]
        if profit_pct <= 0 and current_price < ma20 * 0.95:
            return {"price": current_price, "pnl": (current_price - pos["entry_price"]) * pos["shares"], "reason": "跌破MA20"}

        return None

    def _check_short_trend(self, code: str, date_str: str) -> bool:
        """检查短期趋势: 近5日涨幅>-2% 且 当前不在下跌中"""
        start_date = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=15)).strftime("%Y%m%d")
        df = self.ds.load_stock_daily(code, start_date, date_str)
        if df is None or len(df) < 6:
            return None
        close = df["close"].values
        n = len(close)
        ret5 = (close[-1] / close[-6] - 1) * 100 if n >= 6 else 0
        if ret5 < -2:
            return False
        if close[-1] < close[-2] and close[-2] < close[-3]:
            return False
        return True

    def _get_close_price(self, code: str, date_str: str) -> Optional[float]:
        """获取指定日期的收盘价"""
        start_date = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d")
        df = self.ds.load_stock_daily(code, start_date, date_str)
        if df is None or len(df) == 0:
            return None
        return float(df["close"].values[-1])

    def _compute_metrics(self, equity_curve: List[float], trades: List[Trade],
                         dates: List[str]) -> BacktestResult:
        """计算回测指标"""
        if not equity_curve or len(equity_curve) < 2:
            return BacktestResult()

        equity = np.array(equity_curve)
        final_nav = equity[-1]
        total_return = (final_nav / self.initial_capital - 1) * 100

        # 年化收益
        total_days = len(equity)
        annual_return = ((final_nav / self.initial_capital) ** (252 / max(total_days, 1)) - 1) * 100

        # 夏普比率
        daily_returns = np.diff(equity) / (equity[:-1] + 1e-6)
        if len(daily_returns) > 1 and np.std(daily_returns) > 0:
            sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252)
        else:
            sharpe = 0

        # 最大回撤
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / (peak + 1e-6)
        max_dd = float(np.min(drawdown) * 100) if len(drawdown) > 0 else 0

        # 胜率
        win_trades = [t for t in trades if t.pnl_pct > 0]
        win_rate = len(win_trades) / max(len(trades), 1) * 100

        # 平均收益
        avg_return = np.mean([t.pnl_pct for t in trades]) if trades else 0

        return BacktestResult(
            total_return=round(total_return, 2),
            annual_return=round(annual_return, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_dd, 2),
            win_rate=round(win_rate, 2),
            total_trades=len(trades),
            avg_return=round(avg_return, 2),
            equity_curve=[float(x) for x in equity_curve],
            trades=trades,
            dates=dates,
        )


# ============================================================
# Module 6: 每日运行器 (Daily Runner)
# ============================================================

class DailyRunner:
    """每日运行模式 — 使用 Tushare 数据源"""

    def __init__(self, trade_date: str = None):
        self.ds = DataSource(mode="tushare")
        self.regime_judge = MarketRegimeJudge()
        self.theme_engine = ThemeRotationEngine(self.ds)
        self.stock_selector = StockSelector(self.ds)
        self.entry_timing = EntryTiming()

        if trade_date:
            self.trade_date = trade_date
        else:
            self.trade_date = self._get_latest_trade_date()

    def _get_latest_trade_date(self) -> str:
        self.ds._init_tushare()
        now = datetime.now()
        if now.hour < 15:
            query_date = (now - timedelta(days=1)).strftime("%Y%m%d")
        else:
            query_date = now.strftime("%Y%m%d")
        try:
            cal = self.ds._pro.trade_cal(exchange="", start_date="20200101", end_date=query_date)
            cal = cal[cal["is_open"] == 1]
            return str(cal[cal["cal_date"] <= query_date]["cal_date"].max())
        except Exception:
            return query_date

    def run(self) -> str:
        """执行每日分析，返回报告文本"""
        lines = []
        lines.append("=" * 70)
        lines.append(f"  幻方量化交易系统 — 每日分析报告")
        lines.append(f"  交易日: {self.trade_date}")
        lines.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 70)

        # Step 1: 大势判断
        lines.append("\n" + "─" * 50)
        lines.append("【模块一】大势判断 (Market Regime Judge)")
        lines.append("─" * 50)

        start_date = (datetime.strptime(self.trade_date, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
        sh_df = self.ds.load_index_daily(SH_INDEX, start_date, self.trade_date)
        hs300_df = self.ds.load_index_daily(HS300_INDEX, start_date, self.trade_date)
        zz2000_df = self.ds.load_index_daily(ZZ2000_INDEX, start_date, self.trade_date)

        if sh_df is None or len(sh_df) < 60:
            lines.append("  [错误] 指数数据不足，无法判断大势")
            return "\n".join(lines)

        regime = self.regime_judge.judge(sh_df, hs300_df if hs300_df is not None and len(hs300_df) >= 60 else sh_df, zz2000_df if zz2000_df is not None and len(zz2000_df) >= 60 else sh_df, trade_date=self.trade_date)

        lines.append(f"  市场状态: {regime.regime}")
        lines.append(f"  综合评分: {regime.regime_score:.1f}/100")
        lines.append(f"  趋势分: {regime.trend_score:.1f}  情绪分: {regime.sentiment_score:.1f}")
        lines.append(f"  建议仓位: {regime.position_range} ({regime.position_pct:.0f}%)")
        lines.append(f"  指数详情: 上证{regime.detail['sh_score']:.1f} "
                     f"沪深300:{regime.detail['hs300_score']:.1f} "
                     f"中证2000:{regime.detail['zz2000_score']:.1f}")
        if "index_trend" in regime.detail:
            lines.append(f"  指数趋势: {regime.detail['index_trend']:.1f}  主题趋势: {regime.detail.get('theme_trend', 0):.1f}")
        if "market_regime" in regime.detail:
            lines.append(f"  V8状态: {regime.detail['market_regime']}")
        if "regime_reason" in regime.detail:
            lines.append(f"  判定理由: {regime.detail['regime_reason']}")
        if "structure" in regime.detail:
            s = regime.detail["structure"]
            lines.append(f"  持仓建议: {s.get('集中度','')} | 周期:{s.get('持有周期','')} | 止损:{s.get('止损','')} | 偏好:{s.get('选股偏好','')}")

        if regime.regime in ("主跌退潮期", "顶部分歧期"):
            lines.append("\n  ⚠ 市场偏空建议空仓，不进行选股")
            result = "\n".join(lines)
            self._save_report(result)
            wechat_msg = self._build_wechat_msg(result)
            self._send_pushplus(wechat_msg)
            return result

        # Step 2: 主题轮动
        lines.append("\n" + "─" * 50)
        lines.append("【模块二】主题轮动 (Theme Rotation)")
        lines.append("─" * 50)

        top_themes = self.theme_engine.get_top_themes(self.trade_date, regime.regime)
        if not top_themes:
            lines.append("  [警告] 无有效主题排名")
            result = "\n".join(lines)
            self._save_report(result)
            self._send_pushplus(self._build_wechat_msg(result))
            return result

        for i, theme in enumerate(top_themes):
            lines.append(f"  {i + 1}. {theme.name}({theme.code}) "
                         f"综合分:{theme.total_score:.1f} "
                         f"动量:{theme.momentum:+.2f}% "
                         f"量能:{theme.vol_score:.1f} "
                         f"风险调整:{theme.risk_adj:.1f}")

        lines.append(f"  策略: {'主线集中(TOP1)' if regime.regime == '主升加速期' else '轮动分散(TOP3)'}")

        # Step 3: 选股
        lines.append("\n" + "─" * 50)
        lines.append("【模块三】个股选股 (Stock Selector)")
        lines.append("─" * 50)

        all_candidates = []
        seen_codes = set()
        for theme in top_themes:
            constituents = self.theme_engine.get_theme_constituents(theme.name)
            for c in constituents:
                code = c.get("code", "")
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    all_candidates.append(c)

        if not all_candidates:
            lines.append("  [警告] 主题成份股为空，请先运行 build_theme_stock_map.py")
            result = "\n".join(lines)
            self._save_report(result)
            self._send_pushplus(self._build_wechat_msg(result))
            return result

        lines.append(f"  ETF候选池: {len(all_candidates)}只股票")

        picks = self.stock_selector.select_stocks(all_candidates, self.trade_date, top_n=20)
        if not picks:
            lines.append("  [无结果] 没有符合条件的股票")
            result = "\n".join(lines)
            self._save_report(result)
            self._send_pushplus(self._build_wechat_msg(result))
            return result

        lines.append(f"  选股结果: {len(picks)}只")
        lines.append(f"  {'序号':<4} {'代码':<12} {'名称':<8} {'板块':<4} {'评分':>6} {'斜率%':>7} {'底部':>4} {'长阳':>4}")
        lines.append(f"  {'-'*58}")
        for i, p in enumerate(picks[:15]):
            slope_str = f"{p.ma5_slope:.2f}" if p.board == "主板" else "-"
            bottom_str = "是" if p.bottom_rising else "-"
            yang_str = str(p.long_yang_count) if p.long_yang_count > 0 else "-"
            lines.append(f"  {i+1:<4} {p.ts_code:<12} {p.name:<8} {p.board:<4} "
                         f"{p.score:>6.1f} {slope_str:>7} {bottom_str:>4} {yang_str:>4}")

        # Step 4: 择时
        lines.append("\n" + "─" * 50)
        lines.append("【模块四】择时信号 (Entry Timing) — 盈亏比3:1")
        lines.append("─" * 50)

        signals = []
        for pick in picks[:10]:
            signal = self.entry_timing.check_buy_signal(
                pick.ts_code, pick.name, pick.board, self.trade_date, self.ds
            )
            if signal is not None:
                signal.stock_score = pick.score
                signals.append(signal)

        signals.sort(key=lambda s: s.stock_score, reverse=True)

        if not signals:
            lines.append("  [无信号] 当前没有符合条件的低吸买入信号")
        else:
            lines.append(f"  买入信号: {len(signals)}个")
            lines.append(f"  {'名称':<8} {'代码':<12} {'强度':<4} {'买入价':>7} {'止损':>7} {'止盈':>7} {'盈亏比':>6} {'评分':>5}")
            lines.append(f"  {'-'*65}")

            buy_signals = []
            for s in signals[:8]:
                lines.append(f"  {s.name:<8} {s.ts_code:<12} {s.signal_strength:<4} "
                             f"{s.entry_price:>7.2f} {s.stop_loss:>7.2f} {s.take_profit:>7.2f} "
                             f"{s.risk_reward_ratio:>6.1f} {s.stock_score:>5.1f}")
                buy_signals.append(s)

            lines.append("")
            lines.append("  ★ 推荐买入 (信号强度≥中):")
            buy_candidates = [s for s in signals if s.signal_strength in ("强", "中")][:5]
            if buy_candidates:
                for i, s in enumerate(buy_candidates):
                    lines.append(f"    {i+1}. {s.name}({s.ts_code}) "
                                 f"买入价:{s.entry_price:.2f} 止损:{s.stop_loss:.2f} "
                                 f"止盈:{s.take_profit:.2f} 盈亏比:{s.risk_reward_ratio:.1f}:1")
                    lines.append(f"       信号: {s.signal_reason}")
                    lines.append(f"       均线: MA5={s.ma5:.2f} MA10={s.ma10:.2f} MA20={s.ma20:.2f} "
                                 f"ATR={s.atr:.2f}")
            else:
                lines.append("    (无符合条件的买入信号)")

        lines.append("\n" + "=" * 70)
        lines.append("  免责声明: 本报告仅供参考，不构成投资建议。")
        lines.append("=" * 70)

        result = "\n".join(lines)
        self._save_report(result)
        # 推送微信
        wechat_msg = self._build_wechat_msg(result)
        self._send_pushplus(wechat_msg)
        return result

    def _save_report(self, content: str):
        """保存报告"""
        report_dir = os.path.join(BASE_DIR, "daily_reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"{self.trade_date}_quant_system.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"\n[报告] 已保存至: {report_path}")

    def _build_wechat_msg(self, report: str) -> str:
        """构建微信推送用的精简 Markdown 报告"""
        lines = report.split("\n")
        msg = []
        msg.append(f"## 量化交易系统复盘")
        msg.append(f"**交易日: {self.trade_date}**")
        msg.append("")

        # 提取关键信息
        regime_found = False
        for line in lines:
            stripped = line.strip()
            if "市场状态:" in stripped:
                msg.append(f"**{stripped}**")
                regime_found = True
            elif "综合评分:" in stripped:
                msg.append(f"{stripped}")
            elif "建议仓位:" in stripped:
                msg.append(f"{stripped}")
            elif "策略:" in stripped:
                msg.append(f"{stripped}")
            elif "选股结果:" in stripped:
                msg.append(f"\n{stripped}")
            elif "买入信号:" in stripped:
                msg.append(f"\n{stripped}")

        msg.append("")

        # 买入信号
        in_buy_section = False
        buy_lines = []
        for line in lines:
            stripped = line.strip()
            if "★ 推荐买入" in stripped:
                in_buy_section = True
                continue
            if in_buy_section:
                if stripped.startswith("(无") or stripped == "":
                    continue
                # 检测到分隔线或免责声明, 结束
                if stripped.startswith("===") or "免责声明" in stripped:
                    break
                # 新的股票条目 (以数字开头)
                if stripped and stripped[0].isdigit():
                    buy_lines.append(f"- {stripped}")
                # 信号说明行 (缩进的补充信息), 追加到上一条
                elif stripped.startswith("信号:") or stripped.startswith("均线:"):
                    if buy_lines:
                        buy_lines[-1] += f"\n  {stripped}"
                # 其他非空且非数字开头, 可能是结束标记
                elif stripped and not line.startswith("       "):
                    # 不是缩进的补充行, 退出
                    if not line.startswith("    "):
                        break

        if buy_lines:
            msg.append("### 买入信号")
            msg.extend(buy_lines[:5])
        else:
            # 检查是否有空仓提示
            has_empty = any("空仓" in l or "建议空仓" in l for l in lines)
            market_status = ""
            for l in lines:
                if "市场状态:" in l:
                    market_status = l.strip()
            if "主跌退潮" in market_status or "顶部分歧" in market_status:
                msg.append(f"> 当前市场状态: {market_status}")
                msg.append("> 建议空仓，不进行选股")
            else:
                msg.append("> 无符合条件的买入信号")

        msg.append("")
        msg.append("---")
        msg.append("*免责声明: 本报告仅供参考，不构成投资建议。*")
        return "\n".join(msg)

    def _send_pushplus(self, content: str):
        """通过 PushPlus 推送微信消息"""
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(BASE_DIR), "config", ".env"))
            token = os.getenv("PUSHPLUS")
            if not token:
                print("⚠ PushPlus token 为空，跳过推送")
                return
            url = "https://www.pushplus.plus/send"
            payload = {
                "token": token,
                "title": f"量化交易复盘 - {self.trade_date}",
                "content": content,
                "template": "markdown"
            }
            resp = requests.post(url, json=payload, timeout=15)
            result = resp.json()
            if result.get("code") == 200:
                print("✅ PushPlus 微信推送成功")
            else:
                print(f"⚠ PushPlus 推送失败: {result.get('msg', '未知错误')}")
        except Exception as e:
            print(f"⚠ PushPlus 异常: {e}")


# ============================================================
# Main Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="幻方量化交易系统 v1.0")
    parser.add_argument("--backtest", action="store_true", help="运行回测模式")
    parser.add_argument("--start", type=str, default=None, help="回测开始日期 (YYYYMMDD)")
    parser.add_argument("--end", type=str, default=None, help="回测结束日期 (YYYYMMDD)")
    parser.add_argument("--date", type=str, default=None, help="指定交易日 (YYYYMMDD)")
    parser.add_argument("--tdx-root", type=str, default=r"D:\zd_tdx\vipdoc", help="通达信数据目录")
    args = parser.parse_args()

    if args.backtest:
        print("=" * 60)
        print("  幻方量化交易系统 — 回测模式")
        print("=" * 60)

        if args.end is None:
            args.end = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        if args.start is None:
            args.start = (datetime.strptime(args.end, "%Y%m%d") - timedelta(days=365)).strftime("%Y%m%d")

        print(f"  回测区间: {args.start} ~ {args.end}")
        print(f"  数据源: 通达信 ({args.tdx_root})")

        ds = DataSource(mode="tdx", tdx_root=args.tdx_root)
        bt = BacktestEngine(ds)
        result = bt.run(args.start, args.end)

        print("\n" + "=" * 60)
        print("  回测结果")
        print("=" * 60)
        print(f"  总收益率: {result.total_return:+.2f}%")
        print(f"  年化收益: {result.annual_return:+.2f}%")
        print(f"  夏普比率: {result.sharpe_ratio:.2f}")
        print(f"  最大回撤: {result.max_drawdown:.2f}%")
        print(f"  胜率: {result.win_rate:.2f}%")
        print(f"  总交易次数: {result.total_trades}")
        print(f"  平均收益: {result.avg_return:+.2f}%")

        if result.trades:
            win_trades = [t for t in result.trades if t.pnl_pct > 0]
            loss_trades = [t for t in result.trades if t.pnl_pct <= 0]
            print(f"  盈利次数: {len(win_trades)}  亏损次数: {len(loss_trades)}")
            if win_trades:
                print(f"  平均盈利: {np.mean([t.pnl_pct for t in win_trades]):+.2f}%")
            if loss_trades:
                print(f"  平均亏损: {np.mean([t.pnl_pct for t in loss_trades]):+.2f}%")

            print(f"\n  最近10笔交易:")
            for t in result.trades[-10:]:
                print(f"    {t.name}({t.ts_code}) "
                      f"买:{t.entry_date} {t.entry_price:.2f} "
                      f"卖:{t.exit_date} {t.exit_price:.2f} "
                      f"收益:{t.pnl_pct:+.2f}% "
                      f"原因:{t.exit_reason}")

        # 保存回测结果
        bt_result = {
            "start": args.start,
            "end": args.end,
            "total_return": result.total_return,
            "annual_return": result.annual_return,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown": result.max_drawdown,
            "win_rate": result.win_rate,
            "total_trades": result.total_trades,
            "avg_return": result.avg_return,
            "trades": [
                {
                    "ts_code": t.ts_code, "name": t.name,
                    "entry_date": t.entry_date, "entry_price": t.entry_price,
                    "exit_date": t.exit_date, "exit_price": t.exit_price,
                    "exit_reason": t.exit_reason, "pnl_pct": t.pnl_pct,
                }
                for t in result.trades
            ],
        }
        report_dir = os.path.join(BASE_DIR, "report_daily")
        os.makedirs(report_dir, exist_ok=True)
        bt_path = os.path.join(report_dir, f"backtest_{args.start}_{args.end}.json")
        with open(bt_path, "w", encoding="utf-8") as f:
            json.dump(bt_result, f, ensure_ascii=False, indent=2)
        print(f"\n  回测结果已保存至: {bt_path}")

    else:
        # 每日运行模式
        print("=" * 60)
        print("  幻方量化交易系统 — 每日分析模式")
        print("=" * 60)

        runner = DailyRunner(trade_date=args.date)
        report = runner.run()
        print(report)


if __name__ == "__main__":
    main()