###===自选复盘 - tushare接口===###

import io
import json
import re
import urllib.parse
import os
import struct
import sys

# =========================
# Windows GBK 控制台输出修复：强制 UTF-8 编码
# =========================
#sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
#sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# =========================
# 终极方案：patch os.path.expanduser，不让 tushare 访问用户根目录
# 必须在导入 tushare 之前执行！
# =========================
original_expanduser = os.path.expanduser
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 现在可以安全导入 tushare 了
import markdown2 # type: ignore
import requests
import pandas as pd
import numpy as np
import time

# =========================
# 代理配置
# =========================
PROXY = 'http://127.0.0.1:7897'
PROXY_ENABLED = True  # 设为 True 启用代理

# 创建带代理的 requests Session
def get_requests_session():
    """获取 requests Session，支持代理"""
    session = requests.Session()
    if PROXY_ENABLED:
        session.proxies = {
            'http': PROXY,
            'https': PROXY
        }
    return session

# 默认 Session
default_session = get_requests_session()

from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

import tushare as ts
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3

# 新增：引用新版大盘/主题分析
import daily_analysis_summarizer as das

# =========================
# 产业资金定价AI模型（ICPM）延迟导入
# =========================
_MF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'multi_factor_picker')
if _MF_DIR not in sys.path:
    sys.path.insert(0, _MF_DIR)
_ICPM_AVAILABLE = True  # 标记可用，实际 import 在用到时执行

# =========================
# 环境变量
# =========================
load_dotenv("d:/mystock/config/.env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
MINI_MAX_API_KEY = os.getenv("MINI_MAX_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 缓存/报告目录统一到 d:\stock\ 下
STOCK_DATA_DIR = r"d:\mystock"
CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")
REPORT_DIR = os.path.join(STOCK_DATA_DIR, "report_daily")   
DB_PATH = os.path.join(REPORT_DIR, "stock_result.db")
NEWS_CACHE_DIR = os.path.join(STOCK_DATA_DIR, "news_cache")
# Tushare API 数据缓存（研报、调研）
TUSHARE_API_CACHE_DIR = os.path.join(STOCK_DATA_DIR, "tushare_api_cache")
FUND_CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_fundamental")

os.makedirs(STOCK_DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(NEWS_CACHE_DIR, exist_ok=True)
os.makedirs(TUSHARE_API_CACHE_DIR, exist_ok=True)
os.makedirs(FUND_CACHE_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════
# 缓存API调用（与 wave2_pattern_scanner.py / wave2_daily.py 共用）
# ═══════════════════════════════════════════════════════
def _read_cache(cache_file):
    try:
        if os.path.exists(cache_file):
            df = pd.read_csv(cache_file)
            if not df.empty and 'trade_date' in df.columns:
                df['trade_date'] = df['trade_date'].astype(str)
                return df
    except:
        pass
    return None

def _save_cache(df, cache_file):
    try:
        if df is not None and not df.empty:
            df.to_csv(cache_file, index=False)
    except:
        pass

def cached_stk_factor_pro(ts_code, start_date, end_date):
    """带缓存的 stk_factor_pro（与 wave2_pattern_scanner.py 共用缓存文件）"""
    cache_file = os.path.join(CACHE_DIR, f"stk_pro_{ts_code}.csv")
    df_cache = _read_cache(cache_file)
    if df_cache is not None and not df_cache.empty:
        cached_min = df_cache['trade_date'].min()
        cached_max = df_cache['trade_date'].max()
        # 缓存已覆盖请求范围 → 直接返回子集
        if cached_min <= start_date and cached_max >= end_date:
            mask = (df_cache['trade_date'] >= start_date) & (df_cache['trade_date'] <= end_date)
            subset = df_cache[mask].copy()
            if not subset.empty:
                return subset.sort_values('trade_date').reset_index(drop=True)
    df = pro.stk_factor_pro(ts_code=ts_code, start_date=start_date, end_date=end_date)
    time.sleep(0.06)
    if df is not None and not df.empty:
        df['trade_date'] = df['trade_date'].astype(str)
        if df_cache is not None:
            combined = pd.concat([df_cache, df]).drop_duplicates(subset='trade_date').sort_values('trade_date')
            _save_cache(combined, cache_file)
        else:
            _save_cache(df, cache_file)
        return df.sort_values('trade_date').reset_index(drop=True)
    return df

# =========================
# 主题个股池路径
# =========================
THEME_STOCKS_CACHE = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_pattern_stocks.csv')

# DC热榜缓存目录
DC_HOT_CACHE_DIR = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'dc_hot')

def stock_fundamental_alpha_score(ts_code, pro):
    """
    价值型Alpha评分系统（基本面景气度 + 合理估值）
    核心逻辑：
    - Alpha分数越高，代表当前价格越接近内在价值（被低估）
    - 评估个股基本面景气度（盈利成长）与行业估值中枢的偏离度
    - 核心维度：估值偏离 + 盈利质量 + 成长动能 + 现金流 + 行业比较
    - 优先从 bull_stocks_all.csv 文件读取 final_score
    """
    # 优先从CSV文件读取final_score
    csv_path = r"D:\mystock\solo\report_daily\bull_stocks_all.csv"
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            # 提取股票代码（去掉后缀并转换为整数，因为CSV中是整数）
            code_without_suffix = ts_code.split('.')[0]
            code_int = int(code_without_suffix)  # 转换为整数匹配
            # 查找对应股票
            row = df[df['ts_code'] == code_int]
            if not row.empty:
                final_score = float(row['final_score'].iloc[0])
                return {
                    "alpha_score": final_score,
                    "signal": "从bull_stocks_all.csv读取",
                    "detail": {}
                }
        except Exception as e:
            # CSV读取失败，继续原逻辑
            pass
    
    # 原有逻辑作为后备
    cache_key = f"{ts_code}_{TRADE_DATE}.json"
    cache_path = os.path.join(FUND_CACHE_DIR, cache_key)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass

    score = 0
    detail = {}
    has_data = False

    # =========================
    # ① 估值偏离度（0-25分）：PE/PB相对行业与历史
    # =========================
    try:
        # 获取个股基本信息
        stock_info = pro.stock_basic(ts_code=ts_code)
        industry = None
        if stock_info is not None and len(stock_info) > 0:
            industry = stock_info.iloc[0].get('industry', None)
        
        # 获取PE/PB
        db_df = pro.daily_basic(ts_code=ts_code)
        s1 = 12.5  # 默认中性12.5分
        
        if db_df is not None and len(db_df) >= 60:
            pe_series = db_df['pe_ttm'].dropna().sort_values()
            pb_series = db_df['pb'].dropna().sort_values()
            
            if len(pe_series) >= 30 and len(pb_series) >= 30:
                current_pe = pe_series.iloc[-1]
                current_pb = pb_series.iloc[-1]
                
                # PE历史百分位（越低越接近内在价值=分数越高）
                pe_pct = (pe_series < current_pe).sum() / len(pe_series)
                # PB历史百分位
                pb_pct = (pb_series < current_pb).sum() / len(pb_series)
                
                # 综合估值偏离度（百分位越低越好，0%最被低估）
                if pe_pct < 0.2 and pb_pct < 0.2:
                    s1 = 25  # 极度低估
                elif pe_pct < 0.3 and pb_pct < 0.3:
                    s1 = 22
                elif pe_pct < 0.4:
                    s1 = 18
                elif pe_pct < 0.5:
                    s1 = 15  # 合理偏低
                elif pe_pct < 0.6:
                    s1 = 12.5  # 合理
                elif pe_pct < 0.75:
                    s1 = 8
                elif pe_pct < 0.9:
                    s1 = 4
                else:
                    s1 = 0  # 极度高估
                
                detail["pe_current"] = round(current_pe, 2) if current_pe > 0 else None
                detail["pb_current"] = round(current_pb, 2) if current_pb > 0 else None
                detail["pe_pct_rank"] = round(pe_pct * 100, 1)
                detail["pb_pct_rank"] = round(pb_pct * 100, 1)
                has_data = True
        
        score += s1
        detail["valuation_score"] = s1
    except Exception as e:
        detail["valuation_score"] = 12.5
        detail["valuation_error"] = str(e)

    # =========================
    # ② 盈利质量（0-25分）：ROE趋势 + 净利率
    # =========================
    try:
        fin_df = pro.fina_indicator(ts_code=ts_code)
        s2 = 12.5  # 默认中性
        
        if fin_df is not None and len(fin_df) >= 4:
            fin_df = fin_df.sort_values('end_date', ascending=False).head(8)
            
            # ROE趋势（近4季度平均 vs 历史平均）
            roe_recent = fin_df.head(4)['roe'].mean() if 'roe' in fin_df.columns else None
            roe_hist = fin_df['roe'].mean() if 'roe' in fin_df.columns else None
            
            # 净利率趋势
            netprofit_rt_recent = fin_df.head(4)['netprofit_margin'].mean() if 'netprofit_margin' in fin_df.columns else None
            
            # 毛利率趋势（护城河）
            gross_rt_recent = fin_df.head(4)['grossprofit_margin'].mean() if 'grossprofit_margin' in fin_df.columns else None
            
            if roe_recent is not None and roe_hist is not None:
                roe_trend = roe_recent / (roe_hist + 1e-6) - 1  # 趋势比率
                
                if roe_recent > 20 and roe_trend > 0:  # ROE>20%且在改善
                    s2 = 25
                elif roe_recent > 15 and roe_trend > 0:
                    s2 = 22
                elif roe_recent > 15:
                    s2 = 18
                elif roe_recent > 10 and roe_trend > 0:
                    s2 = 15
                elif roe_recent > 10:
                    s2 = 12.5
                elif roe_recent > 5:
                    s2 = 8
                elif roe_recent > 0:
                    s2 = 4
                else:
                    s2 = 0  # 亏损
                
                detail["roe_recent"] = round(roe_recent, 2)
                detail["roe_hist"] = round(roe_hist, 2)
                detail["roe_trend"] = round(roe_trend * 100, 1)
            
            if netprofit_rt_recent is not None:
                detail["netprofit_margin"] = round(netprofit_rt_recent, 2)
            if gross_rt_recent is not None:
                detail["gross_margin"] = round(gross_rt_recent, 2)
            has_data = True
        
        score += s2
        detail["profit_quality_score"] = s2
    except Exception as e:
        detail["profit_quality_score"] = 12.5
        detail["profit_error"] = str(e)

    # =========================
    # ③ 成长动能（0-25分）：营收/利润增速
    # =========================
    try:
        income = pro.income(ts_code=ts_code)
        s3 = 12.5  # 默认中性
        
        if income is not None and len(income) >= 4:
            income = income.sort_values('end_date', ascending=False)
            
            # 营收同比
            latest = income.iloc[0]
            yoy_rev = None
            yoy_profit = None
            
            # 找去年同期
            for i in range(1, min(8, len(income))):
                prev = income.iloc[i]
                if latest.get('end_date', '')[:4] == prev.get('end_date', '')[:4]:
                    if prev.get('revenue', 0) > 0:
                        yoy_rev = latest['revenue'] / prev['revenue'] - 1
                    if prev.get('net_profit', 0) > 0:
                        yoy_profit = latest['net_profit'] / prev['net_profit'] - 1
                    break
            
            if yoy_rev is not None or yoy_profit is not None:
                # 成长评分（增速越高越好）
                avg_growth = (yoy_rev + yoy_profit) / 2 if yoy_rev and yoy_profit else (yoy_rev or yoy_profit or 0)
                
                if avg_growth > 0.5:  # >50%
                    s3 = 25
                elif avg_growth > 0.3:  # >30%
                    s3 = 22
                elif avg_growth > 0.2:  # >20%
                    s3 = 18
                elif avg_growth > 0.1:  # >10%
                    s3 = 15
                elif avg_growth > 0:  # 正增长
                    s3 = 12.5
                elif avg_growth > -0.1:  # 小幅下滑
                    s3 = 8
                else:  # 大幅下滑
                    s3 = 0
                
                detail["revenue_yoy"] = round(yoy_rev * 100, 1) if yoy_rev is not None else None
                detail["profit_yoy"] = round(yoy_profit * 100, 1) if yoy_profit is not None else None
                has_data = True
        
        score += s3
        detail["growth_score"] = s3
    except Exception as e:
        detail["growth_score"] = 12.5
        detail["growth_error"] = str(e)

    # =========================
    # ④ 现金流质量（0-15分）：经营现金流/净利润
    # =========================
    try:
        cashflow = pro.cashflow(ts_code=ts_code)
        s4 = 7.5  # 默认中性
        
        if cashflow is not None and len(cashflow) >= 2:
            cashflow = cashflow.sort_values('end_date', ascending=False)
            
            # 经营现金流
            ocf = cashflow.head(4)['n_cashflow_act'].mean() if 'n_cashflow_act' in cashflow.columns else None
            netprofit = cashflow.head(4)['net_profit'].mean() if 'net_profit' in cashflow.columns else None
            
            if ocf is not None and netprofit is not None and netprofit > 0:
                ocf_ratio = ocf / netprofit
                
                if ocf_ratio > 1.5:  # 现金流/净利润 > 150%
                    s4 = 15
                elif ocf_ratio > 1.2:
                    s4 = 13
                elif ocf_ratio > 1.0:  # 现金流充足
                    s4 = 10
                elif ocf_ratio > 0.8:
                    s4 = 7.5
                elif ocf_ratio > 0.5:
                    s4 = 5
                else:
                    s4 = 2  # 现金流质量差
                
                detail["ocf_netprofit_ratio"] = round(ocf_ratio, 2)
                has_data = True
        
        score += s4
        detail["cashflow_score"] = s4
    except Exception as e:
        detail["cashflow_score"] = 7.5
        detail["cashflow_error"] = str(e)

    # =========================
    # ⑤ 股息吸引力（0-10分）：股息率
    # =========================
    try:
        s5 = 5  # 默认中性
        db_df = pro.daily_basic(ts_code=ts_code)
        
        if db_df is not None and len(db_df) > 0:
            # 获取最新股息率
            div_ratio = db_df['dv_ttm'].iloc[-1] if 'dv_ttm' in db_df.columns else None
            price = db_df['close'].iloc[-1] if 'close' in db_df.columns else None
            
            if div_ratio is not None and price is not None and price > 0:
                # 股息率 = 股息 / 股价
                div_yield = div_ratio / price * 100
                
                if div_yield > 5:  # 高股息 >5%
                    s5 = 10
                elif div_yield > 3:  # 良好股息 >3%
                    s5 = 8
                elif div_yield > 2:
                    s5 = 6
                elif div_yield > 1:
                    s5 = 4
                elif div_yield > 0:
                    s5 = 2
                else:
                    s5 = 0  # 无分红
                
                detail["dividend_yield"] = round(div_yield, 2)
        
        score += s5
        detail["dividend_score"] = s5
    except Exception as e:
        detail["dividend_score"] = 5
        detail["dividend_error"] = str(e)

    if not has_data:
        result = {
            "ts_code": ts_code,
            "alpha_score": 50.0,
            "detail": {"no_data": True},
            "signal": "数据不足（中性）"
        }
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False)
        except Exception:
            pass
        return result

    final_score = max(0, min(100, score))
    result = {
        "ts_code": ts_code,
        "alpha_score": round(final_score, 2),
        "detail": detail,
        "signal": classify_signal(final_score, detail)
    }
    
    # 保存缓存
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False)
    except Exception:
        pass

    return result


def classify_signal(score, detail=None):
    """价值型Alpha信号分类（返回中线和长线建议）"""
    detail = detail or {}
    pe_pct = detail.get('pe_pct_rank', 50)  # PE历史百分位
    roe = detail.get('roe_recent', 0)  # ROE
    roe_trend = detail.get('roe_trend', 0)  # ROE趋势
    growth = max(detail.get('revenue_yoy', 0) or 0, detail.get('profit_yoy', 0) or 0)  # 增速
    ocf_ratio = detail.get('ocf_netprofit_ratio', 1)  # 现金流比率
    div_yield = detail.get('dividend_yield', 0)  # 股息率
    
    # 中线建议（3-6个月）
    mid_signal = ""
    if score >= 80:
        mid_signal = "极度低估（强烈买入，目标收益20%+）"
    elif score >= 70:
        mid_signal = "明显低估（买入，目标收益15%+）"
    elif score >= 60:
        mid_signal = "轻微低估（持有，目标收益10%+）"
    elif score >= 50:
        mid_signal = "估值合理（中性，目标收益5-10%）"
    elif score >= 40:
        mid_signal = "轻微高估（谨慎，目标收益0-5%）"
    elif score >= 30:
        mid_signal = "明显高估（减仓，控制回撤）"
    else:
        mid_signal = "极度高估（卖出，回避风险）"
    
    # 长线建议（6-12个月）
    long_signal = ""
    
    # 综合评估长线价值
    valuation_ok = pe_pct < 40  # 估值合理
    quality_ok = roe > 10 and (roe_trend >= 0 or roe > 15)  # 盈利质量
    growth_ok = growth > 0  # 正增长
    cashflow_ok = ocf_ratio > 0.8  # 现金流健康
    
    if score >= 80 and valuation_ok and quality_ok and growth_ok and cashflow_ok:
        long_signal = "核心持仓（基本面优质，低估值+高ROE+正增长，可长期持有）"
    elif score >= 70 and valuation_ok and quality_ok:
        long_signal = "优质标的（低估+盈利稳定，适合中长线持有）"
    elif score >= 60 and quality_ok:
        long_signal = "稳健持有（盈利稳定，可中线持有，关注拐点）"
    elif score >= 50:
        if div_yield > 3:
            long_signal = "收息标的（股息率良好，适合保守配置）"
        else:
            long_signal = "观望为主（估值合理，等待更好的买点）"
    elif score >= 40:
        long_signal = "谨慎持有（估值偏高，建议减仓）"
    else:
        long_signal = "长线回避（基本面不支持，不建议持有）"
    
    # 添加股息参考
    if div_yield > 5:
        long_signal += f"，股息率{div_yield:.1f}%可作防御"
    elif div_yield > 3:
        long_signal += f"，股息率{div_yield:.1f}%提供安全垫"
    
    return f"{mid_signal} | {long_signal}"


def calculate_short_term_win_score(ts_code, pro, above_chips_pct=-1, trade_date=None):
    """
    短线胜率评分模型（3-10交易日）
    使用 Tushare stk_factor_pro 技术因子 + 筹码分布（可选）构建

    参数:
        ts_code: 股票代码
        pro: Tushare pro 实例
        above_chips_pct: 上方套牢盘比例(%)，-1表示暂无数据
    返回:
        {...}
        signal: '可参与趋势单' | '分歧博弈' | '持有/分批止盈' | '等待方向确认' | '规避'
    """
    result = {
        "ts_code": ts_code,
        "win_score": 0,
        "breakdown": {
            "trend_score": 0,
            "momentum_score": 0,
            "position_score": 0,
            "volatility_score": 0
        },
        "stage": "",
        "signal": "",
        "key_risk": "",
        "pattern_type": "",
        "ma_structure": "",
        "rsi_signal": "",
        "macd_signal": "",
        "kdj_signal": "",
        "volume_signal": "",
        "signal_resonance": ""
    }

    try:
        # 当日缓存路径（复用当天数据，避免重复API调用）
        _cache_file = os.path.join(CACHE_DIR, f"stk_pro_{ts_code}_{TRADE_DATE}.csv")

        # 1. 优先读缓存
        df = None
        cache_valid = False
        if os.path.exists(_cache_file):
            try:
                df = pd.read_csv(_cache_file)
                df['trade_date'] = df['trade_date'].astype(str)
                # 检查缓存是否包含目标日期
                target_date_str = str(trade_date or TRADE_DATE)
                if target_date_str in df['trade_date'].values:
                    cache_valid = True
                else:
                    print(f"[缓存过期] {ts_code} 缓存中缺少 {target_date_str}，重新获取")
                    df = None
            except Exception as e:
                print(f"[缓存读取失败] {ts_code}: {e}")
                df = None

        # 2. 缓存缺失或过期则调用 stk_factor_pro 专业版接口
        if df is None or df.empty:
            df = pro.stk_factor_pro(ts_code=ts_code, start_date='20250101')
            if df is not None and not df.empty:
                df['trade_date'] = df['trade_date'].astype(str)
                try:
                    df.to_csv(_cache_file, index=False)
                    print(f"[缓存更新] {ts_code} 数据已更新")
                except Exception as e:
                    print(f"[缓存保存失败] {ts_code}: {e}")

        if df is None or df.empty:
            result["stage"] = "数据不足"
            result["signal"] = "规避"
            result["key_risk"] = "技术因子数据获取失败"
            return result

        # 按日期升序排列（便于取前N日）
        df = df.sort_values('trade_date').reset_index(drop=True)
        latest = df.iloc[-1]          # 最新（即今日）
        prev1 = df.iloc[-2] if len(df) >= 2 else None   # 前1日
        prev5 = df.iloc[-6] if len(df) >= 6 else (df.iloc[0] if len(df) >= 1 else None)  # 前5日

        # 提取因子（stk_factor_pro 专业版字段命名为 *_bfq，不复权）
        ma5 = float(latest.get('ma_bfq_5', 0) or 0)
        ma10 = float(latest.get('ma_bfq_10', 0) or 0)
        ma20 = float(latest.get('ma_bfq_20', 0) or 0)
        ma60 = float(latest.get('ma_bfq_60', 0) or 0)
        close = float(latest.get('close', 0) or 0)
        macd = float(latest.get('macd_bfq', 0) or 0)
        dif = float(latest.get('macd_dif_bfq', 0) or 0)
        dea = float(latest.get('macd_dea_bfq', 0) or 0)
        rsi_6 = float(latest.get('rsi_bfq_6', 50) or 50)
        rsi_12 = float(latest.get('rsi_bfq_12', 50) or 50)
        rsi_24 = float(latest.get('rsi_bfq_24', 50) or 50)
        kdj_k = float(latest.get('kdj_k_bfq', 50) or 50)
        kdj_d = float(latest.get('kdj_d_bfq', 50) or 50)
        kdj_j = float(latest.get('kdj_j_bfq', 50) or 50)
        boll_upper = float(latest.get('boll_upper_bfq', 0) or 0)
        boll_mid = float(latest.get('boll_mid_bfq', 0) or 0)
        boll_lower = float(latest.get('boll_lower_bfq', 0) or 0)
        atr = float(latest.get('atr_bfq', 0) or 0)
        volume = float(latest.get('vol', 0) or 0)

        # 前1日 / 前5日指标（用于信号比较）
        prev_dif = float(prev1.get('macd_dif_bfq', 0) or 0) if prev1 is not None else dif
        prev_dea = float(prev1.get('macd_dea_bfq', 0) or 0) if prev1 is not None else dea
        prev_macd = float(prev1.get('macd_bfq', 0) or 0) if prev1 is not None else macd
        prev_rsi6 = float(prev1.get('rsi_bfq_6', 50) or 50) if prev1 is not None else rsi_6
        prev_rsi12 = float(prev1.get('rsi_bfq_12', 50) or 50) if prev1 is not None else rsi_12
        prev_kdj_k = float(prev1.get('kdj_k_bfq', 50) or 50) if prev1 is not None else kdj_k
        prev_kdj_d = float(prev1.get('kdj_d_bfq', 50) or 50) if prev1 is not None else kdj_d
        prev_close = float(prev1.get('close', 0) or 0) if prev1 is not None else close
        prev5_vol = float(prev5.get('vol', 0) or 0) if prev5 is not None else volume
        prev5_close = float(prev5.get('close', 0) or 0) if prev5 is not None else close
        prev_vol = float(prev1.get('vol', 0) or 0) if prev1 is not None else volume

        # =========================
        # ① 趋势结构评分（40分）
        # =========================
        trend_score = 0

        # 判断均线排列结构
        ma_structure_ok = False
        if ma5 > 0 and ma10 > 0 and ma20 > 0 and ma60 > 0:
            if ma5 > ma10 and ma10 > ma20 and ma20 > ma60:
                trend_score = 40
                ma_structure_ok = True
            elif ma5 > ma10 and ma10 > ma20:
                trend_score = 30
                ma_structure_ok = True
            elif ma5 > ma10:
                trend_score = 20
            else:
                trend_score = 5
        else:
            trend_score = 5

        # 【关键修正：股价位置惩罚】
        # 当股价跌破短期均线时，即使均线排列良好也应扣分
        if ma_structure_ok and ma5 > 0:
            # 跌破5日线，严重扣分（滞后性修复）
            if close < ma5:
                trend_score = max(15, trend_score - 15)
            # 贴近但未跌破5日线，轻微扣分
            elif close < ma5 * 1.01:
                trend_score = max(25, trend_score - 5)

        if close > ma20 and ma20 > 0:
            trend_score += 5
        if macd > 0:
            trend_score += 5

        trend_score = min(40, max(0, trend_score))

        # =========================
        # ② 动量健康度（30分）- 强趋势股优化版
        # =========================
        momentum_score = 15

        avg_rsi = (rsi_6 + rsi_12 + rsi_24) / 3

        # 【A】RSI趋势方向判断（利用已有因子）
        rsi6_rising = rsi_6 > prev_rsi6    # RSI_6较前日上升
        rsi12_rising = rsi_12 > prev_rsi12  # RSI_12较前日上升

        # 【B】趋势结构联动：均线多头排列时允许更高RSI
        strong_trend = (ma5 > ma10 and ma10 > ma20 and ma20 > ma60 and close > ma5)

        # 【C】RSI基础评分（对强趋势股放宽上限）
        if 65 <= avg_rsi <= 82:
            momentum_score = 30  # 最佳区间：强趋势股正常活跃区
        elif 55 <= avg_rsi < 65:
            momentum_score = 28  # 偏强区间，可能刚启动或温和上涨
        elif 82 < avg_rsi <= 88:
            # 偏高区间，结合趋势结构判断
            if strong_trend and rsi6_rising:
                momentum_score = 26  # 趋势完好+RSI仍上升=强势延续，轻微扣分
            else:
                momentum_score = 18  # 趋势一般或RSI回落=过热风险，明显扣分
        elif 50 <= avg_rsi < 55:
            momentum_score = 20  # 中性偏弱
        elif avg_rsi > 88:
            if strong_trend and rsi6_rising:
                momentum_score = 15  # 极端高位但趋势完好+RSI仍在涨=趋势加速
            else:
                momentum_score = 5   # 极端高位+趋势弱=危险
        elif 40 <= avg_rsi < 50:
            momentum_score = 10  # 弱势，强趋势策略不适合
        else:
            momentum_score = 5   # <40，极弱

        # 【D】RSI趋势方向加分（无论绝对值，方向正确应加分）
        if rsi6_rising:
            momentum_score = min(30, momentum_score + 3)
        if rsi12_rising:
            momentum_score = min(30, momentum_score + 2)

        # 【E】KDJ修正（对强趋势股放宽）
        if kdj_j > 95:
            momentum_score = max(5, momentum_score - 5)  # 仅极端超买时惩罚
        elif kdj_j < 15 and strong_trend:
            momentum_score = min(30, momentum_score + 3)  # 强趋势中KDJ超卖=回调买点

        # 【F】MACD共振
        if dif > dea:
            momentum_score = min(30, momentum_score + 3)

        # 【G】RSI顶背离检测（独立惩罚）
        # 股价创新高但RSI没跟上 = 背离风险
        if prev1 is not None and len(df) >= 10:
            prev10_close = float(df.iloc[-10].get('close', 0) or 0)
            prev10_rsi6 = float(df.iloc[-10].get('rsi_bfq_6', 50) or 50)
            if prev10_close > 0 and close > prev10_close * 1.05 and rsi_6 < prev10_rsi6:
                momentum_score = max(5, momentum_score - 8)
            elif prev10_close > 0 and close > prev10_close * 1.08 and rsi_6 < prev10_rsi6 * 0.95:
                momentum_score = max(3, momentum_score - 12)

        # 【H】量价齐升加分
        if prev_close > 0 and prev_vol > 0:
            price_up = close > prev_close
            vol_up = volume > prev_vol
            if price_up and vol_up:
                momentum_score = min(30, momentum_score + 4)  # 量价齐升

        # 【I】KDJ金叉加分
        if kdj_k > kdj_d and prev_kdj_k <= prev_kdj_d:
            momentum_score = min(30, momentum_score + 4)  # KDJ金叉

        # 【J】MACD积极信号加分
        # MACD绿柱缩短（macd=histogram，负值向0靠近或已转正）
        if macd > prev_macd:
            momentum_score = min(30, momentum_score + 3)  # 绿柱缩短或红柱增长
        # MACD上穿0轴（绿转红）
        if prev_macd < 0 and macd >= 0:
            momentum_score = min(30, momentum_score + 5)  # 上穿0轴，加分更多

        momentum_score = min(30, max(0, momentum_score))

        # =========================
        # ③ 位置合理性（20分）
        # =========================
        position_score = 10

        if ma20 > 0:
            bias_ma20 = abs(close - ma20) / ma20 * 100

            if bias_ma20 <= 5:
                position_score = 20
            elif bias_ma20 <= 10:
                position_score = 18
            elif bias_ma20 <= 15:
                position_score = 12
            elif bias_ma20 <= 20:
                position_score = 8
            elif bias_ma20 <= 30:
                position_score = 3
            else:
                position_score = 0

        if boll_mid > 0:
            bias_boll = abs(close - boll_mid) / boll_mid * 100
            if bias_boll <= 5:
                position_score = min(20, position_score + 3)
            elif bias_boll > 20:
                position_score = max(0, position_score - 5)

        position_score = min(20, max(0, position_score))

        # =========================
        # ④ 波动匹配度（10分）
        # =========================
        volatility_score = 5

        if atr > 0 and close > 0:
            atr_ratio = atr / close * 100

            if 1 <= atr_ratio <= 5:
                volatility_score = 10
            elif atr_ratio < 1:
                volatility_score = 3
            elif atr_ratio > 8:
                volatility_score = 2

        if boll_upper > 0 and boll_lower > 0:
            boll_width = (boll_upper - boll_lower) / boll_mid * 100 if boll_mid > 0 else 0

            if 10 <= boll_width <= 25:
                volatility_score = min(10, volatility_score + 2)
            elif boll_width > 40:
                volatility_score = max(2, volatility_score - 2)

        volatility_score = min(10, max(0, volatility_score))

        total_score = int(trend_score + momentum_score + position_score + volatility_score)

        # =========================
        # 【关键修正1】RSI超买 + 套牢盘 联合惩罚
        #   RSI>75 且 上方套牢盘>15% → 总分 -15（如康强电子 68→53）
        #   上方套牢盘>40% → 强制归为"规避"（后面信号映射用）
        # =========================
        penalty_note = ""
        
        # 【新增】冲高回落形态识别
        is_high_wick = False
        high = float(latest.get('high', 0) or 0)
        low = float(latest.get('low', 0) or 0)
        open_price = float(latest.get('open', 0) or 0)
        prev_close = float(prev1.get('close', 0) or 0) if prev1 is not None else close
        
        if high > 0 and close > 0:
            wick_ratio = (high - close) / (high - low) if (high - low) > 0 else 0
            if wick_ratio > 0.6 and close < open_price:
                is_high_wick = True
                total_score = max(0, total_score - 15)
                penalty_note = f"冲高回落形态(上影线比例{wick_ratio:.1f})惩罚-15分"
        
        # 【新增】连续下跌天数惩罚（独立生效）
        down_days = int(latest.get('downdays', 0) or 0)
        if down_days >= 2:
            deduct = min(down_days * 5, 20)
            total_score = max(0, total_score - deduct)
            if penalty_note:
                penalty_note += f"；连续下跌{down_days}天惩罚-{deduct}分"
            else:
                penalty_note = f"连续下跌{down_days}天惩罚-{deduct}分"
        
        # 【新增】MACD顶背离识别（独立生效）
        if dif < prev_dif and close > prev_close:
            total_score = max(0, total_score - 15)
            if penalty_note:
                penalty_note += "；MACD顶背离惩罚-15分"
            else:
                penalty_note = "MACD顶背离惩罚-15分"
        
        if not penalty_note:
            if avg_rsi > 85:
                deduct = 20
                total_score = max(0, total_score - deduct)
                penalty_note = f"RSI极度疯狂({avg_rsi:.0f})惩罚-{deduct}分"
            elif avg_rsi > 80:
                deduct = 10
                total_score = max(0, total_score - deduct)
                penalty_note = f"RSI严重超买({avg_rsi:.0f})惩罚-{deduct}分"
            elif avg_rsi > 75 and above_chips_pct >= 0 and above_chips_pct > 15:
                deduct = 15
                total_score = max(0, total_score - deduct)
                penalty_note = f"RSI超买({avg_rsi:.0f})+套牢盘({above_chips_pct:.0f}%)联合惩罚-{deduct}分"

        # =========================
        # 【信号分析】基于前后日对比
        # =========================

        # --- MA结构 ---
        if ma5 > 0 and ma10 > 0 and ma20 > 0 and ma60 > 0:
            if ma5 > ma10 and ma10 > ma20 and ma20 > ma60:
                ma_structure = "完整多头排列"
            elif ma5 > ma10 and ma10 > ma20:
                ma_structure = "短期多头排列"
            elif ma5 > ma10:
                ma_structure = "初步多头"
            else:
                ma_structure = "空头排列"
        else:
            ma_structure = "均线数据不足"

        # --- RSI信号 ---
        rsi_chg = rsi_6 - prev_rsi6
        if avg_rsi < 40:
            rsi_signal = f"RSI弱势区(均值{avg_rsi:.0f})"
        elif avg_rsi < 50:
            if rsi_chg > 3:
                rsi_signal = f"RSI低位金叉向上({prev_rsi6:.0f}→{rsi_6:.0f})，动量转强"
            else:
                rsi_signal = f"RSI偏弱区(均值{avg_rsi:.0f})"
        elif 50 <= avg_rsi < 65:
            if rsi_chg > 3:
                rsi_signal = f"RSI强势区金叉({prev_rsi6:.0f}→{rsi_6:.0f})，上升动量强化"
            else:
                rsi_signal = f"RSI偏强区(均值{avg_rsi:.0f})"
        elif 65 <= avg_rsi < 75:
            rsi_signal = f"RSI强势偏热(均值{avg_rsi:.0f})"
        elif 75 <= avg_rsi < 85:
            rsi_signal = f"RSI高位过热(均值{avg_rsi:.0f})，注意回调"
        else:
            rsi_signal = f"RSI严重超买({avg_rsi:.0f})，风险极高"

        # --- MACD信号 ---
        dif_chg = dif - prev_dif
        if dif > dea and prev_dif <= prev1.get('macd_dea_bfq', dea):
            macd_signal = f"MACD金叉(零轴上方)，DIF拐头向上+{dif_chg:.3f}"
        elif dif > dea:
            macd_signal = f"MACD多头(DIF>DEA)，DIF向上+{dif_chg:.3f}"
        elif dif < dea and dif > 0 and prev_dif <= 0:
            macd_signal = f"MACD零轴附近死叉，注意方向选择"
        elif dif < dea and dif < 0:
            macd_signal = f"MACD空头(DIF<DEA)，DIF下行{abs(dif_chg):.3f}"
        elif dif > 0 and dif_chg > 0.05:
            macd_signal = f"DIF向上拐头({prev_dif:.3f}→{dif:.3f})，底部动量修复"
        elif dif > 0 and dif_chg < -0.05:
            macd_signal = f"DIF向下拐头({prev_dif:.3f}→{dif:.3f})，动量衰减"
        else:
            macd_signal = f"DIF={dif:.3f}，方向中性"

        # --- KDJ信号 ---
        if prev_kdj_k < prev_kdj_d and kdj_k > kdj_d:
            kdj_signal = f"KDJ日线金叉({prev_kdj_k:.1f}→{kdj_k:.1f})"
        elif prev_kdj_k > prev_kdj_d and kdj_k < kdj_d:
            kdj_signal = f"KDJ日线死叉({prev_kdj_k:.1f}→{kdj_k:.1f})"
        elif kdj_j > 90:
            kdj_signal = f"KDJ高位钝化(J={kdj_j:.0f})，警惕回调"
        elif kdj_j < 20:
            kdj_signal = f"KDJ低位钝化(J={kdj_j:.0f})，超卖反弹概率"
        elif kdj_k > kdj_d:
            kdj_signal = f"KDJ多头排列(J={kdj_j:.0f})"
        else:
            kdj_signal = f"KDJ空头排列(J={kdj_j:.0f})"

        # --- 成交量信号 ---
        vol_ratio = volume / prev5_vol if prev5_vol > 0 else 1
        prev_vol_ratio = prev_vol / prev5_vol if prev5_vol > 0 else 1
        close_chg = (close - prev5_close) / prev5_close * 100 if prev5_close > 0 else 0

        if vol_ratio >= 2.0 and close_chg > 3:
            volume_signal = f"放量阳线确认(量比{vol_ratio:.1f}倍，涨幅{close_chg:.1f}%)"
        elif vol_ratio >= 1.5 and close_chg > 1:
            volume_signal = f"温和放量上涨(量比{vol_ratio:.1f}倍)"
        elif vol_ratio >= 1.5 and close_chg < -1:
            volume_signal = f"放量下跌(量比{vol_ratio:.1f}倍)，抛压重"
        elif vol_ratio < 0.6:
            volume_signal = f"缩量整理(量比{vol_ratio:.1f}倍)，观望情绪浓"
        else:
            volume_signal = f"量能平稳(量比{vol_ratio:.1f}倍)"

        # --- 形态类型（二波反转 / 趋势突破 / 回调低吸 / 震荡整理）---
        is_2nd_wave = (rsi_6 > prev_rsi6 and prev_rsi6 < 50 and rsi_6 > 50 and
                       kdj_k > prev_kdj_k and dif > prev_dif and close > ma5)
        is_breakthrough = (ma5 > ma10 and ma10 > ma20 and close > ma5 and
                           rsi_6 > 60 and vol_ratio >= 1.3)
        # 【修正】回调低吸条件：跌破5日线但在20日线之上，RSI处于低位
        is_pullback_buy = (ma5 > ma10 and ma10 > ma20 and  # 均线多头排列
                           close < ma5 and close >= ma20 and  # 跌破5日线但在20日线之上
                           35 <= avg_rsi < 55)  # RSI低位即可

        if is_2nd_wave:
            pattern_type = "二波反转（RSI金叉+动量修复）"
        elif is_breakthrough:
            pattern_type = "趋势突破（均线多头+放量确认）"
        elif is_pullback_buy:
            pattern_type = "回调低吸（回踩均线蓄力）"
        elif trend_score >= 30 and momentum_score >= 25:
            pattern_type = "强势延续（趋势+动量共振）"
        else:
            pattern_type = "震荡整理（信号不共振）"

        # =========================
        # 【关键修正2】重写交易阶段分类（以最终评分为主）
        #   核心原则:
        #     - 加速期(≥80分): 趋势+动量共振强势，应持有/分批止盈
        #     - 弱转强初期(60-80分): 指标共振偏多，可参与趋势单
        #     - 分歧期(45-60分): 多空力量平衡，等待方向确认
        #     - 回调低吸(45-60分): 股价回踩，可低吸
        #     - 退潮期(<45分): 趋势结构弱势，规避
        #   趋势+动量双强是基础前提，否则不应强行归为"可参与"阶段
        # =========================
        trend_baseline_ok = (trend_score >= 30 and momentum_score >= 20)
        
        # 【新增】优先判断回调低吸阶段
        if is_pullback_buy:
            stage = "回调低吸"
        elif trend_baseline_ok:
            if total_score >= 80:
                stage = "加速期"
            elif total_score >= 60:
                stage = "弱转强初期"
            elif total_score >= 45:
                stage = "分歧期"
            else:
                stage = "启动期"
        elif total_score >= 45:
            # 趋势偏弱但分数尚可 → 仍算分歧（多空拉锯）
            stage = "分歧期"
        else:
            stage = "退潮期"

        # =========================
        # 【关键修正3】重写交易信号（胜率-信号映射）
        #   用户规则1: 胜率>=80 and 加速期 → 持有/分批止盈
        #   用户规则1: 胜率>=80 and 分歧期 → 等待方向确认，评分-20
        #   用户规则2: 规避只在 总分<50 / RSI>85 / 套牢盘>40% 时出现
        # =========================
        signal = ""
        key_risk = ""
        key_risk_parts = []

        # 分歧期自动降权逻辑（高胜率+分歧期：说明指标共振偏多，但结构仍不稳）
        # 注: 降权仅用于信号展示，不反向修改total_score避免影响阶段判定
        display_score = total_score
        stage_note = ""
        if stage == "分歧期" and total_score >= 80:
            display_score = max(0, total_score - 20)
            stage_note = f"分歧期高胜率自动降权20分({total_score}→{display_score})"
        elif stage == "分歧期" and total_score >= 60:
            # 用户规则3: 分歧期合理范围45-60，超60应判定为弱转强初期
            # 这里做二次修正：若分数>60却仍被划分为分歧期，说明趋势基线不足
            # 保持分歧期判定，但加注说明趋势不足
            stage_note = f"趋势基线不足(trend={trend_score},mom={momentum_score})，虽分数{total_score}仍判定为分歧期"

        forced_avoid = False
        if total_score < 50:
            forced_avoid = True
            key_risk_parts.append(f"胜率过低({total_score}分)")
        if avg_rsi > 85:
            forced_avoid = True
            key_risk_parts.append(f"RSI极度疯狂({avg_rsi:.0f})")

        if forced_avoid:
            signal = "规避"
            if key_risk_parts:
                key_risk = "；".join(key_risk_parts) + "，观望"
            else:
                key_risk = "趋势结构弱势，观望"
        elif stage == "加速期":
            signal = "持有/分批止盈"
            key_risk = f"胜率{total_score}分处于加速期，注意获利盘抛压"
        elif stage == "回调低吸":
            signal = "可参与低吸"
            key_risk = f"股价回踩20日线支撑，RSI({avg_rsi:.0f})处于低位，适合分批低吸"
        elif stage == "分歧期":
            signal = "等待方向确认"
            if avg_rsi >= 70:
                key_risk = f"RSI偏高({avg_rsi:.0f})，等待回踩或方向选择"
            else:
                key_risk = f"多空力量平衡，需等待趋势确认"
        elif stage == "弱转强初期":
            signal = "可参与趋势单"
            key_risk = ""
        elif stage == "初升期":
            signal = "可参与趋势单"
            key_risk = ""
        elif stage == "启动期":
            signal = "等待转强"
            key_risk = "信号刚转多，等待均线确认"
        else:
            signal = "规避"
            key_risk = "观望"

        # 合并各类备注
        extra_notes = []
        if penalty_note:
            extra_notes.append(penalty_note)
        if stage_note:
            extra_notes.append(stage_note)
        if extra_notes:
            if key_risk:
                key_risk = key_risk + " | " + " | ".join(extra_notes)
            else:
                key_risk = " | ".join(extra_notes)

        # =========================
        # 信号共振判断（基于二波反转+趋势突破）
        # =========================
        buy_signals = sum([
            rsi_chg > 3,
            dif_chg > 0.05,
            kdj_k > prev_kdj_k,
            vol_ratio >= 1.3,
            close > ma5,
            dif > dea,
            position_score >= 15,
        ])
        buy共振 = buy_signals >= 5 and avg_rsi < 80 and trend_score >= 20
        sell共振 = (kdj_k < prev_kdj_k and rsi_chg < -3 and vol_ratio < 0.7) or avg_rsi > 88

        if buy共振:
            signal_resonance = "短线信号共振买入（量价、RSI、MACD、KDJ多维共振）"
        elif sell共振:
            signal_resonance = "短线信号共振卖出（多指标走弱，建议回避）"
        else:
            signal_resonance = "信号混乱，短线方向不明，观望"

        # 填充结果
        result["win_score"] = total_score
        result["breakdown"]["trend_score"] = int(trend_score)
        result["breakdown"]["momentum_score"] = int(momentum_score)
        result["breakdown"]["position_score"] = int(position_score)
        result["breakdown"]["volatility_score"] = int(volatility_score)
        result["stage"] = stage
        result["signal"] = signal
        result["key_risk"] = key_risk
        result["pattern_type"] = pattern_type
        result["ma_structure"] = ma_structure
        result["rsi_signal"] = rsi_signal
        result["macd_signal"] = macd_signal
        result["kdj_signal"] = kdj_signal
        result["volume_signal"] = volume_signal
        result["signal_resonance"] = signal_resonance

    except Exception as e:
        result["stage"] = "计算异常"
        result["signal"] = "规避"
        result["key_risk"] = f"计算错误: {str(e)}"

    return result


def detect_breakout(ts_code, pro, trade_date=None):
    """
    突破型策略检测函数
    总分110分，75分以上视为有效突破，可列入观察/买入名单
    
    评分标准：
    1. 上一波强度加分 (0-10分): 20天振幅越大，突破可靠性越高
    2. 价格突破 (30分): close > boll_upper
    3. 趋势均线 (25分): ma5 > ma10 且 ma10 > ma20 且 close > ma5
    4. 动能共振 (20分): macd > 0 且 dif > dea 且 kdj_j > 80
    5. 空间与安全 (15分): rsi_6 > 65 且 rsi_6 < 85
    6. 波动率辅助 (10分): atr > 0 且 close > ma60
    
    优化重点：
    - 上一波强度越大，突破可靠性越高，增加额外加分
    
    参数:
        ts_code: 股票代码
        pro: Tushare pro 实例
        trade_date: 指定日期（None表示最新）
    返回:
        {
            "breakout_score": int,      # 突破评分
            "is_valid_breakout": bool,  # 是否有效突破(>=75分)
            "signal": str               # 信号建议
        }
    """
    result = {
        "ts_code": ts_code,
        "trade_date": trade_date or TRADE_DATE,
        "breakout_score": 0,
        "is_valid_breakout": False,
        "signal": "非突破形态"
    }
    
    try:
        _cache_file = os.path.join(CACHE_DIR, f"stk_pro_{ts_code}_{TRADE_DATE}.csv")
        
        if os.path.exists(_cache_file):
            df = pd.read_csv(_cache_file)
        else:
            df = pro.stk_factor_pro(ts_code=ts_code, start_date=trade_date, end_date=TRADE_DATE)
            df.to_csv(_cache_file, index=False)
        
        df['trade_date'] = df['trade_date'].astype(str)
        # 按日期升序排列（确保df.iloc[-1]是最新数据）
        df = df.sort_values('trade_date').reset_index(drop=True)
        
        if trade_date:
            mask = df['trade_date'] == trade_date
            if mask.any():
                latest = df[mask].iloc[0]
            else:
                return result
        else:
            # 使用TRADE_DATE查找对应行，而非df.iloc[-1]（支持回溯模式）
            mask = df['trade_date'] == str(trade_date or TRADE_DATE)
            if mask.any():
                latest = df[mask].iloc[0]
            else:
                return result
        
        close = float(latest.get('close', 0) or 0)
        boll_upper = float(latest.get('boll_upper_bfq', 0) or 0)
        ma5 = float(latest.get('ma_bfq_5', 0) or 0)
        ma10 = float(latest.get('ma_bfq_10', 0) or 0)
        ma20 = float(latest.get('ma_bfq_20', 0) or 0)
        ma60 = float(latest.get('ma_bfq_60', 0) or 0)
        macd = float(latest.get('macd_bfq', 0) or 0)
        dif = float(latest.get('macd_dif_bfq', 0) or 0)
        dea = float(latest.get('macd_dea_bfq', 0) or 0)
        kdj_j = float(latest.get('kdj_bfq', 50) or 50)
        rsi_6 = float(latest.get('rsi_bfq_6', 50) or 50)
        atr = float(latest.get('atr_bfq', 0) or 0)
        
        total_score = 0
        
        # ===== 上一波强度加分（20天振幅）=====
        lookback_days = 20
        mask_lookback = df['trade_date'] <= (trade_date or TRADE_DATE)
        df_lookback = df[mask_lookback].tail(lookback_days)
        if not df_lookback.empty:
            lookback_high = df_lookback['high'].max()
            lookback_low = df_lookback['low'].min()
            if lookback_low > 0:
                wave_strength = (lookback_high - lookback_low) / lookback_low * 100
                # 上一波强度越大，突破可靠性越高
                if wave_strength >= 50:
                    total_score += 10  # 强趋势股突破更可靠
                elif wave_strength >= 30:
                    total_score += 5   # 中等趋势
        
        if close > boll_upper and boll_upper > 0:
            total_score += 30
        if ma5 > ma10 and ma10 > ma20 and close > ma5 and ma5 > 0:
            total_score += 25
        if macd > 0 and dif > dea and kdj_j > 80:
            total_score += 20
        if 65 < rsi_6 < 85:
            total_score += 15
        if atr > 0 and close > ma60 and ma60 > 0:
            total_score += 10
        
        result["breakout_score"] = total_score
        result["is_valid_breakout"] = total_score >= 75
        
        if result["is_valid_breakout"]:
            result["signal"] = "有效突破！列入观察/买入名单"
        elif total_score >= 60:
            result["signal"] = "突破待确认，建议观察"
        elif total_score >= 40:
            result["signal"] = "突破迹象初现，继续跟踪"
        else:
            result["signal"] = "非突破形态"
        
    except Exception:
        pass
    
    return result


def detect_wave2_reversal(ts_code, pro, trade_date=None, lookback_days=20):
    """
    二波反转策略检测函数（共振评分版）
    使用10维度共振评分替代旧版评分逻辑，结合形态分类输出
    
    评分维度：
      动量类: RSI(3) + KDJ-J(3) + CCI(2) + WR(2)
      资金类: MFI(2) + OBV方向(1) + 量比启动(2)
      趋势类: MACD金叉(2) + DMI反转(3) + MA位置(1)
      情绪类: BIAS(3) + PSY(2) + VR(1)
      背离类: RSI底背离(3) + MFI底背离(3)
    
    参数:
        ts_code: 股票代码
        pro: Tushare pro 实例
        trade_date: 指定日期（None表示最新）
        lookback_days: 回溯天数
    返回:
        {
            "ts_code": str,
            "trade_date": str,
            "wave2_score": int,          # 共振总评分
            "pattern_score": int,        # 形态基础分
            "resonance_score": int,      # 共振加分
            "is_perfect_signal": bool,
            "signal": str,
            "pattern": str,              # 形态类型
            "score_details": str         # 评分明细
        }
    """
    result = {
        "ts_code": ts_code,
        "trade_date": trade_date or TRADE_DATE,
        "wave2_score": 0,
        "pattern_score": 0,
        "resonance_score": 0,
        "is_perfect_signal": False,
        "signal": "非二波形态",
        "pattern": "",
        "score_details": "",
    }
    
    try:
        _cache_file = os.path.join(CACHE_DIR, f"stk_pro_{ts_code}_{TRADE_DATE}.csv")
        
        if os.path.exists(_cache_file):
            df = pd.read_csv(_cache_file)
        else:
            df = pro.stk_factor_pro(ts_code=ts_code, start_date=trade_date, end_date=TRADE_DATE)
            df.to_csv(_cache_file, index=False)
        
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date').reset_index(drop=True)
        
        if df.empty or len(df) < 30:
            return result
        
        # 安全取值函数
        def safe_val(row, col, default=0.0):
            if col in row and not pd.isna(row[col]):
                return float(row[col])
            return default
        
        # ── 1. 形态分类（找wave1 → 判断二波调整形态）──
        pattern = ""
        wave1_high_idx = None
        surge_gain = 0.0
        
        import numpy as np
        ADJUST_MAX_VAL = 60
        SURGE_DAYS_VAL = 20
        SURGE_MIN_VAL = 0.20
        
        closes = df['close'].values
        for end_idx in range(len(df) - 1, ADJUST_MAX_VAL, -1):
            window_start = end_idx - SURGE_DAYS_VAL
            if window_start < 0:
                break
            window_closes = closes[window_start:end_idx+1]
            low_idx = np.argmin(window_closes)
            high_idx = np.argmax(window_closes)
            if high_idx <= low_idx:
                continue
            if (high_idx - low_idx) > SURGE_DAYS_VAL - 2:
                continue
            wgain = (window_closes[high_idx] - window_closes[low_idx]) / window_closes[low_idx]
            if wgain >= SURGE_MIN_VAL:
                wave1_high_idx = window_start + high_idx
                surge_gain = wgain
                break
        
        if wave1_high_idx is not None:
            # 调用 classify_wave2_pattern 识别形态（先重命名列兼容旧命名）
            rename_cwp = {
                'ma_bfq_5': 'ma5', 'ma_bfq_10': 'ma10', 'ma_bfq_20': 'ma20', 'ma_bfq_60': 'ma60',
                'macd_dif_bfq': 'macd_dif', 'macd_dea_bfq': 'macd_dea',
                'rsi_bfq_6': 'rsi_6',
            }
            df_cwp = df.rename(columns={k: v for k, v in rename_cwp.items() if k in df.columns})
            from tushare_quant import classify_wave2_pattern as cwp
            pattern_name, pdata = classify_wave2_pattern(df_cwp, wave1_high_idx, len(df)-1)
            if pattern_name and pattern_name != '其他':
                pattern = pattern_name
        
        # ── 2. 取最新行和前一行的数据 ──
        latest = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) >= 2 else None
        
        # 构建类似 dict 的访问方式
        row_data = {}
        for col in df.columns:
            row_data[col] = latest[col]
        
        prev_data = None
        if prev_row is not None:
            prev_data = {}
            for col in df.columns:
                prev_data[col] = prev_row[col]
        
        # ── 3. 共振评分 ──
        total_score = 0
        details = []
        
        def _add(pts, desc):
            nonlocal total_score
            total_score += pts
            details.append(f'{desc}(+{pts})')
        
        def v(col, default=0.0):
            return safe_val(row_data, col, default)
        
        def pv(col, default=0.0):
            if prev_data is not None:
                return safe_val(prev_data, col, default)
            return default
        
        # ── 动量类 ──
        rsi = v('rsi_bfq_6', 50)
        if rsi < 20:    _add(3, f'RSI={rsi:.0f}极度超卖')
        elif rsi < 30:  _add(3, f'RSI={rsi:.0f}超卖')
        elif rsi < 40:  _add(2, f'RSI={rsi:.0f}偏低')
        elif rsi < 50:  _add(1, f'RSI={rsi:.0f}中性偏弱')
        
        kdj_j = v('kdj_bfq', 50)
        if kdj_j < -20:  _add(3, f'KDJ-J={kdj_j:.0f}极度超卖')
        elif kdj_j < 0:  _add(3, f'KDJ-J={kdj_j:.0f}超卖')
        elif kdj_j < 20: _add(2, f'KDJ-J={kdj_j:.0f}偏低')
        
        cci = v('cci_bfq', 0)
        if cci < -200:  _add(3, f'CCI={cci:.0f}极度超卖')
        elif cci < -100: _add(2, f'CCI={cci:.0f}超卖')
        
        wr = v('wr_bfq', 50)
        if wr > 90:  _add(3, f'WR={wr:.0f}极度超卖')
        elif wr > 80: _add(2, f'WR={wr:.0f}超卖')
        
        # ── 资金类 ──
        mfi = v('mfi_bfq', 50)
        if mfi < 20:  _add(2, f'MFI={mfi:.0f}资金枯竭')
        elif mfi < 30: _add(1, f'MFI={mfi:.0f}资金偏弱')
        
        if prev_data is not None:
            obv_now = v('obv_bfq', 0)
            obv_prev = pv('obv_bfq', 0)
            if obv_now > obv_prev:
                _add(1, 'OBV上升')
        
        vol_ratio = v('volume_ratio', 1.0)
        if vol_ratio < 0.6:   _add(1, f'量比={vol_ratio:.2f}极度缩量')
        elif vol_ratio < 0.8: _add(1, f'量比={vol_ratio:.2f}缩量')
        
        if prev_data is not None:
            prev_vr = pv('volume_ratio', 1.0)
            if prev_vr < 0.8 and vol_ratio > 1.2:
                _add(2, f'缩量({prev_vr:.2f})→放量({vol_ratio:.2f})启动')
        
        # ── 趋势类 ──
        macd_dif_v = v('macd_dif_bfq', 0)
        macd_dea_v = v('macd_dea_bfq', 0)
        if macd_dif_v > macd_dea_v:
            _add(2, 'MACD金叉')
        
        pdi = v('dmi_pdi_bfq', 20)
        mdi = v('dmi_mdi_bfq', 20)
        adx = v('dmi_adx_bfq', 20)
        if pdi > mdi:
            _add(1, f'PDI({pdi:.0f})>MDI({mdi:.0f})多头')
        else:
            if mdi - pdi < 3:
                _add(1, f'PDI({pdi:.0f})≈MDI({mdi:.0f})即将交叉')
        if adx > 25:
            _add(1, f'ADX={adx:.0f}>25强趋势')
        
        close_price = v('close', 0)
        ma20 = v('ma_bfq_20', 0)
        ma60 = v('ma_bfq_60', 0)
        if close_price > ma20 and ma20 > 0:
            _add(1, 'MA20上方')
        if close_price > ma60 and ma60 > 0:
            _add(1, 'MA60上方')
        
        # ── 情绪类 ──
        bias1 = v('bias1_bfq', 0)
        bias2 = v('bias2_bfq', 0)
        if bias1 < -5:   _add(2, f'BIAS1={bias1:.1f}%极端超卖')
        elif bias1 < -3: _add(1, f'BIAS1={bias1:.1f}%超卖')
        if bias2 < -10:  _add(3, f'BIAS2={bias2:.1f}%极端超卖')
        elif bias2 < -7: _add(1, f'BIAS2={bias2:.1f}%超卖')
        
        psy = v('psy_bfq', 50)
        if psy < 25:  _add(2, f'PSY={psy:.0f}极度悲观')
        elif psy < 37: _add(1, f'PSY={psy:.0f}偏悲观')
        
        vr = v('vr_bfq', 100)
        if vr < 70:   _add(1, f'VR={vr:.0f}地量')
        
        resonance_score = total_score
        
        # ── 4. 底背离检测 ──
        divergence_pts = 0
        if len(df) >= 5:
            current_close = float(latest['close'])
            current_rsi = safe_val(latest, 'rsi_bfq_6', 50)
            current_mfi = safe_val(latest, 'mfi_bfq', 50)
            for lookback in range(1, min(15, len(df))):
                prev_check = df.iloc[-1 - lookback]
                prev_close_val = float(prev_check['close'])
                if prev_close_val > current_close:
                    prev_rsi = safe_val(prev_check, 'rsi_bfq_6', 50)
                    prev_mfi = safe_val(prev_check, 'mfi_bfq', 50)
                    if prev_rsi < current_rsi:
                        _add(3, f'RSI底背离: {lookback}天前价高RSI更低')
                        divergence_pts += 3
                        break
            for lookback in range(1, min(15, len(df))):
                prev_check = df.iloc[-1 - lookback]
                prev_close_val = float(prev_check['close'])
                if prev_close_val > current_close:
                    prev_mfi = safe_val(prev_check, 'mfi_bfq', 50)
                    if prev_mfi < current_mfi:
                        _add(3, f'MFI底背离: {lookback}天前价高MFI更低')
                        divergence_pts += 3
                        break
        
        # ── 5. DMI上穿检测 ──
        dmi_pts = 0
        if len(df) >= 2:
            pdi_now_v = safe_val(latest, 'dmi_pdi_bfq', 0)
            mdi_now_v = safe_val(latest, 'dmi_mdi_bfq', 0)
            prev_row_check = df.iloc[-2]
            pdi_prev_v = safe_val(prev_row_check, 'dmi_pdi_bfq', 0)
            mdi_prev_v = safe_val(prev_row_check, 'dmi_mdi_bfq', 0)
            if pdi_prev_v <= mdi_prev_v and pdi_now_v > mdi_now_v:
                _add(3, f'PDI({pdi_prev_v:.0f}→{pdi_now_v:.0f})上穿MDI({mdi_prev_v:.0f}→{mdi_now_v:.0f})')
                dmi_pts += 3
        
        total_final = total_score
        
        # ── 6. 价格计算：入场价/止损价/目标价 ──
        entry_price = close_price
        stop_loss = entry_price
        stop_pct = 0.0
        target = entry_price
        
        if entry_price > 0:
            atr_val = v('atr_bfq', 0)
            if atr_val > 0:
                stop_distance = 2 * atr_val
                sp = stop_distance / entry_price
                # 强势横盘: 2%~8%; 深度回调: 3%~12%; 其他: 2%~10%
                if pattern == '深度回调':
                    sp = max(0.03, min(0.12, sp))
                elif pattern in ('强势横盘',):
                    sp = max(0.02, min(0.08, sp))
                else:
                    sp = max(0.02, min(0.10, sp))
                stop_loss = round(entry_price * (1 - sp), 2)
                stop_pct = round(sp * 100, 1)
            else:
                # 无ATR数据时使用固定止损
                if pattern == '深度回调':
                    stop_loss = round(entry_price * 0.88, 2)
                    stop_pct = 12.0
                elif pattern in ('强势横盘',):
                    stop_loss = round(entry_price * 0.92, 2)
                    stop_pct = 8.0
                else:
                    stop_loss = round(entry_price * 0.90, 2)
                    stop_pct = 10.0
            
            # 目标价：强势横盘+30%, 深度回调+25%, 其他+20%
            if pattern == '强势横盘':
                target = round(entry_price * 1.30, 2)
            elif pattern == '深度回调':
                target = round(entry_price * 1.25, 2)
            else:
                target = round(entry_price * 1.20, 2)
        
        # ── 7. 组合最终评分 ──
        # 仅使用共振评分，无形态加分
        wave2_score = total_final
        
        result["wave2_score"] = int(wave2_score)
        result["pattern_score"] = 0
        result["resonance_score"] = int(total_final)
        result["pattern"] = pattern
        result["score_details"] = "; ".join(details) if details else ""
        result["is_perfect_signal"] = wave2_score >= 18
        result["entry_price"] = entry_price
        result["stop_loss"] = stop_loss
        result["target"] = target

        if wave2_score >= 18:
            result["signal"] = "完美二波反转！可潜伏买入"
        elif wave2_score >= 12:
            result["signal"] = "二波形态初现，继续跟踪"
        elif wave2_score >= 7:
            result["signal"] = "疑似二波结构，等待确认"
        else:
            result["signal"] = "非二波形态"
        
    except Exception:
        pass
    
    return result


def classify_wave2_pattern(df, surge_end_idx, recent_end_idx):
    """
    根据wave1高点之后的调整形态分类
    df: 日线DataFrame（需包含close, high, low, vol, macd_dif, macd_dea, rsi_6）
    surge_end_idx: wave1高点位置（index）
    recent_end_idx: 最近分析日位置（index）
    返回: (pattern_name, pattern_data_dict)
    
    形态类型：
    - 强势横盘：回调<10%，调整天数<=15天
    - V型急跌：10天内急跌>10%
    - 放量回调：回调10-20%，量能比>0.8
    - 缩量回调：回调10-20%，量能比<=0.8
    - 三角收敛：振幅逐周递减
    - 深度回调：回调>=20%
    """
    import numpy as np
    
    n = len(df)
    if surge_end_idx >= recent_end_idx:
        return None, {}

    post = df.iloc[surge_end_idx:recent_end_idx+1].copy()
    if len(post) < 3:
        return '其他', {}

    closes = post['close'].values
    highs = post['high'].values
    lows = post['low'].values
    vols = post['vol'].values

    wave1_high = closes[0]
    pullback_max = (wave1_high - closes.min()) / wave1_high
    pullback_days = len(post) - 1

    # 基准：wave1前20日均量
    if surge_end_idx >= 20:
        base_vol = df.iloc[surge_end_idx-20:surge_end_idx]['vol'].mean()
    else:
        base_vol = vols.mean()
    vol_ratio = vols.mean() / base_vol if base_vol > 0 else 1.0

    # MA位置
    ma5 = df.iloc[recent_end_idx:recent_end_idx+1]['ma5'].values[0] if 'ma5' in df.columns else np.nan
    ma10 = df.iloc[recent_end_idx:recent_end_idx+1]['ma10'].values[0] if 'ma10' in df.columns else np.nan
    ma20 = df.iloc[recent_end_idx:recent_end_idx+1]['ma20'].values[0] if 'ma20' in df.columns else np.nan
    ma60 = df.iloc[recent_end_idx:recent_end_idx+1]['ma60'].values[0] if 'ma60' in df.columns else np.nan
    current_price = closes[-1]

    above_ma5 = current_price > ma5 if not np.isnan(ma5) else False
    above_ma10 = current_price > ma10 if not np.isnan(ma10) else False
    above_ma20 = current_price > ma20 if not np.isnan(ma20) else False
    above_ma60 = current_price > ma60 if not np.isnan(ma60) else False

    # V型：10天内急跌>10%
    v_crash = False
    if len(post) <= 10 and pullback_max > 0.10:
        v_crash = True

    # 三角收敛：振幅逐周递减
    triangle = False
    if len(post) >= 10:
        weekly_ranges = []
        for w in range(0, len(post)-2, 5):
            chunk = post.iloc[w:min(w+5, len(post))]
            if len(chunk) >= 3:
                weekly_ranges.append(chunk['high'].max() - chunk['low'].min())
        if len(weekly_ranges) >= 2:
            if weekly_ranges[-1] < weekly_ranges[-2] * 0.85:
                triangle = True

    # MACD金叉：当前DIF>DEA
    macd_dif = df.iloc[recent_end_idx:recent_end_idx+1]['macd_dif'].values[0] if 'macd_dif' in df.columns else np.nan
    macd_dea = df.iloc[recent_end_idx:recent_end_idx+1]['macd_dea'].values[0] if 'macd_dea' in df.columns else np.nan
    macd_crossed = (macd_dif > macd_dea) if (not np.isnan(macd_dif) and not np.isnan(macd_dea)) else False

    # RSI
    rsi_now = df.iloc[recent_end_idx:recent_end_idx+1]['rsi_6'].values[0] if 'rsi_6' in df.columns else 50.0
    rsi_now = rsi_now if not np.isnan(rsi_now) else 50.0

    # 量能比（近5日均量/基准量）
    vol_ratio_5d = vols[-5:].mean() / base_vol if base_vol > 0 else 1.0

    # 分类逻辑
    if v_crash and pullback_days <= 10:
        pattern = 'V型急跌'
    elif pullback_max < 0.10 and pullback_days <= 15:
        pattern = '强势横盘'
    elif pullback_max >= 0.10 and pullback_max < 0.20 and vol_ratio > 0.80:
        pattern = '放量回调'
    elif pullback_max >= 0.10 and pullback_max < 0.20 and vol_ratio <= 0.80:
        pattern = '缩量回调'
    elif pullback_max >= 0.20 and triangle:
        pattern = '三角收敛'
    elif pullback_max >= 0.20:
        pattern = '深度回调'
    elif triangle:
        pattern = '三角收敛'
    else:
        pattern = '其他'

    return pattern, {
        'pullback_max': pullback_max,
        'pullback_days': pullback_days,
        'vol_ratio': vol_ratio_5d,
        'above_ma5': above_ma5,
        'above_ma10': above_ma10,
        'above_ma20': above_ma20,
        'above_ma60': above_ma60,
        'macd_crossed': macd_crossed,
        'macd_dif': macd_dif,
        'macd_dea': macd_dea,
        'rsi_now': rsi_now,
        'wave1_high': wave1_high,
        'current_price': current_price,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
    }


def detect_wave2_pattern(ts_code, pro, trade_date=None, surge_days=20, surge_min=0.20):
    """
    检测股票是否符合二波形态
    参数:
        ts_code: 股票代码
        pro: Tushare pro 实例
        trade_date: 指定日期（None表示最新）
        surge_days: 一波拉升窗口
        surge_min: 一波最低涨幅
    返回:
        {
            'ts_code': str,
            'pattern': str,          # 形态类型
            'has_valid_pattern': bool, # 是否符合有效二波形态
            'wave1_gain': float,     # 一波涨幅
            'pullback': float,       # 当前回调幅度
            'pullback_days': int,    # 回调天数
            'rsi_now': float,        # 当前RSI
            'vol_ratio': float,      # 量能比
            'above_ma20': bool,      # 是否在MA20上方
            'above_ma60': bool,      # 是否在MA60上方
            'macd_crossed': bool,    # MACD是否金叉
            'base_score': int,       # 基础评分（基于形态成功率）
        }
    """
    import datetime
    import time
    
    result = {
        'ts_code': ts_code,
        'pattern': '其他',
        'has_valid_pattern': False,
        'wave1_gain': 0,
        'pullback': 0,
        'pullback_days': 0,
        'rsi_now': 50,
        'vol_ratio': 1.0,
        'above_ma20': False,
        'above_ma60': False,
        'macd_crossed': False,
        'base_score': 0,
    }
    
    try:
        end_date = trade_date or TRADE_DATE
        lookback = 90
        start_date = (datetime.datetime.strptime(end_date, '%Y%m%d') - datetime.timedelta(days=lookback+30)).strftime('%Y%m%d')

        # 日线（优先用缓存）
        daily = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if daily is None or len(daily) < 40:
            return result
        daily = daily.sort_values('trade_date').reset_index(drop=True)

        # 技术因子（使用 stk_factor_pro，MA/RSI 等已计算好）
        factor = pro.stk_factor_pro(ts_code=ts_code, start_date=start_date, end_date=end_date)
        time.sleep(0.06)

        # 合并（stk_factor_pro 字段带 _bfq 后缀，重命名为简洁名）
        df = daily.copy()
        if factor is not None and len(factor) > 0:
            factor_rename = {
                'ma_bfq_5': 'ma5', 'ma_bfq_10': 'ma10', 'ma_bfq_20': 'ma20', 'ma_bfq_60': 'ma60',
                'macd_bfq': 'macd', 'macd_dif_bfq': 'macd_dif', 'macd_dea_bfq': 'macd_dea',
                'rsi_bfq_6': 'rsi_6', 'rsi_bfq_12': 'rsi_12', 'rsi_bfq_24': 'rsi_24',
                'kdj_k_bfq': 'kdj_k', 'kdj_d_bfq': 'kdj_d', 'kdj_bfq': 'kdj_j',
                'boll_upper_bfq': 'boll_upper', 'boll_mid_bfq': 'boll_mid', 'boll_lower_bfq': 'boll_lower',
                'cci_bfq': 'cci',
            }
            # 只取factor表中实际存在的列，避免缺失列名导致KeyError
            valid_cols = ['trade_date'] + [k for k in factor_rename if k in factor.columns]
            valid_rename = {k: v for k, v in factor_rename.items() if k in factor.columns}
            factor_subset = factor[valid_cols].rename(columns=valid_rename)
            df = df.merge(factor_subset, on='trade_date', how='left')

        df = df[df['vol'] > 0].reset_index(drop=True)
        if len(df) < 40:
            return result

        # MA 已从 stk_factor_pro 获取，无需手动 rolling

        ADJUST_MAX = 60
        PULLBACK_MIN = 0.05
        
        # 找最近一波20%+拉升
        for end_idx in range(len(df) - 1, ADJUST_MAX, -1):
            window_start = end_idx - surge_days
            if window_start < 0:
                break

            window_closes = df.iloc[window_start:end_idx+1]['close'].values
            low_idx_in_window = df.iloc[window_start:end_idx+1]['close'].idxmin() - window_start
            high_idx_in_window = df.iloc[window_start:end_idx+1]['close'].idxmax() - window_start

            if high_idx_in_window <= low_idx_in_window:
                continue
            if (high_idx_in_window - low_idx_in_window) > surge_days - 2:
                continue

            wave1_gain = (window_closes[high_idx_in_window] - window_closes[low_idx_in_window]) / window_closes[low_idx_in_window]
            if wave1_gain < surge_min:
                continue

            wave1_high_idx = window_start + high_idx_in_window
            post_df = df.iloc[wave1_high_idx:]
            if len(post_df) < 2:
                continue

            # 形态分类
            pattern, pdata = classify_wave2_pattern(df, wave1_high_idx, len(df)-1)
            if pattern is None or pattern == '其他':
                continue

            current_price = df.iloc[-1]['close']
            wave1_high_price = df.iloc[wave1_high_idx]['close']
            pullback = (wave1_high_price - current_price) / wave1_high_price

            if pullback < PULLBACK_MIN:
                continue

            # 基本评分（基于历史回测数据）
            success_rate = {
                '强势横盘': 0.986, 'V型急跌': 0.949, '放量回调': 0.904,
                '深度回调': 0.862, '其他': 0.856, '缩量回调': 0.776, '三角收敛': 0.758
            }.get(pattern, 0.5)

            result.update({
                'pattern': pattern,
                'has_valid_pattern': True,
                'wave1_gain': round(wave1_gain * 100, 1),
                'pullback': round(pullback * 100, 1),
                'pullback_days': pdata.get('pullback_days', 0),
                'rsi_now': round(pdata['rsi_now'], 1),
                'vol_ratio': round(pdata['vol_ratio'], 2),
                'above_ma20': pdata['above_ma20'],
                'above_ma60': pdata['above_ma60'],
                'macd_crossed': pdata['macd_crossed'],
                'base_score': int(success_rate * 100),
            })
            return result

    except Exception:
        pass
    
    return result


def north_moneyflow_score(ts_code, pro):
    """
    北向资金共振因子（0-20分）
    使用 hk_hold（沪深港通持股）和 moneyflow（个股资金流向）数据
    """

    score = 0
    detail = {}

    try:
        # =========================
        # 1. 大单资金净流入（短期，个股资金流向 proxy）
        # =========================
        flow = pro.moneyflow(ts_code=ts_code)

        if flow is not None and len(flow) > 0:
            recent = flow.head(5)

            # 大单+特大单净流入作为机构资金代理
            net_amount = recent["net_mf_amount"].sum()

            # 标准化评分
            if net_amount > 1e6:       # >100万
                s1 = 10
            elif net_amount > 5e5:      # >50万
                s1 = 7
            elif net_amount > 0:
                s1 = 4
            else:
                s1 = 0

            score += s1
            detail["moneyflow_score"] = s1
            detail["net_mf_amount_5d"] = round(net_amount, 2)

        # =========================
        # 2. 趋势一致性（持续净流入天数）
        # =========================
        if flow is not None and len(flow) > 0:
            last_5 = flow.head(5)
            positive_days = (last_5["net_mf_amount"] > 0).sum()

            s2 = positive_days / 5 * 5  # 0-5分
            score += s2
            detail["moneyflow_consistency"] = s2

        # =========================
        # 3. 北向持仓变化（结构性增持）
        # =========================
        hold = pro.hk_hold(ts_code=ts_code)

        if hold is not None and len(hold) > 1:
            latest = hold.iloc[0]
            prev = hold.iloc[1]

            delta = latest["vol"] - prev["vol"]

            if delta > 0:
                s3 = 5
            elif delta == 0:
                s3 = 2
            else:
                s3 = 0

            score += s3
            detail["north_position_change"] = s3

    except:
        detail["north_error"] = 0

    return min(score, 20), detail

def count_hot_list_appearances(ts_code, days=20):
    """统计个股在热榜前20天出现的次数
    
    Args:
        ts_code: 股票代码
        days: 统计天数，默认20天
    
    Returns:
        count: 出现次数
    """
    count = 0
    
    if not os.path.exists(DC_HOT_CACHE_DIR):
        return count
    
    # 获取最近days天的日期
    from datetime import datetime, timedelta
    
    for i in range(days):
        date = datetime.now() - timedelta(days=i)
        date_str = date.strftime('%Y%m%d')
        csv_path = os.path.join(DC_HOT_CACHE_DIR, f'dc_hot_{date_str}.csv')
        
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                if ts_code in df['ts_code'].values:
                    count += 1
            except Exception as e:
                pass
    
    return count


def get_hot_list_bonus(count):
    """根据热榜出现次数计算加分
    
    加分规则：
    - 出现1-3次：+1分
    - 出现4-6次：+3分
    - 出现7-10次：+5分
    - 出现11-15次：+8分
    - 出现16-20次：+12分
    """
    if count >= 16:
        return 12
    elif count >= 11:
        return 8
    elif count >= 7:
        return 5
    elif count >= 4:
        return 3
    elif count >= 1:
        return 1
    else:
        return 0


def get_hot_list_best_rank_bonus(ts_code, days=60):
    """获取股票在热榜中的最佳排名并返回加分
    
    加分规则：
    1. 排名加分（基于最佳排名）：
       - Top10: +12分
       - Top20: +10分
       - Top30: +8分
       - Top50: +6分
       - Top100: +4分
       - 未进Top100: +0分
    
    2. 出现次数加分（基于60天内上榜次数）：
       - 1次: +0分
       - 2-3次: +2分
       - 4-5次: +4分
       - 6-10次: +6分
       - 11次以上: +8分
    
    总加分 = 排名加分 + 出现次数加分
    
    Args:
        ts_code: 股票代码
        days: 统计天数，默认60天
    
    Returns:
        bonus: 总加分
        best_rank: 最佳排名（未进榜返回9999）
        appear_count: 出现次数
    """
    best_rank = 9999
    appear_count = 0
    
    if not os.path.exists(DC_HOT_CACHE_DIR):
        return 0, best_rank, appear_count
    
    from datetime import datetime, timedelta
    
    for i in range(days):
        date = datetime.now() - timedelta(days=i)
        date_str = date.strftime('%Y%m%d')
        csv_path = os.path.join(DC_HOT_CACHE_DIR, f'dc_hot_{date_str}.csv')
        
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                match = df[df['ts_code'] == ts_code]
                if not match.empty:
                    appear_count += 1
                    rank = match.iloc[0].get('hot_rank', match.iloc[0].get('rank', 9999))
                    if pd.notna(rank) and int(rank) < best_rank:
                        best_rank = int(rank)
            except Exception:
                pass
    
    # 1. 排名加分
    if best_rank <= 10:
        rank_bonus = 12
    elif best_rank <= 20:
        rank_bonus = 10
    elif best_rank <= 30:
        rank_bonus = 8
    elif best_rank <= 50:
        rank_bonus = 6
    elif best_rank <= 100:
        rank_bonus = 4
    else:
        rank_bonus = 0
    
    # 2. 出现次数加分
    if appear_count <= 1:
        count_bonus = 0
    elif appear_count <= 3:
        count_bonus = 2
    elif appear_count <= 5:
        count_bonus = 4
    elif appear_count <= 10:
        count_bonus = 6
    else:
        count_bonus = 8
    
    # 总加分
    bonus = rank_bonus + count_bonus
    
    return bonus, best_rank, appear_count


def load_theme_pattern_stocks():
    """读取主题选股结果
    
    基于主题状态区分中期趋势和短线主线：
    - 中期趋势：抱团主升、强趋势（状态稳定且持续）
    - 短线主线：启动、分歧转一致、主升（状态刚启动）
    """
    if not os.path.exists(THEME_STOCKS_CACHE):
        return [], ""
    
    try:
        df = pd.read_csv(THEME_STOCKS_CACHE, encoding='utf-8-sig')
        if df.empty:
            return [], ""
        
        # 基于主题状态区分中期趋势和短线主线
        # 中期趋势：状态稳定且持续（抱团主升、强趋势）
        # 短线主线：状态刚启动（启动、分歧转一致、主升）
        mid_term_states = {'抱团主升', '强趋势'}
        short_term_states = {'启动', '分歧转一致', '主升'}
        
        mid_term = df[df.get('theme_state', '').isin(mid_term_states)]
        short_term = df[df.get('theme_state', '').isin(short_term_states)]
        
        # 中期趋势：中军和补涨中军
        mid_term_zhongjun = mid_term[mid_term.get('buy_type', '') == '中军']
        mid_term_buzhang = mid_term[mid_term.get('buy_type', '') == '补涨中军']
        
        # 短线主线：中军和补涨中军
        short_term_zhongjun = short_term[short_term.get('buy_type', '') == '中军']
        short_term_buzhang = short_term[short_term.get('buy_type', '') == '补涨中军']
        
        # 生成文本格式
        lines = []
        lines.append("=" * 80)
        lines.append("主题个股池选股结果")
        lines.append("=" * 80)
        
        # 中期趋势主题
        if not mid_term.empty:
            lines.append("\n📈 中期趋势主题（60日趋势平均分TOP2）")
            lines.append("-" * 80)
            
            if not mid_term_zhongjun.empty:
                lines.append("🏆 中军（中线布局）")
                for _, row in mid_term_zhongjun.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    close_val = row.get('close', 0) or 0
                    pct_val = row.get('pct_chg', 0) or 0
                    turnover_val = row.get('turnover_rate', 0) or 0
                    theme_val = row.get('theme_name', '') or ''
                    lines.append(f"  {row['code']} {row['name']} | 主题:{theme_val} | "
                               f"现价:{close_val:.2f} | 涨跌:{pct_val:+.2f}% | "
                               f"换手:{turnover_val:.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
            
            if not mid_term_buzhang.empty:
                lines.append("📈 补涨中军（成交活跃+均线金叉）")
                for _, row in mid_term_buzhang.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    close_val = row.get('close', 0) or 0
                    pct_val = row.get('pct_chg', 0) or 0
                    turnover_val = row.get('turnover_rate', 0) or 0
                    theme_val = row.get('theme_name', '') or ''
                    lines.append(f"  {row['code']} {row['name']} | 主题:{theme_val} | "
                               f"现价:{close_val:.2f} | 涨跌:{pct_val:+.2f}% | "
                               f"换手:{turnover_val:.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
        
        # 短线主线主题
        if not short_term.empty:
            lines.append("\n⚡ 短线主线（当日最强主线TOP3）")
            lines.append("-" * 80)
            
            if not short_term_zhongjun.empty:
                lines.append("🏆 中军（短线跟随）")
                for _, row in short_term_zhongjun.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    close_val = row.get('close', 0) or 0
                    pct_val = row.get('pct_chg', 0) or 0
                    turnover_val = row.get('turnover_rate', 0) or 0
                    theme_val = row.get('theme_name', '') or ''
                    lines.append(f"  {row['code']} {row['name']} | 主题:{theme_val} | "
                               f"现价:{close_val:.2f} | 涨跌:{pct_val:+.2f}% | "
                               f"换手:{turnover_val:.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
            
            if not short_term_buzhang.empty:
                lines.append("📈 补涨中军（成交活跃+均线金叉）")
                for _, row in short_term_buzhang.iterrows():
                    mcap = f"{row.get('mcap', 0):.1f}亿" if pd.notna(row.get('mcap')) else "--"
                    close_val = row.get('close', 0) or 0
                    pct_val = row.get('pct_chg', 0) or 0
                    turnover_val = row.get('turnover_rate', 0) or 0
                    theme_val = row.get('theme_name', '') or ''
                    lines.append(f"  {row['code']} {row['name']} | 主题:{theme_val} | "
                               f"现价:{close_val:.2f} | 涨跌:{pct_val:+.2f}% | "
                               f"换手:{turnover_val:.2f}% | 市值:{mcap}")
                    lines.append(f"    推荐理由: {row.get('reason', '')}")
        
        lines.append("=" * 80)
        
        return df.to_dict('records'), "\n".join(lines)
    except Exception as e:
        print(f"读取主题个股池失败: {e}")
        return [], ""

# =========================
# Tushare
# =========================
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")

# 尝试安全设置 tushare token
pro = None
try:
    pro = ts.pro_api(TUSHARE_TOKEN)
except Exception as e:
    print(f"Token 设置失败: {e}")
    print("请正确配置 TUSHARE_TOKEN 后重新运行。")
    import sys
    sys.exit(1)

if pro is None:
    print("Tushare API 未初始化，请配置 Token 后重新运行。")
    import sys
    sys.exit(1)


if not os.path.exists(REPORT_DIR):
    os.makedirs(REPORT_DIR)

# =========================
# 全局换手率缓存（当日批量加载）
# =========================
TURNOVER_CACHE = {}  # {ts_code: turnover_rate}

def load_turnover_cache():
    """批量加载当日换手率到缓存（从daily_basic表）"""
    global TURNOVER_CACHE
    if pro is None:
        return
    cache_file = os.path.join(CACHE_DIR, f"turnover_rate_{TRADE_DATE}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            TURNOVER_CACHE = dict(zip(df['ts_code'], df['turnover_rate']))
            print(f"[缓存] 换手率已加载: {len(TURNOVER_CACHE)} 只")
            return
        except Exception:
            pass
    # 没有缓存则从API批量拉取
    try:
        df = pro.daily_basic(
            trade_date=TRADE_DATE,
            fields='ts_code,turnover_rate'
        )
        if df is not None and not df.empty:
            TURNOVER_CACHE = dict(zip(df['ts_code'], df['turnover_rate']))
            df.to_csv(cache_file, index=False)
            print(f"[缓存] 换手率已保存: {cache_file}")
    except Exception as e:
        print(f"[缓存] 换手率加载失败: {e}")

def get_cached_turnover(ts_code):
    """从缓存获取换手率（单位：%）"""
    return TURNOVER_CACHE.get(ts_code, 0.0)

# =========================
# 通达信目录（修改成你的）
# =========================
TDX_DIR = r"C:\new_tdx"

import pdfkit # type: ignore

WK_PATH = r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"

config = pdfkit.configuration(wkhtmltopdf=WK_PATH)

# =========================
# 最近交易日
# =========================
# =========================
# 获取最近交易日
# =========================

def get_last_trade_date():
    """获取最近的交易日"""

    now = datetime.now()

    # =========================
    # 9点前：视为上一自然日
    # =========================
    if now.hour < 15:

        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    # =========================
    # 如果没有 tushare，根据当前时间计算交易日
    # =========================
    if pro is None:
        # 简单处理：跳过周末
        from datetime import date
        d = date.today()
        if d.weekday() == 5:  # 周六
            d = d - timedelta(days=1)
        elif d.weekday() == 6:  # 周日
            d = d - timedelta(days=2)
        return d.strftime('%Y%m%d')

    # =========================
    # 获取交易日历
    # =========================
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=query_date
    )

    # 只保留开市日
    cal = cal[cal['is_open'] == 1]

    # 最近交易日
    last_trade_date = cal[
        cal['cal_date'] <= query_date
    ]['cal_date'].max()

    return str(last_trade_date)


def validate_trade_date(date_str):
    """验证日期是否为有效交易日，如果不是则返回最近的有效交易日"""
    if pro is None:
        return date_str
    
    try:
        # 获取交易日历
        cal = pro.trade_cal(
            exchange='',
            start_date=date_str,
            end_date=date_str
        )
        
        # 如果当天是交易日
        if not cal.empty and cal.iloc[0]['is_open'] == 1:
            return date_str
        
        # 如果不是交易日，找之前最近的交易日
        cal = pro.trade_cal(
            exchange='',
            start_date=(datetime.strptime(date_str, '%Y%m%d') - timedelta(days=30)).strftime('%Y%m%d'),
            end_date=date_str
        )
        cal = cal[cal['is_open'] == 1]
        last_valid = cal[cal['cal_date'] <= date_str]['cal_date'].max()
        if last_valid:
            print(f"[警告] {date_str} 不是交易日，使用最近交易日: {last_valid}")
            return str(last_valid)
        return date_str
    except Exception as e:
        print(f"[警告] 日期验证失败: {e}，使用原日期: {date_str}")
        return date_str


# 全局交易日变量
TRADE_DATE = get_last_trade_date()
#TRADE_DATE = "20260529" # for test

print("当前交易日1:", TRADE_DATE)

# 启动时加载换手率缓存
load_turnover_cache()

# =========================
# BARSLAST
# =========================
def barslast(series):
    """向量化版本：找到最近True的距离
    
    特殊规则：如果第1天是True，不更新last_true，后续False返回NaN
    """
    arr = series.values.astype(bool)
    n = len(arr)
    
    # 找到所有True的位置
    true_positions = np.where(arr)[0]
    
    if len(true_positions) == 0:
        return pd.Series([np.nan] * n, index=series.index)
    
    # 特殊处理：如果第1天是True，需要跳过它（不作为有效信号）
    start_idx = 0
    if arr[0]:
        # 第1天True，跳过它，从后续找有效True
        valid_positions = true_positions[true_positions > 0]
        if len(valid_positions) == 0:
            # 只有第1天是True，全部返回NaN
            return pd.Series([np.nan] * n, index=series.index)
        true_positions = valid_positions
        start_idx = 1  # 第1天返回NaN
    
    # 使用searchsorted快速定位每个位置最近的True
    indices = np.arange(n)
    idx = np.searchsorted(true_positions, indices, side='right') - 1
    
    # 计算距离
    result = np.where(idx >= 0, indices - true_positions[idx], np.nan)
    
    # 第1天符合条件返回NaN
    if start_idx == 1:
        result[0] = np.nan
    
    # True的位置返回0
    for pos in true_positions:
        result[pos] = 0
    
    return pd.Series(result, index=series.index)


def load_stock_dict():
    """使用tushare获取股票代码和名称映射"""
    global STOCK_DICT
    try:
        if pro is not None:
            df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')
            stock_dict = {}
            for _, row in df.iterrows():
                # 同时存储带后缀和不带后缀的代码
                stock_dict[str(row['symbol'])] = row['name']
                stock_dict[str(row['ts_code'])] = row['name']
            STOCK_DICT = stock_dict
            return stock_dict
    except Exception as e:
        print(f"[警告] tushare股票字典获取失败: {e}")
    
    # 兜底：使用内置基础字典
    STOCK_DICT = {
        '000001': '平安银行', '600000': '浦发银行', '000002': '万科A',
        '600519': '贵州茅台', '300750': '宁德时代', '000001.SZ': '平安银行',
        '600000.SH': '浦发银行', '000002.SZ': '万科A', '600519.SH': '贵州茅台',
        '300750.SZ': '宁德时代'
    }
    return STOCK_DICT

STOCK_DICT = load_stock_dict()

# =========================
# 股票名（简单版）
# =========================
def get_stock_name(code):

    return STOCK_DICT.get(code, code)



# ======================================================
# 获取全部股票
# ======================================================


# ======================================================
# 全市场daily缓存更新（机构级）
# ======================================================
# ======================================================
# 全市场daily缓存（机构级最终版）
# ======================================================
# =========================
# 缓存历史数据
# =========================
def get_hist_data(ts_code):

    cache_file = os.path.join(
        CACHE_DIR,
        f"{ts_code}.csv"
    )

    # =========================
    # 优先读取缓存
    # =========================
    df = None
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            if (df['trade_date'] == TRADE_DATE).any():
                df = df[df['trade_date'] <= TRADE_DATE].sort_values('trade_date')
            else:
                df = None  # 无最新日，重新拉取
        except Exception as e:
            print(f"{ts_code} 缓存读取失败: {e}")
            df = None

    # =========================
    # 缓存缺失 → 重新拉取日线
    # =========================
    if df is None or df.empty:
        try:
            df = pro.daily(ts_code=ts_code, start_date='20250101', end_date=TRADE_DATE)
            if df is None or df.empty:
                return None
            df['trade_date'] = df['trade_date'].astype(str)
            df = df.sort_values('trade_date')
            df.to_csv(cache_file, index=False)
            time.sleep(0.15)
        except Exception as e:
            print(f"{ts_code} 下载失败:", e)
            return None

    return df



# ======================================================
# 批量预取历史数据（解决高频API调用问题）
# ======================================================
def batch_prefetch_hist_data(codes, start_date='20250101'):
    """
    在主循环之前批量预取所有股票数据到本地缓存
    使用 tushare 批量接口 pro.daily(ts_code="code1,code2,...")
    之后 get_hist_data() 将全部命中本地缓存，不再调API
    
    注意：tushare 批量接口有单次返回行数上限（~5000行），
    因此批量下载后，会对每只股票单独回填完整历史数据（从 start_date 开始），
    确保缓存文件包含从 start_date 至今的全量数据。
    """
    if not codes:
        return
    
    # 检查有多少已经在缓存中有今日数据
    cached = []
    missing = []
    for ts_code in codes:
        cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
        if os.path.exists(cache_file):
            try:
                df = pd.read_csv(cache_file)
                df['trade_date'] = df['trade_date'].astype(str)
                if (df['trade_date'] == TRADE_DATE).any():
                    cached.append(ts_code)
                    continue
            except:
                pass
        missing.append(ts_code)
    
    print(f"  批量预取: {len(cached)} 已缓存, {len(missing)} 需下载")
    
    if not missing:
        return
    
    # 分批下载，每批最多20只（避免单次返回行数限制导致数据不全）
    batch_size = 20
    batch_downloaded = set()  # 记录批量下载成功的股票
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        try:
            # 拼接为逗号分隔的代码列表
            ts_list = ",".join(batch)
            df = pro.daily(
                ts_code=ts_list,
                start_date=start_date,
                end_date=TRADE_DATE
            )
            
            if df is not None and not df.empty:
                # 按股票代码分组，保存到各自的缓存文件
                for ts_code in batch:
                    stock_df = df[df['ts_code'] == ts_code]
                    if not stock_df.empty:
                        stock_df = stock_df.sort_values('trade_date')
                        cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
                        # 合并已有缓存（批次接口返回的数据量有限，不能覆盖已有缓存）
                        if os.path.exists(cache_file):
                            try:
                                old_df = pd.read_csv(cache_file)
                                old_df['trade_date'] = old_df['trade_date'].astype(str)
                                stock_df['trade_date'] = stock_df['trade_date'].astype(str)
                                # 合并新旧数据，按trade_date去重，保留旧数据中的非重复行
                                merged = pd.concat([old_df, stock_df], ignore_index=True)
                                merged = merged.drop_duplicates(subset=['trade_date', 'ts_code'], keep='last')
                                merged = merged.sort_values('trade_date').reset_index(drop=True)
                                merged.to_csv(cache_file, index=False)
                                batch_downloaded.add(ts_code)
                                continue
                            except Exception:
                                pass
                        stock_df.to_csv(cache_file, index=False)
                        batch_downloaded.add(ts_code)
                
                downloaded_count = df['ts_code'].nunique()
                print(f"  批次 {i//batch_size + 1}: 成功下载 {downloaded_count}/{len(batch)} 只")
            else:
                print(f"  批次 {i//batch_size + 1}: 下载返回空")
            
            # 防止频率限制
            time.sleep(0.15)
            
        except Exception as e:
            print(f"  批次 {i//batch_size + 1} 下载失败: {e}")
            # 单批失败则逐只重试
            for ts_code in batch:
                try:
                    single_df = pro.daily(
                        ts_code=ts_code,
                        start_date=start_date,
                        end_date=TRADE_DATE
                    )
                    if single_df is not None and not single_df.empty:
                        cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
                        single_df.to_csv(cache_file, index=False)
                        batch_downloaded.add(ts_code)
                    time.sleep(0.15)
                except:
                    pass
    
    # =============================================
    # 回填全量数据：批量接口有行数上限，返回的数据
    # 可能只有最近几十行，需要单独下载补全历史
    # =============================================
    print(f"  回填全量数据: 检查 {len(batch_downloaded)} 只批量下载的股票...")
    backfill_count = 0
    for ts_code in batch_downloaded:
        cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
        if not os.path.exists(cache_file):
            continue
        try:
            df_cache = pd.read_csv(cache_file)
            df_cache['trade_date'] = df_cache['trade_date'].astype(str)
            df_cache = df_cache.sort_values('trade_date')
            
            # 检查缓存是否包含从 start_date 开始的数据
            # 如果 start_date 不是交易日，找其后第一个交易日作为基准
            start_date_to_check = start_date
            if pro is not None:
                try:
                    cal = pro.trade_cal(exchange='', start_date=start_date, end_date=start_date)
                    if cal.empty or cal.iloc[0]['is_open'] != 1:
                        # start_date 不是交易日，找其后第一个交易日
                        end_cal = (datetime.strptime(start_date, '%Y%m%d') + timedelta(days=30)).strftime('%Y%m%d')
                        cal = pro.trade_cal(exchange='', start_date=start_date, end_date=end_cal)
                        cal = cal[cal['is_open'] == 1]
                        first_trade_after_start = cal[cal['cal_date'] >= start_date]['cal_date'].min()
                        if first_trade_after_start:
                            start_date_to_check = str(first_trade_after_start)
                except:
                    pass
            
            earliest_date = df_cache['trade_date'].iloc[0] if not df_cache.empty else TRADE_DATE
            if earliest_date <= start_date_to_check:
                continue  # 已有全量数据，跳过
            
            # 缺失历史数据，单独下载补全
            single_df = pro.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=TRADE_DATE
            )
            if single_df is not None and not single_df.empty:
                single_df = single_df.sort_values('trade_date')
                single_df['trade_date'] = single_df['trade_date'].astype(str)
                
                # 合并并去重
                merged = pd.concat([df_cache, single_df], ignore_index=True)
                merged = merged.drop_duplicates(subset=['trade_date', 'ts_code'], keep='last')
                merged = merged.sort_values('trade_date').reset_index(drop=True)
                merged.to_csv(cache_file, index=False)
                backfill_count += 1
            
            time.sleep(0.12)  # 频率控制
        except Exception as e:
            print(f"    回填失败 {ts_code}: {e}")
    
    if backfill_count > 0:
        print(f"  回填完成: {backfill_count} 只股票已补全历史数据至 {start_date}")
    


# =========================
# AI新闻情绪（缓存版）
# 每日只请求一次，新闻内容无变化则复用缓存
# =========================
def get_news_sentiment(
        code,
        name,
        theme="",
        theme_state=""
):
    """AI基本面+事件驱动分析（集成真实数据）
    
    参数：
        code: 股票代码
        name: 股票名称
        theme: 所属主题（已由 filter_by_top_themes 匹配）
        theme_state: 主题状态
    
    数据来源（一周内）：
    - report_rc: 机构研报
    - stk_surv: 券商调研
    - 网页新闻: 抓取财经新闻（东财/新浪/财联社等来源）
    - 本地缓存: 行情/K线数据
    - 主题状态（直接传入）
    - 热榜数据
    
    缓存逻辑：
    - 如果采集的新闻内容跟上一次相同，直接复用 AI 分析结果
    - 避免重复调用 AI 接口，节省费用
    """
    import hashlib
    
    # =========================
    # 前置判断：如果当日分析文件已存在，直接返回缓存结果
    # =========================
    cache_file = os.path.join(NEWS_CACHE_DIR, f"ai_analysis_{code}_{TRADE_DATE}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
                cached_response = cached.get("response", "")
            
            # 提取综合情绪强度评分
            found = re.search(r'综合情绪强度评分[^\d]{0,5}(\d{1,3})', cached_response)
            if found:
                cached_score = min(max(int(found.group(1)), 0), 100)
            else:
                lines = cached_response.strip().split('\n')
                cached_score = 50
                for line in reversed(lines):
                    nums = re.findall(r'\b(\d{1,3})\b', line.strip())
                    if nums:
                        cached_score = min(max(int(nums[-1]), 0), 100)
                        break
            
            print(f"  [AI情绪] 当日分析已存在，直接返回缓存分数: {cached_score}")
            return cached_score
        except Exception as e:
            print(f"  [AI情绪] 读取缓存失败，继续分析: {e}")
    
    # =========================
    # 采集一周内真实数据（新闻实时采集，不缓存）
    # =========================
    week_ago = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=30)).strftime('%Y%m%d')
    
    # 1. 研报数据 (report_rc) — 量化结构化数据，直接提取关键字段
    report_text = ""
    try:
        report_cache_key = f"report_rc_{code}_{week_ago}_{TRADE_DATE}.json"
        report_cache_file = os.path.join(TUSHARE_API_CACHE_DIR, report_cache_key)
        if os.path.exists(report_cache_file):
            with open(report_cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            df_report = pd.DataFrame(raw) if isinstance(raw, list) else pd.read_json(report_cache_file)
        else:
            df_report = pro.report_rc(
                ts_code=code,
                start_date=week_ago,
                end_date=TRADE_DATE,
                fields=[
                    "ts_code", "name", "report_date", "report_title", "report_type",
                    "classify", "org_name", "author_name", "quarter",
                    "op_rt", "op_pr", "tp", "np", "eps", "pe", "rd", "roe",
                    "ev_ebitda", "rating", "max_price", "min_price"
                ]
            )
            if df_report is not None and not df_report.empty:
                df_report.to_json(report_cache_file, force_ascii=False, orient="records", indent=2)
        
        if df_report is not None and not df_report.empty:
            items = []
            for _, r in df_report.iterrows():
                dt = r.get('report_date', '')
                title = r.get('report_title', r.get('report_title', ''))
                org = r.get('org_name', '')
                rating = r.get('rating', '')
                tp = r.get('tp', '')  # 目标价
                eps = r.get('eps', '')  # 每股收益
                pe = r.get('pe', '')  # 市盈率
                roe = r.get('roe', '')  # ROE
                np_val = r.get('np', '')  # 净利润
                
                # 构造输出行
                item_line = f"  [{dt}] {title} ({org})"
                if rating:
                    item_line += f" | 评级: {rating}"
                if tp:
                    item_line += f" | 目标价: {tp}元"
                items.append(item_line)
                
                # 关键指标行
                metrics = []
                if eps:
                    metrics.append(f"EPS={eps}")
                if pe:
                    metrics.append(f"PE={pe}")
                if roe:
                    metrics.append(f"ROE={roe}%")
                if np_val:
                    metrics.append(f"净利润={np_val}亿")
                if metrics:
                    items.append(f"    指标: {', '.join(metrics)}")
            
            if items:
                report_text = "\n".join(items[:20])
                print(f"  [机构研报] 获取到 {len(df_report)} 条")
    except Exception as e:
        report_text = f"  (获取失败: {e})"
    
    # 2. 调研数据 (stk_surv) — 带接口缓存
    surv_text = ""
    try:
        surv_cache_key = f"stk_surv_{code}_{week_ago}_{TRADE_DATE}.json"
        surv_cache_file = os.path.join(TUSHARE_API_CACHE_DIR, surv_cache_key)
        if os.path.exists(surv_cache_file):
            with open(surv_cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            df_surv = pd.DataFrame(raw) if isinstance(raw, list) else pd.read_json(surv_cache_file, dtype={"surv_date": str})
        else:
            df_surv = pro.stk_surv(
                ts_code=code,
                start_date=week_ago,
                end_date=TRADE_DATE
            )
            if df_surv is not None and not df_surv.empty:
                df_surv.to_json(surv_cache_file, force_ascii=False, orient="records", indent=2)
        if df_surv is not None and not df_surv.empty:
            items = []
            date_col = 'surv_date' if 'surv_date' in df_surv.columns else (
                'survey_date' if 'survey_date' in df_surv.columns else None)
            for _, r in df_surv.iterrows():
                dt = r[date_col] if date_col else str(r.get(list(r.keys())[0], ''))
                content = str(r.get('content', r.get('desc', '')))
                inst = r.get('institution', r.get('org_name', ''))
                items.append(f"  [{dt}] {inst} 调研")
                if len(content) > 200:
                    content = content[:200] + '...'
                items.append(f"    内容: {content}")
            if items:
                surv_text = "\n".join(items[:12])  # 最多6条调研
    except Exception as e:
        surv_text = f"  (获取失败: {e})"
    
    # 3. 行情数据（一周涨跌幅、成交量变化）
    price_text = ""
    try:
        cache_file_k = os.path.join(CACHE_DIR, f"{code}.csv")
        if os.path.exists(cache_file_k):
            df = pd.read_csv(cache_file_k)
            df['trade_date'] = df['trade_date'].astype(str)
            df = df[df['trade_date'] <= TRADE_DATE]
            df = df.sort_values('trade_date')
            if len(df) >= 5:
                recent = df.tail(5)
                chg_5d = ((recent['close'].iloc[-1] / recent['close'].iloc[0]) - 1) * 100
                avg_vol_5d = recent['vol'].mean()
                avg_vol_20d = df.tail(20)['vol'].mean() if len(df) >= 20 else avg_vol_5d
                vol_ratio = avg_vol_5d / avg_vol_20d if avg_vol_20d > 0 else 1.0
                price_text = (
                    f"  近5日涨跌幅: {chg_5d:+.2f}%\n"
                    f"  最新收盘: {recent['close'].iloc[-1]:.2f}\n"
                    f"  量比(5日/20日): {vol_ratio:.2f}\n"
                    f"  5日日均成交: {avg_vol_5d/10000:.0f}万\n"
                    f"  5日高-低: {recent['high'].max():.2f} - {recent['low'].min():.2f}"
                )
    except Exception as e:
        pass
    
    # 4. 主题归属及状态（直接使用传入参数）
    theme_text = ""
    if theme:
        theme_state_str = f" | 状态:{theme_state}" if theme_state else ""
        theme_text = f"  {theme}{theme_state_str}"
    
    # 5. 热榜数据
    hot_text = ""
    try:
        hot_rank_bonus, best_rank, hot_count = get_hot_list_best_rank_bonus(code, days=60)
        if best_rank <= 100:
            hot_text = f"  近60日最佳热榜排名: Top{best_rank} | 上榜次数: {hot_count}次"
    except Exception as e:
        pass

    # 6. 基本面信息
    basic_text = ""
    try:
        df_basic = pro.stock_basic(ts_code=code, fields='ts_code,name,industry,area,list_date')
        if df_basic is not None and not df_basic.empty:
            row = df_basic.iloc[0]
            basic_text = f"  行业: {row.get('industry', '')} | 地区: {row.get('area', '')} | 上市日期: {row.get('list_date', '')}"
    except Exception as e:
        pass

    # 7. 网页财经新闻（已移除 Bing 搜索）

    # —— newspaper3k 辅助抓取函数（自动识别正文+回退到 BeautifulSoup）
    def _extract_article_body(url, http_headers, timeout=10):
        """从新闻URL提取正文摘要。优先用 newspaper3k，失败则回退到 CSS 选择器。
        返回: (body_summary_str, publish_date_str) 或 ('', '')
        """
        body_text = ""
        publish_date = ""
        method_used = ""
        
        # ====== 方法1: newspaper3k（自动识别正文+元信息）
        try:
            from newspaper import Article
            article = Article(url, language='zh', headers=http_headers, request_timeout=timeout)
            article.download()
            article.parse()
            body_text = article.text.strip()
            if article.publish_date:
                publish_date = str(article.publish_date)[:10]
            method_used = "newspaper3k"
        except Exception:
            pass  # newspaper3k 失败，回退
        
        # ====== 方法2: 回退到 BeautifulSoup CSS 选择器
        if not body_text or len(body_text) < 30:
            try:
                import requests as _req
                from bs4 import BeautifulSoup as _soup
                _resp = _req.get(url, headers=http_headers, timeout=timeout)
                if _resp.status_code == 200:
                    _b = _soup(_resp.text, 'html.parser')
                    # 尝试多种正文容器
                    for _sel in ['#mp-editor', '.article-content', '.article-body', '#artibody',
                                 'div[class*="content"]', '.main-text', 'main', '.article']:
                        _e = _b.select_one(_sel)
                        if _e:
                            body_text = _e.get_text(' ', strip=True)
                            break
                    # 回退：取 <p> 段落拼接
                    if not body_text or len(body_text) < 30:
                        _ps = _b.find_all('p')
                        if _ps:
                            body_text = ' '.join([_p.get_text(strip=True) for _p in _ps[:10] if len(_p.get_text(strip=True)) > 15])
                    method_used = "bs4-fallback"
            except Exception:
                pass
        
        # 清理多余空白并截取前300字
        if body_text and len(body_text) > 30:
            body_text = re.sub(r'\s+', ' ', body_text).strip()
            _s = body_text[:300]
            if len(body_text) > 300:
                _s += "..."
            return _s, publish_date, method_used
        return "", "", ""

    # 9. 同花顺个股新闻（近一周，抓取标题+正文摘要）— newspaper3k + 回退
    ths_news_text = ""
    try:
        import requests as ths_requests
        from bs4 import BeautifulSoup as ths_soup

        # 清理股票代码（去掉后缀）
        clean_code = code.replace('.SH', '').replace('.SZ', '')
        url = f'https://stockpage.10jqka.com.cn/{clean_code}/'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        resp = ths_requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = ths_soup(resp.text, 'html.parser')

            # 查找新闻链接
            all_links = soup.find_all('a', href=True)
            news_items = []

            # 计算一周前的日期
            week_ago = datetime.now() - timedelta(days=7)

            for a in all_links:
                href = a.get('href', '')
                text = a.text.strip()

                # 筛选新闻链接
                if 'news.10jqka.com.cn' in href and text and len(text) > 10:
                    date_str = href.split('/')[-2] if len(href.split('/')) > 3 else None
                    is_related = (clean_code in text or name in text or
                                  (date_str and len(date_str) == 8 and
                                   datetime.strptime(date_str, '%Y%m%d') >= week_ago))
                    if is_related or len(news_items) < 5:
                        news_items.append((text[:80], href))

            seen = set()
            filtered_news = []
            for text, href in news_items:
                key = text[:30]
                if key not in seen:
                    seen.add(key)
                    filtered_news.append((text, href))
                if len(filtered_news) >= 8:
                    break

            # 用 newspaper3k+回退抓取前4条正文
            if filtered_news:
                lines = []
                max_fetch_body = min(4, len(filtered_news))
                for idx, (text, href) in enumerate(filtered_news):
                    line = f"  - {text}"
                    if idx < max_fetch_body:
                        summary, pub_date, method = _extract_article_body(href, headers)
                        if summary:
                            line += f"\n    摘要: {summary}"
                            if pub_date:
                                line += f"（发布时间: {pub_date}）"
                    lines.append(line)
                ths_news_text = "\n".join(lines)
                print(f"  [同花顺新闻] 获取到 {len(filtered_news)} 条，前{max_fetch_body}条已抓取正文")
    except Exception as e:
        print(f"  [同花顺新闻] 获取失败: {e}")
    
    # 10. 新浪财经个股新闻（抓取标题+正文摘要）
    sina_news_text = ""
    try:
        import requests as sina_requests
        from bs4 import BeautifulSoup as sina_soup
        
        # 确定交易所前缀（沪市SH，深市SZ，北交所BJ）
        raw_code = code.split('.')[0]
        if raw_code.startswith('688') or raw_code.startswith('8') or raw_code.startswith('4'):
            sina_prefix = 'sh'  # 科创板、北交所用沪市前缀
        elif raw_code.startswith('9'):
            sina_prefix = 'bj'  # 北交所
        elif '.SH' in code.upper():
            sina_prefix = 'sh'
        else:
            sina_prefix = 'sz'
        
        url = f'https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{sina_prefix}{raw_code}.phtml'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        resp = sina_requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = sina_soup(resp.text, 'html.parser')
            
            # 查找新闻链接
            all_links = soup.find_all('a')
            news_items = []  # (title, href)
            
            for a in all_links:
                text = a.text.strip()
                href = a.get('href', '')
                
                # 严格筛选：必须包含股票名称或代码，且不是导航链接
                if text and len(text) > 10 and 'vip.stock' not in href:
                    # 优先选择包含股票名称的新闻
                    if name in text or raw_code in text:
                        # 处理相对链接
                        full_href = href if href.startswith('http') else f"https:{href}" if href.startswith('//') else f"https://vip.stock.finance.sina.com.cn{href}"
                        news_items.append((text[:80], full_href))
            
            # 去重并限制数量（最多8条标题，前4条抓正文）
            seen = set()
            filtered_news = []
            for text, href in news_items:
                key = text[:30]
                if key not in seen:
                    seen.add(key)
                    filtered_news.append((text, href))
                if len(filtered_news) >= 8:
                    break
            
            # 用 newspaper3k+回退抓取前4条正文
            if filtered_news:
                lines = []
                max_fetch_body = min(4, len(filtered_news))
                for idx, (text, href) in enumerate(filtered_news):
                    line = f"  - {text}"
                    if idx < max_fetch_body:
                        summary, pub_date, method = _extract_article_body(href, headers)
                        if summary:
                            line += f"\n    摘要: {summary}"
                            if pub_date:
                                line += f"（发布时间: {pub_date}）"
                    lines.append(line)
                sina_news_text = "\n".join(lines)
                print(f"  [新浪财经新闻] 获取到 {len(filtered_news)} 条，前{max_fetch_body}条已抓取正文")
    except Exception as e:
        print(f"  [新浪财经新闻] 获取失败: {e}")

    # 11. Google新闻（需要代理，使用RSS的description摘要+可选正文抓取）
    google_news_text = ""
    try:
        if PROXY_ENABLED:
            from bs4 import BeautifulSoup as google_soup
            from datetime import timezone
            
            # 搜索关键词：股票名称
            keyword = urllib.parse.quote(name)
            url = f'https://news.google.com/rss/search?q={keyword}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
            headers = {'User-Agent': 'Mozilla/5.0'}
            
            resp = default_session.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                soup = google_soup(resp.text, 'xml')
                items = soup.find_all('item')
                
                # 计算一个月前的日期（北京时间）
                month_ago_beijing = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=30)
                
                # 过滤一个月内的新闻（保存标题+链接+描述）
                news_list = []  # [(title, link, description)]
                for item in items[:50]:
                    title = item.find('title')
                    pubdate = item.find('pubDate')
                    link = item.find('link')
                    desc = item.find('description')
                    if title and title.text:
                        title_text = title.text.strip()
                        # 检查标题是否包含股票名称或代码
                        if name in title_text or clean_code in title_text:
                            # 解析日期（GMT时间转为北京时间）
                            try:
                                from email.utils import parsedate_to_datetime
                                pub_time_gmt = parsedate_to_datetime(pubdate.text)
                                # 转为北京时间
                                pub_time_beijing = pub_time_gmt.astimezone(timezone(timedelta(hours=8)))
                                if pub_time_beijing >= month_ago_beijing:
                                    link_text = link.text.strip() if link and link.text else ''
                                    desc_text = ''
                                    if desc and desc.text:
                                        # 从 description 中提取纯文本（RSS可能有HTML片段）
                                        desc_soup = google_soup(desc.text, 'html.parser')
                                        desc_text = desc_soup.get_text(' ', strip=True)
                                        desc_text = re.sub(r'\s+', ' ', desc_text).strip()
                                    news_list.append((title_text[:80], link_text, desc_text))
                            except Exception:
                                # 如果日期解析失败，只要标题相关也加入
                                link_text = link.text.strip() if link and link.text else ''
                                desc_text = ''
                                if desc and desc.text:
                                    desc_soup = google_soup(desc.text, 'html.parser')
                                    desc_text = desc_soup.get_text(' ', strip=True)
                                    desc_text = re.sub(r'\s+', ' ', desc_text).strip()
                                news_list.append((title_text[:80], link_text, desc_text))
                
                if news_list:
                    # 去重
                    seen = set()
                    unique_news = []
                    for title, link, desc in news_list:
                        key = title[:30]
                        if key not in seen:
                            seen.add(key)
                            unique_news.append((title, link, desc))
                    
                    # 组装输出：最多8条标题，前4条用 newspaper3k+回退抓取详情页正文
                    lines = []
                    max_fetch_body = min(4, len(unique_news))
                    for idx, (title, link, desc) in enumerate(unique_news[:8]):
                        line = f"  - {title}"
                        if idx < max_fetch_body:
                            # 方法1: 用 newspaper3k 抓取详情页正文（优先）
                            body_summary = ""
                            pub_date = ""
                            try:
                                from newspaper import Article
                                # Google 新闻链接可能是跳转链接（news.google.com/...），需要走 default_session 的代理
                                article = Article(link, language='zh', headers=headers, request_timeout=15)
                                article.download()
                                article.parse()
                                body_text = article.text.strip()
                                if article.publish_date:
                                    pub_date = str(article.publish_date)[:10]
                                if len(body_text) > 30:
                                    body_text_clean = re.sub(r'\s+', ' ', body_text).strip()
                                    body_summary = body_text_clean[:300]
                                    if len(body_text_clean) > 300:
                                        body_summary += "..."
                            except Exception:
                                pass  # newspaper3k 失败，回退

                            # 方法2: 回退到 RSS description
                            if not body_summary:
                                if desc and len(desc) > 30:
                                    body_summary = desc[:300]
                                    if len(desc) > 300:
                                        body_summary += "..."

                            if body_summary:
                                line += f"\n    摘要: {body_summary}"
                                if pub_date:
                                    line += f"（发布时间: {pub_date}）"
                        lines.append(line)
                    google_news_text = "\n".join(lines)
                    print(f"  [Google新闻] 获取到 {len(unique_news)} 条，前{max_fetch_body}条已抓取详情页")
    except Exception as e:
        print(f"  [Google新闻] 获取失败: {e}")

    # =========================
    # 组装AI Prompt
    # =========================
    data_section = f"""【股票基本信息】
{basic_text or '  (暂无)'}

【热榜数据】
{hot_text or '  (近60日未上热榜)'}

【行情数据（近5日）】
{price_text or '  (暂无)'}

【主题归属与状态】
{theme_text or '  (暂无主题数据)'}

【同花顺个股新闻（近一周）】
{ths_news_text or '  (暂无相关新闻)'}

【新浪财经个股新闻】
{sina_news_text or '  (暂无相关新闻)'}

【Google新闻（国际视野，近一月）】
{google_news_text or '  (暂无相关新闻)'}

【机构研报（近一月）】
{report_text or '  (无)'}

【券商调研（近一月）】
{surv_text or '  (无)'}
"""

    prompt = f"""你是一个A股短线“消息面驱动提纯分析器”，你的任务不是总结信息，而是：
从所有新闻、公告、研报、舆情中，提炼出唯一最强上涨驱动因素
并判断该股票当前的：
热度强度
驱动来源
是否处于资金关注阶段
一、输入内容

你将收到结构化信息：

新闻列表
公告列表
研报摘要
舆情/热榜数据
行业信息（如有）
⚠️ 二、核心任务（非常重要）
 ② 提炼“一句话驱动逻辑”（必须极简）

要求：

不超过30字
必须是“因果结构”
不能复述新闻

示例：

“AI服务器需求爆发带动光模块放量”
“机构调研确认订单超预期，净利润预测增长100%，买入评级”
“板块情绪共振带动资金抢筹”
🔵 ③ 判断热度等级（0-100）

热度 = 市场关注 + 资金参与 + 信息密度

分级：
0-20：无关注
20-40：弱关注
40-60：中等热度
60-80：强热度
80-100：极强主线热度
🧠 三、强约束规则（非常关键）
❌ 禁止行为：
不要总结所有新闻
不要罗列信息
不要多驱动并列
不要写技术分析
不要写基本面分析
✅ 必须遵守：
1️⃣ 只输出“一个主驱动”
所有信息必须压缩为：
👉 一个最强解释变量
2️⃣ 必须做“去噪”
规则：
公告 < 研报 < 机构调研 < 资金行为 < 龙头带动
3️⃣ 必须做“资金优先判断”

如果出现：

龙虎榜
北向流入
主力净流入
板块联动
👉 优先级最高


请分析以下股票：

{name}（{code}）

以下是系统采集的真实数据（近一个月）：

{data_section}

【信息优先级指引】（按重要程度从高到低）
1. 【最高优先级】券商调研（近一月）：机构实地调研记录，重点关注参与机构级别（券商/基金/保险/私募/QFII等）、调研次数、调研问询内容，机构密集调研通常意味着强烈关注
2. 【高优先级】机构研报（近一月）：券商/机构发布的评级报告、目标价、核心逻辑，重点关注评级变化（首次覆盖/上调/下调）和目标价预期差
3. 【参考优先级】Google新闻、同花顺/新浪财经新闻：作为信息补全，验证其他渠道信息的真实性

【解读要求】
- 券商调研：重点关注有多少家机构参与、什么类型的机构（知名机构>普通机构）、调研地点（现场>电话）、调研问题涉及哪些核心业务方向
- 机构研报：重点关注研报评级（买入/增持/中性/减持）、核心推荐逻辑是否持续有效
- 资讯：作为信息验证来源，判断是否与调研/研报信息相互印证

请标注：
- 上述信息所涉及的产业/概念方向
- 【共振判断】如果个股信息与某主题形成共振（如半导体行业国产替代与半导体主题共振、新能源产业链景气度提升与小金属/固态电池主题共振），请特别标注 ⭐⭐⭐ "与XX主题共振"
- 【差异化判断】如果信息与其他同主题个股存在明显差异，请标注"主题内阿尔法差异"


请给出三个分数（0-100）：
- 短期影响强度（1-5天）
- 中期影响强度（5-20天）
- 预期差强度（核心指标）

其中：
0-20：无影响/噪音
20-40：轻微扰动
40-60：可交易级别催化
60-80：强催化（可能趋势启动）
80-100：极强催化（可能主升浪）

最后，请基于以上分析，给出一个综合情绪强度评分（0-100整数），其中：
90-100：极强利好，机构持续看多
70-89：明显利好
50-69：中性偏好
30-49：偏空
0-29：明显利空

【输出格式和内容要求】
第一行输出综合结论，包括以上所有分析结果，以及最终的综合情绪强度评分。
中间输出：
- 最强驱动源
- 一句话驱动逻辑
- 热度等级
- 是否处于资金关注阶段
最后一行必须是单独的数字，即综合情绪强度评分。
【对AI输出的强制要求】输出的文字要求最精简（【重要】不要输出上面的步骤提示词），只给以上要求的关键词结果即可，对于影响非常重大的新闻进行标题显示。
【重要约束】：
1. 不做任何技术面分析（不要分析价格、均线、成交量、突破等）
2. 不做独立的主题分析（不评价主题整体强弱，仅标注个股信息与主题的共振关系）
3. 所有分析必须严格基于提供的信息内容，不得编造信息或进行无依据的推断
"""

    # =========================
    # 检查缓存：新闻内容无变化则复用
    # =========================
    cache_file = os.path.join(NEWS_CACHE_DIR, f"ai_analysis_{code}_{TRADE_DATE}.json")
    content_hash = hashlib.md5(data_section.encode('utf-8')).hexdigest()
    
    # 读取缓存检查内容哈希
    cached_content_hash = None
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
                cached_content_hash = cached.get("content_hash")
        except Exception:
            pass
    
    # 如果内容哈希相同，直接返回缓存结果
    if cached_content_hash == content_hash:
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
                cached_response = cached.get("response", "")
            
            found = re.search(r'综合情绪强度评分[^\d]{0,5}(\d{1,3})', cached_response)
            if found:
                cached_score = min(max(int(found.group(1)), 0), 100)
            else:
                lines = cached_response.strip().split('\n')
                cached_score = 50
                for line in reversed(lines):
                    nums = re.findall(r'\b(\d{1,3})\b', line.strip())
                    if nums:
                        cached_score = min(max(int(nums[-1]), 0), 100)
                        break

            print(f"  [AI情绪] 内容无变化，复用缓存分数: {cached_score}")
            return cached_score
        except Exception:
            pass

    try:

        r = deepseek(prompt, use_flash=True)

        # =========================
        # 保存输入输出到缓存（便于复盘和问题排查）
        # =========================
        try:
            cache_data = {
                "code": code,
                "name": name,
                "trade_date": TRADE_DATE,
                "theme": theme,
                "content_hash": content_hash,  # 保存内容哈希
                "prompt": prompt,
                "response": r,
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # =========================
        # 提取数字（优先搜索"综合情绪强度评分"关键词）
        # =========================
        found = re.search(r'综合情绪强度评分[^\d]{0,5}(\d{1,3})', r)
        if found:
            score = min(max(int(found.group(1)), 0), 100)
        else:
            lines = r.strip().split('\n')
            score = 50
            for line in reversed(lines):
                nums = re.findall(r'\b(\d{1,3})\b', line.strip())
                if nums:
                    score = min(max(int(nums[-1]), 0), 100)
                    break

        # 防止API过快
        time.sleep(0.5)

        return score

    except Exception as e:

        print(
            f"{code} AI情绪失败:",
            e
        )

        return 50



# =========================
# 批量AI情绪缓存
# =========================
def calc_trend_strength_v2(df):
    """
    更细腻的趋势强度评分（避免1.0被滥用）
    """
    C = df['close']

    ma10 = C.rolling(10).mean()
    ma20 = C.rolling(20).mean()
    ma30 = C.rolling(30).mean()
    ma60 = C.rolling(60).mean()

    score = 0

    # 1. 均线排列（更细化，避免全满分）
    if (ma10.iloc[-1] > ma20.iloc[-1] > ma30.iloc[-1] > ma60.iloc[-1]):
        score += 30  # 完美多头排列得30分
    elif (ma10.iloc[-1] > ma20.iloc[-1] and ma20.iloc[-1] > ma60.iloc[-1]):
        score += 20  # 次好排列得20分
    elif (ma10.iloc[-1] > ma60.iloc[-1]):
        score += 10  # 仅短期在长期上得10分

    # 2. 均线斜率（更细腻）
    ma20_slope = (ma20.iloc[-1] - ma20.iloc[-5]) / ma20.iloc[-5]
    if ma20_slope > 0.03:
        score += 20
    elif ma20_slope > 0.01:
        score += 10
    elif ma20_slope > 0:
        score += 5

    ma60_slope = (ma60.iloc[-1] - ma60.iloc[-10]) / ma60.iloc[-10]
    if ma60_slope > 0.02:
        score += 15
    elif ma60_slope > 0.01:
        score += 8
    elif ma60_slope > 0:
        score += 3

    # 3. 股价位置（更细化）
    price_ma60_ratio = C.iloc[-1] / ma60.iloc[-1]
    if price_ma60_ratio > 1.1:
        score += 15
    elif price_ma60_ratio > 1.05:
        score += 10
    elif price_ma60_ratio > 1.0:
        score += 5

    # 最大不超过85分（避免1.0被滥用）
    return min(score, 85) / 100
def calc_trend_slope(close, window=20):

    if len(close) < window:
        return 0

    y = close.tail(window).values
    x = np.arange(window)

    slope = np.polyfit(x, y, 1)[0]

    # 标准化（按价格尺度）
    mean_price = np.mean(y)
    if mean_price == 0:
        return 0

    return slope / mean_price * 100
def calc_trend_stability(close, window=20):

    if len(close) < window:
        return 0

    ret = close.pct_change().tail(window)

    # 越小越稳定
    vol = ret.std()

    if vol == 0:
        return 10

    trend = calc_trend_slope(close, window)

    # 稳定 = 趋势 / 波动
    return trend / (vol * 10 + 1e-6)

from scipy.stats import linregress
def calc_trend_stability2(close, window=20):

    y = close.tail(window).values

    x = np.arange(window)

    slope, intercept, r, p, stderr = linregress(x, y)

    return r * r


def calc_volume_structure(df):
    if len(df) < 30:
        return 0

    C = df['close']
    V = df['vol']

    vol_ratio = V.iloc[-1] / (V.iloc[:-1].tail(20).mean() + 1e-6) if len(V) > 20 else V.iloc[-1] / (V.mean() + 1e-6)

    price_trend = C.iloc[-1] / C.iloc[-20] - 1

    obv = (np.sign(C.diff()) * V).fillna(0).cumsum()
    obv_strength = obv.iloc[-1] / (abs(obv.tail(20).mean()) + 1e-6)

    # 归一化到 0-1
    vol_component = np.tanh(np.log1p(vol_ratio) * 0.3)
    obv_component = np.tanh(np.log1p(abs(obv_strength)) * 0.3)
    price_component = np.tanh(max(price_trend, 0) * 2)
    return vol_component * 0.4 + obv_component * 0.4 + price_component * 0.2


def calc_accumulation_factor(df):
    if len(df) < 40:
        return 0

    C = df['close']
    V = df['vol']

    # 抗跌结构
    price_hold = C.iloc[-10:].min() / C.iloc[-20:-10].max()

    # 缩量
    vol_shrink = V.tail(5).mean() / (V.tail(20).mean() + 1e-6)

    # 稳定抬升
    slope = calc_trend_slope(C, 20)

    score = 0

    if price_hold > 0.92:
        score += 50

    if vol_shrink < 0.8:
        score += 30

    if slope > 0:
        score += 20

    # 归一化到 0-1
    return score / 100.0
def calc_big_money_factor(df):
    if len(df) < 30:
        return 0

    C = df['close']
    V = df['vol']

    vol_ratio = V.iloc[-1] / (V.iloc[:-1].tail(20).mean() + 1e-6) if len(V) > 20 else V.iloc[-1] / (V.mean() + 1e-6)

    price_change = C.iloc[-1] / C.iloc[-2] - 1

    money_flow = (C.pct_change() * V).tail(5).sum()

    # 资金持续性（关键升级）
    flow_consistency = np.sum((C.pct_change().tail(5) > 0)) / 5

    # 归一化到 0-1
    vol_component = np.tanh(np.log1p(vol_ratio) * 0.3)
    price_component = np.tanh(max(price_change, 0) * 5)
    flow_component = np.tanh(np.log1p(abs(money_flow)) * 0.1)
    consistency_component = flow_consistency
    return vol_component * 0.3 + price_component * 0.3 + flow_component * 0.2 + consistency_component * 0.2    

def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def calc_dual_layer_score_v6(df, ts_code='', theme=''):
    """
    三路径概率系统（游资行为建模 v6）- 修复版
    1. 避免趋势强度1.0被滥用 ✅
    2. 失败概率分层惩罚机制 ✅ 
    3. 支持主题强度纳入（通过外部传入） ✅

    输出：
        P_up        上涨延续概率
        P_fail      突破失败概率
        P_squeeze   洗盘再启动概率
        edge_score  交易边际优势
    """

    C = df['close']
    H = df['high']
    L = df['low']
    VOL = df['vol']


    is_chuangchuang = ts_code.startswith('300') or ts_code.startswith('688')

    beta_multiplier = 1.0

    if ts_code.startswith('300'):   # 创业板（高弹性）
        beta_multiplier = 1.25

    elif ts_code.startswith('688'): # 科创板（更极端弹性）
        beta_multiplier = 1.30
    
    # =========================
    # 弹性结构（游资核心）
    # =========================
    HHV20 = H.iloc[:-1].tail(20).max() if len(H) > 1 else H.tail(20).max()
    LLV20 = L.tail(20).min()

    amp20 = (HHV20 - LLV20) / (LLV20 + 1e-6)

    if np.isnan(amp20) or np.isinf(amp20):
        compression_score = 0.5
    else:
        compression_score = (
            1.0 if amp20 <= 0.15 else
            0.8 if amp20 <= 0.25 else
            0.5 if amp20 <= 0.35 else
            0.2
        )
    volatility = (H.tail(20).max() - L.tail(20).min()) / C.iloc[-1]

    turnover_proxy = VOL.tail(5).mean() / (VOL.tail(20).mean() + 1e-6)

    elastic_score = sigmoid(
        volatility * 1.0 +
        turnover_proxy * 0.8 +
        (1 - compression_score) * 0.6
    )

    # =========================
    # 1. 基础趋势 & 资金结构
    # =========================
    trend_strength = calc_trend_strength_v2(df)
    trend_stability = calc_trend_stability2(C, 20)

    volume_structure = calc_volume_structure(df)
    accumulation = calc_accumulation_factor(df)
    big_money = calc_big_money_factor(df)

    money_momentum = (
        volume_structure * 0.5 +
        accumulation * 0.3 +
        big_money * 0.2
    )

    # =========================
    # 2. 突破结构（关键升级）
    # =========================
    HHV60 = H.iloc[:-1].rolling(60).max().iloc[-1] if len(H) > 1 else H.rolling(60).max().iloc[-1]
    breakout_position = np.clip(
        (C.iloc[-1] / HHV60 - 0.90) / 0.15,
        0, 1
    )

    MA20 = C.rolling(20).mean().iloc[-1]
    MA60 = C.rolling(60).mean().iloc[-1]

    # 价格效率（放量是否有效）
    price_efficiency = abs(C.iloc[-1] - C.iloc[-2]) / (VOL.iloc[-1] + 1e-6)
    price_efficiency = np.tanh(price_efficiency * 3)

    # =========================
    # 3. 压缩与爆发结构
    # =========================
    HHV20 = H.iloc[:-1].tail(20).max() if len(H) > 1 else H.tail(20).max()
    LLV20 = L.tail(20).min()
    amp20 = (HHV20 - LLV20) / LLV20

    compression_score = (
        1.0 if amp20 <= 0.15 else
        0.8 if amp20 <= 0.25 else
        0.5 if amp20 <= 0.35 else
        0.2
    )

    vol5 = VOL.tail(5).mean()
    vol20 = VOL.tail(20).mean()
    burst_score = np.clip(vol5 / (vol20 + 1e-6), 0, 3) / 3

    # =========================
    # 4. 趋势概率（核心）
    # =========================
    trend_prob = sigmoid(
        (trend_strength - 0.5) * 1.5 +
        (trend_stability - 0.5) * 1.0
    )

    # =========================
    # 5. 上涨推进概率（核心）
    # =========================
    break_strength = sigmoid(
        (breakout_position - 0.5) * 1.5 +
        (money_momentum - 0.5) * 1.0 +
        (price_efficiency - 0.5) * 0.8
    )

    P_up = (
        0.45 * trend_prob +
        0.35 * break_strength +
        0.20 * money_momentum
    )

    # =========================
    # 6. 失败概率（最关键风控）
    # =========================
    # 1. 高价风险因子 - 连续值而非二值
    price_ma60_ratio = C.iloc[-1] / MA60
    high_risk_zone = np.clip((price_ma60_ratio - 1.1) / 0.4, 0.0, 1.0)  # 1.1以下0，1.5以上1
    
    # 2. 阻力压力因子 - 连续值
    # 用amp20直接计算，而不是离散的compression_score
    resistance_pressure = np.clip((amp20 - 0.15) / 0.25, 0.0, 1.0)  # 0.15以下0，0.4以上1
    
    # 3. 派发风险因子 - 连续值
    vol_ratio = VOL.iloc[-1] / (VOL.tail(10).mean() + 1e-6)
    price_change = (C.iloc[-1] - C.iloc[-2]) / C.iloc[-2]
    # 放量下跌风险：量比越大且跌幅越大，风险越高
    distribution_risk = 0.0
    if price_change < 0:  # 下跌
        distribution_risk = np.clip((vol_ratio - 1.0) * abs(price_change) * 10, 0.0, 1.0)
    
    # 4. 额外维度：趋势稳定性下降风险
    ma20 = C.rolling(20).mean().iloc[-1]
    ma5 = C.rolling(5).mean().iloc[-1]
    trend_decline_risk = 0.0
    if ma5 < ma20:  # 5日均线跌破20日均线
        trend_decline_risk = np.clip((ma20 - ma5) / ma20 * 20, 0.0, 1.0)
    
    fail_prob = sigmoid(
        (resistance_pressure - 0.5) * 1.5 +
        (high_risk_zone - 0.5) * 1.2 +
        (distribution_risk - 0.5) * 1.5 +
        (trend_decline_risk - 0.5) * 0.8
    )

    # =========================
    # 7. 洗盘再启动概率（游资核心）
    # =========================
    squeeze_prob = sigmoid(
        (compression_score - 0.5) * 1.5 +
        (0.5 - burst_score) * 0.8 +
        (trend_stability - 0.5) * 0.6
    )

    # =========================
    # 8. 交易边际优势
    # =========================
    edge_score = P_up - fail_prob

    # =========================
    # 9. 风险等级（辅助）
    # =========================
    risk_ratio = C.iloc[-1] / MA60

    risk_level = (
        "极高" if risk_ratio > 1.6 else
        "高" if risk_ratio > 1.4 else
        "中" if risk_ratio > 1.2 else
        "低"
    )
    # =========================
    # 10. 总排序评分（用于选股优先级）
    # =========================

    trend_component = trend_prob
    momentum_component = break_strength
    money_component = money_momentum

    # =========================
    # 基础层（核心 alpha）
    # =========================
    base_score = (
        trend_component * 0.4 +
        momentum_component * 0.35 +
        money_component * 0.25
    )

    # =========================
    # 弹性层（游资核心）
    # =========================
    elastic_layer = beta_multiplier * (0.7 + 0.3 * elastic_score)

    # =========================
    # 风险层 - 分层惩罚机制 ✅
    # =========================
    if fail_prob < 0.2:
        # 低失败概率：几乎不惩罚
        risk_layer = 1.0
    elif fail_prob < 0.4:
        # 中低失败概率：轻微惩罚
        risk_layer = 0.9
    elif fail_prob < 0.5:
        # 中等失败概率：中等惩罚
        risk_layer = 0.75
    elif fail_prob < 0.6:
        # 中高失败概率：较重惩罚
        risk_layer = 0.55
    else:
        # 高失败概率：严厉惩罚
        risk_layer = 0.35

    # =========================
    # 主题强度加分（可选）✅
    # =========================
    theme_bonus = 1.0
    if theme:
        try:
            theme_data = das.read_theme_analysis(TRADE_DATE)
            if theme_data:
                for t in theme_data.get('themes', []):
                    if t.get('theme_name') == theme:
                        theme_score = t.get('trend_score', 0)
                        if theme_score >= 80:
                            theme_bonus = 1.3  # 热主题大加分
                        elif theme_score >= 60:
                            theme_bonus = 1.15  # 次热主题加分
                        elif theme_score >= 40:
                            theme_bonus = 1.0  # 不加分
                        else:
                            theme_bonus = 0.9  # 冷主题减分
                        break
        except Exception as e:
            pass

    # =========================
    # 最终评分
    # =========================
    final_rank_score = base_score

    final_rank_score *= elastic_layer
    final_rank_score *= risk_layer
    final_rank_score *= theme_bonus

    # 调整放大倍数，让评分更合理
    final_rank_score = np.clip(final_rank_score * 200, 0, None)
    
    # =========================
    # 输出
    # =========================
    return {
        "趋势概率": round(P_up, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(squeeze_prob, 4),
        "交易优势": round(edge_score, 4),

        "趋势强度": round(trend_strength, 3),
        "趋势稳定": round(trend_stability, 3),

        "资金动量": round(money_momentum, 3),
        "突破强度": round(break_strength, 3),

        "压缩度": round(compression_score, 3),
        "量能爆发": round(burst_score, 3),

        "风险等级": risk_level,
        "总排序评分": round(final_rank_score, 2)
    }


# =========================================================
# V7 评分系统 v6：主题纯度优化 + 主线共振加分
# =========================================================
def calc_dual_layer_score_v7(df, ts_code='', stock_info=None, theme=''):
    """
    V7评分系统 v6：主题纯度优化 + 主线共振加分
    """

    # =========================
    # 获取V6技术指标
    # =========================
    v6_result = calc_dual_layer_score_v6(df, ts_code, theme)

    # V6各指标
    trend_probability = v6_result.get('趋势概率', 0)  # 0-1
    fail_prob = v6_result.get('失败概率', 0)  # 0-1
    breakout_strength = v6_result.get('突破强度', 0)  # 0-1
    money_momentum = v6_result.get('资金动量', 0)  # 0-1
    trend_stability = v6_result.get('趋势稳定', 0)  # 0-1
    volume_explosion = v6_result.get('量能爆发', 0)  # 0-1
    compression_score = v6_result.get('压缩度', 0)  # 0-1
    squeeze_prob = v6_result.get('洗盘概率', 0)  # 0-1

    # =========================
    # 自动选择纯度最高的主题
    # =========================
    all_themes = ''
    if not theme and stock_info:
        theme = _find_best_theme(stock_info)
        all_themes = find_all_themes(stock_info)
    
    # 如果没有找到多主题，使用最佳主题
    if not all_themes:
        all_themes = theme or ''

    # =========================
    # 1. 主题真实性（防止蹭概念）
    # =========================
    theme_confidence = calc_theme_confidence(stock_info, theme) if theme else 30

    # =========================
    # 2. 主题强度 + 主线共振
    # =========================
    theme_strength_bonus = 1.0
    theme_rank_bonus = 0
    mainline_resonance = 0  # 新增：主线共振加分
    if theme:
        try:
            theme_data = das.read_theme_analysis(TRADE_DATE)
            if theme_data:
                for t in theme_data.get('themes', []):
                    if t.get('theme_name') == theme:
                        theme_score = t.get('trend_score', 0)
                        # 主题基础强度
                        if theme_score >= 80:
                            theme_strength_bonus = 1.2  # 降低从1.3降到1.2
                        elif theme_score >= 60:
                            theme_strength_bonus = 1.1
                        elif theme_score >= 40:
                            theme_strength_bonus = 1.0
                        else:
                            theme_strength_bonus = 0.95
                        
                        # 主线共振：如果趋势/情绪/成交量共振
                        trend = t.get('trend', 0)
                        sentiment = t.get('sentiment', 0)
                        vol_increase = t.get('volume_increase', 0)
                        if trend > 70 and sentiment > 70 and vol_increase > 0:
                            mainline_resonance = 5  # 三个共振加5分
                        elif trend > 60 or sentiment > 60:
                            mainline_resonance = 3  # 两个强共振加3分
                        
                        break
        except Exception as e:
            pass

    # =========================
    # 3. 动量维度捕捉启动股
    # =========================
    momentum_score = _calc_momentum_score(df)

    # =========================
    # 4. 压缩+洗盘统一建模
    # =========================
    squeeze_compression_score = _calc_squeeze_compression(df, compression_score, squeeze_prob)

    # =========================
    # 5. V6基础分（重新设计权重）
    # =========================
    # V6技术指标转为基础分（0-100）
    v6_base_score = (
        trend_probability * 30 +      # 趋势概率占30%
        breakout_strength * 25 +      # 突破强度占25%
        money_momentum * 20 +         # 资金动量占20%
        volume_explosion * 15 +       # 量能爆发占15%
        trend_stability * 10          # 趋势稳定占10%
    )

    # =========================
    # 6. 风险调整
    # =========================
    risk_adjustment = 0
    if fail_prob < 0.2:  # 极低失败率：加分
        risk_adjustment = 15
    elif fail_prob < 0.35:
        risk_adjustment = 10
    elif fail_prob < 0.45:
        risk_adjustment = 5
    elif fail_prob < 0.55:
        risk_adjustment = -3  # 从0改为稍微降3分
    elif fail_prob < 0.65:
        risk_adjustment = -10  # 从-8提高到-10
    else:
        risk_adjustment = -18  # 从-15提高到-18

    # =========================
    # 7. V7综合评分
    # =========================
    base_with_theme = v6_base_score * theme_strength_bonus
    momentum_bonus = momentum_score * 15
    squeeze_bonus = squeeze_compression_score * 10
    theme_purity_bonus = theme_confidence * 0.15  # 从10提高到15，注意这里是0.15（百分比系数）
    v7_total = (
        base_with_theme +
        momentum_bonus +
        squeeze_bonus +
        theme_purity_bonus +
        mainline_resonance +
        risk_adjustment
    )

    # 确保在0-100范围内
    v7_total = np.clip(v7_total, 0, 100)

    # =========================
    # 输出结果
    # =========================
    return {
        # V6技术指标
        "趋势概率": round(trend_probability, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(squeeze_prob, 4),
        "交易优势": round(v6_result.get('交易优势', 0), 4),
        "趋势强度": round(v6_result.get('趋势强度', 0), 3),
        "趋势稳定": round(trend_stability, 3),
        "资金动量": round(money_momentum, 3),
        "突破强度": round(breakout_strength, 3),
        "压缩度": round(compression_score, 3),
        "量能爆发": round(volume_explosion, 3),
        "风险等级": v6_result.get('风险等级', '低'),

        # V7新指标
        "所属主题": all_themes,
        "主题纯度": round(theme_confidence, 2),
        "主题强化系数": round(theme_strength_bonus, 2),
        "主线共振加分": round(mainline_resonance, 2),
        "动量得分": round(momentum_score, 2),
        "压缩洗盘得分": round(squeeze_compression_score, 2),
        "风险调整": round(risk_adjustment, 2),

        # V7总分
        "V7总评分": round(v7_total, 2)
    }


# =========================================================
# V7.5 综合评分系统
# =========================================================
def calc_dual_layer_score_v75(df, ts_code='', stock_info=None, theme=''):
    """
    V7.5综合评分系统 - 从V7函数引入所有指标再计算V7.5总分
    
    V7.5在V7基础上叠加：
    - 位置因子（position_factor）：120日高低位位置
    - 龙头因子（leader_factor）：趋势+资金+概率综合
    - 主题排名加成（theme_rank_bonus）：核心公司额外加分
    
    从V7引入的指标：
    - 基础技术指标：趋势概率、突破强度、资金动量等
    - V7复合指标：动量得分、压缩洗盘得分、主线共振加分、主题强化系数
    """

    # =========================
    # 从V7获取所有指标
    # =========================
    v7_result = calc_dual_layer_score_v7(df, ts_code=ts_code, stock_info=stock_info, theme=theme)
    
    # 计算涨跌幅（用于返回给AI报告）
    C = df['close']
    if len(C) >= 2:
        today_pct = ((C.iloc[-1] / C.iloc[-2]) - 1) * 100
    else:
        today_pct = 0.0

    # V7基础技术指标（0-1范围）
    trend_probability = float(v7_result.get('趋势概率', 0.5))
    fail_prob = float(v7_result.get('失败概率', 0.5))
    breakout_strength = float(v7_result.get('突破强度', 0.5))
    money_momentum = float(v7_result.get('资金动量', 0.5))
    trend_stability = float(v7_result.get('趋势稳定', 0.5))
    volume_explosion = float(v7_result.get('量能爆发', 0.5))
    compression_score = float(v7_result.get('压缩度', 0.5))
    trend_strength = float(v7_result.get('趋势强度', 0.5))

    # V7复合指标
    theme_confidence = float(v7_result.get('主题纯度', 30))
    theme_strength_bonus = float(v7_result.get('主题强化系数', 1.0))
    mainline_resonance = float(v7_result.get('主线共振加分', 0))
    momentum_score = float(v7_result.get('动量得分', 0))
    squeeze_compression_score = float(v7_result.get('压缩洗盘得分', 0))
    theme = v7_result.get('所属主题', theme)

    # =========================
    # V7.5独有：位置因子
    # =========================
    position_factor = calc_position_factor(df)

    # =========================
    # V7.5独有：龙头因子
    # =========================
    leader_factor = (
        trend_strength * 0.40 +
        money_momentum * 0.35 +
        trend_probability * 0.25
    )

    # =========================
    # V7.5独有：主题排名加成
    # =========================
    theme_rank_bonus = 0
    if stock_info and theme:
        try:
            cfg_path = os.path.join(BASE_DIR, 'theme.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    theme_cfg = json.load(f).get('HOT_THEMES', {})
                if theme in theme_cfg:
                    core_companies = theme_cfg[theme].get('core_companies', [])
                    if ts_code in core_companies:
                        theme_rank_bonus = 15  # 核心公司额外15分
        except:
            pass

    # ====================
    # V8.0 综合评分（优化版V3 — 聚焦二波启动模式）
    # ====================

    # 第一部分：基础线性评分（权重降低，让二波加分成为区分关键）
    base_score = (
        trend_strength * 18 +           # 趋势强度 — 核心因子
        trend_probability * 9 +
        money_momentum * 16 +           # 资金动量 — 重要
        breakout_strength * 6 +
        volume_explosion * 9 +
        trend_stability * 4 +
        compression_score * 7 +         # 压缩度（震荡调整的标志）
        momentum_score * 13 +           # 动量 — 体现二波启动
        squeeze_compression_score * 9 +  # 压缩洗盘 — 调整后启动前提
        mainline_resonance * 1 +
        position_factor * 6 +           
        leader_factor * 16              # 龙头因子
    )

    # 第二部分：因子交互共振项
    synergy_bonus = (
        compression_score * leader_factor * 7 +
        squeeze_compression_score * volume_explosion * 9 +
        leader_factor * max(theme_strength_bonus - 1, 0) * 15
    )

    # 第三部分：二波启动模式识别（用户偏好：拉升过→震荡调整→第二波刚启动）
    close_series = df['close']
    high_series = df['high']
    hhv60_s = float(high_series.iloc[:-1].tail(60).max()) if len(high_series) > 1 else float(high_series.tail(60).max())
    llv60_s = float(close_series.tail(60).min())
    hhv20_s = float(high_series.iloc[:-1].tail(20).max()) if len(high_series) > 1 else float(high_series.tail(20).max())
    llv20_s = float(close_series.tail(20).min())
    
    close_price = float(close_series.iloc[-1])
    first_wave_height = (hhv60_s - llv60_s) / max(llv60_s, 0.01)  # 60日第一波高度
    pullback_pct = 1 - close_price / max(hhv20_s, 0.01)           # 距20日高回撤
    pos60 = (close_price - llv60_s) / max(hhv60_s - llv60_s, 0.01)  # 60日位置
    
    # 判断是否为"第二波刚启动"（最多加25分）
    second_wave_bonus = 0
    # 条件1：有过明显的拉升（第一波15%+）
    if first_wave_height >= 0.15:
        second_wave_bonus += 6
        # 条件2：从高点回撤5-15%（震荡调整后蓄力）
        if pullback_pct >= 0.05 and pullback_pct <= 0.15:
            second_wave_bonus += 7
        # 条件3：60日位置在40-85%（非底非顶）
        if pos60 >= 0.40 and pos60 <= 0.85:
            second_wave_bonus += 6
        # 条件4：放量上涨3%+（启动迹象）
        if volume_explosion > 0.3 and today_pct > 3:
            second_wave_bonus += 6
    
    # 第四部分：非线性风险惩罚（适度，不压制高分）
    if fail_prob < 0.3:
        risk_penalty = fail_prob * 2
    elif fail_prob < 0.5:
        risk_penalty = 0.6 + (fail_prob - 0.3) * 6
    else:
        risk_penalty = 1.8 + (fail_prob - 0.5) * 10

    # 第五部分：主题置信度门控
    if theme_confidence < 30:
        confidence_gate = 0.85
    elif theme_confidence >= 70:
        confidence_gate = 1.05
    else:
        confidence_gate = 1.0

    # 汇总：直接加法
    v80_raw = (
        base_score 
        + synergy_bonus 
        + second_wave_bonus
        - risk_penalty
    )

    # 主题强化 + 置信度门控
    v75_total = v80_raw * confidence_gate

    # 确保范围
    v75_total = np.clip(v75_total, 0, 100)
    
    # =========================
    # 输出结果
    # =========================
    return {
        # V7技术指标
        "趋势概率": round(trend_probability, 4),
        "失败概率": round(fail_prob, 4),
        "洗盘概率": round(v7_result.get('洗盘概率', 0), 4),
        "交易优势": round(v7_result.get('交易优势', 0), 4),
        "趋势强度": round(trend_strength, 3),
        "趋势稳定": round(trend_stability, 3),
        "资金动量": round(money_momentum, 3),
        "突破强度": round(breakout_strength, 3),
        "压缩度": round(compression_score, 3),
        "量能爆发": round(volume_explosion, 3),
        "风险等级": v7_result.get('风险等级', '低'),

        # V7复合指标
        "所属主题": theme,
        "主题纯度": round(theme_confidence, 2),
        "主题强化系数": round(theme_strength_bonus, 2),
        "主线共振加分": round(mainline_resonance, 2),
        "动量得分": round(momentum_score, 2),
        "压缩洗盘得分": round(squeeze_compression_score, 2),
        
        # V7.5独有指标
        "龙头因子": round(leader_factor, 3),
        "主题排名加成": round(theme_rank_bonus, 2),
        "位置因子": round(position_factor, 3),
        "涨跌幅": round(today_pct, 2),  # 添加涨跌幅给AI报告

        # V7.5总分
        "V7总评分": round(v75_total, 2)
    }




        


def calc_tli_score(theme, top_n=10, days=60):
    """
    TLI (Theme Life Index) 主题生命力评分
    
    核心逻辑：衡量主题在最近N天内的持续活跃程度
    高生命力 = 主题持续出现在市场前排，资金关注度高
    
    计算方法：
    1. 查询最近60天主题排名数据
    2. 统计主题出现在前10名的次数
    3. 根据出现频率和平均排名打分
    
    参数：
        theme: 主题名称
        top_n: 前排定义（默认前10名）
        days: 统计天数（默认60天）
    
    返回：0-100 的主题生命力评分
    """
    try:
        if not theme:
            return 50, {"错误": "主题为空"}
        
        db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.db')
        if not os.path.exists(db_path):
            return 50, {"错误": "数据库不存在"}
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 查询最近60天内该主题的排名情况
        cursor.execute("""
            SELECT trade_date, rank, composite_score 
            FROM theme_scores 
            WHERE theme = ? 
            AND trade_date >= date('now', '-{} days')
            ORDER BY trade_date DESC
        """.format(days), (theme,))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return 50, {"说明": "无历史数据"}
        
        # 统计指标
        total_days = len(rows)
        top10_count = sum(1 for row in rows if row[1] <= top_n)
        top5_count = sum(1 for row in rows if row[1] <= 5)
        top3_count = sum(1 for row in rows if row[1] <= 3)
        
        avg_rank = sum(row[1] for row in rows) / total_days if total_days > 0 else 50
        avg_score = sum(row[2] for row in rows) / total_days if total_days > 0 else 0
        
        # 计算生命力评分 (0-100)
        # 基础分：前10出现频率
        base_score = (top10_count / total_days) * 60 if total_days > 0 else 0
        
        # 加分项：前5和前3次数
        bonus_top5 = (top5_count / total_days) * 20 if total_days > 0 else 0
        bonus_top3 = (top3_count / total_days) * 15 if total_days > 0 else 0
        
        # 平均排名修正：平均排名越靠前，加分越多
        rank_bonus = 0
        if avg_rank <= 3:
            rank_bonus = 5
        elif avg_rank <= 5:
            rank_bonus = 3
        elif avg_rank <= 10:
            rank_bonus = 1
        
        tli_score = base_score + bonus_top5 + bonus_top3 + rank_bonus
        tli_score = min(100, max(0, tli_score))
        
        details = {
            "统计天数": total_days,
            "前10次数": top10_count,
            "前5次数": top5_count,
            "前3次数": top3_count,
            "平均排名": round(avg_rank, 1),
            "平均综合分": round(avg_score, 1),
            "前10频率": f"{top10_count/total_days*100:.1f}%" if total_days > 0 else "0%"
        }
        
        return round(tli_score, 1), details
    
    except Exception as e:
        print(f"[TLI生命力] 计算失败: {e}")
        return 50, {"错误": str(e)}




def calc_sector_position_score(ts_code, stock_info, theme, v75_result, df):
    """sector_position 板块位置评分 V3 - 龙头拉开机制 + 板块分层系统（无连板因子）
    
    核心逻辑：
    1. 板块分层系统：S/A/B/C级主线分层基础分
    2. 龙头拉开机制：真龙头非线性加成，后排惩罚
    
    评分公式：
    final_score = 板块分层基础分 + 龙头加成
    
    参数设计：
    - S级（≥80分）：基础分50，真龙头+50=100，准龙头+30=80，后排-20=0
    - A级（60-80分）：基础分30，真龙头+50=80，准龙头+30=60，后排-20=0
    - B级（40-60分）：基础分15，真龙头+50=65，准龙头+30=45，后排-20=0
    - C级（<40分）：基础分0，真龙头+50=50，准龙头+30=30，后排-20=0
    
    效果：
    - S级龙头自然拉到100分
    - S级核心80分
    - 后排自动掉到0-30分
    """
    try:
        if not theme:
            return 50, {}
        
        # =========================
        # 1. 板块分层系统 - 获取主线等级
        # =========================
        theme_tier = 'C'  # 默认C级
        theme_composite_score = 50
        theme_data = None
        
        try:
            theme_data = das.read_theme_analysis(TRADE_DATE)
            if theme_data and theme_data.get('themes'):
                for t in theme_data['themes']:
                    if t.get('theme_name') == theme:
                        theme_composite_score = float(t.get('composite_score', 50))
                        # 分层：S≥80, A 60-80, B 40-60, C <40
                        if theme_composite_score >= 80:
                            theme_tier = 'S'
                        elif theme_composite_score >= 60:
                            theme_tier = 'A'
                        elif theme_composite_score >= 40:
                            theme_tier = 'B'
                        break
        except:
            pass
        
        # 板块分层基础分
        tier_base_score = {'S': 50, 'A': 30, 'B': 15, 'C': 0}.get(theme_tier, 0)
        
        # =========================
        # 2. 龙头识别 - 多维度判断
        # =========================
        is_leader = False
        is_core = False
        sector_rank = 10  # 默认后排

        
        # 2.1 从主题分析数据获取龙头标记和连板高度
        if theme_data and theme_data.get('themes'):
            for t in theme_data['themes']:
                if t.get('theme_name') == theme:
                    lc = t.get('leader_code', '')
                    cc = t.get('core_code', '')
                    
                    # 龙头代码匹配
                    if lc and str(ts_code) in str(lc):
                        is_leader = True
                        sector_rank = 1
                    elif cc and str(ts_code) in str(cc):
                        is_core = True
                        sector_rank = 2
                    
                    break
                    break
        
        # 2.2 从theme.json配置判断
        if sector_rank >= 10 and stock_info:
            try:
                cfg_path = os.path.join(BASE_DIR, 'theme.json')
                if os.path.exists(cfg_path):
                    with open(cfg_path, 'r', encoding='utf-8') as f:
                        tc = json.load(f).get('HOT_THEMES', {})
                    if theme in tc:
                        td = tc[theme]
                        sn = stock_info.get('name', '')
                        if sn in td.get('leader_companies', []):
                            is_leader = True
                            sector_rank = 1
                        elif sn in td.get('core_companies', []):
                            is_core = True
                            sector_rank = 2
            except:
                pass
        
        # 2.3 从V7指标推断（涨幅、成交额占比）
        if sector_rank >= 10 and v75_result and df is not None:
            try:
                # 今日涨幅
                if len(df) >= 2:
                    today_pct = (df['close'].iloc[-1] / df['close'].iloc[-2] - 1) * 100
                    
                    # 涨幅≥7% 且 量能爆发 → 可能是龙头
                    vol_explosion = float(v75_result.get('量能爆发', 0))
                    if today_pct >= 7 and vol_explosion >= 0.7:
                        is_leader = True
                        sector_rank = 1
                    elif today_pct >= 5 and vol_explosion >= 0.5:
                        is_core = True
                        sector_rank = 2
                
                # 主题纯度高 + 强化系数高
                tc = float(v75_result.get('主题纯度', 30))
                tsb = float(v75_result.get('主题强化系数', 1.0))
                if tc >= 70 and tsb >= 1.3:
                    if sector_rank > 2:
                        sector_rank = 2
                        is_core = True
                elif tc >= 50 and tsb >= 1.1:
                    if sector_rank > 3:
                        sector_rank = 3
            except:
                pass
        
        # 2.4 从V7.5综合评分推断（趋势强度+主题纯度+资金动量都高的视为核心）
        if sector_rank >= 5 and v75_result:
            try:
                trend_strength = float(v75_result.get('趋势强度', 0))
                theme_conf = float(v75_result.get('主题纯度', 30))
                money_momentum = float(v75_result.get('资金动量', 0))
                # 趋势强度>0.7 + 主题纯度>60 + 资金动量>0.6 → 准龙头
                if trend_strength >= 0.75 and theme_conf >= 60 and money_momentum >= 0.65:
                    is_core = True
                    sector_rank = min(sector_rank, 2)
                # 三个维度都很高 → 真龙头
                if trend_strength >= 0.85 and theme_conf >= 75 and money_momentum >= 0.75:
                    is_leader = True
                    sector_rank = 1
            except:
                pass
        
        # =========================
        # 3. 龙头拉开机制 - 非线性加成
        # =========================
        leader_bonus = 0
        if is_leader:
            leader_bonus = 50  # 真龙头 +50分
        elif is_core:
            leader_bonus = 30  # 准龙头 +30分
        elif sector_rank >= 10:
            leader_bonus = -20  # 后排 -20分惩罚
        

        # =========================
        # 5. 综合评分计算
        # =========================
        # 基础分：板块分层
        base_score = tier_base_score
        
        # 龙头加成
        final_score = base_score + leader_bonus
        
        # 限制在0-100范围
        final_score = min(100, max(0, final_score))
        
        # =========================
        # 6. 返回结果
        # =========================
        details = {
            '板块等级': theme_tier,
            '板块综合分': round(theme_composite_score, 1),
            '板块排名': sector_rank,
            '是否龙头': is_leader,
            '是否核心': is_core,
            '分层基础分': tier_base_score,
            '龙头加成': leader_bonus
        }
        
        return round(final_score, 1), details
        
    except Exception as e:
        print(f'[sector_position] 失败: {e}')
        import traceback
        traceback.print_exc()
        return 50, {}


def calc_capital_dominance_score(ts_code, stock_info, theme, v75_result, df):
    """capital_dominance 资金主导力评分 (0-100)
    
    量能集中度(40分) + 资金活跃度(35分) + 主力买入迹象(25分)
    """
    try:
        score = 0
        # 1. 量能集中度 (40分)
        vol_conc = 0
        if df is not None and len(df) >= 20:
            try:
                v = df['vol'].values
                c = df['close'].values
                amt_5d = (v[-5:] * c[-5:]).mean()
                amt_20d = (v * c).mean()
                ar = amt_5d / amt_20d if amt_20d > 0 else 1.0
                if ar >= 2.0: vol_conc = 40
                elif ar >= 1.5: vol_conc = 32
                elif ar >= 1.2: vol_conc = 24
                elif ar >= 1.0: vol_conc = 16
                else: vol_conc = 10
            except:
                pass
        score += vol_conc
        # 2. 资金活跃度 (35分)
        cap_act = 0
        if v75_result:
            try:
                ve = float(v75_result.get('量能爆发', 0))
                mm = float(v75_result.get('资金动量', 0))
                cap_act = min(35, round((ve * 0.5 + mm * 0.5) * 35))
            except:
                pass
        score += cap_act
        # 3. 主力买入迹象 (25分)
        buying = 0
        if df is not None and len(df) >= 2:
            try:
                cv = df['close'].values
                vv = df['vol'].values
                tp = (cv[-1] / cv[-2] - 1) * 100
                vr = vv[-1] / max(vv[-20:].mean(), 0.01)
                if tp >= 5 and vr >= 2.0: buying = 25
                elif tp >= 3 and vr >= 1.5: buying = 20
                elif tp >= 2 and vr >= 1.3: buying = 15
                elif tp >= 1 and vr >= 1.2: buying = 10
                elif tp > 0: buying = 5
                else: buying = 2
            except:
                pass
        score += buying
        score = min(100, max(0, score))
        return round(score, 1), {'量能集中度': vol_conc, '资金活跃度': cap_act, '主力买入': buying}
    except Exception as e:
        print(f'[capital_dominance] 失败: {e}')
        return 50, {}

def calc_unified_stock_score(df, ts_code='', theme='', theme_trend_score=0, theme_sentiment_score=0):
    """
    统一股票评分算法 - 整合V9和整合评分
    
    目标：找到次日介入后上涨概率高、失败概率低的股票
    
    核心公式：
    FinalScore = 趋势强度(30%) + 资金健康度(25%) + 位置安全性(15%) + 热度持续性(20%) + 基本面(10%)
    
    输出：
        - 综合评分 (0-100)
        - 失败概率 (10-90%)
        - 推荐理由
        - 各维度评分详情
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame) or len(df) < 20:
            return 0, "数据不足", {}, 50
        
        df = df.reset_index(drop=True)
        C = df['close'].values
        ts_code = ts_code or ''
        
        # =========================
        # 基础数据计算
        # =========================
        close_series = df['close']
        high_series = df['high']
        MA5 = float(close_series.rolling(5).mean().iloc[-1])
        MA10 = float(close_series.rolling(10).mean().iloc[-1])
        MA20 = float(close_series.rolling(20).mean().iloc[-1])
        MA60 = float(close_series.rolling(60).mean().iloc[-1])
        # 修复: HHV用最高价(非收盘价)，且排除当天数据避免"今天创新高则HHV=今天高点"的循环
        HHV20 = float(high_series.iloc[:-1].tail(20).max()) if len(high_series) > 1 else float(close_series.tail(20).max())
        HHV60 = float(high_series.iloc[:-1].tail(60).max()) if len(high_series) > 1 else float(close_series.tail(60).max())
        LLV20 = float(close_series.tail(20).min())
        current_price = float(C[-1])
        
        if len(C) >= 2:
            today_pct = float((C[-1] / C[-2] - 1) * 100)
        else:
            today_pct = 0
        
        # =========================
        # 1. 趋势强度评分（30%）- 互斥评分
        # =========================
        trend_score = 0
        
        # 均线斜率（趋势反转导向，不依赖均线排列）
        # MA20均线斜率（权重加大）
        if len(C) >= 25:
            ma20_slope = (MA20 - float(close_series.rolling(20).mean().iloc[-6])) / MA20
            if ma20_slope > 0.05:
                trend_score += 35  # 极强上升
            elif ma20_slope > 0.03:
                trend_score += 28  # 强上升
            elif ma20_slope > 0.015:
                trend_score += 20  # 温和上升
            elif ma20_slope > 0:
                trend_score += 10  # 缓慢上升
            elif ma20_slope > -0.015:
                trend_score -= 5   # 轻微下降
            else:
                trend_score -= 15  # 明显下降
        
        # MA10均线斜率（新增，权重加大）
        if len(C) >= 15:
            ma10_current = float(close_series.rolling(10).mean().iloc[-1])
            ma10_prev = float(close_series.rolling(10).mean().iloc[-6])
            ma10_slope = (ma10_current - ma10_prev) / ma10_current if ma10_current > 0 else 0
            if ma10_slope > 0.06:
                trend_score += 30  # 极强上升
            elif ma10_slope > 0.04:
                trend_score += 24  # 强上升
            elif ma10_slope > 0.02:
                trend_score += 18  # 温和上升
            elif ma10_slope > 0:
                trend_score += 8   # 缓慢上升
            elif ma10_slope > -0.02:
                trend_score -= 3   # 轻微下降
            else:
                trend_score -= 12  # 明显下降
        
        # 近期涨幅（5日，趋势反转导向：回调可能是二波机会）
        if len(C) >= 6:
            ret_5 = (C[-1] / C[-6] - 1) * 100
            if 3 <= ret_5 <= 12:
                trend_score += 15  # 最佳区间：稳健上涨
            elif ret_5 > 12:
                trend_score += 5   # 涨幅过大，谨慎
            elif -8 <= ret_5 < 3:
                trend_score += 10  # 小幅回调或横盘，可能是二波蓄力机会
            elif -15 <= ret_5 < -8:
                trend_score += 15  # 深度回调后，反转预期更强
            elif ret_5 < -15:
                trend_score += 5   # 跌幅过大，风险较高
            else:
                trend_score -= 5
        
        # 计算120日高点（用最高价，排除当天）
        HHV120 = float(high_series.iloc[:-1].tail(120).max()) if len(C) >= 120 else HHV20
        
        # 突破前高（不加分，只记录状态用于其他计算）
        breakout_strength = 0.5
        if current_price >= HHV20:
            breakout_strength = 1.0
        elif current_price >= HHV20 * 0.97:
            breakout_strength = 0.85
        elif current_price >= HHV20 * 0.90:
            breakout_strength = 0.6
        
        # 120日新高状态（用于强度减分）
        is_new_high_120 = current_price >= HHV120
        dist_to_120high = (HHV120 - current_price) / HHV120 if HHV120 > 0 else 0
        
        # =========================
        # 新增：假突破识别
        # =========================
        # 判断1：检测'二波'-60日内有过高点后快速回落（失败突破）
        failed_breakout_penalty = 0

        if len(C) >= 30:
            lookback = min(60, len(C) - 5)
            H_series = df['high'].values
            for offset_days_ago in range(lookback, 8, -1):
                idx = len(C) - offset_days_ago
                if idx < 5:
                    continue
                day_high = float(H_series[idx])
                prev_start = max(0, idx - 20)
                hhv_before = float(max(C[prev_start:idx]))
                if hhv_before <= 0:
                    continue
                if day_high >= hhv_before * 0.98:
                    after_3_idx = min(len(C) - 1, idx + 3)
                    if after_3_idx < len(C):
                        after_3_price = float(C[after_3_idx])
                        drop_from_high = (day_high - after_3_price) / day_high * 100
                        if drop_from_high > 8:
                            failed_breakout_penalty = 20
                            break

        # 判断2：长上影K线（冲高回落信号）
        # 注意：科创板/创业板波动大，且大涨日上影5%是正常的
        upper_shadow_penalty = 0
        if 'high' in df.columns and len(df) >= 2:
            today_high = float(df['high'].iloc[-1])
            today_close = C[-1]
            upper_shadow_pct = (today_high - today_close) / today_close * 100

            # 当日涨幅越大，允许的上影越长（科创板涨10%时上影5%很正常）
            if today_pct > 10:
                # 大涨日：上影>8%才算异常
                if upper_shadow_pct > 10:
                    upper_shadow_penalty = 20
                elif upper_shadow_pct > 8:
                    upper_shadow_penalty = 10
            elif today_pct > 5:
                # 中涨日：上影>5%开始扣分
                if upper_shadow_pct > 7:
                    upper_shadow_penalty = 15
                elif upper_shadow_pct > 5:
                    upper_shadow_penalty = 8
            else:
                # 小涨/下跌日：上影>3%开始扣分
                if upper_shadow_pct > 6:
                    upper_shadow_penalty = 20
                elif upper_shadow_pct > 4:
                    upper_shadow_penalty = 15
                elif upper_shadow_pct > 3:
                    upper_shadow_penalty = 8

        # 判断3：删除"接近高点未突破"惩罚
        # 理由：接近高点是强势表现，只有"突破后快速回落"才是假突破
        # 该判断已被判断1（失败突破检测）覆盖，此处不再重复惩罚
        near_high_no_break_penalty = 0

        # 假突破综合惩罚
        total_fake_breakout_penalty = failed_breakout_penalty + upper_shadow_penalty + near_high_no_break_penalty
        trend_score -= total_fake_breakout_penalty

        trend_score = min(100, max(0, trend_score))  # 限制上限

        
        # =========================
        # 2. 资金健康度评分（25%）
        # =========================
        capital_score = 50
        
        # 量能分析（使用当日量比：当日成交量/5日均量，均线排除当天）
        vol_ratio = 1.0
        vol_vs_high_ratio = 1.0  # 当日量能 vs 60日最高量能
        if 'vol' in df.columns and len(df) >= 10:
            vol_today = float(df['vol'].iloc[-1])
            vol_hist = df['vol'].iloc[:-1]  # 排除当天的历史成交量
            vol_ma5 = float(vol_hist.tail(5).mean()) if len(vol_hist) >= 5 else vol_today
            vol_ma20 = float(vol_hist.tail(20).mean()) if len(vol_hist) >= 20 else vol_today
            # 当日量比 = 当日成交量 / 5日均量（均线不含当天，真实放量倍数）
            vol_ratio = vol_today / vol_ma5 if vol_ma5 > 0 else 1.0
            # 5日/20日量比用于平滑判断
            vol_ma_ratio = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
            
            # 关键改进：当日量 vs 60日最高量（识别量能萎缩，max不含当天）
            vol_max_60d = float(vol_hist.tail(60).max()) if len(vol_hist) >= 5 else vol_today
            vol_vs_high_ratio = vol_today / vol_max_60d if vol_max_60d > 0 else 1.0
            
            # 当日放量越大，加分越多
            if vol_ratio > 3.0:
                capital_score += 35  # 巨量爆发
            elif vol_ratio > 2.0:
                capital_score += 25  # 明显放量
            elif vol_ratio > 1.5:
                capital_score += 15  # 温和放量
            elif vol_ratio > 1.0:
                capital_score += 5   # 轻微放量
            
            # 5日/20日量比辅助判断持续性
            if vol_ma_ratio > 2.0:
                capital_score += 10  # 持续放量
            
            # 新增：量能萎缩惩罚
            # 当日量能 vs 60日最高量能（解决立新能源问题：量比~1但远低于历史高点）
            if vol_vs_high_ratio < 0.2:
                capital_score -= 20  # 量能极度萎缩（不足高峰期的20%）
            elif vol_vs_high_ratio < 0.3:
                capital_score -= 15  # 量能严重萎缩（<30%）
            elif vol_vs_high_ratio < 0.4:
                capital_score -= 10  # 量能明显萎缩（<40%）
            elif vol_vs_high_ratio < 0.5:
                capital_score -= 5   # 量能有所萎缩（<50%）
        
        # 机构资金流
        inst_flow_score = calc_institutional_flow_score(ts_code)
        capital_score += inst_flow_score * 15
        
        capital_score = min(100, max(0, capital_score))
        
        # =========================
        # 3. 位置安全性评分（15%）
        # =========================
        position_score = 50
        
        # 距离前高位置
        dist_to_high = (HHV20 - current_price) / HHV20 if HHV20 > 0 else 0
        if dist_to_high <= 0.05:
            position_score += 25  # 接近前高
        elif dist_to_high <= 0.15:
            position_score += 15
        
        # 从低点涨幅
        run_up = (current_price - LLV20) / LLV20 if LLV20 > 0 else 0
        if run_up <= 0.15:
            position_score += 20
        elif run_up <= 0.25:
            position_score += 10
        
        # 90日振幅（压缩度）
        if len(df) >= 90:
            range90 = (df['high'].values[-90:].max() - df['low'].values[-90:].min()) / df['low'].values[-90:].min()
            if range90 < 0.25:
                position_score += 10
        
        # 接近历史高点但未突破：不扣分（强势蓄能形态）
        # 理由：接近高点说明价格强势，不应该惩罚；
        #      是否为假突破由"失败突破检测"和"长上影K线"判断，
        #      不通过距离高点的远近直接扣分
        # 该部分贡献分数：0
        pass
        
        position_score = min(100, max(0, position_score))
        
        # =========================
        # 4. 热度持续性评分（20%）
        # =========================
        hot_score = 50
        
        # 主题生命力（降低加分幅度）
        tli_score, _ = calc_tli_score(theme, top_n=10, days=60)
        hot_score += (tli_score - 50) * 0.3  # 从0.5降到0.3
        
        # 热榜排名加分
        hot_rank_bonus, best_rank, hot_appear_count = get_hot_list_best_rank_bonus(ts_code, days=60)
        hot_score += hot_rank_bonus * 0.5  # 降低热榜加分权重
        
        # 检查主题是否连续3天情绪排名前5（避免追涨高潮主题）
        if theme:
            try:
                db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.db')
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path)
                    # 获取最近5个交易日的排名数据
                    dates_df = pd.read_sql(
                        f"SELECT DISTINCT trade_date FROM theme_scores WHERE trade_date <= '{TRADE_DATE}' ORDER BY trade_date DESC LIMIT 5",
                        conn
                    )
                    if not dates_df.empty:
                        recent_dates = dates_df['trade_date'].tolist()
                        consecutive_top5 = 0
                        # 检查是否连续在TOP5情绪
                        for td in recent_dates:
                            rank_df = pd.read_sql(
                                f"SELECT theme, sentiment_score FROM theme_scores WHERE trade_date = '{td}' ORDER BY sentiment_score DESC",
                                conn
                            )
                            if not rank_df.empty:
                                top5_themes = rank_df.head(5)['theme'].tolist()
                                if theme in top5_themes:
                                    consecutive_top5 += 1
                                else:
                                    break  # 不连续则停止
                        # 连续3天TOP5情绪 = 追涨惩罚
                        if consecutive_top5 >= 3:
                            hot_score -= 15  # 连续高潮，追涨风险高
                            recommendation = recommendation.replace("热榜Top1", "⚠️连续高潮")
                    conn.close()
            except Exception as e:
                pass  # 查询失败不阻塞
        
        # 过滤条件：非双创板股票且未上过热榜的，直接返回0分
        # 双创板：创业板(300开头)、科创板(688开头)
        is_innovation_board = ts_code.startswith('300') or ts_code.startswith('688')
        if not is_innovation_board and hot_appear_count<=0:
            return 0, "非双创板且未上热榜", {}, 90
        
        # 根据主题趋势分和情绪分调整热度（避免高潮后追高）
        # 情绪分过高（>80）= 高潮期，可能回落，降低热度
        # 趋势分下降（<30）= 主题走弱，降低热度
        if theme_sentiment_score > 80:
            # 情绪高潮，可能即将回落
            hot_score -= 15
        elif theme_sentiment_score > 70:
            # 情绪偏高，谨慎
            hot_score -= 8
        elif theme_sentiment_score < 30:
            # 情绪低迷，热度不足
            hot_score -= 10
        
        if theme_trend_score < 30:
            # 趋势走弱
            hot_score -= 12
        elif theme_trend_score < 40:
            # 趋势偏弱
            hot_score -= 5
        elif theme_trend_score > 70:
            # 趋势强劲，热度有支撑
            hot_score += 5
        
        # 主题TOP3次数
        if hasattr(getattr(globals().get('result_df', None), 'iloc', None), '__call__'):
            # 从主题评分中获取
            pass
        
        hot_score = min(100, max(0, hot_score))
        
        # =========================
        # 5. 基本面评分（使用V2增强模块）
        # =========================
        # 调用新的基本面评分模块
        try:
            fund_result = calc_fundamental_score_v2(
                ts_code=ts_code,
                theme_name=theme,
                theme_trend_score=theme_trend_score,
                theme_sentiment_score=theme_sentiment_score,
                hot_rank=best_rank if best_rank <= 100 else 9999,
                hot_count=hot_appear_count
            )
            fundamental_score = fund_result['base_score']  # 使用base_score作为基本面分
            synergy_coeff = fund_result['synergy_coeff']   # 共振系数用于后续调整
            fund_logic = fund_result['logic']
        except Exception as e:
            print(f"[统一评分] 基本面V2评分失败: {e}")
            fundamental_score = 50
            synergy_coeff = 1.0
            fund_logic = []
        
        fundamental_score = min(100, max(0, fundamental_score))
        
        # 独立的Alpha评分（机构基本面+北向资金共振，0-100分，已缓存）
        fundamental_alpha_score_val = 0
        fundamental_alpha_signal = ""
        try:
            alpha_result = stock_fundamental_alpha_score(ts_code, pro)
            fundamental_alpha_score_val = alpha_result.get('alpha_score', 0)
            fundamental_alpha_signal = alpha_result.get('signal', '')
        except Exception:
            pass
        
        # =========================
        # 6. 追高惩罚（优化版：考虑缩量调整后风险释放）
        # =========================
        penalty = 0
        if len(C) >= 6:
            ret_5 = (C[-1] / C[-6] - 1) * 100
            if ret_5 > 8:
                penalty += min((ret_5 - 8) * 3, 25)
            if ret_5 > 15:
                penalty += 10  # 额外惩罚
        
        # 新增：缩量调整折扣系数（基于 MA20 乖离率 + 趋势过热保护）
        # 区分两种情形：
        #   1. 严重透支（MA20乖离>20% 或 近10日涨幅>25%）：过热 → 保持高惩罚
        #   2. 温和整理（MA20乖离<15% + 量能萎缩）：风险释放 → 惩罚打折
        if penalty > 0 and len(C) >= 10 and len(df) >= 10:
            position_discount = 1.0
            ma20_discount = 1.0
            volume_discount = 1.0
            consolidation_factor = 1.0
            reasons = []

            # 判断1：MA20 乖离率（核心指标）
            if MA20 > 0:
                bias_to_ma20 = (C[-1] - MA20) / MA20 * 100
                if bias_to_ma20 > 20:
                    ma20_discount = 1.0  # 严重乖离 → 不打折
                    reasons.append(f"严重乖离{bias_to_ma20:.0f}%")
                elif bias_to_ma20 > 15:
                    ma20_discount = 0.9  # 偏高
                    reasons.append(f"乖离偏高{bias_to_ma20:.0f}%")
                elif bias_to_ma20 > 10:
                    ma20_discount = 0.7  # 中等
                    reasons.append(f"乖离中等{bias_to_ma20:.0f}%")
                else:
                    ma20_discount = 0.5  # 接近趋势线
                    reasons.append(f"乖离健康{bias_to_ma20:.0f}%")

            # 判断2：趋势过热保护（新增）
            # 如果近10日涨幅过高，即使MA20乖离合理也不能大折扣
            ret_10 = (C[-1] / C[-11] - 1) * 100
            ret_5_local = (C[-1] / C[-6] - 1) * 100
            trend_overheat_limit = 0.0  # 默认=不限制（取更严格的折扣）
            if ret_10 > 30:
                trend_overheat_limit = 1.0  # 近10日涨超30% → 不打折
                reasons.append(f"10日过热{ret_10:.0f}%")
            elif ret_10 > 25:
                trend_overheat_limit = 0.95  # 近10日25-30% → 几乎不打折
                reasons.append(f"10日偏热{ret_10:.0f}%")
            elif ret_5_local > 20:
                trend_overheat_limit = 0.9  # 近5日>20% → 轻微限制
                reasons.append(f"5日过热{ret_5_local:.0f}%")

            # 判断3：位置修复（距20日高点回撤）
            if dist_to_high > 0.03:
                if dist_to_high > 0.15:
                    position_discount = 0.5
                elif dist_to_high > 0.08:
                    position_discount = 0.7
                else:
                    position_discount = 0.8
                reasons.append(f"高点回撤{dist_to_high*100:.0f}%")

            # 判断4：量能萎缩（不是放量冲顶）
            if vol_ratio < 1.0:
                volume_discount = 0.7
                reasons.append(f"缩量(量比{vol_ratio:.1f})")
            elif vol_ratio < 1.3:
                volume_discount = 0.85
                reasons.append(f"温和量能(量比{vol_ratio:.1f})")

            # 判断5：近3日横盘整理（振幅<10%）
            if len(C) >= 8:
                recent_3_high = max(C[-1], C[-2], C[-3])
                recent_3_low = min(C[-1], C[-2], C[-3])
                consolidation_range = (recent_3_high - recent_3_low) / recent_3_low * 100
                if consolidation_range < 6:
                    consolidation_factor = 0.7
                    reasons.append(f"横盘整理(振幅{consolidation_range:.0f}%)")
                elif consolidation_range < 10:
                    consolidation_factor = 0.85

            # 综合折扣 = 先取各维度最严格的，再被过热保护限制
            raw_discount = min(position_discount, ma20_discount, volume_discount, consolidation_factor)
            discount = max(raw_discount, trend_overheat_limit)  # 过热保护不允许折扣过低

            if discount < 1.0:
                penalty = round(penalty * discount, 1)
        
        # =========================
        # 7. 龙头/核心加分
        # =========================
        leader_bonus = 0
        if breakout_strength >= 0.95 and dist_to_high <= 0.05:
            leader_bonus = 15  # 突破前高的龙头
        elif breakout_strength >= 0.80:
            leader_bonus = 8   # 接近前高的核心
        
        # =========================
        # 8. 历史辨识度加分（YRI-H） + 二波机会
        # =========================
        second_wave_bonus = 0
        recognition_bonus = 0
        yri_h_score = 0
        yri_h_tags = []
        yri_level = ""
        yri_portrait = ""
        yri_avg_amount = 0
        yri_zt_count = 0
        yri_max_consec_zt = 0
        
        # 优先使用 YRI-H 历史辨识度评分（替换旧的 calc_yri_score）
        if ts_code:
            try:
                yri_result = calc_yri_history(ts_code, debug=False)
                if isinstance(yri_result, dict) and "错误" not in yri_result:
                    yri_h_score = float(yri_result.get("YRI历史总分", 0))
                    yri_h_tags = yri_result.get("核心历史标签", [])
                    yri_level = yri_result.get("等级", "")
                    yri_portrait = yri_result.get("股性画像", "")
                    yri_a = yri_result.get("A_资金活跃度", {})
                    if isinstance(yri_a, dict):
                        yri_avg_amount = float(yri_a.get("日均成交额(万元)", 0))
                    yri_g = yri_result.get("G_涨停基因", {})
                    if isinstance(yri_g, dict):
                        yri_zt_count = int(yri_g.get("涨停次数", 0))
                    yri_s = yri_result.get("S_空间记忆", {})
                    if isinstance(yri_s, dict):
                        yri_max_consec_zt = int(yri_s.get("最大连板", 0))
                    # YRI-H 总分100分，最高贡献 +10分（从0-5分提升至0-10分）
                    recognition_bonus = (yri_h_score / 100) * 10
                    if recognition_bonus > 0:
                        recommendation = f"YRI{yri_h_score:.0f} | " + recommendation
            except Exception:
                pass
        
        # 降级方案：尝试读取二波扫描结果
        if recognition_bonus == 0:
            second_wave_file = os.path.join(BASE_DIR, 'report_daily', 'mainboard_second_wave.json')
            if os.path.exists(second_wave_file):
                try:
                    with open(second_wave_file, 'r', encoding='utf-8') as f:
                        second_wave_data = json.load(f)
                    for stock in second_wave_data.get('data', []):
                        if stock.get('ts_code') == ts_code:
                            recognition_score = stock.get('recognition_score', 0)
                            second_wave_score = stock.get('second_wave_score', 0)
                            recognition_bonus = (recognition_score / 100) * 5
                            second_wave_bonus = (second_wave_score / 100) * 8
                            if recognition_bonus > 0:
                                recommendation = f"辨识度{recognition_score:.0f} | " + recommendation
                            if second_wave_bonus > 0:
                                recommendation = f"二波{second_wave_score:.0f} | " + recommendation
                            break
                except Exception:
                    pass
        
        # =========================
        # 9. 综合得分 - 趋势强度主导
        # =========================
        # 基础分 = 其他维度加权（降低热度权重）
        base_score = (
            capital_score * 0.30 +
            position_score * 0.25 +
            hot_score * 0.20 +
            fundamental_score * 0.25
        )
        
        # 趋势强度作为乘数因子（趋势越强，总分越高）
        # 缩窄倍数范围，避免顶部聚类
        trend_multiplier = 0.7 + (trend_score / 100) * 0.6  # 0.7 ~ 1.3
        
        # 共振系数改为加法项而非乘法，避免双乘数叠加导致的顶部溢出
        synergy_bonus = (synergy_coeff - 0.8) * 25  # 系数0.5→-7.5分, 1.0→+5分, 1.5→+17.5分
        
        # 综合分 = 基础分 × 趋势乘数 + 共振加分 - 惩罚 + 龙头加分 + 二波加分
        final_score = base_score * trend_multiplier + synergy_bonus - penalty + leader_bonus + second_wave_bonus + recognition_bonus
        
        # 趋势强度额外加成：趋势分>70的股票获得额外加分
        if trend_score >= 80:
            final_score += 8  # 强趋势加成
        elif trend_score >= 70:
            final_score += 4   # 中等趋势加成
        elif trend_score < 40:
            final_score -= 8   # 弱趋势惩罚
        
        final_score = min(100, max(5, final_score))
        
        # =========================
        # 9. 失败概率计算
        # =========================
        failure_prob = 50
        
        failure_prob -= (trend_score - 50) * 0.25
        failure_prob -= (capital_score - 50) * 0.25
        failure_prob -= (position_score - 50) * 0.20
        failure_prob -= (hot_score - 50) * 0.15
        failure_prob -= (fundamental_score - 50) * 0.15
        
        failure_prob += penalty * 1.5
        
        if hot_score >= 85:
            failure_prob += 8  # 过热风险
        elif hot_score < 40:
            # 关键修正：缩量整理(vol_ratio<1.2)≠缺乏热度，而是健康洗盘
            if vol_ratio >= 1.2:
                failure_prob += 10  # 放量下跌或长期无人关注 = 缺乏热度风险
        
        failure_prob = min(90, max(10, failure_prob))
        
        # 失败概率修正：失败概率越低，加分越多（反向激励）
        # 以30%为基准，每低1%加0.5分，每高1%扣0.5分
        failure_bonus = (30 - failure_prob) * 0.5
        final_score += failure_bonus
        
        # =========================
        # 10. 推荐理由
        # =========================
        reason_parts = []
        
        if trend_score >= 80:
            reason_parts.append("趋势强劲")
        elif trend_score >= 60:
            reason_parts.append("趋势良好")
        
        if capital_score >= 80:
            reason_parts.append("资金充沛")
        elif capital_score >= 60:
            reason_parts.append("资金健康")
        
        if position_score >= 80:
            reason_parts.append("位置安全")
        elif position_score >= 60:
            reason_parts.append("位置合理")
        
        if hot_score >= 70:
            reason_parts.append("热度持续")
        
        if leader_bonus >= 10:
            reason_parts.append("👑龙头")
        elif leader_bonus >= 5:
            reason_parts.append("⭐核心")
        
        if hot_rank_bonus > 0:
            reason_parts.append(f"热榜Top{best_rank}")
        
        if penalty > 5:
            reason_parts.append(f"⚠️追高-{penalty:.0f}")
        
        recommendation = " | ".join(reason_parts) if reason_parts else "观察中"
        
        # =========================
        # 11. 详细信息
        # =========================
        details = {
            '趋势强度': round(trend_score, 1),
            '资金健康度': round(capital_score, 1),
            '位置安全性': round(position_score, 1),
            '热度持续性': round(hot_score, 1),
            '基本面': round(fundamental_score, 1),
            'Alpha评分': round(fundamental_alpha_score_val, 1),
            'Alpha信号': fundamental_alpha_signal,
            '共振系数': round(synergy_coeff, 2),
            '追高惩罚': round(penalty, 1),
            '龙头加分': leader_bonus,
            '二波加分': round(second_wave_bonus, 1),
            '辨识度加分': round(recognition_bonus, 1),
            'YRI历史总分': round(yri_h_score, 1),
            'YRI标签': ", ".join(yri_h_tags) if yri_h_tags else "",
            '量能爆发': round(vol_ratio, 2),
            '热榜最佳排名': best_rank if best_rank <= 100 else 0,
            '热榜上榜次数': hot_appear_count,
            '基本面逻辑': fund_logic[:3] if fund_logic else [],
            'YRI等级': yri_level,
            'YRI股性画像': yri_portrait,
            'YRI日均成交(万)': round(yri_avg_amount, 0),
            'YRI涨停次数': yri_zt_count,
            'YRI最大连板': yri_max_consec_zt,
        }
        
        return round(final_score, 1), recommendation, details, round(failure_prob, 1)
        
    except Exception as e:
        print(f"[统一评分] 异常: {e}")
        import traceback
        traceback.print_exc()
        return 0, "计算异常", {}, 50


def calc_dual_layer_score_v9(df, ts_code='', stock_info=None, theme=''):
    """
    V9综合评分系统 V4 - 龙头拉开机制 + 板块分层系统
    兼容旧接口，内部调用统一评分算法
    """
    # 转换为旧格式输出
    final_score, recommendation, details, failure_prob = calc_unified_stock_score(df, ts_code, theme)
    return {
        "V9总评分": final_score,
        "失败概率": failure_prob,
        "推荐理由": recommendation,
        "趋势强度": details.get('趋势强度', 0),
        "资金健康度": details.get('资金健康度', 0),
        "位置安全性": details.get('位置安全性', 0),
        "热度持续性": details.get('热度持续性', 0),
        "基本面": details.get('基本面', 0),
        "V9评分说明": f"综合评分={final_score} | 失败概率={failure_prob}%"
    }


def calc_position_factor(df):

    low120 = df["low"].tail(120).min()
    high120 = df["high"].tail(120).max()

    close = df["close"].iloc[-1]

    pos = (close - low120) / (high120 - low120)

    if pos < 0.30:
        score = 0.2

    elif pos < 0.50:
        score = 0.4

    elif pos < 0.70:
        score = 0.7

    elif pos < 0.90:
        score = 1.0

    else:
        score = 0.8

    return score


def _calc_momentum_score(df):
    """
    新增：动量评分，捕捉启动股
    """
    C = df['close']
    VOL = df['vol']

    score = 0

    # 1. 5日价格动量
    if len(C) >= 5:
        pct5 = (C.iloc[-1] / C.iloc[-5]) - 1
        if pct5 > 0.05:
            score += 0.3
        elif pct5 > 0.02:
            score += 0.2
        elif pct5 > 0:
            score += 0.1

    # 2. 20日趋势
    if len(C) >= 20:
        pct20 = (C.iloc[-1] / C.iloc[-20]) - 1
        if pct20 > 0.15:
            score += 0.25
        elif pct20 > 0.08:
            score += 0.15
        elif pct20 > 0:
            score += 0.05

    # 3. 量能配合
    if len(VOL) >= 5:
        vol_ratio = VOL.tail(5).mean() / (VOL.tail(20).mean() + 1e-6)
        if vol_ratio > 1.5:
            score += 0.2
        elif vol_ratio > 1.2:
            score += 0.1
        elif vol_ratio > 1.0:
            score += 0.05

    # 4. 突破近期高点
    if len(C) >= 10:
        recent_high = C.tail(10).max()
        if C.iloc[-1] >= recent_high * 0.98:
            score += 0.25

    # 归一化到0-1
    return np.clip(score, 0, 1)

def _calc_squeeze_compression(df, compression_score, squeeze_prob):
    """
    新增：压缩+洗盘统一建模
    组合压缩度和洗盘概率
    """
    # 压缩度和洗盘概率的乘积，重点是高压缩度 + 高洗盘概率 = 好启动机会
    score = 0

    # 组合得分
    if compression_score >= 0.8 and squeeze_prob >= 0.5:
        score += 0.4
    elif compression_score >= 0.6 and squeeze_prob >= 0.4:
        score += 0.3
    elif compression_score >= 0.5:
        score += 0.2

    # 加上两个指标平均
    avg = (compression_score + squeeze_prob) / 2
    score += avg * 0.5

    return np.clip(score, 0, 1)


def _find_best_theme(stock_info):
    """
    自动选择纯度最高的主题
    遍历所有主题配置，计算个股与每个主题的纯度，返回最高的主题名
    """
    if not stock_info:
        return ''

    try:
        # 加载主题配置
        cfg_path = os.path.join(BASE_DIR, 'theme.json')
        if not os.path.exists(cfg_path):
            return ''

        with open(cfg_path, 'r', encoding='utf-8') as f:
            theme_cfg = json.load(f).get('HOT_THEMES', {})

        best_theme = ''
        best_confidence = 0

        # 遍历所有主题，找纯度最高的
        for theme_name in theme_cfg.keys():
            confidence = calc_theme_confidence(stock_info, theme_name)
            if confidence > best_confidence:
                best_confidence = confidence
                best_theme = theme_name

        return best_theme

    except Exception as e:
        print(f"[V7自动主题] 选择失败: {e}")
        return ''


def find_all_themes(stock_info, min_confidence=0.3):
    """
    查找股票所属的所有主题（支持多主题）
    
    Args:
        stock_info: 股票信息字典
        min_confidence: 最小置信度阈值，默认0.3
    
    Returns:
        所有匹配主题的字符串，用逗号分隔
    """
    if not stock_info:
        return ''

    try:
        # 加载主题配置
        cfg_path = os.path.join(BASE_DIR, 'theme.json')
        if not os.path.exists(cfg_path):
            return ''

        with open(cfg_path, 'r', encoding='utf-8') as f:
            theme_cfg = json.load(f).get('HOT_THEMES', {})

        # 收集所有置信度超过阈值的主题
        matched_themes = []
        for theme_name in theme_cfg.keys():
            confidence = calc_theme_confidence(stock_info, theme_name)
            if confidence >= min_confidence:
                # 带上置信度信息
                matched_themes.append(f"{theme_name}({confidence:.2f})")

        # 按置信度降序排序
        matched_themes.sort(key=lambda x: float(x.split('(')[1].rstrip(')')), reverse=True)
        
        return ','.join(matched_themes) if matched_themes else ''

    except Exception as e:
        print(f"[V7多主题] 选择失败: {e}")
        return ''



def calc_tech_barrier_score(ts_code):
    """
    技术壁垒评分（基本面）
    从 tushare 财务缓存读取 ROE/毛利率/研发费用率

    Returns:
        float: 0~10 分
    """
    try:
        csv_path = os.path.join(CACHE_DIR, f"{ts_code}.csv")
        if not os.path.exists(csv_path):
            return 0
        df = pd.read_csv(csv_path)
        if df.empty or 'close' not in df.columns:
            return 0
        df = df.sort_values('trade_date', ascending=False)

        # 用 pct_chg 的标准差衡量波动（波动小→壁垒高→高分）
        if 'pct_chg' in df.columns and len(df) >= 20:
            pct_vals = df['pct_chg'].head(60).dropna().astype(float)
            if len(pct_vals) >= 20:
                vol = pct_vals.std()
                # 低波动→壁垒高：vol<=2%→5分, vol>=4%→0分
                vol_score = max(0, min(5, 5 * (4 - vol) / 2))
            else:
                vol_score = 0
        else:
            vol_score = 0

        # 用涨幅趋势判断基本面强度
        if len(df) >= 60:
            close_60d = float(df.iloc[min(59, len(df)-1)]['close'])
            close_now = float(df.iloc[0]['close'])
            ret_60d = (close_now - close_60d) / close_60d if close_60d > 0 else 0
            # 60日涨幅>20%→+3分, >50%→+5分
            trend_score = min(5, max(0, ret_60d * 10))
        else:
            trend_score = 0

        return round(min(10, vol_score + trend_score), 1)
    except Exception:
        return 0


def calc_institutional_flow_score(ts_code):
    """
    机构资金流评分
    从 moneyflow 缓存读取大单数据

    Returns:
        float: 0~5 分
    """
    try:
        # 尝试从当日 moneyflow 数据获取
        from datetime import datetime, timedelta
        for offset in range(5):
            check_date = (datetime.now() - timedelta(days=offset)).strftime('%Y%m%d')
            mf_path = os.path.join(CACHE_DIR, f"moneyflow_{check_date}.csv")
            if os.path.exists(mf_path):
                mf_df = pd.read_csv(mf_path)
                if ts_code in mf_df['ts_code'].values:
                    row = mf_df[mf_df['ts_code'] == ts_code].iloc[0]
                    buy_lg = float(row.get('buy_lg_vol', 0)) if pd.notna(row.get('buy_lg_vol')) else 0
                    sell_lg = float(row.get('sell_lg_vol', 0)) if pd.notna(row.get('sell_lg_vol')) else 0
                    net_lg = buy_lg - sell_lg
                    if net_lg > 1e6:
                        return 5  # 大单大幅净买入
                    elif net_lg > 0:
                        return 3  # 大单小幅净买入
                    else:
                        return 0  # 大单净卖出
        return 0
    except Exception:
        return 0


def calc_fundamental_score_v2(ts_code, theme_name='', theme_trend_score=0, theme_sentiment_score=0,
                                stock_info=None, hot_rank=9999, hot_count=0):
    """
    行业景气度 + 个股基本面优势评分模块（短线增强型）
    
    目标：判断行业是否是资金主线，个股是否具备成为龙头的基本面支撑
    
    返回：
        dict: {
            "industry_score": 0-100,
            "fundamental_score": 0-100,
            "base_score": 0-100,
            "synergy_coeff": 0.5-1.5,
            "is_mainline": bool,
            "stage": str,
            "logic": [str, ...]
        }
    """
    try:
        logic = []
        
        # =========================
        # 一、行业景气度评分（0~100）
        # =========================
        
        # 【1】产业趋势强度（40%）
        trend_strength = 50  # 基础分
        if theme_trend_score >= 80:
            trend_strength = 95  # 绝对主线
            logic.append(f"产业趋势：绝对主线（趋势分{theme_trend_score:.0f}）")
        elif theme_trend_score >= 65:
            trend_strength = 80  # 强主线分支
            logic.append(f"产业趋势：强主线分支（趋势分{theme_trend_score:.0f}）")
        elif theme_trend_score >= 45:
            trend_strength = 65  # 轮动热点
            logic.append(f"产业趋势：轮动热点（趋势分{theme_trend_score:.0f}）")
        elif theme_trend_score >= 30:
            trend_strength = 45  # 边缘
            logic.append(f"产业趋势：边缘方向（趋势分{theme_trend_score:.0f}）")
        else:
            trend_strength = 25  # 退潮
            logic.append(f"产业趋势：退潮期（趋势分{theme_trend_score:.0f}）")
        
        # 【2】资金集中度（30%）
        # 使用热榜出现次数和排名作为代理
        concentration = 50
        if hot_count >= 5:
            if hot_rank <= 10:
                concentration = 95  # 高集中龙头
                logic.append("资金集中：高集中龙头，持续吸金")
            elif hot_rank <= 30:
                concentration = 80
                logic.append("资金集中：核心标的，资金关注度高")
            else:
                concentration = 65
                logic.append("资金集中：有一定资金关注")
        elif hot_count >= 2:
            concentration = 55
            logic.append("资金集中：偶尔上榜")
        else:
            concentration = 40
            logic.append("资金集中：无显著资金集中")
        
        # 【3】板块阶段（20%）
        stage = "未知"
        stage_score = 50
        if theme_sentiment_score >= 80:
            stage = "高潮期"
            stage_score = 55  # 风险上升
            logic.append("板块阶段：高潮期，谨慎追高")
        elif theme_sentiment_score >= 60:
            stage = "发酵期"
            stage_score = 75
            logic.append("板块阶段：发酵期，可参与")
        elif theme_sentiment_score >= 40:
            stage = "启动期"
            stage_score = 90  # 最优
            logic.append("板块阶段：启动期，最佳介入窗口")
        else:
            stage = "退潮期"
            stage_score = 30
            logic.append("板块阶段：退潮期，建议回避")
        
        # 【4】情绪热度（10%）
        emotion_heat = 50
        if hot_count >= 10:
            emotion_heat = 90
        elif hot_count >= 5:
            emotion_heat = 75
        elif hot_count >= 2:
            emotion_heat = 60
        elif hot_count >= 1:
            emotion_heat = 45
        else:
            emotion_heat = 30
        
        # 科技/创新主题溢价：新兴产业赛道获得额外加分
        # 传统产业（电力链、煤炭、银行等）即使短期趋势强，长期成长性也不如科技主题
        tech_innovation_themes = {
            '人形机器人', 'AI算力链', 'AI服务器与算力基建', 'AI芯片', 'AI模型与AI Agent',
            'AI应用', 'AI终端', 'AI文化娱乐', 'AI能源链',
            '半导体设备', '半导体制造', '半导体材料', '半导体封测', '半导体EDA/IP',
            '存储芯片', '先进封装', '先进封装材料', 'IC设计',
            '光刻机链', '光通信', 'PCB电子电路', '光学光电子', '消费电子', '被动元件',
            '物理AI', '智能驾驶', '低空经济', '商业航天', '军工',
            '脑机接口', '固态电池', '氢能', '核聚变', '新型储能',
            '数据要素', '信创软件', '金融科技', '电网数字化', '工业母机',
        }
        traditional_themes = {
            '电力链', '煤炭链', '银行', '保险', '券商', '贵金属', '工业金属', '小金属',
            '能源金属', '新能源汽车链', '必选消费红利链', '情绪消费成长链',
            '创新医药主线', '硫磺磷化工链', '电力设备出海',
        }
        
        if theme_name in tech_innovation_themes:
            tech_premium = 15  # 新兴产业溢价
            if logic:
                logic.insert(-4, f"产业趋势：科技创新主题溢价+{tech_premium}")
        elif theme_name in traditional_themes:
            tech_premium = -8  # 传统产业折价
            if logic:
                logic.insert(-4, f"产业趋势：传统行业折价{tech_premium}")
        else:
            tech_premium = 0
        
        trend_strength = max(10, min(100, trend_strength + tech_premium))
        
        # 计算行业景气度（主题质量权重从40%大幅降至15%）
        # 理由：主题质量波动大（今天高明天可能回落），不宜过度依赖
        industry_score = (
            trend_strength * 0.15 +  # 从0.40大幅降至0.15
            concentration * 0.30 +
            stage_score * 0.30 +    # 从0.20升至0.30（情绪更实时）
            emotion_heat * 0.25     # 从0.10升至0.25（热度更即时）
        )
        
        # =========================
        # 二、个股基本面优势评分（0~100）
        # =========================
        
        # 【1】产业地位（40%）
        position_score = 50
        
        # 尝试从stock_info获取市值信息，无stock_info时用hot_count代理
        is_large_cap = False
        if stock_info and 'total_market_cap' in stock_info:
            # 总市值大于500亿视为大市值
            if stock_info['total_market_cap'] > 500e8:
                is_large_cap = True
        elif hot_count >= 20:
            # 60天内上热榜20次+ = 大市值指数成份股（如鹏鼎控股、茅台等）
            # 这类股票有持续的市场关注度，但缺乏短线爆发弹性
            is_large_cap = True
        
        # 根据历史热榜排名判断是否为龙头
        if hot_rank <= 10 and hot_count >= 3:
            position_score = 95
            logic.append("个股地位：核心龙头，市场认可度高")
        elif hot_rank <= 30 and hot_count >= 2:
            position_score = 80
            logic.append("个股地位：强势标的，有辨识度")
        elif hot_rank <= 50 or hot_count >= 1:
            position_score = 65
            logic.append("个股地位：有资金关注")
        else:
            position_score = 45
            logic.append("个股地位：跟随标的，辨识度低")
        
        # 大市值扣分（短线资金偏好中小市值）
        if is_large_cap:
            position_score -= 10
            logic.append("个股地位：大市值，弹性受限")
        
        position_score = max(0, min(100, position_score))
        
        # 【2】成长弹性（30%）
        growth_score = 50
        # 使用60日涨幅作为代理
        try:
            csv_path = os.path.join(CACHE_DIR, f"{ts_code}.csv")
            if os.path.exists(csv_path):
                df = pd.read_csv(csv_path)
                if len(df) >= 60:
                    df = df.sort_values('trade_date', ascending=True)
                    close_60d = float(df.iloc[-60]['close'])
                    close_now = float(df.iloc[-1]['close'])
                    ret_60d = (close_now - close_60d) / close_60d if close_60d > 0 else 0
                    
                    if ret_60d >= 0.5:
                        growth_score = 95
                        logic.append(f"成长弹性：强趋势（60日涨幅{ret_60d*100:.0f}%）")
                    elif ret_60d >= 0.3:
                        growth_score = 80
                        logic.append(f"成长弹性：良好（60日涨幅{ret_60d*100:.0f}%）")
                    elif ret_60d >= 0.1:
                        growth_score = 65
                        logic.append(f"成长弹性：一般（60日涨幅{ret_60d*100:.0f}%）")
                    else:
                        growth_score = 45
                        logic.append(f"成长弹性：偏弱（60日涨幅{ret_60d*100:.0f}%）")
        except:
            pass
        
        # 【3】事件催化（20%）
        # 使用主题生命力作为代理
        catalyst_score = 50
        try:
            tli_score, _ = calc_tli_score(theme_name, top_n=10, days=30)
            if tli_score >= 80:
                catalyst_score = 85
                logic.append("事件催化：强催化窗口")
            elif tli_score >= 60:
                catalyst_score = 70
                logic.append("事件催化：有催化预期")
            elif tli_score >= 40:
                catalyst_score = 55
                logic.append("事件催化：催化减弱")
            else:
                catalyst_score = 40
                logic.append("事件催化：无明显催化")
        except:
            pass
        
        # 【4】市场记忆度（10%）
        memory_score = 50
        if hot_count >= 5:
            memory_score = 90  # 反复炒作
            logic.append("市场记忆：历史龙头，反复活跃")
        elif hot_count >= 2:
            memory_score = 70
            logic.append("市场记忆：有一定炒作基础")
        else:
            memory_score = 40
            logic.append("市场记忆：缺乏辨识度")
        
        # 计算个股基本面优势
        fundamental_score = (
            position_score * 0.40 +
            growth_score * 0.30 +
            catalyst_score * 0.20 +
            memory_score * 0.10
        )
        
        # =========================
        # 三、共振系数计算
        # =========================
        base = industry_score * 0.6 + fundamental_score * 0.4
        
        if base >= 85:
            synergy_coeff = 1.30 + min(0.2, (base - 85) / 75)  # 1.30-1.50
        elif base >= 70:
            synergy_coeff = 1.10 + (base - 70) / 75  # 1.10-1.30
        elif base >= 50:
            synergy_coeff = 0.90 + (base - 50) / 100  # 0.90-1.10
        else:
            synergy_coeff = 0.60 + base / 125  # 0.60-0.90
        
        # =========================
        # 四、短线过滤规则
        # =========================
        is_mainline = True
        if industry_score < 40 and fundamental_score < 50:
            synergy_coeff = min(synergy_coeff, 0.8)
            is_mainline = False
            logic.append("过滤规则：行业+个股双弱，强制降权")
        
        # 高潮期+高情绪 = 风险
        if theme_sentiment_score >= 85 and hot_count >= 5:
            synergy_coeff *= 0.9
            logic.append("风险提示：高潮期+高热度，降低预期")
        
        synergy_coeff = round(max(0.5, min(1.5, synergy_coeff)), 2)
        
        # 最终结论
        if synergy_coeff >= 1.2:
            logic.append(f"结论：强共振环境（系数{synergy_coeff}），优先考虑")
        elif synergy_coeff >= 1.0:
            logic.append(f"结论：可交易环境（系数{synergy_coeff}）")
        elif synergy_coeff >= 0.8:
            logic.append(f"结论：普通环境（系数{synergy_coeff}），谨慎参与")
        else:
            logic.append(f"结论：弱势环境（系数{synergy_coeff}），建议回避")
        
        return {
            "industry_score": round(industry_score, 1),
            "fundamental_score": round(fundamental_score, 1),
            "base_score": round(base, 1),
            "synergy_coeff": synergy_coeff,
            "is_mainline": is_mainline,
            "stage": stage,
            "logic": logic
        }
        
    except Exception as e:
        print(f"[基本面评分] 异常: {e}")
        return {
            "industry_score": 50,
            "fundamental_score": 50,
            "base_score": 50,
            "synergy_coeff": 1.0,
            "is_mainline": False,
            "stage": "未知",
            "logic": ["评分异常，使用默认值"]
        }


def rank_top_stocks_for_open(df_list, results_list):
    """
    对TOP10股票进行游资开仓排名
    
    参数：
        df_list: K线数据DataFrame列表
        results_list: V7评分结果列表
    
    返回：
        排序后的股票列表，包含开仓评分和推荐
    """
    ranked_stocks = []
    
    for i, (df, v7_result) in enumerate(zip(df_list, results_list)):
        if v7_result is None or df is None:
            continue
        
        # 确保df是DataFrame且有足够数据
        if not isinstance(df, pd.DataFrame) or len(df) < 20:
            continue
            
        # 确保必要字段存在
        if 'close' not in df.columns or 'vol' not in df.columns:
            continue
            
        try:
            # V10开仓评分
            open_score, structure_type, recommendation = calc_hot_money_open_score_v10(
                 v7_result, df, v7_result, v7_result.get('所属主题', '')
            )
            
        except Exception as e:
            print(f"[开仓评分V10] {v7_result.get('代码', '')} 计算失败: {e}")
            continue
        
        ranked_stocks.append({
            '代码': v7_result.get('代码', ''),
            '名称': v7_result.get('名称', ''),
            '现价': v7_result.get('现价', 0),
            '涨跌幅': v7_result.get('涨跌幅', 0),
            '所属主题': v7_result.get('所属主题', ''),
            'V9总评分': v7_result.get('V9总评分', v7_result.get('V7总评分', 0)),
            '失败概率': v7_result.get('失败概率', 0),
            '量能爆发': v7_result.get('量能爆发', 0),
            '突破强度': v7_result.get('突破强度', 0),
            '结构类型': structure_type,
            '开仓评分': open_score,
            '推荐理由': recommendation,
            '所属状态': v7_result.get('所属状态', ''),
            '主题趋势分': v7_result.get('主题趋势分', 0),
            '主题情绪分': v7_result.get('主题情绪分', 0),
        })
    
    # 按开仓评分降序排序
    ranked_stocks.sort(key=lambda x: x['开仓评分'], reverse=True)
    return ranked_stocks


def print_hot_money_open_report(ranked_stocks, top_n=10):
    """打印游资开仓报告"""
    print("\n" + "=" * 80)
    print("🔥 游资最强开仓标的 (TOP " + str(top_n) + ")")
    print("=" * 80)
    
    for i, stock in enumerate(ranked_stocks[:top_n], 1):
        print(f"\n【第{i}名】{stock['名称']} ({stock['代码']})")
        print(f"  结构类型: {stock['结构类型']}")
        print(f"  开仓评分: {stock['开仓评分']}")
        print(f"  V9基础分: {stock['V9总评分']} | 失败概率: {stock['失败概率']:.1%}")
        print(f"  今日涨幅: {stock['涨跌幅']:.2f}%")
        print(f"  量能爆发: {stock['量能爆发']:.2f} | 突破强度: {stock['突破强度']:.2f}")
        print(f"  所属主题: {stock.get('所属主题', '')}")
        print(f"  推荐理由: {stock['推荐理由']}")
    
    print("\n" + "-" * 80)
    print("📋 完整排名表:")
    print("-" * 80)
    print(f"{'排名':<4} {'代码':<12} {'名称':<8} {'结构类型':<10} {'开仓分':<8} {'V9分':<8} {'主题':<12}")
    print("-" * 80)
    
    for i, stock in enumerate(ranked_stocks, 1):
        print(f"{i:<4} {stock['代码']:<12} {stock['名称']:<8} {stock['结构类型']:<10} "
              f"{stock['开仓评分']:<8.1f} {stock['V9总评分']:<8.1f} {stock['所属主题']:<12}")
    
    print("=" * 80)
    
    return ranked_stocks[:top_n]


def calc_hot_money_open_score_v10(v7_result, df, stock_info, theme=''):
    """
    游资最强开仓评分 V10

    V10 = V9综合评分 + 结构加分 + 突破加分 + 主题热度分 + 热榜排名分 - 追高扣分

    结构识别（参考V9）：
    - 🟢启动型：接近20日高点90~105% + 今日涨幅>1%
    - 🟡加速型：price>MA20>MA60 + 今日涨
    - 🔴高位分歧：距20日低点已涨>20% + price>前高105%
    - 🟡调整型：缩量回调到均线附近 + 跌幅>-3%
    - 兜底：震荡型

    BREAKOUT_BONUS（不变）：
    - 距60日新高<2%:   +10
    - 距120日新高<2%:  +10

    加减项（透明显示）：
    - 结构加分：启动+15, 加速0, 调整+3, 高位-8
    - 追高扣分：加重（5日>5%起扣，线性递增）
    - 压缩加分：90日振幅<30% → +5（避免高分堆积）
    - 主题热度分：60天内进入TOP3次数，每次+1分，最多+10分
    - 热榜排名分：20天内最佳排名分段加分
        - Top10: +15分
        - Top20: +12分
        - Top30: +10分
        - Top50: +8分
        - Top100: +5分
    - 总分使用非线性压缩公式：open = 100 * raw / (raw + 50) 防止天花板效应
    """
    try:
        if not v7_result or df is None or not isinstance(df, pd.DataFrame) or len(df) < 20:
            return 0, "数据不足", ""

        df = df.reset_index(drop=True)
        C = df['close'].values

        # V9综合评分作为基础分
        base_score = float(v7_result.get('V9总评分', v7_result.get('V7总评分', 50)))

        # =========================
        # K线形态基础数据
        # =========================
        close_series = df['close']
        high_series = df['high']
        MA20 = float(close_series.rolling(20).mean().iloc[-1])
        MA60 = float(close_series.rolling(60).mean().iloc[-1])
        # 修复: HHV用最高价(非收盘价)，且排除当天数据
        HHV20 = float(high_series.iloc[:-1].tail(20).max()) if len(high_series) > 1 else float(close_series.tail(20).max())
        HHV60 = float(high_series.iloc[:-1].tail(60).max()) if len(high_series) > 1 else float(close_series.tail(60).max())
        LLV20 = float(close_series.tail(20).min())
        current_price = float(C[-1])

        if len(C) >= 2:
            today_pct = float((C[-1] / C[-2] - 1) * 100)
        else:
            today_pct = 0
        
        price_position = current_price / MA20 if MA20 > 0 else 1.0
        run_up_from_20d_low = (current_price - LLV20) / max(LLV20, 0.01)

        volume_explosion = float(v7_result.get('量能爆发', 0))  # 0-1

        # =========================
        # 结构识别（参照V9逻辑）
        # =========================
        structure_type = "未知"
        structure_desc = ""
        structure_bonus = 0

        # 🟢 启动型：接近前高但未大幅透支 + 今日有量
        # 严格条件：低点上来涨幅<20% + 均线乖离<8% + 接近前高
        if (current_price <= HHV20 * 1.05 and current_price >= HHV20 * 0.90 and
            today_pct > 1 and
            run_up_from_20d_low < 0.20 and
            price_position < 1.08):
            structure_type = "🟢启动型"
            structure_bonus = 15
            structure_desc = f"接近前高，均线乖离{((price_position-1)*100):.0f}%，低点上来{run_up_from_20d_low*100:.0f}%，启动形态"
        # 🟡 加速型：均线多头 + 趋势延续（本身已在趋势中，不加分）
        elif price_position > 1.05 and MA20 > MA60 and today_pct > 0:
            structure_type = "🟡加速型"
            structure_bonus = 0
            structure_desc = "趋势加速中"
        # 🔴 高位分歧：大幅透支 + 累计涨幅已大
        elif current_price > HHV20 * 1.05 and run_up_from_20d_low > 0.20:
            structure_type = "🔴高位分歧"
            structure_bonus = -8
            structure_desc = "高位分歧，风险较大"
        # 🟡 调整型：缩量回调到均线附近
        elif price_position < 1.02 and volume_explosion < 0.3 and today_pct > -3:
            structure_type = "🟡调整型"
            structure_bonus = 3
            structure_desc = "缩量调整，关注均线支撑"
        # ⚪ 震荡型：兜底
        else:
            structure_type = "⚪震荡型"
            structure_bonus = 0
            structure_desc = "震荡整理，需观察方向"

        # =========================
        # 换手率打分（相对指标，考虑流通盘大小 + 量比放大程度）
        # =========================
        turnover_rank_score = 50
        try:
            # 从 stock_info/v7_result 获取 ts_code
            ts_code_for_turnover = ""
            if isinstance(stock_info, dict):
                ts_code_for_turnover = stock_info.get('代码', '') or stock_info.get('ts_code', '')
            elif hasattr(stock_info, 'get'):
                ts_code_for_turnover = stock_info.get('代码', '')
            
            # 1. 当日换手率（相对活跃程度，占70%权重）
            today_turnover = 0
            if ts_code_for_turnover:
                today_turnover = get_cached_turnover(ts_code_for_turnover)
            
            # 2. 量比（放大程度，占30%权重，均线排除当天）
            if len(df) >= 22:
                vol_hist = df['vol'].iloc[:-1]  # 排除当天
                recent_5d = vol_hist.tail(5)
                recent_20d = vol_hist.tail(20)
                vol_ratio_for_rank = recent_5d.mean() / recent_20d.mean() if recent_20d.mean() > 0 else 1.0
            else:
                vol_ratio_for_rank = 1.0
            
            # 综合打分：换手率 + 量比放大
            if today_turnover > 0:
                # 换手率评分（70分）
                turnover_sub_score = 0
                if today_turnover >= 10:
                    turnover_sub_score = 70
                elif today_turnover >= 5:
                    turnover_sub_score = 60
                elif today_turnover >= 3:
                    turnover_sub_score = 50
                elif today_turnover >= 1.5:
                    turnover_sub_score = 40
                else:
                    turnover_sub_score = 25
                
                # 量比评分（30分）
                vol_ratio_sub_score = 0
                if vol_ratio_for_rank >= 2.0:
                    vol_ratio_sub_score = 30
                elif vol_ratio_for_rank >= 1.5:
                    vol_ratio_sub_score = 25
                elif vol_ratio_for_rank >= 1.2:
                    vol_ratio_sub_score = 20
                elif vol_ratio_for_rank >= 1.0:
                    vol_ratio_sub_score = 15
                else:
                    vol_ratio_sub_score = 10
                
                turnover_rank_score = turnover_sub_score + vol_ratio_sub_score
            else:
                # 无换手率数据时降级为量比评分
                if vol_ratio_for_rank >= 2.0:
                    turnover_rank_score = 85
                elif vol_ratio_for_rank >= 1.5:
                    turnover_rank_score = 70
                elif vol_ratio_for_rank >= 1.0:
                    turnover_rank_score = 55
                else:
                    turnover_rank_score = 35
        except Exception:
            pass

        # =========================
        # 压缩加分（近90日振幅<30%）
        # =========================
        compression_bonus = 0
        try:
            if len(df) >= 90:
                h90 = df['high'].values[-90:]
                l90 = df['low'].values[-90:]
                range90 = (h90.max() - l90.min()) / l90.min() if l90.min() > 0 else 0
                if range90 < 0.30:
                    compression_bonus = 5
        except Exception:
            pass

        # =========================
        # BREAKOUT_BONUS
        # =========================
        breakout_bonus = 0
        try:
            if len(df) >= 120:
                hhv_60 = df['high'].values[-60:].max()
                hhv_120 = df['high'].values[-120:].max()
                dist_60 = (current_price - hhv_60) / hhv_60 if hhv_60 > 0 else 0
                dist_120 = (current_price - hhv_120) / hhv_120 if hhv_120 > 0 else 0
                if dist_60 > -0.02:
                    breakout_bonus += 10
                if dist_120 > -0.02:
                    breakout_bonus += 10
            elif len(df) >= 60:
                hhv_60 = df['high'].values[-60:].max()
                dist_60 = (current_price - hhv_60) / hhv_60 if hhv_60 > 0 else 0
                if dist_60 > -0.02:
                    breakout_bonus += 10
        except Exception:
            pass

        # =========================
        # 追高扣分（加重版：从5日涨5%开始扣，线性递增）
        # =========================
        recent_penalty = 0
        try:
            n = len(C)
            ret_5 = (C[-1] / C[-6] - 1) * 100 if n >= 6 else 0
            ret_10 = (C[-1] / C[-11] - 1) * 100 if n >= 11 else 0
            ret_20 = (C[-1] / C[-21] - 1) * 100 if n >= 21 else 0
            # 5日涨幅扣分：5%起扣，每多1%多扣2分，最高扣20分
            if ret_5 > 5:
                recent_penalty += min((ret_5 - 5) * 2, 20)
            # 10日涨幅额外扣分：20%起扣
            if ret_10 > 20:
                recent_penalty += min((ret_10 - 20) * 1.5, 15)
            # 20日涨幅额外扣分：40%起扣
            if ret_20 > 40:
                recent_penalty += min((ret_20 - 40) * 1.0, 10)
            recent_penalty = min(recent_penalty, 30)
        except Exception:
            pass

        # =========================
        # 主题热度分（V10新增）
        # 计算60天内该主题出现在TOP3的次数，每次+1分，最多20分
        # =========================
        theme_hot_score = 0
        theme_top3_count = 0
        try:
            if theme:
                db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.db')
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path)
                    # 获取过去60天的交易日
                    trade_dates_df = pd.read_sql(
                        "SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT 60",
                        conn
                    )
                    trade_dates = trade_dates_df['trade_date'].tolist()
                    
                    # 统计该主题进入TOP3的次数
                    for td in trade_dates:
                        day_df = pd.read_sql(
                            f"SELECT theme FROM theme_scores WHERE trade_date = '{td}' ORDER BY composite_score DESC LIMIT 3",
                            conn
                        )
                        if not day_df.empty and theme in day_df['theme'].values:
                            theme_top3_count += 1
                    
                    conn.close()
                    
                    # 热度分 = min(次数, 10)
                    theme_hot_score = min(theme_top3_count, 10)
        except Exception as e:
            pass

        # =========================
        # 基本面因子分（技术壁垒+机构资金流）
        # =========================
        ts_code = v7_result.get('代码', '')
        tech_barrier_score = calc_tech_barrier_score(ts_code)
        inst_flow_score = calc_institutional_flow_score(ts_code)

        # =========================
        # 热榜排名加分（V10新增：20天内最佳排名分段加分）
        # =========================
        hot_rank_bonus, best_rank, hot_appear_count = get_hot_list_best_rank_bonus(ts_code, days=20)

        # =========================
        # 总分（非线性压缩公式：天然防止天花板效应）
        # raw = 基础 + 各加分 - 追高扣分
        # open_score = 100 * raw / (raw + 50)
        #    raw=30→38, raw=50→50, raw=70→58, raw=100→67
        #    raw=150→75, raw=200→80（高分区间自然收敛）
        # =========================
        raw_score = base_score + structure_bonus + breakout_bonus + compression_bonus + theme_hot_score + hot_rank_bonus - recent_penalty
        raw_score += tech_barrier_score + inst_flow_score
        open_score = 100 * raw_score / (raw_score + 50) if raw_score > 0 else 0
        open_score = min(95, max(0, open_score))  # 95硬上限防溢出

        # =========================
        # 推荐理由（参照V9风格）
        # =========================
        recommendation = f"{structure_desc}"
        recommendation += f" | V9基础{base_score:.1f}分"
        recommendation += f" | 换手率{turnover_rank_score:.0f}分"

        # 加分项拆解
        bonus_parts = []
        if structure_bonus > 0:
            bonus_parts.append(f"结构+{structure_bonus}分")
        elif structure_bonus < 0:
            bonus_parts.append(f"结构{structure_bonus}分")
        if breakout_bonus > 0:
            bonus_parts.append(f"突破+{breakout_bonus}分")
        if compression_bonus > 0:
            bonus_parts.append(f"压缩+{compression_bonus}分")
        if theme_hot_score > 0:
            bonus_parts.append(f"主题热度+{theme_hot_score}分({theme_top3_count}次TOP3)")
        if recent_penalty > 0:
            bonus_parts.append(f"追高-{recent_penalty}分")
        if tech_barrier_score > 0:
            bonus_parts.append(f"基本面+{tech_barrier_score}分")
        if inst_flow_score > 0:
            bonus_parts.append(f"资金流+{inst_flow_score}分")
        if hot_rank_bonus > 0:
            bonus_parts.append(f"热榜Top{best_rank}+{hot_rank_bonus}分({hot_appear_count}次)")

        if bonus_parts:
            recommendation += f" | 修正:{','.join(bonus_parts)}"

        recommendation += f" | V10开仓={open_score:.1f} (raw={raw_score:.0f})"

        # 特殊标记（参照V9）
        if structure_type == "🟢启动型" and float(v7_result.get('失败概率', 0.5)) < 0.25:
            recommendation = "⭐重点关注: " + recommendation
        elif structure_type == "🔴高位分歧":
            recommendation = "⚠️谨慎: " + recommendation

        return round(open_score, 1), structure_type, recommendation

    except Exception as e:
        print(f"[开仓评分V10] 异常: {e}")
        import traceback
        traceback.print_exc()
        return 0, "计算异常", ""


def calc_theme_confidence(stock_info, theme):
    """
    计算个股与主题的纯度/置信度评分（0-100）

    参数：
        stock_info: 股票信息字典，包含：
            - industries: 行业列表
            - concepts: 概念列表
            - business_text: 业务描述文本
            - name: 股票名称
        theme: 主题名称（如"AI算力链"、"半导体"等）

    返回：
        主题纯度评分（0-100）
    """
    if not stock_info or not theme:
        return 0

    score = 0

    # 行业匹配得分
    score += calc_industry_score(
        stock_info.get("industries", []),
        theme
    )

    # 概念匹配得分
    score += calc_concept_score(
        stock_info.get("concepts", []),
        theme
    )

    # 关键词命中得分
    score += calc_keyword_score(
        stock_info.get("business_text", ""),
        theme
    )

    # 业务相关性得分
    score += calc_business_score(
        stock_info.get("business_text", ""),
        theme
    )

    # 核心公司加分
    score += calc_core_company_score(
        stock_info.get("name", ""),
        theme
    )

    # 核心龙头股额外加分（对行业内具有核心地位的龙头公司给予加成）
    score += calc_leader_bonus(
        stock_info.get("name", ""),
        theme
    )

    # 排除惩罚
    score -= calc_penalty(
        stock_info.get("business_text", ""),
        theme
    )

    return max(min(score, 100), 0)


def calc_industry_score(industries, theme):
    """行业匹配得分（满分25）"""
    if not industries or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    target_industries = set(cfg.get("industry", []))

    for ind in industries:
        if ind in target_industries:
            score += 25
            break
        # 部分匹配
        for target in target_industries:
            if target in ind or ind in target:
                score += 15
                break

    return min(score, 25)


def calc_concept_score(concepts, theme):
    """概念匹配得分（满分25）"""
    if not concepts or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    target_concepts = set(cfg.get("concept", []))

    matched_count = 0
    for c in concepts:
        if c in target_concepts:
            matched_count += 1

    # 匹配数量越多，得分越高
    if matched_count >= 5:
        score = 25
    elif matched_count >= 3:
        score = 20
    elif matched_count >= 2:
        score = 15
    elif matched_count >= 1:
        score = 10

    return min(score, 25)


def calc_keyword_score(business_text, theme):
    """关键词命中得分（满分20）"""
    if not business_text or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    keywords = cfg.get("keywords", [])
    exclude_keywords = set(cfg.get("exclude_keywords", []))

    text_lower = business_text.lower()

    hit_count = 0
    for kw in keywords:
        if kw.lower() in text_lower:
            hit_count += 1

    # 根据命中数量计算得分
    if len(keywords) > 0:
        hit_rate = hit_count / len(keywords)
        score = hit_rate * 20

    return min(score, 20)


def calc_business_score(business_text, theme):
    """业务相关性得分（满分15）"""
    if not business_text or not theme:
        return 0

    score = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    # 核心关键词
    core_keywords = cfg.get("keywords", [])[:5]  # 取前5个核心词

    text_lower = business_text.lower()
    core_hits = 0
    for kw in core_keywords:
        if kw.lower() in text_lower:
            core_hits += 1

    # 核心关键词命中给高分
    if core_hits >= 3:
        score = 15
    elif core_hits >= 2:
        score = 10
    elif core_hits >= 1:
        score = 5

    return min(score, 15)


def calc_core_company_score(stock_name, theme):
    """核心公司加分（满分10）- 从theme.json读取"""
    if not stock_name or not theme:
        return 0

    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    # 从theme.json获取核心公司列表
    companies = cfg.get("core_companies", [])
    for c in companies:
        if c in stock_name:
            return 10

    return 0


def calc_leader_bonus(stock_name, theme):
    """核心龙头股额外加分（满分15）- 从theme.json读取"""
    if not stock_name or not theme:
        return 0

    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    # 从theme.json获取核心龙头股列表（优先使用leader_companies，没有则用core_companies前3个）
    leader_companies = cfg.get("leader_companies", [])
    if not leader_companies:
        # 如果没有定义leader_companies，使用core_companies的前3个作为龙头
        core_list = cfg.get("core_companies", [])
        leader_companies = core_list[:3]

    for c in leader_companies:
        if c in stock_name:
            return 15

    return 0


def calc_penalty(business_text, theme):
    """排除惩罚（扣分项）"""
    if not business_text or not theme:
        return 0

    penalty = 0
    cfg = _get_theme_config(theme)
    if not cfg:
        return 0

    exclude_keywords = cfg.get("exclude_keywords", [])
    text_lower = business_text.lower()

    for kw in exclude_keywords:
        if kw.lower() in text_lower:
            penalty += 15

    return min(penalty, 30)  # 最多扣30分


def _get_theme_config(theme):
    """获取主题配置"""
    try:
        cfg_path = os.path.join(BASE_DIR, 'theme.json')
        if not os.path.exists(cfg_path):
            return None

        with open(cfg_path, 'r', encoding='utf-8') as f:
            theme_cfg = json.load(f).get('HOT_THEMES', {})

        return theme_cfg.get(theme, {})
    except Exception as e:
        print(f"[主题纯度] 配置读取失败: {e}")
        return None


def strategy(df, code, emotion_stage, total_mv=0):
    """优化版本：向量化计算 + 提前过滤 + 缓存复用"""
    
    # ===== 快速前置过滤（低成本判断优先）=====
    if len(df) < 80:
        return False
    
    # 总市值≥80亿
    if total_mv / 10000 < 80:
        return False
    
    # ST股票过滤（代码前缀判断，无需查询字典）
    if code.startswith('1') or code.startswith('2'):
        return False
    
    # 两个月涨幅过滤
    if len(df) >= 40:
        close_values = df['close'].values
        ret_2m = close_values[-1] / close_values[-40] - 1
        if ret_2m > 1.0:
            return False
    
    # ===== 数据提取（一次提取，多次使用）=====
    C = df['close'].values
    O = df['open'].values
    H = df['high'].values
    L = df['low'].values
    VOL = df['vol'].values
    
    # ===== 创业板/科创板判断（仅用于今日涨停过滤）=====
    # 主板：10% 涨停；双创板：20% 涨停
    IS_CYB_KCB = (code.startswith('3') or code.startswith('688') or code.startswith('689'))
    ZT_SINGLE_UP = 1.198 if IS_CYB_KCB else 1.098

    # ===== 快速过滤：今天已涨停 → 直接排除 =====
    if len(df) >= 3:
        today_ratio = C[-1] / C[-2]
        if today_ratio >= ZT_SINGLE_UP:
            return False

    # ST名称过滤（延后到这里，只在必要时调用）
    StockName = get_stock_name(code)
    ST1 = (StockName.upper().startswith('ST') or 
            StockName.upper().startswith('*ST'))
    if ST1:
        return False
    
    # ===== 启动过滤：60日振幅 =====
    if len(df) >= 20:
        hh = H[-20:].max()
        ll = L[-20:].min()
        if (hh / ll - 1) > 1.8:
            return False
    
    # ===== 均线计算（一次计算，多次使用）=====
    close_arr = df['close'].values if hasattr(df['close'], 'values') else np.asarray(df['close'])
    C_series = pd.Series(close_arr)
    ma5 = C_series.rolling(5).mean().values
    ma10 = C_series.rolling(10).mean().values
    ma20 = C_series.rolling(20).mean().values
    ma22 = C_series.rolling(30).mean().values
    ma60 = C_series.rolling(60).mean().values
    
    # 均线条件
    if C[-1] >= ma20[-1] * 1.3 or C[-1] / ma60[-1] > 2:
        return False
    
    # 股价必须站上5日、10日、20日均线
    if  C[-1] < ma20[-1] or ma10[-1] < ma20[-1]*0.97 or ma5[-1] < ma10[-1]*0.97:
        return False
    
    # ===== 涨停判断（向量化）=====
    ZT_1day = (C_series.shift(1) / C_series.shift(2) < 1.08) & (C_series / C_series.shift(1) > 1.098)
    ZT_2day = (C_series.shift(1) / C_series.shift(2) >= 1.051) & (C_series / C_series.shift(1) >= 1.051) & (C_series / C_series.shift(2) >= 1.11)
    ZT = ZT_1day | ZT_2day
    
    # 使用向量化的barslast
    ZTTS = barslast(ZT)
    
    # 原版逻辑：如果今天信号(ztts=0)，取前一个信号
    ztts = ZTTS.iloc[-1]
    if ztts == 0:
        ztts = ZTTS.iloc[-2]
    
    if np.isnan(ztts):
        return False
    
    ztts = int(ztts)
    
    # ===== 过滤：近5天累计涨幅超过20% = 乖离过大，跳过 =====
    if len(C) >= 6 and (C[-1] / C[-6] - 1) > 0.3:
        return False
    
    # ===== 条件1：ztts范围（距上一波高点的天数）=====
    if ztts < 3 or ztts > 90:
        return False
    
    # ===== 缓存ztts区间数据（避免重复切片）=====
    ztts_close = C[-ztts:]
    ztts_df = df.iloc[-ztts:]
    ztts_vol = ztts_df['vol'].values
    vol_ma5 = ztts_df['vol'].rolling(5).mean().values  # 只计算一次
    
    # ===== TJ条件判断 =====
    ref_close = C[-ztts-1]
    cond2 = (ztts_close < ref_close).sum() == 0
    cond3 = (ztts_close.max() / ztts_close.min()) < 1.3
    cond4 = (C[-1] / H[-ztts-1]) < 1.2  # 修复：H.shift(ztts).iloc[-1] = H[-ztts-1]
    cond5 = H[-ztts:].max() >= H[-60:].max() * 0.8
    cond6 = ma22[-1] >= ma22[-2]
    
    # 量能条件（复用vol_ma5）
    cond_low_vol = (ztts_vol < vol_ma5 * 0.9).any()
    
    # 回撤计算（向量化）
    cum_max = np.maximum.accumulate(ztts_close)
    drawdown = (ztts_close - cum_max) / cum_max
    cond_dd = drawdown.min() >= -0.15
    
    # 放量大跌判断（复用vol_ma5）
    down_k = ztts_df['close'].values < ztts_df['open'].values
    big_vol = ztts_vol > vol_ma5 * 1.5
    big_drop = ztts_df['pct_chg'].values < -5
    cond_no_bad_k = ~(down_k & big_vol & big_drop).any()
    
    cond7 = cond_low_vol and cond_no_bad_k
    
    TJ = cond3 and cond4 and cond5 and cond6 
    if not TJ:
        return False
    
    # ===== XH 判断 =====
    highest_close = C[-ztts-1:-1].max()
    
    # 量能接近前高条件：当前成交量达到ztts区间内最高成交量的80%以上
    vol_peak = VOL[-ztts-1:-1].max()
    vol_condition = VOL[-1] >= vol_peak * 0.7 if vol_peak > 0 else True
    #做突破
    cond_xh1 = C[-1] > highest_close
    cond_xh2 = C[-1] > C[-2] and C[-1] / ma5[-1] < 1.11 and C[-1] / ma5[-1] > 0.97
  
    
    return cond_xh1 and cond_xh2


def strategy_dx(df, code, emotion_stage, total_mv=0):
    """优化版本：向量化计算 + 提前过滤 + 缓存复用"""
    
    # ===== 快速前置过滤（低成本判断优先）=====
    if len(df) < 80:
        return False
    
    # 总市值≥80亿
    if total_mv / 10000 < 80:
        return False
    
    # ST股票过滤（代码前缀判断，无需查询字典）
    if code.startswith('1') or code.startswith('2'):
        return False
    
   
    # ===== 数据提取（一次提取，多次使用）=====
    C = df['close'].values
    O = df['open'].values
    H = df['high'].values
    L = df['low'].values
    VOL = df['vol'].values
    
    # ===== 创业板/科创板判断（仅用于今日涨停过滤）=====
    # 主板：10% 涨停；双创板：20% 涨停
    IS_CYB_KCB = (code.startswith('3') or code.startswith('688') or code.startswith('689'))
    ZT_SINGLE_UP = 1.198 if IS_CYB_KCB else 1.098

    # ===== 快速过滤：今天已涨停 → 直接排除 =====
    if len(df) >= 3:
        today_ratio = C[-1] / C[-2]
        if today_ratio >= ZT_SINGLE_UP:
            return False

    # ST名称过滤（延后到这里，只在必要时调用）
    StockName = get_stock_name(code)
    ST1 = (StockName.upper().startswith('ST') or 
            StockName.upper().startswith('*ST'))
    if ST1:
        return False
    
   
    # ===== 均线计算（一次计算，多次使用）=====
    close_arr = df['close'].values if hasattr(df['close'], 'values') else np.asarray(df['close'])
    C_series = pd.Series(close_arr)
    ma5 = C_series.rolling(5).mean().values
    ma10 = C_series.rolling(10).mean().values
    ma20 = C_series.rolling(20).mean().values
    ma22 = C_series.rolling(30).mean().values
    ma60 = C_series.rolling(60).mean().values
   
    # ===== 找上一波拉升（仅依赖收盘价，无需因子数据）=====
    SURGE_MIN_VAL = 0.25       # 一波涨幅>=25%
    SURGE_DAYS_MIN_VAL = 7     # 一波最少天数
    SURGE_DAYS_MAX_VAL = 21    # 一波最多天数
    
    closes = df['close'].values
    n = len(closes)
    wave1_high_idx = None
    wave1_gain = 0.0
    
    # 从近向远扫描（找最近的wave1）
    for i in range(min(n - 30, n - 3), 30, -1):
        for wave1_len in range(SURGE_DAYS_MIN_VAL, min(SURGE_DAYS_MAX_VAL + 1, i)):
            window = closes[i - wave1_len:i + 1]
            if len(window) < wave1_len:
                continue
            
            low_idx = np.argmin(window[:wave1_len // 2])
            high_idx = np.argmax(window[low_idx:]) + low_idx
            
            if high_idx <= low_idx or high_idx - low_idx < 5:
                continue
            
            wgain = (window[high_idx] - window[low_idx]) / window[low_idx]
            if wgain < SURGE_MIN_VAL:
                continue
            
            wave1_high_idx = i - wave1_len + high_idx
            wave1_gain = wgain
            break
        if wave1_high_idx is not None:
            break
    
    if wave1_high_idx is None:
        return False
    
    # ===== 120日均线乖离率过滤（仅依赖H/close，无需因子）=====
    ma120_arr = C_series.rolling(120).mean().values
    wave1_high_price = H[wave1_high_idx]
    ma120_at_wave1 = ma120_arr[wave1_high_idx]
    if ma120_at_wave1 > 0 and (wave1_high_price / ma120_at_wave1) >= 2:
        return False
    
    # ===== 按需获取因子数据（wave1初筛通过后才取，避免无效API调用）=====
    need_factor = ('rsi_bfq_6' not in df.columns or 'dmi_adx_bfq' not in df.columns)
    if need_factor:
        try:
            df_factor = cached_stk_factor_pro(code, '20251001', TRADE_DATE)
            if df_factor is not None and not df_factor.empty:
                df_factor['trade_date'] = df_factor['trade_date'].astype(str)
                factor_cols = [c for c in df_factor.columns if c not in ('ts_code', 'trade_date', 'close', 'open', 'high', 'low', 'vol', 'amount')]
                if factor_cols:
                    df = df.merge(df_factor[['trade_date'] + factor_cols], on='trade_date', how='left')
                    # 重新取 C/H/L/VOL（因子合并后行数不变，但确保引用新df）
                    C = df['close'].values
                    O = df['open'].values
                    H = df['high'].values
                    L = df['low'].values
                    VOL = df['vol'].values
        except Exception:
            pass
    
    # ===== 计算上一波期间RSI最大 < 80 =====
    rsi_col = 'rsi_bfq_6'
    if rsi_col in df.columns:
        start_idx = max(0, wave1_high_idx - 20)
        segment_rsi = df.iloc[start_idx:wave1_high_idx + 1][rsi_col]
        max_rsi = segment_rsi.max()
        if pd.isna(max_rsi) or max_rsi >= 99:
            return False
    

    
    # ===== 计算ztts（距上一波高点的天数）=====
    ztts = n - 1 - wave1_high_idx
    
    # ===== 条件1：ztts范围（调整天数低于60天）=====
    if ztts < 2 or ztts > 60:
        return False
    
    # ===== 缓存ztts区间数据（避免重复切片）=====
    ztts_close = C[-ztts:]
    ztts_df = df.iloc[-ztts:]
    ztts_vol = ztts_df['vol'].values
    vol_ma5 = ztts_df['vol'].rolling(5).mean().values  # 只计算一次
    
    # ===== TJ条件判断 =====
    ref_close = C[-ztts-1]
    cond2 = (ztts_close < ref_close).sum() == 0
    cond3 = (ztts_close.max() / ztts_close.min()) < 1.3
    cond4 = (C[-1] / H[-ztts-1]) < 1.2  # 修复：H.shift(ztts).iloc[-1] = H[-ztts-1]
    cond5 = H[-ztts:].max() >= H[-60:].max() * 0.8
    cond6 = ma22[-1] >= ma22[-2]
    
    # 量能条件（复用vol_ma5）
    cond_low_vol = (ztts_vol < vol_ma5 * 0.9).any()
    
    # 回撤计算（向量化）
    cum_max = np.maximum.accumulate(ztts_close)
    drawdown = (ztts_close - cum_max) / cum_max
    cond_dd = drawdown.min() >= -0.15
    
    # 放量大跌判断（复用vol_ma5）
    down_k = ztts_df['close'].values < ztts_df['open'].values
    big_vol = ztts_vol > vol_ma5 * 1.5
    big_drop = ztts_df['pct_chg'].values < -5
    cond_no_bad_k = ~(down_k & big_vol & big_drop).any()
    
    cond7 = cond_low_vol and cond_no_bad_k
    
    #TJ = cond3 and cond4 and cond5 and cond6 
    #if not TJ:
    #    return False
  

    # ===== XH 判断 =====
    highest_close = C[-ztts-1:-1].max()
    
    # 量能接近前高条件：当前成交量达到ztts区间内最高成交量的80%以上
    vol_peak = VOL[-ztts-1:-1].max()
    vol_condition = VOL[-1] >= vol_peak * 0.7 if vol_peak > 0 else True
    cond_xh1 = C[-1] < ma5[-1] and C[-1]<ref_close and ma5[-1] < ma5[-2]
    
    # ===== 二波入场RSI条件：当前RSI < 60 =====
    if rsi_col in df.columns:
        cur_rsi = float(df.iloc[-1][rsi_col])
        if pd.isna(cur_rsi) or cur_rsi >= 60:
            return False
    
    return cond_xh1


def deepseek(prompt, use_flash=False):
    """DeepSeek API 调用
    Args:
        prompt: 提示词
        use_flash: True=用Flash快速模型，False=用Pro深度模型
    """
    url = "https://api.deepseek.com/chat/completions"

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 选择模型：Flash=快速低费用，Pro=深度分析
    model = "deepseek-v4-flash" if use_flash else "deepseek-v4-pro"

    data = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是A股顶级机构和游资短线投资分析师"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],

        "temperature": 0.1,
        "extra_body":[{"enable_search": True}]

    }

    r = requests.post(
        url,
        headers=headers,
        json=data
    )

    if r.status_code != 200:

        print(r.text)

        return ""

    return r.json()['choices'][0]['message']['content']


    

# =========================
# MiniMax（备用）
# =========================
def minimax(prompt):
    url = "https://api.minimax.chat/v1/text/chatcompletion_v2"

    headers = {
        "Authorization": f"Bearer {MINI_MAX_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "MiniMax-M2.7",
        "messages": [
            {
                "role": "system",
                "content": "你是A股顶级机构趋势投资专家"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.2,
        "top_p": 0.5,
        "max_tokens": 40960
    }

    r = requests.post(
        url,
        headers=headers,
        json=data
    )

    if r.status_code != 200:

        print(r.text)

        return ""

    return r.json()['choices'][0]['message']['content']

##== KIMI ==##
def kimi(prompt):

    KIMI_API_KEY = os.getenv("KIMI_API_KEY")
    URL = "https://api.moonshot.cn/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {KIMI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "kimi-k2.6",
        "messages": [
            {
                "role": "system",
                "content": "你是专业A股机构分析师"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
                
        
    }

    try:

        response = requests.post(
            URL,
            headers=headers,
            json=payload,
            timeout=600
        )

        data = response.json()

        return data["choices"][0]["message"]["content"]

    except Exception as e:

        print("Kimi接口错误:", e)

        try:
            print(data)
        except:
            pass

        return ""
    
##== 豆包 ==##
def ask_doubao(prompt):
    DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY")
    URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DOUBAO_API_KEY}"
    }

    payload = {
        # 模型名称
        "model": "doubao-seed-2-0-pro-260215",

        "messages": [
            {
                "role": "system",
                "content": "你是专业A股机构分析师"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],

        # 稳定输出参数
        "temperature": 0.2,
        "top_p": 0.5,
        "max_tokens": 40960
    }
    try:

        response = requests.post(
            URL,
            headers=headers,
            json=payload,
            timeout=600
        )

        data = response.json()

        return data["choices"][0]["message"]["content"]
    except Exception as e:

        print("Doubao接口错误:", e)

        try:
            print(data)
        except:
            pass

        return ""

from openai import OpenAI
##== 千问 ==##
def ask_qwen(prompt):
    try:
        client = OpenAI(
            api_key=QWEN_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

        completion = client.chat.completions.create(
            model="qwen3-max",
            messages=[
                {
                    "role": "system",
                    "content": "你是专业A股机构分析师"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            # 稳定输出参数
            temperature=0.2,
            top_p=0.5,
            max_tokens=40960
        )

        if completion and completion.choices and len(completion.choices) > 0:
            message = completion.choices[0].message
            if message and hasattr(message, 'content'):
                return message.content
        print("千问接口返回数据格式异常")
        return ""
    except Exception as e:
        print(f"千问接口错误: {str(e)}")
        return ""
    


def send_wechat(msg, key):
    import re
    # 清理HTML标签（Server酱不支持HTML）
    msg = re.sub(r'<[^>]+>', '', msg)
    msg = msg.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')

    url = f"https://sctapi.ftqq.com/{key}.send"

    data = {
        "title": f"每日复盘 - {TRADE_DATE}",
        "desp": msg
    }

    requests.post(url, data=data)




def markdown_to_html_report(
        markdown_text,
        output_file="stock_report.html",
        pdf_file="stock_report.pdf",
        title="AI股票分析报告"
):

    # ========= Markdown 转 HTML =========
    body = markdown2.markdown(
        markdown_text,
        extras=[
            "tables",
            "fenced-code-blocks",
            "strike",
            "task_list"
        ]
    )

    # ========= CSS美化 =========
    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">

<head>
<meta charset="UTF-8">

<title>{title}</title>

<style>

body {{
    background-color: #f5f7fa;
    color: #222;

    font-family:
        "PingFang SC",
        "Microsoft YaHei",
        Arial;

    max-width: 1000px;

    margin: 40px auto;

    padding: 40px;

    background: white;

    border-radius: 16px;

    box-shadow:
        0 4px 20px rgba(0,0,0,0.08);

    line-height: 1.8;
}}

h1 {{
    border-bottom: 3px solid #1677ff;
    padding-bottom: 12px;
    color: #1677ff;
}}

h2 {{
    margin-top: 35px;
    color: #0f172a;
}}

h3 {{
    color: #334155;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 20px;
    margin-bottom: 20px;
}}

th {{
    background: #1677ff;
    color: white;
    padding: 12px;
}}

td {{
    border: 1px solid #dcdfe6;
    padding: 10px;
}}

tr:nth-child(even) {{
    background: #f8fafc;
}}

code {{
    background: #f1f5f9;
    padding: 2px 6px;
    border-radius: 6px;
}}

pre {{
    background: #0f172a;
    color: #f8fafc;

    padding: 20px;

    border-radius: 12px;

    overflow-x: auto;
}}

blockquote {{
    border-left: 5px solid #1677ff;
    padding-left: 15px;
    color: #555;
    background: #f8fafc;
    margin: 20px 0;
}}

ul {{
    padding-left: 25px;
}}

li {{
    margin-bottom: 8px;
}}

strong {{
    color: #d4380d;
}}

</style>
</head>

<body>

{body}

</body>
</html>
"""

    # ========= 保存HTML =========
    with open(
            output_file,
            "w",
            encoding="utf-8"
    ) as f:

        f.write(html)

    print(f"HTML报告已生成: {output_file}")



# =========================
# 市场数据（带缓存）
# =========================
def get_market():
    import sys
    print("[DBG] get_market 开始"); sys.stdout.flush()
    cache_file = os.path.join(
        CACHE_DIR,
        f"market_{TRADE_DATE}.csv"
    )
    print(f"[DBG] get_market cache_file={cache_file}"); sys.stdout.flush()

    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            print(f"[DBG] get_market 缓存命中"); sys.stdout.flush()
            print(f"[缓存] 市场数据已加载: {cache_file}")
            return df
        except Exception as e:
            print(f"[缓存] 市场数据读取失败: {e}")

    print("[DBG] get_market 准备调用API"); sys.stdout.flush()

    daily = pro.daily(
        trade_date=TRADE_DATE
    )

    basic = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,name'
    )

    mv = pro.daily_basic(
        trade_date=TRADE_DATE,
        fields='ts_code,total_mv'
    )

    df = daily.merge(
        basic,
        on='ts_code',
        how='left'
    )

    df = df.merge(
        mv,
        on='ts_code',
        how='left'
    )

    df.to_csv(cache_file, index=False)
    print(f"[缓存] 市场数据已保存: {cache_file}")

    return df

##==========缓存代码
def init_db():
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_result (
                date TEXT,
                rank INTEGER,
                code TEXT,
                name TEXT,
                close REAL,
                amount REAL,
                score REAL,
                theme TEXT
            )
        """)
        # 兼容旧表：如果表存在但没有theme列，则添加
        try:
            cursor.execute("ALTER TABLE stock_result ADD COLUMN theme TEXT")
        except:
            pass  # 列已存在，忽略
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"数据库初始化失败，跳过: {e}")

def save_result(df):
    try:
        conn = sqlite3.connect(DB_PATH)
        today = TRADE_DATE
        # 清理当天旧数据（避免重复）
        conn.execute(
            "DELETE FROM stock_result WHERE date=?",
            (today,)
        )
        for i, row in enumerate(df.itertuples()):
            conn.execute("""
                INSERT INTO stock_result
                (date, rank, code, name, close, amount, score, theme)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today,
                i + 1,
                getattr(row, "代码", ""),
                getattr(row, "名称", ""),
                float(getattr(row, "现价", 0)) if getattr(row, "现价", 0) not in ['', None] else 0.0,
                float(getattr(row, "成交额", 0)) if getattr(row, "成交额", 0) not in ['', None] else 0.0,
                float(getattr(row, "总排序评分", 0)) if getattr(row, "总排序评分", 0) not in ['', None] else 0.0,
                getattr(row, "所属主题", "")
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"保存结果到数据库失败，跳过: {e}")

def load_history(days=10):
    try:
        # 尝试从旧目录和新目录都读取数据
        dataframes = []
        print(DB_PATH)
        if os.path.exists(DB_PATH):
            try:
                conn = sqlite3.connect(DB_PATH)
                today = TRADE_DATE
                start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
                query = f"""
                    SELECT *
                    FROM stock_result
                    WHERE date >= '{start_date}'
                    AND date < '{today}'
                    ORDER BY date DESC, rank ASC
                """
                df_new = pd.read_sql(query, conn)
                conn.close()
                if not df_new.empty:
                    dataframes.append(df_new)
                    print(f"从新目录加载历史数据: {len(df_new)} 条")
            except Exception as e:
                print(f"从新目录加载历史数据失败: {e}")
        
        # 合并数据
        if len(dataframes) > 0:
            df_new = pd.concat(dataframes, ignore_index=True)
            # 去重
            df_new = df_new.drop_duplicates(subset=['date', 'code'], keep='last')
            df_new = df_new.sort_values(['date', 'rank'], ascending=[False, True])
            return df_new
        
        else:
            print("未找到任何历史数据")
            return pd.DataFrame(columns=['date', 'rank', 'code', 'name', 'close', 'amount', 'score'])
    except Exception as e:
        print(f"加载历史数据失败，返回空数据: {e}")
        return pd.DataFrame(columns=['date', 'rank', 'code', 'name', 'close', 'amount', 'score'])




# ======================================================
# 筹码分布（cyq_chips / cyq_perf）—— 压力/突破判断的核心数据源
# 采用文件缓存：chip_{ts_code}_{trade_date}_chips.csv / _perf.csv
# 一旦生成不会变化，命中缓存即可省 2 次 API 调用 + 节流等待
# ======================================================
def get_chip_distribution(ts_code, trade_date, current_price=None):
    """基于 cyq_chips + cyq_perf 计算筹码分布
    返回: dict，包含：
      - above_chips_pct: 当前价上方套牢盘比例(%)
      - below_chips_pct: 当前价下方获利盘比例(%)
      - avg_cost: 加权平均成本
      - winner_rate: 盈利筹码占比(%)
      - his_low / his_high: 历史最低/最高
      - pressure_peaks: 上方最大的3个筹码峰 [(价格, 占比), ...]
      - support_peaks: 下方最大的3个筹码峰 [(价格, 占比), ...]
      - cost_50pct: 成本中位数
      - cost_85pct / cost_95pct: 压力成本带
    """
    result = {}
    # ========= 缓存文件路径 =========
    td = str(trade_date).replace('-', '')
    chip_chips_file = os.path.join(CACHE_DIR, f"chip_{ts_code}_{td}_chips.csv")
    chip_perf_file = os.path.join(CACHE_DIR, f"chip_{ts_code}_{td}_perf.csv")

    try:
        # ========= cyq_chips：全量筹码分布（先读缓存，再回源） =========
        df = None
        cache_hit = False
        if os.path.exists(chip_chips_file):
            try:
                df = pd.read_csv(chip_chips_file)
                cache_hit = True
            except Exception as e:
                print(f"  [筹码] 缓存读失败 {ts_code} {td}: {e}")
                df = None

        if df is None or len(df) == 0:
            df = pro.cyq_chips(ts_code=ts_code, trade_date=td)
            time.sleep(0.12)
            # 缓存：仅写当日，非当日（回测）也写
            if df is not None and len(df) > 0:
                try:
                    df.to_csv(chip_chips_file, index=False)
                except Exception:
                    pass
        else:
            # 缓存命中记录（可保留）
            pass

        if df is None or len(df) == 0:
            return result

        if current_price is None:
            # 从 daily 数据取最近收盘价（不走缓存路径，因为 get_hist_data 已有缓存）
            cache_daily = os.path.join(CACHE_DIR, f"{ts_code}.csv")
            if os.path.exists(cache_daily):
                try:
                    d = pd.read_csv(cache_daily)
                    if d is not None and len(d) > 0:
                        current_price = float(d['close'].iloc[-1])
                except Exception:
                    pass
            if current_price is None:
                d = pro.daily(ts_code=ts_code, trade_date=td)
                if d is not None and len(d) > 0:
                    current_price = float(d['close'].iloc[0])
                else:
                    return result

        # 上方套牢盘 vs 下方获利盘
        above = df[df['price'] > current_price]
        below = df[df['price'] <= current_price]
        result['above_chips_pct'] = round(float(above['percent'].sum()), 2)
        result['below_chips_pct'] = round(float(below['percent'].sum()), 2)

        # 上方压力位（取占比最大的前3个）
        if len(above) > 0:
            peaks_above = above.nlargest(3, 'percent')
            result['pressure_peaks'] = [
                (float(r['price']), round(float(r['percent']), 2))
                for _, r in peaks_above.iterrows()
                if r['percent'] >= 0.3  # 过滤零散筹码
            ]
        else:
            result['pressure_peaks'] = []

        # 下方支撑位（取占比最大的前3个）
        if len(below) > 0:
            peaks_below = below.nlargest(3, 'percent')
            result['support_peaks'] = [
                (float(r['price']), round(float(r['percent']), 2))
                for _, r in peaks_below.iterrows()
            ]
        else:
            result['support_peaks'] = []

        # ========= cyq_perf：筹码成本与胜率（先读缓存，再回源） =========
        perf = None
        if os.path.exists(chip_perf_file):
            try:
                perf = pd.read_csv(chip_perf_file)
            except Exception:
                perf = None

        if perf is None or len(perf) == 0:
            perf = pro.cyq_perf(ts_code=ts_code, trade_date=td)
            time.sleep(0.12)
            if perf is not None and len(perf) > 0:
                try:
                    perf.to_csv(chip_perf_file, index=False)
                except Exception:
                    pass

        if perf is not None and len(perf) > 0:
            row = perf.iloc[0]
            result['his_low'] = round(float(row['his_low']), 2)
            result['his_high'] = round(float(row['his_high']), 2)
            result['avg_cost'] = round(float(row['weight_avg']), 2)
            result['winner_rate'] = round(float(row['winner_rate']), 2)
            result['cost_5pct'] = round(float(row['cost_5pct']), 2)
            result['cost_50pct'] = round(float(row['cost_50pct']), 2)
            result['cost_85pct'] = round(float(row['cost_85pct']), 2)
            result['cost_95pct'] = round(float(row['cost_95pct']), 2)

        # ========= 综合压力判断（取代之前的K线高点判断） =========
        # 关键判断: 是否真的"上方无压力"
        above_pct = result.get('above_chips_pct', 0)
        if above_pct < 3:
            result['pressure_level'] = '轻'  # 上方几乎无套牢盘
            result['pressure_desc'] = f'上方筹码仅{above_pct:.1f}%，基本无短期套牢压力'
        elif above_pct < 15:
            result['pressure_level'] = '中'
            result['pressure_desc'] = f'上方筹码{above_pct:.1f}%，存在一定解套压力'
        else:
            result['pressure_level'] = '重'
            result['pressure_desc'] = f'上方筹码{above_pct:.1f}%，上方套牢盘密集，突破需要放量'

        # 突破有效性判断（新增信号）
        avg_cost = result.get('avg_cost', current_price)
        if current_price > avg_cost:
            # 已站上平均成本，筹码整体盈利
            result['breakout_status'] = '已突破筹码平均成本'
        else:
            result['breakout_status'] = '仍在平均成本下方运行'

        # 最近压力位（取上方第一个有显著占比的价格）
        if result['pressure_peaks']:
            result['nearest_pressure'] = result['pressure_peaks'][0][0]
        else:
            # 即使没有大峰，也取最近一个价格
            above_sorted = above[above['price'] > current_price].sort_values('price')
            if len(above_sorted) > 0:
                result['nearest_pressure'] = round(float(above_sorted['price'].iloc[0]), 2)
            else:
                result['nearest_pressure'] = round(float(df['price'].max()), 2)

    except Exception as e:
        # 失败时记录但不抛出，避免影响主流程
        print(f"  [筹码] {ts_code} {td} 获取失败: {e}")
    return result


def calc_tech_indicators(df, ts_code=None, trade_date=None):
    """计算关键技术指标价格（供AI分析使用）
    核心数据源：MA均线 + K线高点（辅助参考）+ 筹码分布 cyq_chips / cyq_perf（主压力判断）
    筹码分布是判断"上方是否真的有套牢盘"的权威数据源，取代仅靠K线高点推测的旧逻辑
    """
    result = {}
    if df is None or len(df) < 5 or not isinstance(df, pd.DataFrame):
        return result

    try:
        close = df['close']
        high = df['high']
        low = df['low'] if 'low' in df.columns else close
        current_price = float(close.iloc[-1])
        result['current_price'] = current_price

        # 均线价格
        result['ma5'] = round(float(close.rolling(5).mean().iloc[-1]), 2) if len(close) >= 5 else current_price
        result['ma10'] = round(float(close.rolling(10).mean().iloc[-1]), 2) if len(close) >= 10 else current_price
        result['ma20'] = round(float(close.rolling(20).mean().iloc[-1]), 2) if len(close) >= 20 else current_price
        result['ma60'] = round(float(close.rolling(60).mean().iloc[-1]), 2) if len(close) >= 60 else current_price

        # =========================
        # K线高点（辅助参考，不作为压力判断主依据）
        # =========================
        def hhv_exclude_today(window):
            if len(high) >= window + 1:
                return float(high.iloc[:-1].tail(window).max())
            elif len(high) >= 2:
                return float(high.iloc[:-1].max())
            else:
                return float(high.max())

        hist_high_20 = hhv_exclude_today(20)
        hist_high_60 = hhv_exclude_today(60)
        hist_high_120 = hhv_exclude_today(120)
        hist_high_250 = hhv_exclude_today(250)
        hist_high_all = float(high.iloc[:-1].max()) if len(high) > 1 else float(high.max())

        result['high_20d'] = round(hist_high_20, 2)
        result['high_60d'] = round(hist_high_60, 2)
        result['high_120d'] = round(hist_high_120, 2)
        result['high_250d'] = round(hist_high_250, 2)
        result['high_all'] = round(hist_high_all, 2)

        # 历史低点
        hist_low_20 = float(low.iloc[:-1].tail(20).min()) if len(close) > 1 else float(low.tail(20).min())
        hist_low_60 = float(low.iloc[:-1].tail(60).min()) if len(close) > 1 else float(low.tail(60).min())
        result['low_20d'] = round(hist_low_20, 2)
        result['low_60d'] = round(hist_low_60, 2)

        # 距各周期高点的百分比
        if hist_high_20 > 0:
            result['dist_to_high20_pct'] = round(((hist_high_20 - current_price) / current_price) * 100, 1)
        if hist_high_60 > 0:
            result['dist_to_high60_pct'] = round(((hist_high_60 - current_price) / current_price) * 100, 1)
        if hist_high_120 > 0:
            result['dist_to_high120_pct'] = round(((hist_high_120 - current_price) / current_price) * 100, 1)
        if hist_high_250 > 0:
            result['dist_to_high250_pct'] = round(((hist_high_250 - current_price) / current_price) * 100, 1)
        if hist_high_all > 0:
            result['dist_to_highall_pct'] = round(((hist_high_all - current_price) / current_price) * 100, 1)

        # =========================
        # 筹码分布（主压力判断依据，cyq_chips / cyq_perf）
        # =========================
        chip_result = {}
        if ts_code and trade_date:
            # 优先从 df 的最后一行取 trade_date
            try:
                td_actual = str(df['trade_date'].iloc[-1]).split(' ')[0] if 'trade_date' in df.columns else trade_date
            except Exception:
                td_actual = trade_date
            chip_result = get_chip_distribution(ts_code, td_actual, current_price)

        if chip_result:
            # 筹码核心数据
            result['above_chips_pct'] = chip_result.get('above_chips_pct', 0)
            result['below_chips_pct'] = chip_result.get('below_chips_pct', 0)
            result['avg_cost'] = chip_result.get('avg_cost', current_price)
            result['winner_rate'] = chip_result.get('winner_rate', 0)
            result['cost_50pct'] = chip_result.get('cost_50pct', current_price)
            result['cost_85pct'] = chip_result.get('cost_85pct', current_price)
            result['cost_95pct'] = chip_result.get('cost_95pct', current_price)
            result['chip_his_high'] = chip_result.get('his_high', 0)
            result['nearest_pressure'] = chip_result.get('nearest_pressure', 0)

            # 压力位（格式化：价格+占比）
            pressure_peaks = chip_result.get('pressure_peaks', [])
            if pressure_peaks:
                result['pressure_peak_1_price'] = pressure_peaks[0][0]
                result['pressure_peak_1_pct'] = pressure_peaks[0][1]
                if len(pressure_peaks) > 1:
                    result['pressure_peak_2_price'] = pressure_peaks[1][0]
                    result['pressure_peak_2_pct'] = pressure_peaks[1][1]
            support_peaks = chip_result.get('support_peaks', [])
            if support_peaks:
                result['support_peak_1_price'] = support_peaks[0][0]
                result['support_peak_1_pct'] = support_peaks[0][1]

            # 综合压力判断（筹码优先）
            result['pressure_level'] = chip_result.get('pressure_level', '未知')
            result['pressure_desc'] = chip_result.get('pressure_desc', '筹码数据不可用')
            result['breakout_status'] = chip_result.get('breakout_status', '')

            # ===== 筹码突破真假评分 =====
            chip_bt = calc_chip_breakthrough_score(chip_result, current_price)
            result['chip_breakthrough_score'] = chip_bt['score']
            result['chip_breakthrough_level'] = chip_bt['level']

            # 保留原有字段名（兼容）
            pct = chip_result.get('above_chips_pct', 0)
            result['has_upper_pressure'] = pct >= 3  # 3%为阈值，不是10%K线标准
            pressure_parts = []
            if pressure_peaks:
                for p_price, p_pct in pressure_peaks[:2]:
                    pressure_parts.append(f"{p_price:.1f}元(占比{p_pct:.1f}%)")
            if pressure_parts:
                result['upper_pressure_desc'] = "；".join(pressure_parts)
            else:
                result['upper_pressure_desc'] = result['pressure_desc']
        else:
            # 筹码不可用时退化为K线判断
            upper_pressure_levels = []
            if hist_high_all > current_price * 1.01:
                pct_all = ((hist_high_all - current_price) / current_price) * 100
                upper_pressure_levels.append(f"全历史高点{result['high_all']:.2f}元(距+{pct_all:.1f}%)")
            if hist_high_250 > current_price * 1.01:
                pct_250 = ((hist_high_250 - current_price) / current_price) * 100
                upper_pressure_levels.append(f"250日高点{result['high_250d']:.2f}元(距+{pct_250:.1f}%)")
            if hist_high_120 > current_price * 1.01:
                pct_120 = ((hist_high_120 - current_price) / current_price) * 100
                upper_pressure_levels.append(f"120日高点{result['high_120d']:.2f}元(距+{pct_120:.1f}%)")
            result['upper_pressure_desc'] = "；".join(upper_pressure_levels) if upper_pressure_levels else "上方无显著历史套牢盘"
            result['has_upper_pressure'] = len(upper_pressure_levels) > 0
            result['pressure_level'] = 'K线估算'
            result['pressure_desc'] = result['upper_pressure_desc']

        # 距20/60日均线百分比
        if result['ma20'] > 0:
            result['dist_to_ma20_pct'] = round(((current_price - result['ma20']) / result['ma20']) * 100, 1)
        if result['ma60'] > 0:
            result['dist_to_ma60_pct'] = round(((current_price - result['ma60']) / result['ma60']) * 100, 1)

        # 近5日K线概况
        recent_5 = df.tail(5)
        if len(recent_5) >= 5:
            result['recent5_high'] = round(float(recent_5['high'].max()), 2)
            result['recent5_low'] = round(float(recent_5['low'].min()), 2) if 'low' in df.columns else round(float(recent_5['close'].min()), 2)
            result['recent5_range_pct'] = round(((result['recent5_high'] - result['recent5_low']) / result['recent5_low']) * 100, 1)

        # 近10日涨跌幅
        if len(df) >= 10:
            price_10d_ago = float(close.iloc[-10])
            if price_10d_ago > 0:
                result['chg_10d_pct'] = round(((current_price - price_10d_ago) / price_10d_ago) * 100, 1)

        # 均线方向
        if len(close) >= 25:
            ma20_prev = float(close.iloc[:-1].tail(20).mean())
            result['ma20_trend'] = '向上' if result['ma20'] > ma20_prev else '向下'
        if len(close) >= 65:
            ma60_prev = float(close.iloc[:-1].tail(60).mean())
            result['ma60_trend'] = '向上' if result['ma60'] > ma60_prev else '向下'

    except Exception as e:
        print(f"  [tech] calc_tech_indicators 异常: {e}")
    return result


def get_tracking_stocks():
    """跟踪近20天历史选股数据，先主题过滤，再技术过滤，最后计算整合评分并排序输出"""
    try:
        # 加载近20天的历史数据（不含TRADE_DATE当天）
        history_df = load_history(days=20)
        
        if history_df.empty:
            return [], "暂无历史数据", ""
        
        # 去重，保留每只股票最近一次出现的信息
        recent_stocks = history_df.drop_duplicates(subset=['code'], keep='first')
        
        # ===== 先进行主题过滤 =====
        print(f"\n[跟踪池] 历史选股共 {len(recent_stocks)} 只，先进行主题过滤...")
        tracking_df = recent_stocks.rename(columns={'code': '代码', 'name': '名称'})
        tracking_df = filter_by_top_themes(tracking_df)
        
        if tracking_df.empty:
            return [], "主题过滤后无股票", ""
        
        # 转换回字典列表
        filtered_stocks = tracking_df.to_dict('records')
        print(f"[跟踪池] 主题过滤后剩余 {len(filtered_stocks)} 只")
        
        # ===== 技术过滤 + 整合评分 =====
        tracking_stocks = []
        
        for row in filtered_stocks:
            ts_code = row['代码']
            stock_name = row['名称']
            
            try:
                # 读取股票K线数据
                cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
                old_cache_file = os.path.join(os.path.dirname(BASE_DIR), "cache_daily", f"{ts_code}.csv")
                
                if os.path.exists(cache_file):
                    df = pd.read_csv(cache_file)
                elif os.path.exists(old_cache_file):
                    df = pd.read_csv(old_cache_file)
                else:
                    continue
                
                df['trade_date'] = df['trade_date'].astype(str)
                df = df[df['trade_date'] <= TRADE_DATE]
                df = df.sort_values('trade_date').reset_index(drop=True)
                
                if len(df) < 20:
                    continue
                
                # 计算均线
                df['ma5'] = df['close'].rolling(window=5).mean()
                df['ma20'] = df['close'].rolling(window=20).mean()
                
                latest = df.iloc[-1]
                current_close = float(latest['close'])
                current_open = float(latest['open']) if 'open' in df.columns else current_close
                current_ma5 = float(latest['ma5'])
                current_ma20 = float(latest['ma20'])
                
                # 过滤0：今日涨停过滤（只做下跌低吸或中小阳线突破）
                # 计算今日涨幅
                if current_open > 0:
                    today_pct = (current_close - current_open) / current_open * 100
                else:
                    today_pct = 0
                
                # 判断是否涨停（根据股票类型）
                is_cyb_kcb = ts_code.startswith('3') or ts_code.startswith('688') or ts_code.startswith('689')
                zt_threshold = 19.8 if is_cyb_kcb else 9.8  # 涨停阈值
                
                # 过滤涨停的股票
                if today_pct >= zt_threshold:
                    print(f"[跟踪池] {ts_code} {stock_name} 今日涨停({today_pct:.1f}%)，跳过")
                    continue
                
                # 过滤1：当前收盘价不能高于最近入库日期的价格10%
                last_db_price = float(row.get('close', 0)) if str(row.get('close', '')).strip() not in ['', 'None'] else 0.0
                if last_db_price > 0 and current_close > last_db_price * 1.10:
                    continue
                
                # 过滤2：股价不能低于20日均线
                if current_close < current_ma20:
                    continue
                
                # 过滤3：5日均线不能低于20日均线
                if current_ma5 < current_ma20:
                    continue
                
                # 计算整合评分
                integrated_score = 0.0
                recommendation = ""
                details = {}
                failure_prob = 0.0
                tech = {}
                
                try:
                    df_hist = get_hist_data(ts_code)
                    if df_hist is not None and len(df_hist) >= 60:
                        integrated_score, recommendation, details, failure_prob = calc_unified_stock_score(
                            df_hist, ts_code
                        )
                        tech = calc_tech_indicators(df_hist, ts_code, TRADE_DATE)
                except Exception as e:
                    print(f"整合评分计算失败 {ts_code}: {e}")
                    continue
                
                # 计算突破信号和二波反转信号
                try:
                    # 今日突破分
                    breakout_result = detect_breakout(ts_code, pro)
                    breakout_score = breakout_result.get('breakout_score', 0)
                    breakout_signal_val = breakout_result.get('signal', '')
                    
                    # 前一个交易日突破分（用于判断是否刚形成突破）
                    prev_breakout_score = 0
                    for offset in range(1, 5):
                        prev_date = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=offset)).strftime('%Y%m%d')
                        prev_breakout = detect_breakout(ts_code, pro, trade_date=prev_date)
                        if prev_breakout.get('signal', '') != '指定日期无数据':
                            prev_breakout_score = prev_breakout.get('breakout_score', 0)
                            break
                    
                                        
                    # 二波信号（形态检测+共振评分二合一）
                    wave2_result = detect_wave2_reversal(ts_code, pro)
                    wave2_pattern = wave2_result.get('pattern', '其他')
                    wave2_score = wave2_result.get('wave2_score', 0)
                    wave2_signal_val = wave2_result.get('signal', '')
                    entry_price = wave2_result.get('entry_price', 0)
                    stop_loss = wave2_result.get('stop_loss', 0)
                    target = wave2_result.get('target', 0)
                    
                    # 根据股票板块类型过滤形态
                    is_cyb_kcb = ts_code.startswith('3') or ts_code.startswith('688') or ts_code.startswith('689')
                    if is_cyb_kcb:
                        valid_patterns = ['V型急跌', '深度回调', '放量回调']
                    else:
                        valid_patterns = ['强势横盘', 'V型急跌', '放量回调']
                    
                    if not wave2_pattern or wave2_pattern == '其他' or wave2_pattern not in valid_patterns:
                        wave2_score = 0
                        wave2_signal_val = '非二波形态'
                    
                    #print(f"[跟踪池] {ts_code} {stock_name}: 突破={breakout_score}分(前日{prev_breakout_score}), 二波={wave2_score}分")
                except Exception as e:
                    print(f"[跟踪池] {ts_code} 信号计算失败: {e}")
                    breakout_score = 0
                    wave2_score = 0
                    breakout_signal_val = ''
                    wave2_signal_val = ''

                tracking_stocks.append({
                    # 添加突破信号和二波信号到字典
                    'breakout_score': breakout_score,
                    'breakout_signal': breakout_signal_val,
                    'wave2_score': wave2_score,
                    'wave2_signal': wave2_signal_val,
                    'wave2_pattern': wave2_pattern,  # 新增：二波形态类型
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'target': target,
                    'code': ts_code,
                    'name': stock_name,
                    'last_date': str(row.get('date', TRADE_DATE)),
                    'last_close': current_close,
                    'last_db_price': last_db_price,
                    '所属主题': row.get('所属主题', ''),
                    '所属状态': row.get('所属状态', ''),
                    '主题趋势分': row.get('主题趋势分', 0),
                    '主题情绪分': row.get('主题情绪分', 0),
                    '非一日游阶段': row.get('非一日游阶段', ''),
                    '确认天数': row.get('确认天数', 0),
                    '龙头序列': row.get('龙头序列', ''),
                    'open_score': integrated_score,
                    'open_recommendation': recommendation,
                    'failure_prob': failure_prob,
                    'trend_score': details.get('趋势强度', 0),
                    'capital_score': details.get('资金健康度', 0),
                    'position_score': details.get('位置安全性', 0),
                    'heat_score': details.get('热度持续性', 0),
                    'fundamental_score': details.get('基本面', 0),
                    'alpha_score': details.get('Alpha评分', 0),
                    'alpha_signal': details.get('Alpha信号', ''),
                    'ma5': tech.get('ma5', current_close),
                    'ma10': tech.get('ma10', current_close),
                    'ma20': tech.get('ma20', current_close),
                    'ma60': tech.get('ma60', current_close),
                    'high20': tech.get('high_20d', current_close),
                    'high60': tech.get('high_60d', current_close),
                    'high120': tech.get('high_120d', current_close),
                    'high250': tech.get('high_250d', current_close),
                    'high_all': tech.get('high_all', current_close),
                    'dist_high20': tech.get('dist_to_high20_pct', 0),
                    'dist_high60': tech.get('dist_to_high60_pct', 0),
                    'dist_high120': tech.get('dist_to_high120_pct', 0),
                    'dist_high250': tech.get('dist_to_high250_pct', 0),
                    'dist_high_all': tech.get('dist_to_highall_pct', 0),
                    'upper_pressure': tech.get('upper_pressure_desc', ''),
                    'has_upper_pressure': '有' if tech.get('has_upper_pressure', False) else '无',
                    'ma20_trend': tech.get('ma20_trend', ''),
                    'ma60_trend': tech.get('ma60_trend', ''),
                    'chg_10d': tech.get('chg_10d_pct', 0),
                    # 筹码分布（cyq_chips / cyq_perf）主判断依据
                    'chip_above_pct': tech.get('above_chips_pct', -1),
                    'chip_below_pct': tech.get('below_chips_pct', -1),
                    'chip_avg_cost': tech.get('avg_cost', current_close),
                    'chip_winner': tech.get('winner_rate', 0),
                    'chip_cost_50pct': tech.get('cost_50pct', current_close),
                    'chip_cost_85pct': tech.get('cost_85pct', current_close),
                    'chip_cost_95pct': tech.get('cost_95pct', current_close),
                    'chip_pressure_level': tech.get('pressure_level', 'K线估算'),
                    'chip_pressure_desc': tech.get('pressure_desc', ''),
                    'chip_breakout_status': tech.get('breakout_status', ''),
                    'chip_nearest_pressure': tech.get('nearest_pressure', 0),
                    'chip_his_high': tech.get('chip_his_high', 0),
                    # 筹码突破真假评分
                    'chip_breakthrough_score': tech.get('chip_breakthrough_score', 50),
                    'chip_breakthrough_level': tech.get('chip_breakthrough_level', ''),
                    # 短线胜率评分
                    'short_win_score': 0,
                    'short_stage': '',
                    'short_signal': '',
                    'short_key_risk': '',
                    'short_pattern_type': '',
                    'short_ma_structure': '',
                    'short_rsi_signal': '',
                    'short_macd_signal': '',
                    'short_kdj_signal': '',
                    'short_volume_signal': '',
                    'short_resonance': '',
                    # 历史知名度（供AI判断主题地位：龙头/中军/弹性/跟风）
                    'yri_total_score': details.get('YRI历史总分', 0),
                    'yri_tags': details.get('YRI标签', ''),
                    'yri_level': details.get('YRI等级', ''),
                    'yri_portrait': details.get('YRI股性画像', ''),
                    'yri_avg_amount_wan': details.get('YRI日均成交(万)', 0),
                    'yri_zt_count': details.get('YRI涨停次数', 0),
                    'yri_max_consec_zt': details.get('YRI最大连板', 0),
                })
                
                # 计算短线胜率（含筹码分布）
                try:
                    t_price = tracking_stocks[-1].get('close', 0) or tracking_stocks[-1].get('收盘价', 0)
                    t_above_pct = -1
                    try:
                        t_chip = get_chip_distribution(ts_code, TRADE_DATE, t_price)
                        t_above_pct = t_chip.get('above_chips_pct', -1)
                    except Exception:
                        t_above_pct = -1

                    short_result = calculate_short_term_win_score(ts_code, pro, t_above_pct)
                    tracking_stocks[-1]['short_win_score'] = short_result.get('win_score', 0)
                    tracking_stocks[-1]['short_stage'] = short_result.get('stage', '')
                    tracking_stocks[-1]['short_signal'] = short_result.get('signal', '')
                    tracking_stocks[-1]['short_key_risk'] = short_result.get('key_risk', '')
                    tracking_stocks[-1]['short_pattern_type'] = short_result.get('pattern_type', '')
                    tracking_stocks[-1]['short_ma_structure'] = short_result.get('ma_structure', '')
                    tracking_stocks[-1]['short_rsi_signal'] = short_result.get('rsi_signal', '')
                    tracking_stocks[-1]['short_macd_signal'] = short_result.get('macd_signal', '')
                    tracking_stocks[-1]['short_kdj_signal'] = short_result.get('kdj_signal', '')
                    tracking_stocks[-1]['short_volume_signal'] = short_result.get('volume_signal', '')
                    tracking_stocks[-1]['short_resonance'] = short_result.get('signal_resonance', '')
                except Exception as e:
                    print(f"短线胜率计算失败 {ts_code}: {e}")
            except Exception as e:
                continue
        
        # 调试：打印过滤前的股票及分数
        print(f"\n[跟踪池] 技术过滤后剩余 {len(tracking_stocks)} 只")
        
        # 按Alpha评分从高到低排序，取前5只（过滤非二波）
        tracking_stocks = [s for s in tracking_stocks if s.get('wave2_signal', '') != '非二波形态']
        tracking_stocks = sorted(tracking_stocks, key=lambda x: -x['wave2_score'])[:5]
        
        # 生成AI需要的文本格式（精简版）
        lines = []
        lines.append("=" * 60)
        lines.append("🔥 跟踪分析股票池")
        lines.append("=" * 60)
        if tracking_stocks:
            for i, stock in enumerate(tracking_stocks, 1):
                alpha_val = stock.get('alpha_score', 0)
                alpha_sig = stock.get('alpha_signal', '')
                alpha_str = f" (Alpha={alpha_val:.1f} {alpha_sig})" if alpha_sig else f" (Alpha={alpha_val:.1f})"
                price = stock.get('last_close', 0)
                in_date = stock.get('last_date', '')
                lines.append(f"【跟踪第{i}名】{stock['name']} ({stock['code']}) 现价={price:.2f} 入库日={in_date}{alpha_str}")
                # 所属主题 + 非一日游阶段 + 确认天数 + 龙头序列
                cycle = stock.get('非一日游阶段', '') or stock.get('所属状态', '')
                confirm_days = stock.get('确认天数', 0)
                cycle_str = f"非一日游:{cycle}" if cycle else ""
                days_str = f"{confirm_days}天" if confirm_days > 0 else ""
                leader_seq = stock.get('龙头序列', '')
                leader_str = f"龙头:{leader_seq}" if leader_seq else ""
                info_parts = [stock.get('所属主题', '')]
                if cycle_str: info_parts.append(cycle_str)
                if days_str: info_parts.append(days_str)
                if leader_str: info_parts.append(leader_str)
                lines.append(f"  主题: {' | '.join(info_parts)}")
                # 二波形态 + 二波评分
                wave2_score = stock.get('wave2_score', 0)
                wave2_pattern = stock.get('wave2_pattern', '')
                wave2_signal = stock.get('wave2_signal', '')
                entry_price = stock.get('entry_price', 0)
                stop_loss = stock.get('stop_loss', 0)
                target = stock.get('target', 0)
                price_str = f" 入场={entry_price:.2f} 止损={stop_loss:.2f} 目标={target:.2f}" if entry_price > 0 else ''
                lines.append(f"  二波: {wave2_pattern} 评分={wave2_score} | {wave2_signal}{price_str}")
                # 突破信号
                breakout_signal = stock.get('breakout_signal', '')
                breakout_score = stock.get('breakout_score', 0)
                if breakout_signal:
                    lines.append(f"  突破: 评分={breakout_score} | {breakout_signal}")
                # YRI辨识度
                yri_total = stock.get('yri_total_score', 0)
                if yri_total > 0:
                    yri_tags = stock.get('yri_tags', '')
                    yri_max_lb = stock.get('yri_max_consec_zt', 0)
                    lines.append(f"  YRI: 总分={yri_total:.0f} 最大连板={yri_max_lb}板 标签={yri_tags}")
                lines.append("")
            lines.append("=" * 60)
        #print(lines)
        return tracking_stocks, "\n".join(lines), ""
    except Exception as e:
        print(f"筛选跟踪分析个股失败: {e}")
        import traceback
        traceback.print_exc()
        return [], "数据加载失败", ""

# ======================================================
# 筹码突破真假判断评分（基于 cyq_chips / cyq_perf）
# 判断突破是否健康：真突破vs假突破
# 返回 0-100 分，越高代表真突破可信度越高
# ======================================================
def calc_chip_breakthrough_score(chip_result, current_price):
    """
    基于筹码分布数据，计算短线真突破可信度评分
    
    核心逻辑：
    - 真突破：上方套牢盘少、集中度健康、筹码已获利、无密集压力峰
    - 假突破：上方套牢盘多、筹码分散、avg_cost在上方、压力峰密集
    
    参数：
        chip_result: get_chip_distribution 返回值
        current_price: 当前价
    返回：
        dict: {score, level, factors}  score=0-100
    """
    if not chip_result or not current_price or current_price <= 0:
        return {'score': 50, 'level': '未知', 'factors': {}, 'judgement': '数据不足'}
    
    factors = {}
    
    # ===== 因子1: 上方套牢盘比例（weight=30） =====
    above_pct = chip_result.get('above_chips_pct', 100)
    if above_pct < 3:
        s1 = 100
    elif above_pct < 8:
        s1 = 85
    elif above_pct < 15:
        s1 = 70
    elif above_pct < 25:
        s1 = 50
    elif above_pct < 40:
        s1 = 30
    else:
        s1 = 10
    factors['above_chips'] = {'score': s1, 'weight': 30, 'value': above_pct}
    
    # ===== 因子2: 盈利筹码比例（weight=20） =====
    winner = chip_result.get('winner_rate', 0)
    if winner > 85:
        s2 = 100
    elif winner > 70:
        s2 = 80
    elif winner > 55:
        s2 = 60
    elif winner > 40:
        s2 = 40
    elif winner > 25:
        s2 = 20
    else:
        s2 = 0
    factors['winner_rate'] = {'score': s2, 'weight': 20, 'value': winner}
    
    # ===== 因子3: 当前价与平均成本距离（weight=20） =====
    avg_cost = chip_result.get('avg_cost', current_price)
    if avg_cost > 0:
        dist_ratio = (current_price - avg_cost) / avg_cost * 100
        if dist_ratio > 15:
            s3 = 100
        elif dist_ratio > 8:
            s3 = 80
        elif dist_ratio > 3:
            s3 = 60
        elif dist_ratio > -2:
            s3 = 50
        elif dist_ratio > -10:
            s3 = 30
        else:
            s3 = 10
    else:
        s3 = 50
    factors['cost_distance'] = {'score': s3, 'weight': 20, 'value': round(dist_ratio, 1)}
    
    # ===== 因子4: 最近压力位距离（weight=15） =====
    nearest_p = chip_result.get('nearest_pressure', 0)
    if nearest_p > 0:
        dist_to_pressure = (nearest_p - current_price) / current_price * 100
        if dist_to_pressure < 0:
            s4 = 100
        elif dist_to_pressure < 2:
            s4 = 30
        elif dist_to_pressure < 5:
            s4 = 50
        elif dist_to_pressure < 10:
            s4 = 70
        else:
            s4 = 85
    else:
        s4 = 80
    factors['pressure_distance'] = {'score': s4, 'weight': 15,
                                     'value': round(dist_to_pressure, 1) if nearest_p > 0 else 999}
    
    # ===== 因子5: 集中度（weight=15） =====
    cost_85 = chip_result.get('cost_85pct', 0)
    cost_95 = chip_result.get('cost_95pct', 0)
    if cost_85 > 0 and cost_95 > 0:
        spread_pct = (cost_95 - cost_85) / cost_85 * 100
        if spread_pct < 8:
            s5 = 100
        elif spread_pct < 15:
            s5 = 80
        elif spread_pct < 25:
            s5 = 60
        elif spread_pct < 40:
            s5 = 40
        else:
            s5 = 20
    else:
        s5 = 50
    factors['concentration'] = {'score': s5, 'weight': 15,
                                 'value': round(spread_pct, 1) if cost_85 > 0 and cost_95 > 0 else 999}
    
    # ===== 综合评分 =====
    total_weight = sum(f['weight'] for f in factors.values())
    total_score = sum(f['score'] * f['weight'] for f in factors.values()) / total_weight if total_weight > 0 else 50
    total_score = round(max(0, min(100, total_score)), 1)
    
    # 判断等级
    if total_score >= 85:
        level = '真突破'
    elif total_score >= 70:
        level = '大概率真突破'
    elif total_score >= 55:
        level = '中性偏多'
    elif total_score >= 40:
        level = '中性偏空'
    elif total_score >= 25:
        level = '很可能假突破'
    else:
        level = '假突破'
    
    return {
        'score': total_score,
        'level': level,
        'factors': factors,
        'judgement': level
    }

# =========================
# 涨跌停数据（替代 emotion.get_limit_stats）
# =========================
def get_limit_stats():
    """获取涨跌停数据，替代原 emotion.get_limit_stats
    优化：优先使用收盘后的实际涨跌停数据，避免盘中触板数据干扰
    """
    try:
        print("开始获取涨跌停数据...")
        zt_codes = []
        dt_codes = []
        broken_rate = 0.0

        # 方法1：使用每日行情数据计算真实的涨跌停（收盘价）
        try:
            # 获取当日所有股票的收盘价和涨跌幅
            daily = pro.daily(trade_date=TRADE_DATE)
            if daily is not None and not daily.empty:
                # 计算涨跌停阈值（简化版：主板10%，科创板/创业板20%）
                daily['is_kcb'] = daily['ts_code'].str.startswith(('688', '301'))
                daily['is_cn'] = daily['ts_code'].str.startswith('300')
                daily['limit_up'] = daily.apply(
                    lambda x: 20.0 if x['is_kcb'] or x['is_cn'] else 10.0, axis=1
                )
                daily['limit_down'] = -daily['limit_up']
                
                # 真实涨停：收盘价涨幅接近涨停价（>=99%的涨停幅度）
                zt_mask = (daily['pct_chg'] >= daily['limit_up'] * 0.99) & (daily['pct_chg'] < daily['limit_up'] + 0.1)
                # 真实跌停：收盘价跌幅接近跌停价
                dt_mask = (daily['pct_chg'] <= daily['limit_down'] * 0.99) & (daily['pct_chg'] > daily['limit_down'] - 0.1)
                
                zt_codes = daily[zt_mask]['ts_code'].tolist()
                dt_codes = daily[dt_mask]['ts_code'].tolist()
                
                print(f"涨停(真实收盘): {len(zt_codes)}只")
                print(f"跌停(真实收盘): {len(dt_codes)}只")
                
                # 获取炸板数据（盘中触及涨停但未封住）
                try:
                    limit_df = pro.limit_list_d(trade_date=TRADE_DATE)
                    if limit_df is not None and not limit_df.empty:
                        # limit='D'表示最终封住, limit='Z'表示炸板
                        zhaban_codes = limit_df[limit_df['limit'] == 'Z']['ts_code'].astype(str).tolist()
                        zhaban_count = len(zhaban_codes)
                        
                        # 炸板率 = 炸板数 ÷ (封住数 + 炸板数)
                        total_touch = len(zt_codes) + zhaban_count
                        if total_touch > 0:
                            broken_rate = (zhaban_count / total_touch) * 100
                            print(f"炸板率: {broken_rate:.1f}% (炸板{zhaban_count}只/触及涨停{total_touch}只)")
                except Exception as e:
                    print(f"获取炸板数据失败: {e}")
                    
        except Exception as e:
            print(f"方法1失败: {e}")

        # 如果以上方法都失败，使用ths接口作为备选（但不作为主要数据源）
        if not zt_codes and not dt_codes:
            print("[备选] 使用ths接口...")
            try:
                ths_zt = pro.limit_list_ths(trade_date=TRADE_DATE, limit_type='涨停池')
                if ths_zt is not None and not ths_zt.empty:
                    zt_codes = ths_zt['ts_code'].astype(str).tolist()
                    print(f"涨停(ths备选): {len(zt_codes)}只")
            except Exception as e:
                print(f"ths涨停失败: {e}")

            try:
                ths_dt = pro.limit_list_ths(trade_date=TRADE_DATE, limit_type='跌停池')
                if ths_dt is not None and not ths_dt.empty:
                    dt_codes = ths_dt['ts_code'].astype(str).tolist()
                    print(f"跌停(ths备选): {len(dt_codes)}只")
            except Exception as e:
                print(f"ths跌停失败: {e}")

        return {
            "zt_count": len(zt_codes),
            "dt_count": len(dt_codes),
            "zt_codes": zt_codes,
            "dt_codes": dt_codes,
            "broken_rate": round(broken_rate, 1)
        }
    except Exception as e:
        print("获取涨跌停失败:", e)
        return {"zt_count": 0, "dt_count": 0, "zt_codes": [], "dt_codes": [], "broken_rate": 0}

def calc_max_limit_height():
    """计算最高连板高度，替代原 emotion.calc_max_limit_height"""
    try:
        # 优先使用 akshare 免费接口
        import akshare as ak
        try:
            # akshare 1.18.x 版本使用 stock_zt_pool_em
            zt_df = ak.stock_zt_pool_em(date=TRADE_DATE)
            if zt_df is not None and not zt_df.empty:
                # 提取连板数并转换为整数
                if '连板数' in zt_df.columns:
                    zt_df['连板数'] = pd.to_numeric(zt_df['连板数'], errors='coerce').fillna(1).astype(int)
                    max_lb = zt_df['连板数'].max()
                    print(f"[连板高度] akshare获取成功: 最高连板 {max_lb} 板")
                    return int(max_lb)
                elif '连扳数' in zt_df.columns:  # 兼容不同版本的字段名
                    zt_df['连扳数'] = pd.to_numeric(zt_df['连扳数'], errors='coerce').fillna(1).astype(int)
                    max_lb = zt_df['连扳数'].max()
                    print(f"[连板高度] akshare获取成功: 最高连板 {max_lb} 板")
                    return int(max_lb)
        except Exception as ak_error:
            print(f"[连板高度] akshare获取失败: {ak_error}")
        
        # akshare失败时，尝试 tushare pro 接口
        if pro is not None:
            zt_df = pro.limit_step(trade_date=TRADE_DATE)
            if zt_df is not None and not zt_df.empty:
                if 'nums' in zt_df.columns:
                    max_lb = zt_df['nums'].fillna(1).astype(int).max()
                    return int(max_lb)
            return 1
        
        # 都失败时返回默认值
        return 3
    except Exception as e:
        print(f"[连板高度] 计算失败: {e}")
        return 3






# =========================
# 主题过滤：以60天平均综合分前15 + 当日前10为主题范围筛选
# =========================
def filter_by_top_themes(result_df, top_n=10):
    """
    主题筛选
    
    核心逻辑：
    1. 统计60天内各主题的平均综合分，取前15名
    2. 叠加当日前10热门的主题
    
    参数：
        result_df: 待过滤的股票DataFrame
        top_n: 主题综合排名取前N名
    
    返回：
        过滤后的DataFrame，注入主题相关字段
    """
    if result_df.empty:
        return result_df

    # 1. 从 theme_trend_sentiment.db 读取过去60天数据
    db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.db')
    if not os.path.exists(db_path):
        print("[主题过滤] 数据库不存在，跳过过滤")
        return result_df

    try:
        conn = sqlite3.connect(db_path)
        
        # 获取过去60天的所有交易日（从旧到新排序，基于TRADE_DATE回溯）
        trade_dates_df = pd.read_sql(
            f"SELECT DISTINCT trade_date FROM theme_scores "
            f"WHERE trade_date <= '{TRADE_DATE}' "
            f"ORDER BY trade_date ASC LIMIT 60",
            conn
        )
        all_trade_dates = trade_dates_df['trade_date'].tolist()
        
        if not all_trade_dates:
            print("[主题过滤] 无历史数据，跳过过滤")
            conn.close()
            return result_df
        
        total_days = len(all_trade_dates)
        print(f"[主题过滤] 分析最近 {total_days} 个交易日（截至{TRADE_DATE}）")
        
        # 以最近10天为"近期"窗口
        recent_window = min(10, total_days // 3)  # 至少取1/3时间
        recent_dates = set(all_trade_dates[-recent_window:])  # 最近N天
        early_dates = set(all_trade_dates[:-recent_window])    # 之前的天
        
        # 数据结构
        # theme_stats[theme] = {
        #     'total_top5_count': 总上榜次数,
        #     'recent_top5_count': 近N天上榜次数,
        #     'early_top5_count': 之前上榜次数,
        #     'first_seen_idx':  首次出现索引,
        #     'consecutive_high_sentiment': 连续高潮天数,
        #     'sentiment_scores': [近N天情绪分],
        #     'latest_state': 最新状态,
        #     'latest_trend': 最新趋势分,
        #     'latest_sentiment': 最新情绪分,
        #     'latest_composite': 最新综合分,
        #     'last_3_sentiment': 最近3天情绪分,
        # }
        theme_stats = {}
        
        for day_idx, trade_date in enumerate(all_trade_dates):
            day_df = pd.read_sql(
                f"SELECT theme, trend_score, sentiment_score, composite_score, theme_state "
                f"FROM theme_scores WHERE trade_date = '{trade_date}'",
                conn
            )
            if day_df.empty:
                continue
            
            # 按综合分排序取TOP10（扩大窗口，避免半导体类独占导致其他主题被过滤）
            day_df = day_df.sort_values('composite_score', ascending=False).head(10)
            
            for _, row in day_df.iterrows():
                theme = row['theme']
                
                if theme not in theme_stats:
                    theme_stats[theme] = {
                        'total_top5_count': 0,
                        'recent_top5_count': 0,
                        'early_top5_count': 0,
                        'first_seen_idx': day_idx,
                        'consecutive_high_sentiment': 0,
                        'sentiment_scores': [],
                        'latest_state': '',
                        'latest_trend': 0,
                        'latest_sentiment': 0,
                        'latest_composite': 0,
                        'last_3_sentiment': [],
                    }
                
                theme_stats[theme]['total_top5_count'] += 1
                
                # 区分近期和早期
                if trade_date in recent_dates:
                    theme_stats[theme]['recent_top5_count'] += 1
                else:
                    theme_stats[theme]['early_top5_count'] += 1
                
                # 记录情绪分数用于检测高潮（带上日期）
                sentiment = float(row.get('sentiment_score', 0) or 0)
                theme_stats[theme]['sentiment_scores'].append((trade_date, sentiment))
                
                # 保存最新数据（仅来自TOP5记录，可能不是最新日期的）
                theme_stats[theme]['latest_state'] = row.get('theme_state', '')
                theme_stats[theme]['latest_trend'] = float(row.get('trend_score', 0) or 0)
                theme_stats[theme]['latest_sentiment'] = sentiment
                theme_stats[theme]['latest_composite'] = float(row.get('composite_score', 0) or 0)
        
        # 补充：用最新交易日（TRADE_DATE）的数据覆盖所有主题的latest_*字段
        # 因为TOP5记录可能不是最新日期（已退潮主题最后进TOP5可能很久以前）
        latest_date = TRADE_DATE  # 直接用TRADE_DATE，all_trade_dates[-1]因LIMIT截断可能不是最新
        if latest_date:
            try:
                extra_conn = sqlite3.connect(db_path)
                latest_day_df = pd.read_sql(
                    f"SELECT theme, trend_score, sentiment_score, composite_score, theme_state "
                    f"FROM theme_scores WHERE trade_date = '{latest_date}'",
                    extra_conn
                )
                extra_conn.close()
                for _, row in latest_day_df.iterrows():
                    theme = row['theme']
                    if theme in theme_stats:
                        # 用最新交易日的数据覆盖，无论是否在TOP5中
                        theme_stats[theme]['latest_trend'] = float(row.get('trend_score', 0) or 0)
                        theme_stats[theme]['latest_sentiment'] = float(row.get('sentiment_score', 0) or 0)
                        theme_stats[theme]['latest_composite'] = float(row.get('composite_score', 0) or 0)
                        theme_stats[theme]['latest_state'] = row.get('theme_state', '')
                    else:
                        # 即使从未进入TOP5，也保留其最新数据供参考
                        theme_stats[theme] = {
                            'total_top5_count': 0,
                            'recent_top5_count': 0,
                            'early_top5_count': 0,
                            'first_seen_idx': 9999,
                            'consecutive_high_sentiment': 0,
                            'sentiment_scores': [],
                            'latest_state': row.get('theme_state', ''),
                            'latest_trend': float(row.get('trend_score', 0) or 0),
                            'latest_sentiment': float(row.get('sentiment_score', 0) or 0),
                            'latest_composite': float(row.get('composite_score', 0) or 0),
                        }
            except Exception as e:
                print(f"[主题过滤] 补充最新交易日数据失败: {e}")
        
        conn.close()
        
        if not theme_stats:
            print("[主题过滤] 无主题数据，跳过过滤")
            return result_df
        
        # 2. 计算60天平均趋势分，用于排名筛选
        # 从数据库获取每个主题所有日期的趋势分
        conn = sqlite3.connect(db_path)
        avg_trend_query = f"""
            SELECT theme, AVG(trend_score) as avg_trend, AVG(composite_score) as avg_composite
            FROM theme_scores 
            WHERE trade_date <= '{TRADE_DATE}' AND trade_date >= '{all_trade_dates[0]}'
            GROUP BY theme
        """
        avg_trend_df = pd.read_sql(avg_trend_query, conn)
        avg_trend_map = {}
        for _, row in avg_trend_df.iterrows():
            avg_trend_map[row['theme']] = {
                'avg_trend': float(row['avg_trend'] or 0),
                'avg_composite': float(row['avg_composite'] or 0),
            }
        conn.close()
        
        # 3. 筛选主题：白名单 + 60天平均综合分前15 + 本交易日综合分前10
        tech_mainline_whitelist = {
            '人形机器人', 'AI算力链', 'AI服务器与算力基建', 'AI芯片',
            'AI终端', '半导体设备', '半导体制造', '数据中心瓶颈硬件链',
            '半导体材料', '半导体封测', '存储芯片', '先进封装', '先进封装材料' 
        }
        
        # 60天平均综合分排名前15（用 avg_composite 而非趋势分）
        sorted_by_avg = sorted(
            avg_trend_map.items(), key=lambda x: -x[1]['avg_composite']
        )[:15]
        avg_top15 = {t[0] for t in sorted_by_avg}
        
        # 本交易日综合分排名前15
        today_composites = {}
        for theme, stats in theme_stats.items():
            today_composites[theme] = stats['latest_composite']
        sorted_today = sorted(today_composites.items(), key=lambda x: -x[1])[:15]
        today_top15 = {t[0] for t in sorted_today}
        
        # 合并：白名单主题 + 60天平均综合分前15 + 本交易日综合分前15
        keep_themes = tech_mainline_whitelist | avg_top15 | today_top15

        # ========== 非一日游确认主题扩展 ==========
        # 调用 analyze_non_daytrip_themes 获取近20天确认的主题
        try:
            import theme_trend_sentiment_score as theme_ts
            non_daytrip_result = theme_ts.analyze_non_daytrip_themes(TRADE_DATE, ndays=20)
            non_daytrip_confirmed = non_daytrip_result.get("confirmed", [])
            non_daytrip_details = non_daytrip_result.get("details_by_theme", {})

            # 将非一日游确认主题加入保留列表
            confirmed_themes = {d["theme"] for d in non_daytrip_confirmed}
            newly_added = confirmed_themes - keep_themes
            if newly_added:
                print(f"[非一日游] 新增 {len(newly_added)} 个确认主题: {sorted(newly_added)}")
            keep_themes |= confirmed_themes
        except Exception as e:
            print(f"[非一日游] 获取失败: {e}")
            non_daytrip_details = {}
            non_daytrip_confirmed = []

        print(f"\n[主题过滤] 保留{len(keep_themes)}个主题:")
        print(f"  白名单: {len(tech_mainline_whitelist)}个")
        print(f"  60天平均综合分前15: {[t[0] for t in sorted_by_avg]}")
        print(f"  本交易日综合分前15: {[t[0] for t in sorted_today]}")
        if non_daytrip_confirmed:
            print(f"  非一日游确认: {[d['theme'] for d in non_daytrip_confirmed]}")
        print()
        
        # 构建主题状态映射（包含非一日游周期阶段）
        theme_state_map = {}
        for theme in keep_themes:
            if theme in theme_stats:
                d = theme_stats[theme]
                # 从非一日游详情中获取周期阶段
                nd = non_daytrip_details.get(theme, {})
                theme_state_map[theme] = {
                    'theme_state': d.get('latest_state', ''),
                    'trend_score': d.get('latest_trend', 0),
                    'sentiment_score': d.get('latest_sentiment', 0),
                    'composite_score': d.get('latest_composite', 0),
                    # 非一日游周期阶段（启动确认/中期延续/高潮尾声/休眠等待）
                    'cycle_phase': nd.get('cycle_phase', ''),
                    'confirmed_days': nd.get('confirmed_active_days', 0),
                    'leader_sequence': nd.get('leader_sequence', ''),
                }
            else:
                nd = non_daytrip_details.get(theme, {})
                theme_state_map[theme] = {
                    'theme_state': '',
                    'trend_score': 0,
                    'sentiment_score': 0,
                    'composite_score': 0,
                    'cycle_phase': nd.get('cycle_phase', ''),
                    'confirmed_days': nd.get('confirmed_active_days', 0),
                    'leader_sequence': nd.get('leader_sequence', ''),
                }

    except Exception as e:
        print(f"[主题过滤] 读取历史数据失败: {e}")
        import traceback
        traceback.print_exc()
        return result_df

    # 2. 加载主题配置（只保留有效主题）
    theme_cfg = {}
    cfg_path = os.path.join(BASE_DIR, 'theme.json')
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            all_themes = json.load(f).get('HOT_THEMES', {})
            for theme_name in keep_themes:
                if theme_name in all_themes:
                    theme_cfg[theme_name] = all_themes[theme_name]

    # 3. 从 JSON 缓存加载主题-个股映射（由 build_theme_stock_map.py 生成）
    try:
        import theme_trend_sentiment_score as theme_ts
        theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = theme_ts.load_theme_stock_map_from_json()
    except Exception as e:
        print(f"[主题过滤] load_theme_stock_map_from_json 失败: {e}")
        import traceback
        traceback.print_exc()
        return _filter_by_top_themes_fallback(result_df, keep_themes, theme_cfg)

    # 4. 遍历股票，匹配主题并注入主题状态
    keep = []
    matched_themes = []
    match_scores = []
    theme_states_list = []
    theme_trends = []
    theme_sentiments = []
    cycle_phases = []      # 非一日游周期阶段
    confirmed_days_list = []  # 连续确认天数
    leader_sequences_list = []  # 龙头序列

    for _, row in result_df.iterrows():
        ts_code = row['代码']
        stock_name = row.get('名称', '')
        found_theme = ''
        # 只匹配保留的主题（keep_themes），而非全部主题
        for theme_name in keep_themes:
            stocks = theme_stock_map.get(theme_name, {})
            if ts_code in stocks:
                found_theme = theme_name
                break
        if found_theme:
            keep.append(True)
            matched_themes.append(found_theme)
            match_scores.append(100)
            st = theme_state_map.get(found_theme, {})
            theme_states_list.append(st.get("theme_state", ""))
            theme_trends.append(st.get("trend_score", 0))
            theme_sentiments.append(st.get("sentiment_score", 0))
            cycle_phases.append(st.get("cycle_phase", ""))
            confirmed_days_list.append(st.get("confirmed_days", 0))
            leader_sequences_list.append(st.get("leader_sequence", ""))
        else:
            keep.append(False)
            matched_themes.append('')
            match_scores.append(0)
            theme_states_list.append('')
            theme_trends.append(0)
            theme_sentiments.append(0)
            cycle_phases.append('')
            confirmed_days_list.append(0)
            leader_sequences_list.append('')

    # 5. 应用过滤 + 注入字段
    before = len(result_df)
    result_df = result_df[keep].reset_index(drop=True)
    kept_indices = [i for i in range(len(keep)) if keep[i]]
    result_df['所属主题'] = [matched_themes[i] for i in kept_indices]
    result_df['主题匹配度'] = [match_scores[i] for i in kept_indices]
    result_df['所属状态'] = [theme_states_list[i] for i in kept_indices]
    result_df['主题趋势分'] = [theme_trends[i] for i in kept_indices]
    result_df['主题情绪分'] = [theme_sentiments[i] for i in kept_indices]
    result_df['非一日游阶段'] = [cycle_phases[i] for i in kept_indices]
    result_df['确认天数'] = [confirmed_days_list[i] for i in kept_indices]
    result_df['龙头序列'] = [leader_sequences_list[i] for i in kept_indices]

    print(f"[主题过滤] 过滤后 {before} -> {len(result_df)} 只")
    return result_df


def _filter_by_top_themes_fallback(result_df, valid_themes, theme_cfg):
    """降级版主题匹配算法：当match_theme_stocks不可用时使用"""
    print("[主题过滤] 使用降级匹配逻辑")
    
    keep = []
    matched_themes = []
    match_scores = []
    
    for _, row in result_df.iterrows():
        ts_code = row['代码']
        stock_name = row.get('名称', '')
        
        best_theme = ''
        best_score = 0
        
        # 检查核心公司
        for theme_name in valid_themes:
            cfg = theme_cfg.get(theme_name, {})
            core_companies = cfg.get('core_companies', [])
            if stock_name in core_companies:
                best_theme = theme_name
                best_score = 100
                break
        
        if best_theme:
            keep.append(True)
            matched_themes.append(best_theme)
            match_scores.append(best_score)
        else:
            keep.append(False)
            matched_themes.append('')
            match_scores.append(0)
    
    before = len(result_df)
    result_df = result_df[keep].reset_index(drop=True)
    result_df['所属主题'] = [matched_themes[i] for i in range(len(matched_themes)) if keep[i]]
    result_df['主题匹配度'] = [match_scores[i] for i in range(len(match_scores)) if keep[i]]
    
    print(f"[主题过滤] 降级过滤后 {before} -> {len(result_df)} 只")
    return result_df


# =========================
# 主程序
# =========================
def run(target_date=None, simple_mode=False):
    """运行量化选股分析
    
    Args:
        target_date: 目标日期，格式为 'YYYYMMDD'，默认为当前交易日
        simple_mode: 简易模式，只输出个股和评分，不进行AI分析、不发送微信
    """
    global TRADE_DATE
    
    # 如果指定了目标日期，验证并设置
    if target_date:
        target_date = str(target_date)
        TRADE_DATE = validate_trade_date(target_date)
        print(f"\n{'='*60}")
        print(f"[回溯模式] 目标日期: {TRADE_DATE}")
        print(f"{'='*60}\n")
    
    # =========================
    # 主题状态全景（来自主题分析简报）
    # =========================
    print("\n========== 主题状态全景（来自主题分析简报）==========\n")
    REPORT_DIR_TS = os.path.join(BASE_DIR, "..", "report_daily")
    report_path = os.path.join(REPORT_DIR_TS, f"theme_analysis_{TRADE_DATE}.txt")
    sector_text_his = ""
    try:
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                content = f.read()
            sector_text_his = content
        else:
            print(f"  未找到简报文件: {report_path}")
            print("  请先运行 theme_trend_sentiment_score.py 生成分析报告")
            sector_text_his = ""
    except Exception as e:
        print(f"⚠️ 读取主题状态简报失败: {e}")
        sector_text_his = ""
    
    emotion_stage = "强"
    
    # 市场情绪 → 使用 market_analysis.py 的大盘分析
    import importlib
    ma = importlib.import_module("market_analysis")
    ma_results, ma_position, ma_reason, ma_style_allocations, ma_overview = ma.analyze_market(TRADE_DATE)

    # 从数据库获取趋势评分（用于趋势分计算）
    theme_csv = os.path.join(BASE_DIR, "cache_backbone_tushare", "theme_trend_sentiment.csv")
    theme_top3_scores = None
    if os.path.exists(theme_csv):
        try:
            df_theme = pd.read_csv(theme_csv, encoding='utf-8-sig')
            if not df_theme.empty and 'trend_score' in df_theme.columns:
                df_theme = df_theme.sort_values('rank').head(3)
                theme_top3_scores = df_theme['trend_score'].tolist()
        except:
            pass
    
    ts, it, tt = ma.calculate_market_trend_score(ma_results, theme_top3_scores)
    ms, pr, tp = ma.get_market_status_and_position(ts)

    # 构建 emotion_text 用于 DeepSeek 日报
    emotion_lines = ["【大盘分析】"]
    status_icon = "🚀" if "主升浪" in ms else ("📈" if "上升" in ms or "良好" in ms else ("⚠️" if "退潮" in ms or "主跌" in ms else "📊"))
    emotion_lines.append(f"  {status_icon} 市场状态: 【{ms}】")
    emotion_lines.append(f"  总趋势分: {ts:.1f} | 指数趋势: {it:.1f} | 主题趋势: {tt:.1f}")
    emotion_lines.append(f"  建议仓位: {ma_position}%")
    emotion_lines.append("")
    
    if ma_overview:
        ov = ma_overview
        emotion_lines.append(f"【市场概况】")
        emotion_lines.append(f"  上证{ov['sh_index']:.2f}({ov['sh_pct']:+.2f}%) "
                             f"成交{ov['total_amount']:.0f}亿 涨{ov['up_count']}跌{ov['down_count']} "
                             f"涨停{ov['zt_count']}跌停{ov['dt_count']}炸板率{ov['zb_rate']}%")
    emotion_lines.append("")
    emotion_lines.append(f"【各指数分析】")
    for r in ma_results:
        emotion_lines.append(f"  {r['name']}: 趋势{r['trend_score']:.1f}({r['trend_status']}) 情绪{r['sentiment_score']:.1f}({r['sentiment_status']}) 涨跌{r['pct_chg']:+.2f}%")
    emotion_lines.append(f"\n综合建议仓位: {ma_position}%")
    emotion_lines.append(f"理由: {ma_reason}")
    emotion_text = "\n".join(emotion_lines)
    print(emotion_text)

    result = []
    result_dx = []

    # 批量预取：解决高频API调用问题
    # 在循环之前一次性下载所有股票数据到本地缓存

    market = get_market()
    if market is not None and not market.empty:
        all_codes = market['ts_code'].tolist()
        print(f"\n[批量预取] 共 {len(all_codes)} 只股票，开始下载历史数据...")
        batch_prefetch_hist_data(all_codes)
        print(f"[批量预取] 完成，后续循环将命中本地缓存\n")

    total = len(market)

    for idx, row in market.iterrows():

        ts_code = row['ts_code']

        #print(f"[{idx+1}/{total}] {ts_code}")

        try:

            hist = get_hist_data(ts_code)

            if hist is None or len(hist) < 80:
                continue
            #print(f"[{idx+1}/{total}] {ts_code}")
            ok = strategy(
                hist,
                ts_code,
                emotion_stage,
                total_mv=row.get('total_mv', 0)
            )
            
            if ok:

                result.append({
                    '代码': ts_code,
                    '名称': row.get('name', ''),
                    '现价': row.get('close', 0),
                    '涨跌幅': row.get('pct_chg', 0),
                    '成交额': row.get('amount', 0),
                    '总市值（亿元）': row.get('total_mv', 0)/10000,
                    'total_market_cap': row.get('total_mv', 0) * 10000,  # 转换为元
                    'market_cap': row.get('total_mv', 0) * 10000,       # 兼容字段
                })

                print("✅ 命中突破:", ts_code, row.get('name', ''))

            ok2 = strategy_dx(
                hist,
                ts_code,
                emotion_stage,
                total_mv=row.get('total_mv', 0)
            )
            if ok2:

                result_dx.append({
                    '代码': ts_code,
                    '名称': row.get('name', ''),
                    '现价': row.get('close', 0),
                    '涨跌幅': row.get('pct_chg', 0),
                    '成交额': row.get('amount', 0),
                    '总市值（亿元）': row.get('total_mv', 0)/10000,
                    'total_market_cap': row.get('total_mv', 0) * 10000,  # 转换为元
                    'market_cap': row.get('total_mv', 0) * 10000,       # 兼容字段
                })

                print("✅ 命中低吸:", ts_code, row.get('name', ''))
                
        except Exception as e:

            print(ts_code, e)

            continue

    # =========================
    # 输出
    # =========================
    result_df = pd.DataFrame(result)

    if result_df.empty and not result_dx:
        print("无结果")
        return

    # =========================
    # 主题过滤：注入所属主题等字段
    # =========================
    if not result_df.empty:
        result_df = filter_by_top_themes(result_df)
    result_dx_df = pd.DataFrame(result_dx)
    if not result_dx_df.empty:
        result_dx_df = filter_by_top_themes(result_dx_df)

    # =========================
    # 突破股池：统一评分 + 二波形态 + 筹码
    # =========================
    ranked_stocks = []
    for idx, row in result_df.iterrows():
        ts_code = row['代码']
        name = row['名称']
        theme_name = str(row.get('所属主题', ''))
        
        df = get_hist_data(ts_code)
        if df is None or len(df) < 20 or not isinstance(df, pd.DataFrame) or 'close' not in df.columns:
            continue
        
        try:
            today_pct = ((df['close'].iloc[-1] / df['close'].iloc[-2]) - 1) * 100 if len(df) >= 2 else float(row.get('涨跌幅', 0))
            today_amount = 0.0
            today_close = float(df['close'].iloc[-1])
            if 'amount' in df.columns:
                today_amount = round(float(df['amount'].iloc[-1]) / 100000, 2)
            today_turnover = get_cached_turnover(ts_code)
            
            theme_trend_score = float(row.get('主题趋势分', 0))
            theme_sentiment_score = float(row.get('主题情绪分', 0))
            integrated_score, recommendation, details, failure_prob = calc_unified_stock_score(
                df, ts_code, theme_name, theme_trend_score, theme_sentiment_score
            )
            tech = calc_tech_indicators(df, ts_code, TRADE_DATE)
            
            stock_data = {
                '代码': ts_code, '名称': name, '现价': today_close,
                '涨跌幅': today_pct, '成交额': today_amount, '换手率': today_turnover,
                '所属主题': theme_name, '整合评分': integrated_score, '失败概率': failure_prob,
                '推荐理由': recommendation,
                'Alpha评分': details.get('Alpha评分', 0), 'Alpha信号': details.get('Alpha信号', ''),
                '量能爆发': details.get('量能爆发', 0),
                '所属状态': str(row.get('所属状态', '')),
                '主题趋势分': float(row.get('主题趋势分', 0)), '主题情绪分': float(row.get('主题情绪分', 0)),
                '非一日游阶段': str(row.get('非一日游阶段', '')),
                '确认天数': int(row.get('确认天数', 0)),
                '龙头序列': str(row.get('龙头序列', '')),
                '筹码上方套牢盘%': tech.get('above_chips_pct', -1),
                '筹码平均成本': tech.get('avg_cost', today_close),
                '筹码胜率%': tech.get('winner_rate', 0),
                '筹码压力等级': tech.get('pressure_level', 'K线估算'),
                '筹码压力描述': tech.get('pressure_desc', ''),
                '筹码突破状态': tech.get('breakout_status', ''),
                '筹码最近压力位': tech.get('nearest_pressure', 0),
                'YRI历史总分': details.get('YRI历史总分', 0), 'YRI标签': details.get('YRI标签', ''),
                'YRI最大连板': details.get('YRI最大连板', 0),
            }
            ranked_stocks.append(stock_data)
        except Exception as e:
            print(f"[突破评分] {ts_code} {name} 失败: {e}")
            continue
    
    # 每个主题只保留失败概率最低的3只
    theme_groups = {}
    for s in ranked_stocks:
        theme = s['所属主题']
        theme_groups.setdefault(theme, []).append(s)
    filtered_stocks = []
    for theme, stocks in theme_groups.items():
        filtered_stocks.extend(sorted(stocks, key=lambda x: x['失败概率'])[:3])
    ranked_stocks = filtered_stocks
    
    # 突破 + 二波信号 + 筹码增强
    for s in ranked_stocks:
        try:
            current_price = s.get('现价', 0)
            try:
                chip_res = get_chip_distribution(s['代码'], TRADE_DATE, current_price)
                s['筹码上方套牢盘%'] = chip_res.get('above_chips_pct', -1)
                s['筹码平均成本'] = chip_res.get('avg_cost', current_price)
                s['筹码胜率%'] = chip_res.get('winner_rate', 0)
                s['筹码压力描述'] = chip_res.get('pressure_desc', '')
                s['筹码最近压力位'] = chip_res.get('nearest_pressure', 0)
                s['筹码压力等级'] = chip_res.get('pressure_level', 'K线估算')
                s['筹码突破状态'] = chip_res.get('breakout_status', '')
            except Exception:
                pass
            
            # 突破信号
            breakout_result = detect_breakout(s['代码'], pro)
            s['突破信号'] = breakout_result.get('signal', '')
            s['突破评分'] = breakout_result.get('breakout_score', 0)
            
            # 二波形态检测+共振评分
            wave2_result = detect_wave2_reversal(s['代码'], pro)
            s['二波形态'] = wave2_result.get('pattern', '其他')
            s['二波信号'] = wave2_result.get('signal', '')
            s['二波评分'] = wave2_result.get('wave2_score', 0)
            s['入场价'] = wave2_result.get('entry_price', 0)
            s['止损价'] = wave2_result.get('stop_loss', 0)
            s['目标价'] = wave2_result.get('target', 0)
        except Exception:
            s['突破信号'] = ''; s['突破评分'] = 0
            s['二波信号'] = '非二波形态'; s['二波评分'] = 0
    
    # 按整合评分排序
    ranked_stocks = sorted(ranked_stocks, key=lambda x: -x['整合评分'])
    lines = []
    lines.append("=" * 60)
    lines.append("🔥 突破股池 (按整合评分排序)")
    lines.append("=" * 60)
    
    top_stocks = ranked_stocks[:10]
    for i, s in enumerate(top_stocks, 1):
        alpha_val = s.get('Alpha评分', 0)
        alpha_sig = s.get('Alpha信号', '')
        alpha_str = f" (Alpha={alpha_val:.1f} {alpha_sig})" if alpha_sig else f" (Alpha={alpha_val:.1f})"
        lines.append(f"【第{i}名】{s['名称']} ({s['代码']}) 现价={s['现价']:.2f} 涨跌幅={s['涨跌幅']:+.2f}% 成交额={s['成交额']:.2f}亿 量能爆发={s['量能爆发']:.2f}{alpha_str}")
        lines.append(f"  整合评分: {s['整合评分']:.1f} | 失败概率: {s['失败概率']:.1f}%")
        # 主题信息
        cycle = s.get('非一日游阶段', '') or s.get('所属状态', '')
        confirm_days = s.get('确认天数', 0)
        cycle_str = f"非一日游:{cycle}" if cycle else ""
        days_str = f"{confirm_days}天" if confirm_days > 0 else ""
        leader_seq = s.get('龙头序列', '')
        leader_str = f"龙头:{leader_seq}" if leader_seq else ""
        info_parts = [s['所属主题']]
        if cycle_str: info_parts.append(cycle_str)
        if days_str: info_parts.append(days_str)
        if leader_str: info_parts.append(leader_str)
        lines.append(f"  主题: {' | '.join(info_parts)}")
        # 突破
        bs = s.get('突破信号', '')
        bsc = s.get('突破评分', 0)
        lines.append(f"  突破: 评分={bsc} | {bs}" if bs else f"  突破: 评分={bsc}")
        # YRI
        yri_total = s.get('YRI历史总分', 0)
        if yri_total > 0:
            yri_tags = s.get('YRI标签', '')
            yri_lb = s.get('YRI最大连板', 0)
            lines.append(f"  YRI: 总分={yri_total:.0f} 最大连板={yri_lb}板 标签={yri_tags}")
        # 筹码
        chip_pct = s.get('筹码上方套牢盘%', -1)
        if chip_pct >= 0:
            avg_cost = s.get('筹码平均成本', s['现价'])
            winner = s.get('筹码胜率%', 0)
            nearest_p = s.get('筹码最近压力位', 0)
            lines.append(f"  筹码: 成本={avg_cost:.2f}元 套牢={chip_pct:.1f}% 盈利={winner:.1f}% 压力位={nearest_p:.2f}元 {s.get('筹码突破状态','')}")
        lines.append("")
    
    hot_money_open_text = "\n".join(lines)
    print(hot_money_open_text)
    
    icpm_top10_list = []
    for s in ranked_stocks[:10]:
        icpm_top10_list.append({
            'code': s.get('代码', ''),
            'name': s.get('名称', ''),
            'theme': s.get('所属主题', ''),
            'open_score': s.get('整合评分', 0),
        })
        
    # =========================
    # 处理低吸股票池（result_dx）：同突破池一样的评分/技术指标/筹码/短线流程
    # =========================
    dx_ranked_stocks = []
    if not result_dx_df.empty:
        for idx, row in result_dx_df.iterrows():
            ts_code = row['代码']
            name = row['名称']
            theme_name = str(row.get('所属主题', ''))
            
            df = get_hist_data(ts_code)
            if df is None or len(df) < 20 or not isinstance(df, pd.DataFrame) or 'close' not in df.columns:
                continue
            
            try:
                today_pct = ((df['close'].iloc[-1] / df['close'].iloc[-2]) - 1) * 100 if len(df) >= 2 else float(row.get('涨跌幅', 0))
                today_amount = 0.0
                today_close = float(df['close'].iloc[-1])
                if 'amount' in df.columns:
                    today_amount = round(float(df['amount'].iloc[-1]) / 100000, 2)
                today_turnover = get_cached_turnover(ts_code)
                
                theme_trend_score = float(row.get('主题趋势分', 0))
                theme_sentiment_score = float(row.get('主题情绪分', 0))
                integrated_score, recommendation, details, failure_prob = calc_unified_stock_score(
                    df, ts_code, theme_name, theme_trend_score, theme_sentiment_score
                )
                
                tech = calc_tech_indicators(df, ts_code, TRADE_DATE)
                
                stock_data = {
                    '代码': ts_code, '名称': name, '现价': today_close,
                    '涨跌幅': today_pct, '成交额': today_amount, '换手率': today_turnover,
                    '所属主题': theme_name,
                    '整合评分': integrated_score, '失败概率': failure_prob,
                    '推荐理由': recommendation,
                    '趋势强度': details.get('趋势强度', 0), '资金健康度': details.get('资金健康度', 0),
                    '位置安全性': details.get('位置安全性', 0), '热度持续性': details.get('热度持续性', 0),
                    '基本面': details.get('基本面', 0),
                    'Alpha评分': details.get('Alpha评分', 0),
                    'Alpha信号': details.get('Alpha信号', ''),
                    '热榜最佳排名': details.get('热榜最佳排名', 0), '热榜上榜次数': details.get('热榜上榜次数', 0),
                    '所属状态': str(row.get('所属状态', '')),
                    '主题趋势分': float(row.get('主题趋势分', 0)), '主题情绪分': float(row.get('主题情绪分', 0)),
                    '非一日游阶段': str(row.get('非一日游阶段', '')),
                    '确认天数': int(row.get('确认天数', 0)),
                    '龙头序列': str(row.get('龙头序列', '')),
                    '量能爆发': details.get('量能爆发', 0), '突破强度': float(row.get('突破强度', 0)),
                    'MA5价': tech.get('ma5', today_close), 'MA10价': tech.get('ma10', today_close),
                    'MA20价': tech.get('ma20', today_close), 'MA60价': tech.get('ma60', today_close),
                    '20日高点': tech.get('high_20d', today_close), '60日高点': tech.get('high_60d', today_close),
                    '120日高点': tech.get('high_120d', today_close), '250日高点': tech.get('high_250d', today_close),
                    '全历史高点': tech.get('high_all', today_close),
                    '距20日高点%': tech.get('dist_to_high20_pct', 0), '距60日高点%': tech.get('dist_to_high60_pct', 0),
                    '距120日高点%': tech.get('dist_to_high120_pct', 0), '距250日高点%': tech.get('dist_to_high250_pct', 0),
                    '距全历史高点%': tech.get('dist_to_highall_pct', 0),
                    '上方套牢盘描述': tech.get('upper_pressure_desc', ''),
                    '是否有上方套牢盘': '有' if tech.get('has_upper_pressure', False) else '无',
                    '距MA20%': tech.get('dist_to_ma20_pct', 0), '距MA60%': tech.get('dist_to_ma60_pct', 0),
                    'MA20方向': tech.get('ma20_trend', ''), 'MA60方向': tech.get('ma60_trend', ''),
                    '近5日最高': tech.get('recent5_high', today_close), '近5日最低': tech.get('recent5_low', today_close),
                    '近10日涨跌%': tech.get('chg_10d_pct', 0),
                    '筹码上方套牢盘%': tech.get('above_chips_pct', -1),
                    '筹码平均成本': tech.get('avg_cost', today_close),
                    '筹码胜率%': tech.get('winner_rate', 0),
                    '筹码压力等级': tech.get('pressure_level', 'K线估算'),
                    '筹码压力描述': tech.get('pressure_desc', ''),
                    '筹码突破状态': tech.get('breakout_status', ''),
                    '筹码最近压力位': tech.get('nearest_pressure', 0),
                    '筹码支撑1价': tech.get('support_peak_1_price', 0),
                    '筹码支撑1占比%': tech.get('support_peak_1_pct', 0),
                    'YRI历史总分': details.get('YRI历史总分', 0), 'YRI标签': details.get('YRI标签', ''),
                    'YRI等级': details.get('YRI等级', ''), 'YRI股性画像': details.get('YRI股性画像', ''),
                    'YRI日均成交额(万)': details.get('YRI日均成交(万)', 0),
                    'YRI涨停次数': details.get('YRI涨停次数', 0), 'YRI最大连板': details.get('YRI最大连板', 0),
                }
                dx_ranked_stocks.append(stock_data)
            except Exception as e:
                print(f"[低吸评分] {ts_code} {name} 失败: {e}")
                continue

        # 低吸池二波形态检测过滤
        for s in dx_ranked_stocks:
            ts_code = s['代码']
            try:
                # 一次调用：形态检测+共振评分
                wave2_result = detect_wave2_reversal(ts_code, pro)
                s['二波形态'] = wave2_result.get('pattern', '其他')
                s['二波评分'] = wave2_result.get('wave2_score', 0)
                s['二波信号'] = wave2_result.get('signal', '')
                s['入场价'] = wave2_result.get('entry_price', 0)
                s['止损价'] = wave2_result.get('stop_loss', 0)
                s['目标价'] = wave2_result.get('target', 0)
                
                # 根据板块类型过滤形态
                is_cyb_kcb = ts_code.startswith('3') or ts_code.startswith('688') or ts_code.startswith('689')
                valid_patterns = ['V型急跌', '深度回调', '放量回调'] if is_cyb_kcb else ['强势横盘', 'V型急跌', '放量回调']
                
                pattern = s['二波形态']
                if not pattern or pattern == '其他' or pattern not in valid_patterns:
                    s['二波信号'] = '非二波形态'
                    s['二波评分'] = 0
            except Exception:
                s['二波信号'] = '非二波形态'
                s['二波评分'] = 0

        # 低吸池：过滤掉非二波 + 二波评分≥10胜率更高 + 按二波分从高到低排序
        # 信号等级：>=18完美, >=12跟踪, >=7疑似
        dx_ranked_stocks = [s for s in dx_ranked_stocks if s.get('二波信号', '') != '非二波形态' and s.get('二波评分', 0) >= 10]
        dx_ranked_stocks = sorted(dx_ranked_stocks, key=lambda x: -x['二波评分'])

    # =========================
    # 构建低吸股票池输出文本（精简版）
    # =========================
    dixi_lines = []
    dixi_lines.append("=" * 60)
    dixi_lines.append("🔥 今日低吸股票池 (低吸二波信号)")
    dixi_lines.append("=" * 60)
    
    if dx_ranked_stocks:
        for i, s in enumerate(dx_ranked_stocks[:10], 1):
            alpha_val = s.get('Alpha评分', 0)
            alpha_sig = s.get('Alpha信号', '')
            alpha_str = f" (Alpha={alpha_val:.1f} {alpha_sig})" if alpha_sig else f" (Alpha={alpha_val:.1f})"
            price = s.get('现价', 0)
            pct = s.get('涨跌幅', 0)
            pct_str = f" {pct:+.2f}%" if pct != 0 else " 0.00%"
            dixi_lines.append(f"【低吸第{i}名】{s['名称']} ({s['代码']}) 现价={price:.2f}{pct_str}{alpha_str}")
            # 所属主题 + 非一日游阶段 + 确认天数 + 龙头序列
            cycle = s.get('非一日游阶段', '') or s.get('所属状态', '')
            confirm_days = s.get('确认天数', 0)
            cycle_str = f"非一日游:{cycle}" if cycle else ""
            days_str = f"{confirm_days}天" if confirm_days > 0 else ""
            leader_seq = s.get('龙头序列', '')
            leader_str = f"龙头:{leader_seq}" if leader_seq else ""
            info_parts = [s.get('所属主题', '')]
            if cycle_str: info_parts.append(cycle_str)
            if days_str: info_parts.append(days_str)
            if leader_str: info_parts.append(leader_str)
            dixi_lines.append(f"  主题: {' | '.join(info_parts)}")
            # 二波形态 + 二波评分
            wave2_score = s.get('二波评分', 0)
            wave2_pattern = s.get('二波形态', '')
            wave2_signal = s.get('二波信号', '')
            ep = s.get('入场价', 0)
            sl = s.get('止损价', 0)
            tp = s.get('目标价', 0)
            price_str = f" 入场={ep:.2f} 止损={sl:.2f} 目标={tp:.2f}" if ep > 0 else ''
            dixi_lines.append(f"  二波: {wave2_pattern} 评分={wave2_score} | {wave2_signal}{price_str}")
            # YRI辨识度
            yri_total = s.get('YRI历史总分', 0)
            if yri_total > 0:
                yri_tags = s.get('YRI标签', '')
                yri_max_lb = s.get('YRI最大连板', 0)
                dixi_lines.append(f"  YRI: 总分={yri_total:.0f} 最大连板={yri_max_lb}板 标签={yri_tags}")
            dixi_lines.append("")
    else:
        dixi_lines.append("  今日无低吸信号个股")
    dixi_stock_text = "\n".join(dixi_lines)
    print(dixi_stock_text)



    """
    # ===== 对前10名个股调用AI基本面+事件驱动分析 =====
    print(f"\n{'='*60}")
    print(f"🔥 对前10名个股进行AI基本面+事件驱动分析...")
    print(f"{'='*60}")
    news_analysis_list = []
    for i, s in enumerate(ranked_stocks[:10], 1):
        ts_code = s.get('代码', '')
        name = s.get('名称', '')
        print(f"  [{i}/10] {name} ({ts_code})...", end=' ')
        try:
            # 判断是否为回溯模式（指定了目标日期）
            is_backtest = target_date is not None and str(target_date) != ""
            
            if is_backtest:
                # 回溯模式：查找最新的缓存文件
                print("[回溯模式] 使用最新缓存数据...", end=' ')
                cache_files = []
                for f in os.listdir(NEWS_CACHE_DIR):
                    if f.startswith(f"ai_analysis_{ts_code}_") and f.endswith(".json"):
                        cache_files.append(f)
                if cache_files:
                    # 按日期排序，取最新的
                    cache_files.sort(reverse=True)
                    latest_cache = cache_files[0]
                    cache_file = os.path.join(NEWS_CACHE_DIR, latest_cache)
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cache_data = json.load(f)
                    score = cache_data.get("score", 50)
                    full_analysis = cache_data.get("response", "")
                else:
                    # 没有缓存，使用默认值
                    score = 50
                    full_analysis = ""
            else:
                # 正常模式：调用AI分析
                score = get_news_sentiment(ts_code, name, s.get('所属主题', ''), s.get('所属状态', ''))
                # 读取缓存中的完整分析（文件名必须与 get_news_sentiment 保存一致）
                cache_file = os.path.join(NEWS_CACHE_DIR, f"ai_analysis_{ts_code}_{TRADE_DATE}.json")
                full_analysis = ""
                if os.path.exists(cache_file):
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cache_data = json.load(f)
                        full_analysis = cache_data.get("response", "")
            print(f"情绪分={score}")
            news_analysis_list.append({
                'rank': i,
                'code': ts_code,
                'name': name,
                'score': score,
                'response': full_analysis,
            })
        except Exception as e:
            print(f"失败: {e}")
            news_analysis_list.append({
                'rank': i,
                'code': ts_code,
                'name': name,
                'score': 50,
                'response': "",
            })
    
    # 生成新闻分析文本
    news_analysis_text = ""
    if news_analysis_list:
        parts = []
        for item in news_analysis_list:
            parts.append(f"【第{item['rank']}名】{item['name']} ({item['code']}) — 综合情绪强度评分: {item['score']}")
            if item['response']:
                # 提取分析中的关键部分（去除最后的数字行）
                analysis_lines = item['response'].strip().split('\n')
                # 取前15行作为摘要
                summary_lines = [l for l in analysis_lines if l.strip() and not l.strip().isdigit()][:15]
                for line in summary_lines:
                    parts.append(f"  {line}")
            parts.append("")
        news_analysis_text = "\n".join(parts)
    """
    # 生成stock_text供后续使用
    stock_text = "\n".join([f"{s['代码']} {s['名称']} | 评分:{s['整合评分']:.1f}" for s in ranked_stocks[:10]])
    all_stock_text = "\n".join([f"{s['代码']} {s['名称']} | 评分:{s['整合评分']:.1f}" for s in ranked_stocks])

    # =========================
    # 获取跟踪分析个股（函数内部已完成主题过滤、技术过滤、整合评分）
    # =========================
    try:
        result = get_tracking_stocks()
        if isinstance(result, tuple) and len(result) == 3:
            tracking_stocks, tracking_stocks_text, ai_report = result
        elif isinstance(result, tuple) and len(result) == 2:
            tracking_stocks, tracking_stocks_text = result
            ai_report = ""
        else:
            tracking_stocks, tracking_stocks_text, ai_report = [], "", ""
        
        # 过滤掉与今日量化股池前10名重复的个股
        top10_codes = {s['code'] for s in icpm_top10_list}
        tracking_stocks = [s for s in tracking_stocks if s.get('code', '') not in top10_codes]
        
    except Exception as e:
        print(f"获取跟踪分析个股失败: {e}")
        tracking_stocks, tracking_stocks_text, ai_report = [], "", ""
    
    if tracking_stocks_text:
        print("\n========== 跟踪分析个股 ==========\n")
        print(tracking_stocks_text)
    else:
        print(f"\n========== 暂无跟踪分析个股 ==========\n")


    # =========================
    # 读取主题选股结果
    # =========================
    theme_stocks_records, theme_stocks_text = load_theme_pattern_stocks()
    if theme_stocks_text:
        print("\n========== 主题选股结果 ==========\n")
        #print(theme_stocks_text)
    else:
        print("\n========== 未找到主题选股结果 ==========")


    # =========================
    # 获取非一日游确认主题数据（供AI分析主题可持续性）
    # =========================
    non_daytrip_for_ai = ""
    try:
        import theme_trend_sentiment_score as theme_ts
        nd_result = theme_ts.analyze_non_daytrip_themes(TRADE_DATE, ndays=20)
        nd_confirmed = nd_result.get("confirmed", [])
        if nd_confirmed:
            lines = []
            lines.append("★ 非一日游确认主题（可持续强势主题，连续确认天数、周期阶段、龙头序列）★")
            lines.append("-" * 80)
            lines.append(f"  确认主题数: {len(nd_confirmed)} 个（连续2天以上确认，非一日游脉冲）")
            lines.append("-" * 60)
            for d in nd_confirmed[:16]:
                leader_seq = d.get('leader_sequence', '')
                leader_str = f" | 近5日龙头: {leader_seq}" if leader_seq and leader_seq != "无" else ""
                lines.append(
                    f"  ● {d['theme']:<12} 连续{d['confirmed_active_days']}天[{d['cycle_phase']}]  "
                    f"综:{d['current_composite']:.0f} 情:{d['current_sentiment']:.0f} 涨停:{d['current_zt']}家 龙头:{d['current_leader']}{leader_str}"
                )
            lines.append("-" * 80)
            # 周期分布
            from collections import Counter
            phase_count = Counter(d['cycle_phase'] for d in nd_confirmed)
            phase_str = "、".join([f"{k}{v}个" for k, v in phase_count.most_common()])
            lines.append(f"  周期分布: {phase_str}")
            lines.append("")
            lines.append("  【周期阶段说明】启动确认=首次突破确认线，中期延续=连续3天+确认，高潮尾声=情绪高潮后分歧，休眠等待=暂未激活")
            lines.append("")
            non_daytrip_for_ai = "\n".join(lines)
    except Exception as e:
        print(f"[非一日游] AI数据获取失败: {e}")
        non_daytrip_for_ai = ""


    #return
    prompt = f"""
以下是我自己计算的量化分析结果：
当前市场情绪：

{emotion_text}

今日主题分析情况：

{sector_text_his}
---------------------------------------

{non_daytrip_for_ai}
主题个股池选股结果（来自 theme_pattern_stock_picker.py）：
（这是根据主题趋势和情绪筛选出的优质个股，包含中期趋势主题和短线主线的龙头和中军）
{theme_stocks_text}
---------------------------------------

---------------------------------------
今日低吸股票池（低吸二波信号，按二波评分从高到低排序）：
{dixi_stock_text}

---------------------------------------
近20日跟踪分析股票池（从历史自选股中筛选未大涨过的个股，经主题过滤后按综合评分排序）：
{tracking_stocks_text}

请分析并输出内容：
标题：每日复盘({TRADE_DATE})
内容(分成以下部分)：
1、大盘情绪：简明扼要，重点是仓位建议及理由，操作要点
2、今日主题分析情况:
   【严格按以下固定模板输出，禁止自由发挥格式】

   第一段：用1-2句话概述今日市场风格（基于主题趋势分和情绪分判断核心主线）。

   操作要点：
   - 要点1（锁定核心方向）
   - 要点2（操作风格/市值偏好提示）

   主要关注主题及补涨中军：
   - 主题名1: 股票名1、股票名2（从主题个股池中取，加黑加粗）
   - 主题名2: 股票名3、股票名4、股票名5
   - 主题名3: 股票名6、股票名7
   - 【最多列出8个最强主题，每个主题列出2-3只补涨中军】

   明日主题预测：
   - 明日最看好主题1：简要说明理由（趋势/情绪/稳定性/资金流入状态）
   - 明日次看好主题2：简要说明理由
   - 【最多列出3个明日预测主题】

   【非一日游主题分析】：
   - 【必须引用】在"★ 非一日游确认主题"数据块中已提供各主题的连续确认天数、周期阶段和龙头序列
   - 在主要关注主题和明日预测中，必须结合非一日游信息判断主题持续性：
     * 连续≥3天的"中期延续"主题更有持续性，龙头切换代表资金在板块内轮动挖掘
     * 连续1-2天的"启动确认"主题需观察是否持续；首次进入确认线往往是最佳买点
     * "高潮尾声"主题提示情绪高潮后分歧风险，应降低预期
     * 明日预测必须优先选择"非一日游确认主题"中的标的，并说明其连续确认天数和周期阶段
   【重要约束】：股票名必须从下方"主题个股池选股结果"和"今日突破股票池"中选取，禁止凭空编造。主题名必须是下方已有的主题，不要自创。
3、今日低吸股票池分析（低吸二波信号标的全部输出，按二波评分从高到低排序）：
   - 【必须输出】无论是否有符合条件的个股，都必须输出此段落
   - 如果"今日低吸股票池"中有个股，包含代码、名称、收盘价、涨跌幅，按二波评分降序分析每只：
     - 二波评分和Alpha评分
     - 所属主题、非一日游阶段（含连续确认天数）和龙头序列
     - 【重点强调】二波信号分析：必须引用上方标注的二波形态和评分真实数据，并标注入场价/止损价/目标价
   - 如果无个股，直接输出"今日无符合条件的低吸二波标的"
4、跟踪分析个股：从上方跟踪股票池中输出，包含代码、名称、收盘价、涨跌幅，严格按以下格式分析：
    - 二波形态与二波评分（含入场价/止损价/目标价）
    - 所属主题、非一日游阶段、确认天数、龙头序列
    - 主题地位：直接输出"主题与地位: 所属主题为XXX。主题地位：XXX。辨识度YRI总分=XX。"
    - 如遇重大风险标注"【警告】有重大风险"，否则不输出
    【约束】禁止编造价格数据，严格使用上方现价。
    

格式要求：
- Top30个股分析中，每只股票单独分段，用【股票名+代码】作为小标题，加黑加粗显示
- 股票分析另起一行，分点说明
- 风格简洁明了，适合阅读

"""
    if not simple_mode:
        print("\n========== Deepseek AI分析 ==========\n")
        report = deepseek(prompt)
        print(report)

        try:
            ds_file = os.path.join(CACHE_DIR, f"Deepseek_Self_{TRADE_DATE}.md")
            with open(ds_file, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"✅ Deepseek报告已保存: {ds_file}")
        except Exception as e:
            print(f"⚠️ Deepseek报告保存失败: {e}")
        
        # 保存最终报告
        final_report = report
        
        # 先发送微信（即使报告保存失败也要发送）
        send_wechat(
            final_report,
            os.getenv("WECHAT_SCKEY")
        )   
        print("✅ 微信已发送")

        # 保存报告（带异常处理）
        try:
            report_file = os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.md")
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(final_report)
            print(f"✅ 报告已保存: {report_file}")
        except Exception as e:
            print(f"⚠️ 报告保存失败: {e}")

        try:
            html_file = os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.html")
            markdown_to_html_report(final_report, 
                                    output_file=html_file, 
                                    pdf_file=os.path.join(REPORT_DIR, f"Final_Self_{TRADE_DATE}.pdf"), 
                                    title=f"复盘及精选个股({TRADE_DATE})"
                                    )
        except Exception as e:
            print(f"⚠️ HTML报告生成失败: {e}")
    else:
        print(f"\n{'='*60}")
        print(f"[简易模式] 跳过AI分析和微信发送")
        print(f"{'='*60}")

    #result = send_wechat_message(report)

# =========================
# YRI-H 历史辨识度评分（过去252个交易日，约1年）
# =========================
def _is_zt_day(pct_chg, ts_code):
    """判断单日是否涨停（主板9.8%+/双创19.8%+）"""
    if pct_chg is None:
        return False
    IS_CYB_KCB = (ts_code.startswith('3') or ts_code.startswith('688') or ts_code.startswith('689'))
    zt_threshold = 19.8 if IS_CYB_KCB else 9.8
    return pct_chg >= zt_threshold


def calc_yri_history(ts_code, debug=False):
    """
    计算历史辨识度指标 YRI-H（满分100，基于过去252个交易日）
    
    返回: dict, 包含各项得分、总分、等级、标签、画像
    """
    ts_code = ts_code.strip()
    
    # 1. 获取历史日线数据（近1年）
    df = get_hist_data(ts_code)
    if df is None or len(df) < 30:
        return {
            "错误": f"历史数据不足（仅{len(df) if df is not None else 0}天）",
            "建议": "请确认股票代码是否正确，或先运行主程序缓存数据"
        }
    
    # 取最近252个交易日
    df = df.sort_values('trade_date').tail(252).reset_index(drop=True)
    n_days = len(df)
    
    # ========== 1. 计算基础指标 ==========
    zt_count = 0  # 涨停次数
    max_consec_zt = 0  # 最大连板
    current_consec = 0
    big_up_days = 0  # 大阳线（+5%以上）天次
    pct_chg_list = []  # 每日涨跌幅
    
    for _, row in df.iterrows():
        pct = row.get('pct_chg', 0)
        pct_chg_list.append(pct)
        
        if _is_zt_day(pct, ts_code):
            zt_count += 1
            current_consec += 1
            max_consec_zt = max(max_consec_zt, current_consec)
        else:
            current_consec = 0
        
        if pct >= 5:  # 大阳线计数（趋势股重要指标）
            big_up_days += 1
    
    # 计算辅助指标
    avg_pct_abs = sum([abs(x) for x in pct_chg_list]) / len(pct_chg_list) if pct_chg_list else 0
    first_close = df['close'].iloc[0]
    last_close = df['close'].iloc[-1]
    stock_return = (last_close / first_close - 1) * 100 if first_close > 0 else 0
    
    # 年化因子（如果实际交易日不足252天，按比例放大阈值分母）
    # 这样短周期数据不会因为观察期短就低估辨识度
    annual_factor = 252 / n_days if n_days > 0 else 1.0
    
    # 年化涨停/大阳次数（用于调整评分阈值）
    zt_count_annual = zt_count * annual_factor
    big_up_days_annual = big_up_days * annual_factor
    
    # 计算近1年最大滚动涨幅（用局部高点法，O(n)复杂度）
    closes = df['close'].values
    max_drawup = 0.0
    min_close_seen = closes[0]
    for c in closes:
        if c < min_close_seen:
            min_close_seen = c
        drawup = (c / min_close_seen - 1) * 100 if min_close_seen > 0 else 0
        max_drawup = max(max_drawup, drawup)
    max_excess = max(max_drawup, stock_return)
    
    # 历史新高检查
    is_new_high = last_close >= df['close'].max() * 0.98
    
    # ========== 2. 涨停基因 G（30分） ==========
    # 趋势股涨停少但大阳线多，组合打分
    # 使用年化涨停次数评估"涨停基因强度"
    G_raw = 0
    if zt_count_annual >= 20:
        G_raw += 30
    elif zt_count_annual >= 15:
        G_raw += 25
    elif zt_count_annual >= 10:
        G_raw += 20
    elif zt_count_annual >= 5:
        G_raw += 15
    elif zt_count_annual >= 2:
        G_raw += 10
    elif zt_count_annual >= 1:
        G_raw += 5
    
    # 大阳线补偿（趋势股没有涨停但有很多5%+阳线时的补偿，最多+25分）
    # 每6天出现一次大阳线给3分
    big_up_bonus = min(int(big_up_days_annual / 6) * 3, 25)
    
    # 趋势股"资金关注"特征：平均日波动>2%也说明有资金在操作
    volatility_bonus = 0
    if avg_pct_abs >= 3.0:
        volatility_bonus = 8
    elif avg_pct_abs >= 2.5:
        volatility_bonus = 6
    elif avg_pct_abs >= 2.0:
        volatility_bonus = 4
    elif avg_pct_abs >= 1.5:
        volatility_bonus = 2
    
    G_score = min(G_raw + big_up_bonus + volatility_bonus, 30)
    
    # ========== 3. 空间记忆 S（25分） ==========
    S_score = 0
    # 连板记忆（15分）
    if max_consec_zt >= 6:
        S_score += 15
    elif max_consec_zt >= 5:
        S_score += 12
    elif max_consec_zt >= 4:
        S_score += 10
    elif max_consec_zt >= 3:
        S_score += 7
    elif max_consec_zt >= 2:
        S_score += 4
    elif max_consec_zt >= 1:
        S_score += 2
    
    # 趋势空间（10分）- 最大涨幅体现"翻倍空间"
    trend_space_score = 0
    if max_excess >= 200:
        trend_space_score = 10
    elif max_excess >= 100:
        trend_space_score = 8
    elif max_excess >= 60:
        trend_space_score = 6
    elif max_excess >= 40:
        trend_space_score = 4
    elif max_excess >= 25:
        trend_space_score = 3
    elif max_excess >= 15:
        trend_space_score = 2
    
    S_score = min(S_score + trend_space_score, 25)
    
    # ========== 4. 历史资金活跃度 A（20分） ==========
    # 4a. 当日真实换手率（从缓存读取）
    today_turnover = get_cached_turnover(ts_code)
    avg_turnover = today_turnover if today_turnover and today_turnover > 0 else 0
    
    # 4b. 若有量数据，vol均值/当日vol的比值估算历史换手
    if 'vol' in df.columns and avg_turnover <= 0:
        vol_1y_avg = df['vol'].mean()
        today_vol = df['vol'].iloc[-1]
        if today_vol > 0:
            if vol_1y_avg / today_vol > 1.5:
                avg_turnover = 5.0
            elif vol_1y_avg / today_vol > 1.0:
                avg_turnover = 3.0
            else:
                avg_turnover = 1.5
    
    # 4c. A评分 - 换手率（12分）
    if avg_turnover >= 15:
        A_turnover = 12
    elif avg_turnover >= 10:
        A_turnover = 11
    elif avg_turnover >= 8:
        A_turnover = 10
    elif avg_turnover >= 6:
        A_turnover = 8
    elif avg_turnover >= 5:
        A_turnover = 7
    elif avg_turnover >= 3:
        A_turnover = 5
    elif avg_turnover >= 2:
        A_turnover = 3
    elif avg_turnover >= 1:
        A_turnover = 2
    else:
        A_turnover = 1
    
    # 4d. 日均成交额百分位（8分）- 用历史数据估算
    # 注意：tushare的amount字段单位是"千元"，需乘以1000转为元，再除以10000转为万
    avg_amount_1y = 0.0
    if 'amount' in df.columns:
        avg_amount_1y = df['amount'].mean() * 1000 / 10000  # = amount.mean() / 10
    elif 'vol' in df.columns and 'close' in df.columns:
        avg_amount_1y = (df['vol'] * df['close']).mean() / 10000
    
    # 成交额分级评分
    A_percentile_bonus = 0
    if avg_amount_1y > 50000:  # 50亿+
        A_percentile_bonus = 8
    elif avg_amount_1y > 20000:  # 20亿+
        A_percentile_bonus = 6
    elif avg_amount_1y > 10000:  # 10亿+
        A_percentile_bonus = 5
    elif avg_amount_1y > 5000:  # 5亿+
        A_percentile_bonus = 4
    elif avg_amount_1y > 2000:  # 2亿+
        A_percentile_bonus = 3
    elif avg_amount_1y > 1000:  # 1亿+
        A_percentile_bonus = 2
    else:
        A_percentile_bonus = 1
    
    A_score = min(A_turnover + A_percentile_bonus, 20)
    
    # ========== 5. 股性弹性 E（15分） ==========
    # 用近1年最大涨幅+累计涨幅综合判断
    E_by_max = 0
    if max_excess >= 200:
        E_by_max = 15
    elif max_excess >= 100:
        E_by_max = 12
    elif max_excess >= 60:
        E_by_max = 10
    elif max_excess >= 40:
        E_by_max = 8
    elif max_excess >= 30:
        E_by_max = 6
    elif max_excess >= 20:
        E_by_max = 4
    elif max_excess >= 10:
        E_by_max = 2
    else:
        E_by_max = 1
    
    # 累计涨幅加分（趋势股累计涨幅大）
    E_by_return = 0
    if stock_return >= 100:
        E_by_return = 5
    elif stock_return >= 50:
        E_by_return = 3
    elif stock_return >= 20:
        E_by_return = 2
    elif stock_return >= 0:
        E_by_return = 1
    
    E_score = min(E_by_max + E_by_return, 15)
    
    # ========== 6. 关注度持续性 C（10分） ==========
    # 6a. 热榜天数
    hot_days = 0
    try:
        if os.path.exists(DC_HOT_CACHE_DIR):
            files = sorted([f for f in os.listdir(DC_HOT_CACHE_DIR) if f.startswith('dc_hot_')])
            for f in files:
                try:
                    fpath = os.path.join(DC_HOT_CACHE_DIR, f)
                    hdf = pd.read_csv(fpath)
                    if '代码' in hdf.columns:
                        if ts_code in hdf['代码'].head(50).values:
                            hot_days += 1
                    elif 'ts_code' in hdf.columns:
                        if ts_code in hdf['ts_code'].head(50).values:
                            hot_days += 1
                except:
                    continue
    except:
        pass
    
    # 6b. 市场波动关注度 proxy - 平均日波动大说明市场反复关注
    market_attention_score = 0
    if avg_pct_abs >= 3.5:
        market_attention_score = 10
    elif avg_pct_abs >= 2.8:
        market_attention_score = 8
    elif avg_pct_abs >= 2.2:
        market_attention_score = 6
    elif avg_pct_abs >= 1.8:
        market_attention_score = 4
    elif avg_pct_abs >= 1.4:
        market_attention_score = 3
    else:
        market_attention_score = 2
    
    # 6c. 热榜天数评分
    hot_score = 0
    if hot_days > 50:
        hot_score = 10
    elif hot_days >= 20:
        hot_score = 8
    elif hot_days >= 10:
        hot_score = 6
    elif hot_days >= 5:
        hot_score = 4
    elif hot_days >= 2:
        hot_score = 2
    else:
        hot_score = 1
    
    C_score = max(hot_score, market_attention_score)
    
    # ========== 7. 总分计算 ==========
    total_score = G_score + S_score + A_score + E_score + C_score
    total_score = round(total_score, 1)
    
    # 等级判定
    if total_score >= 80:
        level = "历史大妖/板块核心中军"
    elif total_score >= 65:
        level = "强股性活跃标的/趋势龙头"
    elif total_score >= 50:
        level = "中等辨识度"
    elif total_score >= 30:
        level = "股性一般/非核心"
    else:
        level = "历史冷门/低辨识度"
    
    # 核心历史标签
    tags = []
    if zt_count >= 10:
        tags.append("涨停常客")
    if max_consec_zt >= 3:
        tags.append("连板记忆")
    if big_up_days >= 30:
        tags.append("大阳趋势")
    if avg_turnover >= 5:
        tags.append("高换手")
    elif avg_turnover >= 3:
        tags.append("中高换手")
    if avg_amount_1y > 10000:
        tags.append("资金深度参与")
    if max_excess >= 100:
        tags.append("高弹性")
    elif max_excess >= 50:
        tags.append("中高弹性")
    if hot_days >= 10 or market_attention_score >= 4:
        tags.append("市场关注")
    if stock_return >= 50:
        tags.append("趋势上涨")
    
    if not tags:
        tags = ["低辨识度"]
    
    # 股性画像
    if total_score >= 80:
        portrait = "板块核心中军/历史级大妖，资金深度参与，股性极度活跃，具有强烈市场记忆点"
    elif total_score >= 65:
        portrait = "趋势龙头/板块活跃股，资金关注度高，具备良好波段和短线价值"
    elif total_score >= 50:
        portrait = "股性中等，有一定表现，主题共振时可参与"
    elif total_score >= 30:
        portrait = "股性一般，非核心标的，适合有明确主题催化时谨慎参与"
    else:
        portrait = "冷门股/低辨识度，缺乏资金关注，非主线行情不建议参与"
    
    result = {
        "股票代码": ts_code,
        "分析天数": n_days,
        "G_涨停基因": {
            "涨停次数": zt_count,
            "大阳线天次(+5%)": big_up_days,
            "涨停得分": G_raw,
            "大阳补偿": big_up_bonus,
            "得分": G_score
        },
        "S_空间记忆": {
            "最大连板": max_consec_zt,
            "最大涨幅(%)": round(max_excess, 1),
            "连板得分": S_score - trend_space_score,
            "趋势空间得分": trend_space_score,
            "得分": S_score
        },
        "A_资金活跃度": {
            "日均换手率(%)": round(avg_turnover, 2),
            "日均成交额(万元)": round(avg_amount_1y, 0),
            "换手得分": A_turnover,
            "成交额百分位": A_percentile_bonus,
            "得分": A_score
        },
        "E_股性弹性": {
            "近1年最大涨幅(%)": round(max_excess, 1),
            "近1年累计涨幅(%)": round(stock_return, 1),
            "得分": E_score
        },
        "C_关注度持续性": {
            "热榜天数": hot_days,
            "平均日波动(%)": round(avg_pct_abs, 2),
            "得分": C_score
        },
        "YRI历史总分": total_score,
        "等级": level,
        "核心历史标签": tags,
        "股性画像": portrait
    }
    
    if debug:
        print(f"\n{'='*60}")
        print(f"  YRI-H 历史辨识度评分 - {ts_code}")
        print(f"{'='*60}")
        print(f"  样本: {n_days}天  涨停{zt_count}次  大阳{big_up_days}次  连板{max_consec_zt}天")
        print(f"  G 涨停基因(30): {G_score}  (涨停{G_raw} + 大阳{big_up_bonus})")
        print(f"  S 空间记忆(25):  {S_score}  (连板{S_score - trend_space_score} + 趋势空间{trend_space_score})")
        print(f"  A 资金活跃(20):  {A_score}  (换手{A_turnover} + 成交{A_percentile_bonus})")
        print(f"  E 股性弹性(15):  {E_score}  (最大涨幅{max_excess:.0f}% + 累计{stock_return:.0f}%)")
        print(f"  C 关注度(10):   {C_score}  (热榜{hot_days}天/波动{avg_pct_abs:.1f}%)")
        print(f"  {'-'*60}")
        print(f"  YRI-H 总分: {total_score}  →【{level}】")
        print(f"  标签: {', '.join(tags)}")
        print(f"{'='*60}\n")
    
    return result


def yri_batch_analysis(codes):
    """批量分析多只股票YRI-H并排序"""
    results = []
    for code in codes:
        r = calc_yri_history(code, debug=False)
        if isinstance(r, dict) and "错误" not in r:
            results.append({
                "代码": r["股票代码"],
                "YRI总分": r["YRI历史总分"],
                "等级": r["等级"],
                "涨停": r["G_涨停基因"]["涨停次数"],
                "大阳+5%": r["G_涨停基因"]["大阳线天次(+5%)"],
                "最大连板": r["S_空间记忆"]["最大连板"],
                "最大涨幅%": r["S_空间记忆"]["最大涨幅(%)"],
                "日均换手%": r["A_资金活跃度"]["日均换手率(%)"],
                "日均成交万": r["A_资金活跃度"]["日均成交额(万元)"],
                "标签": ", ".join(r["核心历史标签"])
            })
    
    if results:
        results_df = pd.DataFrame(results).sort_values("YRI总分", ascending=False).reset_index(drop=True)
        results_df.index = results_df.index + 1
        pd.set_option('display.width', 200)
        print(f"\n{'='*120}")
        print(f"  YRI-H 批量历史辨识度评分（共{len(results)}只，按总分排序）")
        print(f"{'='*120}")
        print(results_df.to_string(index=True))
        print(f"{'='*120}\n")
        return results_df
    return None


# =========================
# 启动
# =========================
if __name__ == "__main__":
    import argparse
    import json
    
    parser = argparse.ArgumentParser(description='A股量化选股分析系统')
    parser.add_argument('-d', '--date', type=str, default=None,
                        help='指定目标日期，格式: YYYYMMDD（如: 20260601）')
    parser.add_argument('--no-send', action='store_true',
                        help='不发送微信消息')
    parser.add_argument('--simple', action='store_true',
                        help='简易模式，只输出个股和评分，不进行AI分析、不发送微信')
    parser.add_argument('--yri', type=str, default=None,
                        help='历史辨识度分析，输入股票代码(如 002426.SZ 或 002426)，多只用逗号分隔')
    parser.add_argument('--yri-json', type=str, default=None,
                        help='同--yri，但仅输出JSON格式结果（便于程序调用）')
    
    args = parser.parse_args()
    
    # --- YRI-H 历史辨识度分析（独立模式） ---
    if args.yri_json:
        codes = [c.strip() for c in args.yri_json.split(',') if c.strip()]
        results = {}
        for code in codes:
            # 自动补全 .SZ/.SH
            if '.' not in code:
                if code.startswith(('6', '9')):
                    code_full = code + '.SH'
                else:
                    code_full = code + '.SZ'
            else:
                code_full = code
            r = calc_yri_history(code_full, debug=False)
            results[code] = r
        print(json.dumps(results, ensure_ascii=False, indent=2))
        exit(0)
    
    if args.yri:
        codes = [c.strip() for c in args.yri.split(',') if c.strip()]
        # 自动补全 .SZ/.SH
        full_codes = []
        for code in codes:
            if '.' not in code:
                if code.startswith(('6', '9')):
                    full_codes.append(code + '.SH')
                else:
                    full_codes.append(code + '.SZ')
            else:
                full_codes.append(code)
        
        if len(full_codes) == 1:
            result = calc_yri_history(full_codes[0], debug=True)
            if "错误" in result:
                print(f"\n❌ {result['错误']} - {result['建议']}")
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            yri_batch_analysis(full_codes)
        exit(0)
    
    # 运行
    run(target_date=args.date, simple_mode=args.simple)


