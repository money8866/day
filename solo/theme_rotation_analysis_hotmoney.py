#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日盘后复盘和热点轮动分析系统 - 短线游资优化版
主题排名 + 10日平均分 + 调整后回升概率分析 + SQLite数据库存储 + 大盘情绪分析
优化内容：
1. 主题评分：短期爆发力 + 量能确认 + 板块强度 + 赚钱效应
2. 个股评分：短线游资风格，优先爆发力、涨停基因、量价配合
"""
import os
import sys
import pickle
import warnings
import time
import json
import glob
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv

import numpy as np
import pandas as pd
import tushare as ts
import requests

warnings.filterwarnings('ignore')

# =========================
# 环境变量
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

SCKEY = os.getenv("WECHAT_SCKEY")

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")

# 从父目录导入千问AI接口
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tushare_quant import ask_qwen

try:
    from openai import OpenAI
    QWEN_SEARCH_AVAILABLE = True
except ImportError:
    QWEN_SEARCH_AVAILABLE = False


def ask_qwen_with_search(prompt):
    """千问通义千问联网搜索版 - 可搜索网络资讯验证基本面"""
    if not QWEN_API_KEY:
        return ""
    if not QWEN_SEARCH_AVAILABLE:
        return ask_qwen(prompt)
    try:
        client = OpenAI(
            api_key=QWEN_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        completion = client.chat.completions.create(
            model="qwen3-max",
            messages=[
                {"role": "system", "content": "你是专业A股机构分析师，请联网搜索核实每只股票的近期基本面信息"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            top_p=0.5,
            max_tokens=40960,
            extra_body={"enable_search": True}
        )
        if completion and completion.choices and len(completion.choices) > 0:
            message = completion.choices[0].message
            if message and hasattr(message, 'content'):
                return message.content
        return ""
    except Exception as e:
        print(f"千问联网搜索失败，回退普通模式: {e}")
        return ask_qwen(prompt)


def send_serverchan_push(title, content):
    """通过Server酱发送微信推送"""
    if not SCKEY:
        print("⚠ 未配置WECHAT_SCKEY，跳过微信推送")
        return
    url = f"https://sctapi.ftqq.com/{SCKEY}.send"
    try:
        resp = requests.post(url, data={"title": title[:100], "desp": content[:20000]}, timeout=15)
        print(f"📱 微信推送完成: {resp.status_code}")
    except Exception as e:
        print(f"❌ 微信推送失败: {e}")


def get_last_trade_date():

    now = datetime.now()

    # =========================
    # 9点前：视为上一自然日
    # =========================
    if now.hour < 15:

        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

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

TRADE_DATE = get_last_trade_date()
#TRADE_DATE = "20260527" # for test

print("当前交易日:", TRADE_DATE)
# =========================
# DeepSeek 基本面估值和风险排除
# =========================
def analyze_stock_fundamental(stock_info):
    """
    使用 DeepSeek 分析股票基本面
    """
    if not DEEPSEEK_KEY:
        return None
    
    prompt = f"""你是一位专业的A股量化分析师。请分析以下股票，要求基于业绩分析和机构研报进行估值计算。

股票信息：
- 股票名称：{stock_info.get('name', 'N/A')}
- 股票代码：{stock_info.get('ts_code', 'N/A')}
- 综合评分：{stock_info.get('total_score', 0):.1f}
- 5日涨幅：{stock_info.get('change_5', 0):.1f}%
- 20日涨幅：{stock_info.get('change_20', 0):.1f}%
- 5日乖离率：{stock_info.get('ma_data', {}).get('ma5_biased', 0):.1f}%
- 量比：{stock_info.get('ma_data', {}).get('volume_ratio', 0):.2f}
- 风险等级：{stock_info.get('score_details', {}).get('回落风险等级', 'N/A')}
- 二波信号：{stock_info.get('score_details', {}).get('二波信号等级', 'N/A')}
- 所属主题：{', '.join(stock_info.get('themes', []))}

请分析：
1. 估值水平：低/中/高
2. 业绩分析：最近一年业绩增长情况、行业地位
3. 机构研报评级：机构一致预期
4. 估值波动空间：
   - 合理估值区间
   - 相对现价的上涨乐观幅度（%）
5. 风险提示
6. 中长线投资建议（推荐/谨慎/不推荐）
7. 简要理由

格式要求（必须严格按此格式）：
- 估值水平：低/中/高
- 业绩分析：...
- 机构评级：...
- 合理估值区间：XX-XX元
- 上涨乐观幅度：XX%
- 风险提示：...
- 投资建议：推荐/谨慎/不推荐
- 理由：...
"""
    
    try:
        headers = {
            'Authorization': f'Bearer {DEEPSEEK_KEY}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是一位专业的A股量化分析师，擅长结合业绩数据和机构研报进行估值分析。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 800
        }
        
        resp = requests.post('https://api.deepseek.com/v1/chat/completions', 
                            headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        return result['choices'][0]['message']['content']
    except Exception as e:
        print(f"DeepSeek 分析失败: {e}")
        return None

# =========================
# Qwen千问AI选股验证 - 基本面风险过滤 + 情绪面强化分析
# =========================
_stock_price_cache = {}

def get_stock_realtime_price(ts_code):
    """获取股票实时价格数据（带缓存）"""
    if ts_code in _stock_price_cache:
        return _stock_price_cache[ts_code]

    func_name = "stock_price"
    cached_data = cache_manager.get(func_name, ts_code=ts_code)
    if cached_data is not None:
        _stock_price_cache[ts_code] = cached_data
        return cached_data

    try:
        df = pro.daily(ts_code=ts_code, trade_date=TRADE_DATE)
        if df is not None and not df.empty:
            price_data = {
                'close': df.iloc[0]['close'],
                'pct_chg': df.iloc[0]['pct_chg'],
                'vol': df.iloc[0]['vol'],
                'amount': df.iloc[0]['amount']
            }
            _stock_price_cache[ts_code] = price_data
            cache_manager.set(func_name, price_data, ts_code=ts_code)
            return price_data
    except:
        pass
    return None

def qwen_stock_validator(selected_stocks, market_emotion, theme_info):
    """
    使用千问AI对选出的个股进行基本面风险过滤和情绪面强化分析
    重点关注游资介入爆发力

    参数:
        selected_stocks: 初步选出的股票列表
        market_emotion: 大盘情绪数据
        theme_info: 主题信息

    返回:
        最终推荐的股票列表及分析报告
    """
    if not QWEN_API_KEY:
        print("未配置QWEN_API_KEY，跳过AI验证")
        return selected_stocks, None

    if not selected_stocks:
        return [], None

    # 构建股票信息文本（包含实时价格，避免AI幻觉）
    stocks_text = ""
    for i, stock in enumerate(selected_stocks[:10], 1):  # 最多分析10只
        ts_code = stock.get('ts_code', 'N/A')
        price_data = get_stock_realtime_price(ts_code)
        current_price = price_data['close'] if price_data else 'N/A'
        today_pct = price_data['pct_chg'] if price_data else stock.get('change_5', 0)

        stocks_text += f"""
{i}. {stock.get('name', 'N/A')} ({ts_code})
   - 当前价格: {current_price}元
   - 今日涨跌幅: {today_pct:+.2f}%（若为N/A则使用5日涨幅: {stock.get('change_5', 0):+.1f}%）
   - 综合评分: {stock.get('total_score', 0):.1f}
   - 20日涨幅: {stock.get('change_20', 0):+.1f}%
   - 所属主题: {', '.join(stock.get('themes', []))}
   - 换手率: {stock.get('volume_ratio', 0):.2f}
   - 5日乖离率: {stock.get('ma_data', {}).get('ma5_biased', 0):+.1f}%
   - 20日乖离率: {stock.get('ma_data', {}).get('ma20_biased', 0):+.1f}%
   - 涨停次数: {stock.get('limit_up_count', 0)}次
   - 冲高回落概率: {stock.get('score_details', {}).get('冲高回落概率', 0)}%
   - 二波启动概率: {stock.get('score_details', {}).get('二波启动概率', 0)}%
"""
    
    # 大盘情绪文本
    emotion_text = f"""
大盘情绪数据：
- 情绪指数: {market_emotion.get('情绪指数', 0):.1f}
- 大盘点位: {market_emotion.get('大盘点位', 0)}
- 大盘涨跌幅: {market_emotion.get('大盘涨跌幅', 0):.2f}%
- 市场阶段: {market_emotion.get('市场阶段', 'N/A')}
- 涨停家数: {market_emotion.get('涨停家数', 0)}
- 跌停家数: {market_emotion.get('跌停家数', 0)}
- 上涨占比: {market_emotion.get('上涨占比', 0):.1f}%
- 强势股占比: {market_emotion.get('强势股占比', 0):.1f}%
- 连板高度: {market_emotion.get('连板高度', 0)}
- 风险等级: {market_emotion.get('风险等级', 'N/A')}
- 均线状态: {market_emotion.get('均线状态', 'N/A')}
"""
    
    prompt = f"""你是一位资深的A股短线游资专家，精通情绪周期、主力动向和技术分析。

【重要约束 - 请严格遵守】
1. 请使用联网搜索核实每只股票的**近期基本面信息**，包括但不限于：
   - 近三个月有无定增预案或减持公告
   - 未来半年有无解禁压力
   - 有无重大诉讼或财务风险
   - 机构持仓是否有明显变化
   - 是否属于ST或业绩变脸股
2. 你只能使用下方提供的候选个股列表中的数据，禁止编造任何未提供的数据
3. 对于价格、涨跌幅等数据，必须以上方"当前价格"和"今日涨跌幅"为准
4. 如果某项数据标记为"N/A"，请明确说明"数据未提供"，不得自行假设或推测
5. 买卖点建议必须基于提供的当前价格计算，绝对不能使用你自行编造的价格
6. 所有分析结论必须来源于提供的数据特征或联网搜索到的真实信息，不得凭空臆测

当前市场背景：
{emotion_text}

候选个股列表（初步量化筛选）：
{stocks_text}

请进行深度AI验证分析：

## 1. 基本面风险过滤（必须逐个排查，请联网搜索核实）
对每个股票排查以下风险：
- 近三个月有无定增预案或减持公告
- 未来半年有无解禁压力
- 有无重大诉讼或财务风险
- 机构持仓是否稳定
- 是否属于ST或业绩变脸股
- 近期有无负面新闻或舆情风险

## 2. 情绪面强化分析
结合当前大盘情绪和市场阶段：
- 分析个股与主线的关联强度
- 游资介入迹象是否明显（换手率、涨停基因、连板情况）
- 情绪周期位置（启动、发酵、高潮、退潮、冰点）
- 与市场情绪的共振程度

## 3. 短线爆发力评估
重点评估：
- 题材正宗性（是否贴合当前最强主线）
- 筹码结构（超跌程度、套牢盘、获利盘）
- 资金强度（游资席位、成交量放大情况）
- 图形位置（突破、回调、二波启动）
- 情绪溢价（市场对该题材的认可度）

## 4. 最终精选（2-5只）
基于以上分析，给出最终短线游资介入爆发力最强的个股推荐。

输出格式：
### 基本面风险排除
（对每个股票的风险评估，标注：合格/不合格+理由）

### 情绪面分析
（个股与市场情绪的共振分析）

### 短线爆发力排序
1. [股票名称]（当前价格:XX元）- 爆发力评级：强/中/弱 - 理由...
2. ...

### 最终推荐（2-5只）
（必须使用提供的"当前价格"计算买卖点，禁止编造价格）
格式：
| 股票名称 | 当前价格 | 买入建议 | 目标价格 | 止损价格 | 仓位 |
|---------|---------|---------|---------|---------|-----|
| | 元 | | 元（涨幅:%） | 元（跌幅:%） | % |

"""
    
    try:
        print("\n========== Qwen千问AI选股验证 ==========\n")
        report = ask_qwen_with_search(prompt)
        print(report)
        return selected_stocks, report
    except Exception as e:
        print(f"Qwen AI验证失败: {e}")
        return selected_stocks, None

# =========================
# 简化版大盘情绪分析
# =========================
def analyze_market_emotion_simple():
    try:
        now = datetime.now()
        if now.hour < 15:
            query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
        else:
            query_date = now.strftime('%Y%m%d')
        
        cal = cached_trade_cal(start_date='20250101', end_date=query_date)
        cal = cal[cal['is_open'] == 1]
        trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
        
        daily_df = cached_daily_single(trade_date=trade_date)
        if daily_df is None or daily_df.empty:
            return {}
        
        total = len(daily_df)
        up_count = (daily_df['pct_chg'] > 0).sum()
        up_ratio = (up_count / total) * 100 if total > 0 else 0
        strong_count = (daily_df['pct_chg'] >= 5).sum()
        strong_ratio = (strong_count / total) * 100 if total > 0 else 0
        
        zt_count = 0
        dt_count = 0
        broken_rate = 0
        try:
            limit_df = cached_limit_list_ths(trade_date=trade_date, limit_type='涨停池')
            if limit_df is not None and not limit_df.empty:
                zt_count = len(limit_df)
        except Exception as e:
            print(f"获取涨停数据失败: {e}")
        
        try:
            limit_df_d = cached_limit_list_ths(trade_date=trade_date, limit_type='跌停池')
            if limit_df_d is not None and not limit_df_d.empty:
                dt_count = len(limit_df_d)
                if 'open_times' in limit_df_d.columns:
                    broken_count = (limit_df_d['open_times'].fillna(0) > 0).sum()
                    broken_rate = (broken_count / zt_count) * 100 if zt_count > 0 else 0
        except Exception as e:
            print(f"获取跌停数据失败: {e}")
        
        max_lb = 0
        try:
            lb_df = cached_limit_step(trade_date=trade_date)
            if lb_df is not None and not lb_df.empty and 'nums' in lb_df.columns:
                max_lb = int(lb_df['nums'].fillna(1).astype(int).max())
        except:
            pass
        
        close = 0
        pct_chg = 0
        bias20 = 0
        ma20_slope = 0
        above_ma5 = False
        ma5_slope = 0
        above_ma20 = False
        above_ma60 = False
        ma60_slope = 0
        try:
            index_df = cached_index_daily(ts_code='000001.SH', start_date='20250101', end_date=trade_date)
            if index_df is not None and not index_df.empty:
                index_df = index_df.sort_values('trade_date').tail(90)
                close = index_df['close'].iloc[-1]
                pct_chg = index_df['pct_chg'].iloc[-1]
                ma20 = index_df['close'].rolling(20).mean().iloc[-1]
                ma60 = index_df['close'].rolling(60).mean().iloc[-1]
                bias20 = ((close / ma20) - 1) * 100 if ma20 > 0 else 0
                
                if len(index_df) >= 25:
                    ma20_prev = index_df['close'].iloc[-25]
                    ma20_slope = 1 if ma20 > ma20_prev else -1
                above_ma20 = close >= ma20
                
                if len(index_df) >= 5:
                    ma5 = index_df['close'].rolling(5).mean().iloc[-1]
                    ma5_prev = index_df['close'].rolling(5).mean().iloc[-2]
                    above_ma5 = close >= ma5
                    ma5_slope = 1 if ma5 > ma5_prev else -1
                
                if len(index_df) >= 65:
                    ma60_prev = index_df['close'].iloc[-65]
                    ma60_slope = 1 if ma60 > ma60_prev else -1
                above_ma60 = close >= ma60
        except:
            pass
        
        trend_score = 50
        if pct_chg > 0:
            trend_score += 10
        else:
            trend_score -= 10
        
        if ma20_slope > 0:
            trend_score += 15
        else:
            trend_score -= 20
        
        if above_ma5:
            trend_score += 12
        else:
            trend_score -= 18
        
        if ma5_slope > 0:
            trend_score += 8
        else:
            trend_score -= 10
        
        if above_ma20:
            trend_score += 10
        else:
            trend_score -= 15
        
        if ma20_slope > 0:
            trend_score += 8
        else:
            trend_score -= 12
        
        if above_ma60:
            trend_score += 15
        else:
            trend_score -= 20
        
        if ma60_slope > 0:
            trend_score += 10
        else:
            trend_score -= 15
        
        if bias20 > 10:
            trend_score -= 10
        elif bias20 < -5:
            trend_score += 8
        
        ma_duotou = above_ma5 and above_ma20 and above_ma60
        if ma_duotou:
            trend_score += 15
        
        ma_kongtou = not above_ma5 and not above_ma20 and not above_ma60
        if ma_kongtou:
            trend_score -= 20
        
        if trend_score >= 70:
            trend_risk = "低风险"
        elif trend_score >= 50:
            trend_risk = "中性"
        elif trend_score >= 35:
            trend_risk = "高风险"
        else:
            trend_risk = "系统风险"
        
        base_score = 20
        zt_score = np.log1p(zt_count) * 12
        dt_score = np.log1p(dt_count) * 10
        lb_score = 25 if max_lb >= 7 else 18 if max_lb >= 5 else 10 if max_lb >= 3 else 3
        broken_penalty = broken_rate * 0.35
        
        risk_penalty = 0
        if dt_count >= 30:
            risk_penalty = 25
        elif dt_count >= 15:
            risk_penalty = 15
        elif dt_count >= 5:
            risk_penalty = 8
        
        earning_score = up_ratio * 0.3 + strong_ratio * 1.2
        
        emotion_score = base_score + zt_score + lb_score + earning_score - dt_score - broken_penalty - risk_penalty
        
        emotion_score = max(0, min(100, np.tanh(emotion_score / 80) * 100))
        
        if not above_ma5:
            emotion_score *= 0.80
        elif not above_ma20:
            emotion_score *= 0.90
        elif not above_ma60:
            emotion_score *= 0.95
        
        final_emotion = emotion_score * 0.6 + trend_score * 0.4
        final_emotion = max(0, min(100, final_emotion))
        
        ma_status = "多头排列" if ma_duotou else "空头排列" if ma_kongtou else "短期偏弱" if not above_ma5 else "中期偏弱" if not above_ma20 else "长期偏弱"
        
        if final_emotion >= 85:
            stage = "高潮"
            position = "80%"
        elif final_emotion >= 70:
            stage = "主升"
            position = "70%"
        elif final_emotion >= 55:
            stage = "修复"
            position = "50%"
        elif final_emotion >= 40:
            stage = "震荡"
            position = "35%"
        elif emotion_score >= 25:
            stage = "退潮"
            position = "20%"
        else:
            stage = "冰点"
            position = "10%"
        
        market_amount = daily_df['amount'].sum() / 100000
        
        return {
            "情绪指数": round(final_emotion, 1),
            "大盘点位": round(close, 2) if close else 0,
            "大盘涨跌幅": round(pct_chg, 2) if pct_chg else 0,
            "全市场成交额（亿元）": round(market_amount, 2),
            "市场阶段": stage,
            "涨停家数": int(zt_count),
            "跌停家数": int(dt_count),
            "连板高度": int(max_lb),
            "炸板率": round(broken_rate, 1),
            "上涨占比": round(up_ratio, 1),
            "强势股占比": round(strong_ratio, 1),
            "指数环境": trend_risk,
            "风险等级": "低风险" if final_emotion >= 70 else "中性" if final_emotion >= 50 else "高风险",
            "20日偏离率": round(bias20, 2),
            "MA20方向": ma20_slope,
            "趋势分": round(trend_score, 1),
            "最终建议仓位": position,
            "均线状态": ma_status,
            "站上MA5": above_ma5,
            "站上MA20": above_ma20,
            "站上MA60": above_ma60,
        }
    except Exception as e:
        print(f"大盘情绪分析失败: {e}")
        import traceback
        traceback.print_exc()
        return {}

# =========================
# 缓存管理器
# =========================
class CacheManager:
    def __init__(self, cache_dir, expire_minutes=240):
        self.cache_dir = cache_dir
        self.expire_minutes = expire_minutes
        os.makedirs(cache_dir, exist_ok=True)
    
    def _get_cache_key(self, func_name, **kwargs):
        key_parts = [func_name]
        for k, v in sorted(kwargs.items()):
            key_parts.append(f"{k}_{v}")
        return "_".join(key_parts)
    
    def _get_cache_file(self, cache_key):
        safe_key = cache_key.replace("/", "_").replace(":", "_")
        return os.path.join(self.cache_dir, f"cache_{safe_key}.pkl")
    
    def get(self, func_name, **kwargs):
        cache_key = self._get_cache_key(func_name, **kwargs)
        cache_file = self._get_cache_file(cache_key)
        
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'rb') as f:
                    cache_data = pickle.load(f)
                
                cache_time = cache_data.get('timestamp', 0)
                current_time = time.time()
                
                if current_time - cache_time < self.expire_minutes * 60:
                    return cache_data.get('data')
            except:
                pass
        
        return None
    
    def set(self, func_name, data, **kwargs):
        cache_key = self._get_cache_key(func_name, **kwargs)
        cache_file = self._get_cache_file(cache_key)
        
        cache_data = {
            'timestamp': time.time(),
            'data': data
        }
        
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(cache_data, f)
        except Exception as e:
            print(f"   缓存保存失败: {e}")

cache_manager = CacheManager(CACHE_DIR, expire_minutes=240)

# =========================
# SQLite数据库管理器
# =========================
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS theme_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                theme_name TEXT NOT NULL,
                today_score REAL,
                avg_score_10d REAL,
                avg_rank_10d REAL,
                score_trend TEXT,
                rank_change INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, theme_name)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leader_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                name TEXT,
                theme_name TEXT,
                total_score REAL,
                change_5 REAL,
                change_20 REAL,
                ma5_biased REAL,
                ma20_biased REAL,
                volume_ratio REAL,
                pullback_prob INTEGER,
                second_wave_prob INTEGER,
                limit_up_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, ts_code, theme_name)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                name TEXT,
                total_score REAL,
                probability INTEGER,
                ma5_biased REAL,
                volume_ratio REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, strategy_type, ts_code)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def save_theme_scores(self, trade_date, ranked_themes, theme_summary):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for theme, today_score in ranked_themes:
            summary = theme_summary.get(theme, {})
            cursor.execute('''
                INSERT OR REPLACE INTO theme_scores 
                (trade_date, theme_name, today_score, avg_score_10d, avg_rank_10d, score_trend, rank_change)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_date,
                theme,
                today_score,
                summary.get('avg_score_10d', 0),
                summary.get('avg_rank_10d', 0),
                summary.get('score_trend', '未知'),
                summary.get('rank_change', 0)
            ))
        
        conn.commit()
        conn.close()
        print(f"✓ 主题评分已保存至数据库: {len(ranked_themes)} 条记录")
    
    def save_leader_scores(self, trade_date, theme_leaders, theme_summary):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        count = 0
        for theme, leaders in theme_leaders.items():
            for leader in leaders:
                details = leader['score_details']
                ma_data = leader['ma_data']
                
                cursor.execute('''
                    INSERT OR REPLACE INTO leader_scores 
                    (trade_date, ts_code, name, theme_name, total_score, change_5, change_20,
                     ma5_biased, ma20_biased, volume_ratio, pullback_prob, second_wave_prob, limit_up_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    trade_date,
                    leader['ts_code'],
                    leader['name'],
                    theme,
                    leader['total_score'],
                    leader['change_5'],
                    leader['change_20'],
                    ma_data['ma5_biased'] if ma_data else 0,
                    ma_data['ma20_biased'] if ma_data else 0,
                    leader['volume_ratio'],
                    details.get('冲高回落概率', 0),
                    details.get('二波启动概率', 0),
                    leader['limit_up_count']
                ))
                count += 1
        
        conn.commit()
        conn.close()
        print(f"✓ 龙头股评分已保存至数据库: {count} 条记录")
    
    def save_strategy_recommendations(self, trade_date, strategies):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        strategy_names = {
            'strategy1': '强者恒强',
            'strategy2': '低吸潜伏',
            'strategy3': '轮动切换'
        }
        
        count = 0
        for strategy_key, strategy_type in strategy_names.items():
            leaders = strategies.get(strategy_key, [])
            for leader in leaders:
                ma_data = leader['ma_data']
                details = leader['score_details']
                
                cursor.execute('''
                    INSERT OR REPLACE INTO strategy_recommendations 
                    (trade_date, strategy_type, ts_code, name, total_score, probability, ma5_biased, volume_ratio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    trade_date,
                    strategy_type,
                    leader['ts_code'],
                    leader['name'],
                    leader['total_score'],
                    details.get('二波启动概率', 0),
                    ma_data['ma5_biased'] if ma_data else 0,
                    ma_data['volume_ratio'] if ma_data else 1
                ))
                count += 1
        
        conn.commit()
        conn.close()
        print(f"✓ 策略推荐已保存至数据库: {count} 条记录")

# 初始化数据库
DB_PATH = os.path.join(CACHE_DIR, "theme_analysis_hotmoney.db")
db_manager = DatabaseManager(DB_PATH)

# =========================
# 缓存的API调用函数
# =========================
def cached_trade_cal(start_date, end_date):
    func_name = "trade_cal"
    cached_data = cache_manager.get(func_name, start_date=start_date, end_date=end_date)
    if cached_data is not None:
        return cached_data
    df = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
    time.sleep(0.1)
    cache_manager.set(func_name, df, start_date=start_date, end_date=end_date)
    return df

def get_limit_list_d(trade_date):
    try:
        df = pro.limit_list_ths(trade_date=trade_date, limit_type='涨停池')
        return df
    except Exception as e:
        print(f"获取涨停数据失败: {e}")
        return pd.DataFrame()

def cached_limit_list_d(trade_date):
    func_name = "limit_list_d"
    cached_data = cache_manager.get(func_name, trade_date=trade_date)
    if cached_data is not None:
        return cached_data
    df = get_limit_list_d(trade_date)
    if df is not None and not df.empty:
        cache_manager.set(func_name, df, trade_date=trade_date)
    return df

def cached_daily_single(trade_date):
    func_name = "daily_single"
    cached_data = cache_manager.get(func_name, trade_date=trade_date)
    if cached_data is not None:
        return cached_data
    df = pro.daily(trade_date=trade_date)
    if df is not None and not df.empty:
        cache_manager.set(func_name, df, trade_date=trade_date)
    return df

def cached_limit_list_ths(trade_date, limit_type):
    func_name = "limit_list_ths"
    cached_data = cache_manager.get(func_name, trade_date=trade_date, limit_type=limit_type)
    if cached_data is not None:
        return cached_data
    try:
        df = pro.limit_list_ths(trade_date=trade_date, limit_type=limit_type)
        if df is not None and not df.empty:
            cache_manager.set(func_name, df, trade_date=trade_date, limit_type=limit_type)
        return df
    except Exception:
        return pd.DataFrame()

def cached_limit_step(trade_date):
    func_name = "limit_step"
    cached_data = cache_manager.get(func_name, trade_date=trade_date)
    if cached_data is not None:
        return cached_data
    try:
        df = pro.limit_step(trade_date=trade_date)
        if df is not None and not df.empty:
            cache_manager.set(func_name, df, trade_date=trade_date)
        return df
    except Exception:
        return pd.DataFrame()

def cached_index_daily(ts_code, start_date, end_date):
    func_name = "index_daily"
    cached_data = cache_manager.get(func_name, ts_code=ts_code, start_date=start_date, end_date=end_date)
    if cached_data is not None:
        return cached_data
    try:
        df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            cache_manager.set(func_name, df, ts_code=ts_code, start_date=start_date, end_date=end_date)
        return df
    except Exception:
        return pd.DataFrame()

_stock_limit_cache = {}

def get_stock_limit_info(ts_code):
    # 先检查内存缓存
    if ts_code in _stock_limit_cache:
        return _stock_limit_cache[ts_code]
    
    # 检查文件缓存
    func_name = "stock_limit_info"
    cached_data = cache_manager.get(func_name, ts_code=ts_code)
    if cached_data is not None:
        _stock_limit_cache[ts_code] = cached_data
        return cached_data
    
    try:
        time.sleep(0.2)

        df = pro.stock_basic(ts_code=ts_code)
        if df is not None and not df.empty:
            list_date = df.iloc[0].get('list_date', '')
            name = df.iloc[0].get('name', '')
            
            if name.startswith('*ST') or name.startswith('ST'):
                result = {'limit_up': 5.0, 'limit_down': -5.0, 'is_st': True}
            elif len(list_date) >= 8 and int(list_date) > 20230101:
                result = {'limit_up': 20.0, 'limit_down': -20.0, 'is_new': True}
            else:
                result = {'limit_up': 10.0, 'limit_down': -10.0, 'is_st': False}
            
            # 保存到缓存
            _stock_limit_cache[ts_code] = result
            cache_manager.set(func_name, result, ts_code=ts_code)
            return result
    except Exception as e:
        print(f"获取股票涨跌停信息失败: {e}")
    
    result = {'limit_up': 10.0, 'limit_down': -10.0, 'is_st': False}
    _stock_limit_cache[ts_code] = result
    return result

def is_limit_up(row, limit_info):
    pct_chg = row.get('pct_chg', 0)
    limit_up = limit_info.get('limit_up', 10.0)
    
    if limit_up == 5.0:
        return pct_chg >= 4.9
    elif limit_up == 20.0:
        return pct_chg >= 19.8
    else:
        return pct_chg >= 9.9

def _need_refresh_today_data(cached_data):
    if cached_data is None or cached_data.empty:
        return True
    
    now = datetime.now()
    if now.hour < 15:
        return False
    
    if 'trade_date' not in cached_data.columns:
        return True
    
    today_str = now.strftime('%Y%m%d')
    cached_dates = cached_data['trade_date'].astype(str).tolist()
    
    return today_str not in cached_dates

def cached_daily(ts_code, start_date, end_date):
    func_name = "daily"
    cached_data = cache_manager.get(func_name, ts_code=ts_code, start_date=start_date, end_date=end_date)
    
    need_refresh = _need_refresh_today_data(cached_data)
    
    if cached_data is not None and not need_refresh:
        return cached_data
    
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        time.sleep(0.02)
        
        cache_manager.expire_minutes = 999999999
        cache_manager.set(func_name, df, ts_code=ts_code, start_date=start_date, end_date=end_date)
        cache_manager.expire_minutes = 240
        
        if df is not None and not df.empty:
            limit_info = get_stock_limit_info(ts_code)
            df['is_limit_up'] = df.apply(lambda row: is_limit_up(row, limit_info), axis=1)
        
        return df
    except Exception as e:
        print(f"获取日线数据失败: {e}")
        return pd.DataFrame()

_trade_dates_cache = None
_bulk_daily_cache = {}  # {ts_code: DataFrame} 批量预加载缓存


def preload_bulk_daily_data(all_ts_codes, start_date, end_date):
    """批量预加载所有股票日线数据（一次API调用），避免逐只股票分别请求"""
    global _bulk_daily_cache
    if not all_ts_codes:
        return

    need_codes = [c for c in all_ts_codes if c not in _bulk_daily_cache]
    if not need_codes:
        return

    print(f"\n⏳ 批量预加载 {len(need_codes)} 只股票日线数据 ({start_date}~{end_date})...")
    batch_size = 2000
    total = 0
    for i in range(0, len(need_codes), batch_size):
        batch = need_codes[i:i + batch_size]
        try:
            df = pro.daily(ts_code=','.join(batch), start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                for ts_code in df['ts_code'].unique():
                    sub = df[df['ts_code'] == ts_code].copy()
                    sub['trade_date'] = sub['trade_date'].astype(str)
                    _bulk_daily_cache[ts_code] = sub.sort_values('trade_date').reset_index(drop=True)
                total += len(df['ts_code'].unique())
        except Exception as e:
            print(f"  ⚠ 批量加载部分失败: {e}")
        time.sleep(0.2)
    print(f"✅ 批量预加载完成: {total} / {len(need_codes)} 只股票")

def get_trade_dates(n_days=25):
    global _trade_dates_cache
    if _trade_dates_cache is not None:
        return _trade_dates_cache
    
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    cal = cached_trade_cal('20250101', query_date)
    cal = cal[cal['is_open'] == 1]
    cal = cal.sort_values('cal_date', ascending=False)
    trade_dates = cal['cal_date'].head(n_days).tolist()
    trade_dates.reverse()
    _trade_dates_cache = [str(d) for d in trade_dates]
    return _trade_dates_cache

# =========================
# 从SQLite数据库加载主题成份股（备用：从CSV文件加载）
# =========================
def load_theme_portfolio_from_csv():
    # 优先从SQLite数据库加载
    db_path = os.path.join(CACHE_DIR, "theme_portfolio.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio'")
            if cursor.fetchone():
                cursor.execute("SELECT COUNT(*) FROM portfolio")
                count = cursor.fetchone()[0]
                if count > 0:
                    cursor.execute("SELECT ts_code, name, theme_name FROM portfolio")
                    rows = cursor.fetchall()
                    conn.close()

                    theme_stocks_map = {}
                    name_map = {}
                    for ts_code, name, theme_name in rows:
                        if theme_name not in theme_stocks_map:
                            theme_stocks_map[theme_name] = []
                        theme_stocks_map[theme_name].append(ts_code)
                        if ts_code not in name_map:
                            name_map[ts_code] = name

                    print(f"从数据库加载主题投资组合: {len(theme_stocks_map)} 个主题，{len(name_map)} 只股票")
                    return theme_stocks_map, name_map
            conn.close()
        except Exception as e:
            print(f"从数据库加载失败，尝试CSV文件: {e}")
    
    # 回退到从CSV文件加载
    csv_pattern = os.path.join(CACHE_DIR, "theme_portfolio_*.csv")
    csv_files = glob.glob(csv_pattern)
    
    if not csv_files:
        print("未找到主题投资组合数据，请先运行 theme_portfolio_strategy_cached.py")
        return {}, {}
    
    latest_file = max(csv_files, key=os.path.getmtime)
    print(f"从CSV文件加载主题投资组合: {latest_file}")
    
    df = pd.read_csv(latest_file, encoding='utf-8-sig')
    
    theme_stocks_map = {}
    name_map = {}
    
    for _, row in df.iterrows():
        theme = row['themes']
        ts_code = row['ts_code']
        name = row['name']
        
        if theme not in theme_stocks_map:
            theme_stocks_map[theme] = []
        theme_stocks_map[theme].append(ts_code)
        
        if ts_code not in name_map:
            name_map[ts_code] = name
    
    print(f"加载了 {len(theme_stocks_map)} 个主题，{len(name_map)} 只股票")
    return theme_stocks_map, name_map

# =========================
# 获取股票历史数据
# =========================
def get_stock_history(ts_code, n_days=25):
    # 优先使用批量预加载缓存
    if ts_code in _bulk_daily_cache:
        df = _bulk_daily_cache[ts_code].copy()
        if df is not None and not df.empty and len(df) >= 3:
            return df.sort_values('trade_date').reset_index(drop=True)
    
    trade_dates = get_trade_dates(n_days)
    start_date = trade_dates[0]
    end_date = trade_dates[-1]
    df = cached_daily(ts_code, start_date, end_date)
    if df is None or df.empty or len(df) < 3:
        return pd.DataFrame()
    df['trade_date'] = df['trade_date'].astype(str)
    df = df[df['trade_date'].isin(trade_dates)]
    if len(df) >= 3:
        return df.sort_values('trade_date').reset_index(drop=True)
    return pd.DataFrame()

# =========================
# 均线和乖离率计算
# =========================
def calculate_ma_and_biased(df):
    if df is None or df.empty or len(df) < 20:
        return None
    
    df = df.sort_values('trade_date').copy()
    close_prices = df['close'].values if 'close' in df.columns else df.iloc[:, 0].values
    
    ma5 = np.mean(close_prices[-5:]) if len(close_prices) >= 5 else close_prices[-1]
    ma10 = np.mean(close_prices[-10:]) if len(close_prices) >= 10 else ma5
    ma20 = np.mean(close_prices[-20:]) if len(close_prices) >= 20 else ma10
    
    current_price = close_prices[-1]
    
    ma5_biased = ((current_price - ma5) / ma5) * 100 if ma5 != 0 else 0
    ma10_biased = ((current_price - ma10) / ma10) * 100 if ma10 != 0 else 0
    ma20_biased = ((current_price - ma20) / ma20) * 100 if ma20 != 0 else 0
    
    ma5_slope = ((ma5 - np.mean(close_prices[-10:-5])) / np.mean(close_prices[-10:-5])) * 100 if len(close_prices) >= 10 and np.mean(close_prices[-10:-5]) != 0 else 0
    ma20_slope = ((ma20 - np.mean(close_prices[-25:-20])) / np.mean(close_prices[-25:-20])) * 100 if len(close_prices) >= 25 and np.mean(close_prices[-25:-20]) != 0 else 0
    
    volume = df['vol'].values if 'vol' in df.columns else df['amount'].values if 'amount' in df.columns else np.array([1]*len(df))
    avg_volume_5 = np.mean(volume[-5:]) if len(volume) >= 5 else np.mean(volume)
    avg_volume_20 = np.mean(volume[-20:]) if len(volume) >= 20 else avg_volume_5
    volume_ratio = avg_volume_5 / avg_volume_20 if avg_volume_20 != 0 else 1
    
    return {
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
        'current_price': current_price,
        'ma5_biased': ma5_biased, 'ma10_biased': ma10_biased, 'ma20_biased': ma20_biased,
        'ma5_slope': ma5_slope, 'ma20_slope': ma20_slope,
        'volume_ratio': volume_ratio, 'avg_volume_5': avg_volume_5, 'avg_volume_20': avg_volume_20
    }

# =========================
# 冲高回落概率计算
# =========================
def calculate_pullback_probability(ma_data, recent_data):
    if ma_data is None:
        return 0, "数据不足", []
    
    pullback_score = 0
    reasons = []
    
    if ma_data['ma5_biased'] > 10:
        pullback_score += 30
        reasons.append(f"5日乖离率过大({ma_data['ma5_biased']:.1f}%)")
    elif ma_data['ma5_biased'] > 5:
        pullback_score += 15
        reasons.append(f"5日乖离率偏高({ma_data['ma5_biased']:.1f}%)")
    
    if ma_data['ma20_biased'] > 20:
        pullback_score += 30
        reasons.append(f"20日乖离率过大({ma_data['ma20_biased']:.1f}%)")
    elif ma_data['ma20_biased'] > 10:
        pullback_score += 15
        reasons.append(f"20日乖离率偏高({ma_data['ma20_biased']:.1f}%)")
    
    if ma_data['volume_ratio'] < 0.7:
        pullback_score += 20
        reasons.append(f"量能萎缩({ma_data['volume_ratio']:.2f})")
    elif ma_data['volume_ratio'] < 0.9:
        pullback_score += 10
        reasons.append(f"量能不足({ma_data['volume_ratio']:.2f})")
    
    if ma_data['ma5_slope'] > 10:
        pullback_score += 20
        reasons.append("5日均线上升过陡")
    
    if recent_data is not None and not recent_data.empty and len(recent_data) >= 5:
        recent_5 = recent_data.tail(5)
        last_change = recent_5.iloc[-1]['pct_chg'] if 'pct_chg' in recent_5.columns else 0
        if last_change > 8:
            pullback_score += 15
            reasons.append(f"单日涨幅过大({last_change:.1f}%)")
    
    pullback_probability = min(100, pullback_score)
    
    if pullback_probability >= 70:
        risk_level = "⚠️ 高风险"
    elif pullback_probability >= 50:
        risk_level = "⚡ 中风险"
    elif pullback_probability >= 30:
        risk_level = "📊 低风险"
    else:
        risk_level = "✅ 安全"
    
    return pullback_probability, risk_level, reasons

# =========================
# 二波启动概率计算（优化版）
# =========================
def calculate_second_wave_probability(ma_data, recent_data):
    if ma_data is None:
        return 0, "数据不足", []
    
    second_wave_score = 0
    reasons = []
    penalties = []
    
    is_first_day_correction = False
    if recent_data is not None and not recent_data.empty and len(recent_data) >= 2:
        last_day = recent_data.iloc[-1]['pct_chg'] if 'pct_chg' in recent_data.columns else 0
        prev_day = recent_data.iloc[-2]['pct_chg'] if 'pct_chg' in recent_data.columns else 0
        
        if prev_day > 3 and last_day < -3:
            is_first_day_correction = True
            penalties.append("⚠️ 第一天从大涨转为下跌，需确认")
        elif prev_day > 0 and last_day < -5:
            is_first_day_correction = True
            penalties.append("⚠️ 第一天调整，跌幅较大")
    
    if is_first_day_correction:
        return 30, "🔄 待确认", penalties
    
    if -5 <= ma_data['ma5_biased'] <= 2:
        second_wave_score += 25
        reasons.append(f"5日乖离率适中({ma_data['ma5_biased']:.1f}%)")
    elif -8 <= ma_data['ma5_biased'] < -5:
        second_wave_score += 15
        reasons.append(f"5日均线支撑({ma_data['ma5_biased']:.1f}%)")
    elif ma_data['ma5_biased'] > 5:
        penalties.append(f"5日乖离偏高({ma_data['ma5_biased']:.1f}%)")
    
    if ma_data['ma5'] > ma_data['ma10'] and ma_data['ma10'] > ma_data['ma20']:
        second_wave_score += 20
        reasons.append("均线多头排列")
    elif ma_data['ma5'] < ma_data['ma10']:
        penalties.append("5日均线破10日线")
    
    if ma_data['ma20_slope'] > 0:
        second_wave_score += 10
        reasons.append("20日均线向上")
    
    if 0.7 <= ma_data['volume_ratio'] <= 1.3:
        second_wave_score += 20
        reasons.append(f"量能健康({ma_data['volume_ratio']:.2f})")
    elif ma_data['volume_ratio'] > 1.3:
        second_wave_score += 25
        reasons.append(f"量能放大({ma_data['volume_ratio']:.2f})")
    elif ma_data['volume_ratio'] < 0.6:
        penalties.append(f"量能萎缩({ma_data['volume_ratio']:.2f})")
    
    if -5 <= ma_data['ma20_biased'] <= 10:
        second_wave_score += 15
        reasons.append(f"20日乖离率健康({ma_data['ma20_biased']:.1f}%)")
    elif ma_data['ma20_biased'] > 15:
        penalties.append(f"20日乖离过高({ma_data['ma20_biased']:.1f}%)")
    
    if recent_data is not None and not recent_data.empty and len(recent_data) >= 10:
        recent_10 = recent_data.tail(10)
        max_change = recent_10['pct_chg'].max() if 'pct_chg' in recent_10.columns else 0
        if max_change >= 10:
            second_wave_score += 10
            reasons.append(f"前期有涨停({max_change:.1f}%)")
    
    if recent_data is not None and not recent_data.empty and len(recent_data) >= 5:
        recent_5 = recent_data.tail(5)
        recent_5_changes = recent_5['pct_chg'].values if 'pct_chg' in recent_5.columns else []
        
        if len(recent_5_changes) >= 4:
            if all(x > 0 for x in recent_5_changes[:-1]) and recent_5_changes[-1] < -3:
                second_wave_score = max(0, second_wave_score - 30)
                penalties.append("连续上涨后首次大跌，需要确认")
    
    second_wave_probability = min(100, second_wave_score)
    
    if second_wave_probability >= 80:
        wave_level = "🚀 强二波信号"
    elif second_wave_probability >= 60:
        wave_level = "📈 二波概率高"
    elif second_wave_probability >= 40:
        wave_level = "🔄 待确认"
    else:
        wave_level = "❌ 二波概率低"
    
    all_reasons = reasons + penalties
    return second_wave_probability, wave_level, all_reasons

# =========================
# 调整后回升概率计算（优化版）
# =========================
def calculate_rebound_probability(ma_data, recent_data, theme_avg_change):
    if ma_data is None:
        return 0, "数据不足", []
    
    rebound_score = 0
    reasons = []
    penalties = []
    
    is_first_day_correction = False
    if recent_data is not None and not recent_data.empty and len(recent_data) >= 2:
        last_day = recent_data.iloc[-1]['pct_chg'] if 'pct_chg' in recent_data.columns else 0
        prev_day = recent_data.iloc[-2]['pct_chg'] if 'pct_chg' in recent_data.columns else 0
        
        if prev_day > 3 and last_day < -3:
            is_first_day_correction = True
            penalties.append("⚠️ 第一天从大涨转为下跌，需确认")
        elif prev_day > 0 and last_day < -5:
            is_first_day_correction = True
            penalties.append("⚠️ 第一天调整，跌幅较大")
    
    if is_first_day_correction:
        rebound_score = 20
        return 20, "🔄 待确认", penalties
    
    if -3 <= ma_data['ma5_biased'] <= 2:
        rebound_score += 20
        reasons.append(f"5日均线乖离率健康({ma_data['ma5_biased']:.1f}%)")
    elif -5 <= ma_data['ma5_biased'] < -3:
        rebound_score += 15
        reasons.append(f"5日均线乖离率偏大({ma_data['ma5_biased']:.1f}%)")
    elif ma_data['ma5_biased'] > 5:
        penalties.append(f"5日乖离偏高({ma_data['ma5_biased']:.1f}%)")
    
    if -5 <= ma_data['ma20_biased'] <= 5:
        rebound_score += 20
        reasons.append(f"20日均线乖离率健康({ma_data['ma20_biased']:.1f}%)")
    elif -10 <= ma_data['ma20_biased'] < -5:
        rebound_score += 15
        reasons.append(f"20日均线乖离率偏大({ma_data['ma20_biased']:.1f}%)")
    elif ma_data['ma20_biased'] > 12:
        penalties.append(f"20日乖离过高({ma_data['ma20_biased']:.1f}%)")
    
    if 0.5 <= ma_data['volume_ratio'] <= 0.8:
        rebound_score += 20
        reasons.append(f"量能萎缩至地量，调整充分({ma_data['volume_ratio']:.2f})")
    elif ma_data['volume_ratio'] < 0.5:
        rebound_score += 25
        reasons.append(f"极度缩量，主力控盘({ma_data['volume_ratio']:.2f})")
    elif ma_data['volume_ratio'] > 1.5:
        penalties.append(f"量能过大({ma_data['volume_ratio']:.2f})")
    
    if ma_data['ma5'] > ma_data['ma10']:
        rebound_score += 15
        reasons.append("5日均线向上金叉")
    elif ma_data['ma5'] < ma_data['ma10']:
        penalties.append("5日均线破10日线")
    
    if ma_data['ma10'] > ma_data['ma20']:
        rebound_score += 15
        reasons.append("10日均线向上金叉")
    
    if recent_data is not None and not recent_data.empty and len(recent_data) >= 5:
        recent_5 = recent_data.tail(5)
        recent_5_change = recent_5['pct_chg'].sum() if 'pct_chg' in recent_5.columns else 0
        
        if -10 <= recent_5_change <= -5:
            rebound_score += 20
            reasons.append(f"近期调整幅度适中({recent_5_change:.1f}%)")
        elif recent_5_change < -10:
            rebound_score += 15
            reasons.append(f"近期超跌，存在反弹需求({recent_5_change:.1f}%)")
        elif recent_5_change > 0:
            penalty = f"近期还在上涨({recent_5_change:.1f}%)，不是调整"
            penalties.append(penalty)
    
    if theme_avg_change > 0:
        rebound_score += 10
        reasons.append(f"板块整体趋势向上({theme_avg_change:.1f}%)")
    
    if 0.8 <= ma_data['volume_ratio'] <= 1.2:
        rebound_score += 10
        reasons.append("量价配合良好")
    
    if penalties:
        rebound_score = max(0, rebound_score - len(penalties) * 10)
    
    rebound_probability = min(100, rebound_score)
    
    if rebound_probability >= 80:
        rebound_level = "🚀 强回升信号"
    elif rebound_probability >= 60:
        rebound_level = "📈 回升概率高"
    elif rebound_probability >= 40:
        rebound_level = "🔄 待确认"
    else:
        rebound_level = "❌ 回升概率低"
    
    all_reasons = reasons + penalties
    return rebound_probability, rebound_level, all_reasons

# =========================
# 增强版成交量结构评分（游资看重放量）
# =========================
def volume_structure_enhanced(df):
    try:
        vol_trend = df['vol'].tail(20).corr(pd.Series(range(20), index=df.tail(20).index))
        base_score = max(0, min(10, vol_trend * 10))
        
        latest = df.iloc[-1]
        vol_ratio = latest['vol'] / latest['vol'].rolling(5).mean().iloc[-1] if len(df) >= 5 else 1
        
        vol_bonus = 0
        if vol_ratio > 2.0:
            vol_bonus = 8
        elif vol_ratio > 1.5:
            vol_bonus = 5
        elif vol_ratio > 1.2:
            vol_bonus = 3
        
        return min(15, base_score + vol_bonus)
    except:
        return 5

# =========================
# 涨停基因检测（游资特色指标）
# =========================
def limit_up_dna(df):
    try:
        limit_up_count = 0
        for i in range(min(20, len(df)-1)):
            pct = df['pct_chg'].iloc[-i-1]
            if pct >= 9.5:
                limit_up_count += 1
        
        if limit_up_count >= 3:
            return 15
        elif limit_up_count >= 2:
            return 10
        elif limit_up_count >= 1:
            return 5
        return 0
    except:
        return 0

# =========================
# 短线游资风格 - 个股评分
# =========================
def calculate_hotmoney_stock_score(df, ma_data):
    if df is None or df.empty or ma_data is None:
        return 0
    
    score = 0
    latest = df.iloc[-1]
    
    if len(df) >= 5:
        change_5 = df['pct_chg'].tail(5).sum()
        score += change_5 * 1.5
    
    if len(df) >= 3:
        change_3 = df['pct_chg'].tail(3).sum()
        score += change_3 * 2
    
    if ma_data['ma5'] > ma_data['ma10'] and ma_data['ma10'] > ma_data['ma20']:
        score += 20
    elif ma_data['ma5'] > ma_data['ma20']:
        score += 10
    
    vol_ratio = ma_data.get('volume_ratio', 1)
    if vol_ratio > 2.0:
        score += 15
    elif vol_ratio > 1.5:
        score += 10
    elif vol_ratio > 1.2:
        score += 5
    
    score += limit_up_dna(df)
    
    if len(df) >= 5:
        recent_big_drop = 0
        for i in range(min(5, len(df)-1)):
            pct = df['pct_chg'].iloc[-i-1]
            if pct <= -5:
                recent_big_drop += 1
        
        if recent_big_drop == 0:
            score += 10
        elif recent_big_drop == 1:
            score += 3
    
    return round(score, 2)

# =========================
# 计算股票综合评分（游资风格）
# =========================
def calculate_comprehensive_leader_score(ts_code, name_map):
    df = get_stock_history(ts_code, 25)
    
    if df is None or df.empty or len(df) < 20:
        return None
    
    recent_5 = df.tail(5)
    recent_10 = df.tail(10)
    recent_20 = df.tail(20) if len(df) >= 20 else df
    
    ma_data = calculate_ma_and_biased(df)
    
    if ma_data is None:
        return None
    
    total_score = calculate_hotmoney_stock_score(df, ma_data)
    
    score_details = {}
    
    change_5 = recent_5['pct_chg'].sum()
    change_20 = recent_20['pct_chg'].sum()
    
    pullback_prob, pullback_level, pullback_reasons = calculate_pullback_probability(ma_data, df)
    score_details['冲高回落概率'] = pullback_prob
    score_details['回落风险等级'] = pullback_level
    score_details['回落原因'] = pullback_reasons
    
    second_wave_prob, wave_level, wave_reasons = calculate_second_wave_probability(ma_data, df)
    score_details['二波启动概率'] = second_wave_prob
    score_details['二波信号等级'] = wave_level
    score_details['二波原因'] = wave_reasons
    
    limit_info = get_stock_limit_info(ts_code)
    limit_up_count = len(df[df.apply(lambda row: is_limit_up(row, limit_info), axis=1)])
    
    if total_score > 30:
        return {
            'ts_code': ts_code,
            'name': name_map.get(ts_code, ts_code),
            'total_score': total_score,
            'score_details': score_details,
            'ma_data': ma_data,
            'change_5': change_5,
            'change_20': change_20,
            'limit_up_count': limit_up_count,
            'volume_ratio': ma_data['volume_ratio']
        }
    
    return None

# =========================
# 计算主题历史排名和平均分
# =========================
def calculate_theme_historical_rankings(theme_stocks_map, trade_dates):
    theme_daily_scores = {theme: [] for theme in theme_stocks_map.keys()}
    
    print("\n计算近10日主题排名和平均分...")
    
    for date_idx in range(len(trade_dates) - 10, len(trade_dates)):
        date = trade_dates[date_idx]
        daily_scores = {}
        
        for theme_name, theme_stocks in theme_stocks_map.items():
            stock_scores = []
            for ts_code in list(theme_stocks)[:20]:
                df = get_stock_history(ts_code, date_idx + 5)
                if df is not None and not df.empty and date in df['trade_date'].values:
                    daily_data = df[df['trade_date'] == date].iloc[0]
                    score = daily_data.get('pct_chg', 0) * 2 + 50
                    stock_scores.append(score)
            
            if stock_scores:
                avg_score = np.mean(stock_scores)
                daily_scores[theme_name] = avg_score
            else:
                daily_scores[theme_name] = 50
        
        sorted_themes = sorted(daily_scores.items(), key=lambda x: x[1], reverse=True)
        for rank, (theme, score) in enumerate(sorted_themes, 1):
            theme_daily_scores[theme].append({
                'date': date,
                'score': score,
                'rank': rank
            })
    
    theme_summary = {}
    for theme, daily_records in theme_daily_scores.items():
        if daily_records:
            scores = [r['score'] for r in daily_records]
            ranks = [r['rank'] for r in daily_records]
            
            theme_summary[theme] = {
                'avg_score_10d': np.mean(scores),
                'avg_rank_10d': np.mean(ranks),
                'score_trend': '上升' if scores[-1] > scores[0] else '下降' if scores[-1] < scores[0] else '震荡',
                'rank_change': ranks[0] - ranks[-1],
                'daily_scores': scores,
                'daily_ranks': ranks
            }
    
    return theme_summary

# =========================
# 识别主题龙头（游资风格）
# =========================
def identify_theme_leaders(theme_stocks, name_map):
    leaders = []
    
    for ts_code in list(theme_stocks)[:50]:
        result = calculate_comprehensive_leader_score(ts_code, name_map)
        
        if result is not None:
            leaders.append(result)
    
    leaders.sort(key=lambda x: x['total_score'], reverse=True)
    return leaders[:10]

# =========================
# 主题投资优化评分
# =========================
def _clip_score(value, min_value=0, max_value=100):
    return max(min_value, min(max_value, value))

def calculate_theme_market_stats(theme_stocks, max_stocks=50):
    """
    主题级市场统计：用于衡量主题是否只是单一龙头强，还是板块整体有赚钱效应。
    """
    records = []
    
    for ts_code in list(theme_stocks)[:max_stocks]:
        df = get_stock_history(ts_code, 25)
        if df is None or df.empty or len(df) < 5:
            continue
        
        ma_data = calculate_ma_and_biased(df)
        recent_3 = df['pct_chg'].tail(3).sum() if len(df) >= 3 else 0
        recent_5 = df['pct_chg'].tail(5).sum()
        latest = df.iloc[-1]
        
        records.append({
            'ts_code': ts_code,
            'pct_1d': latest.get('pct_chg', 0),
            'pct_3d': recent_3,
            'pct_5d': recent_5,
            'volume_ratio': ma_data.get('volume_ratio', 1) if ma_data else 1,
            'ma5_biased': ma_data.get('ma5_biased', 0) if ma_data else 0,
            'is_limit_up': latest.get('pct_chg', 0) >= 9.5,
            'is_big_drop': latest.get('pct_chg', 0) <= -5,
        })
    
    if not records:
        return {
            'sample_size': 0,
            'avg_1d': 0,
            'avg_3d': 0,
            'avg_5d': 0,
            'up_ratio': 0,
            'strong_ratio': 0,
            'limit_up_count': 0,
            'big_drop_ratio': 0,
            'avg_volume_ratio': 1,
            'avg_ma5_biased': 0,
        }
    
    df_stats = pd.DataFrame(records)
    sample_size = len(df_stats)
    
    return {
        'sample_size': sample_size,
        'avg_1d': float(df_stats['pct_1d'].mean()),
        'avg_3d': float(df_stats['pct_3d'].mean()),
        'avg_5d': float(df_stats['pct_5d'].mean()),
        'up_ratio': float((df_stats['pct_1d'] > 0).mean() * 100),
        'strong_ratio': float((df_stats['pct_1d'] >= 3).mean() * 100),
        'limit_up_count': int(df_stats['is_limit_up'].sum()),
        'big_drop_ratio': float(df_stats['is_big_drop'].mean() * 100),
        'avg_volume_ratio': float(df_stats['volume_ratio'].mean()),
        'avg_ma5_biased': float(df_stats['ma5_biased'].mean()),
    }

def calculate_optimized_theme_score(theme_name, theme_stocks, leaders, theme_summary):
    """
    面向主题投资的综合评分：
    - 龙头强度：主题是否有可交易的核心标的
    - 广度/赚钱效应：不是只靠一只票拉分
    - 涨停扩散：短线资金是否形成共识
    - 量能确认：放量但不过度拥挤
    - 持续性：近10日排名趋势与评分趋势
    - 风险惩罚：乖离过高、跌幅扩散、龙头回落风险
    """
    stats = calculate_theme_market_stats(theme_stocks)
    summary = theme_summary.get(theme_name, {})
    
    if leaders:
        leader_scores = [l['total_score'] for l in leaders[:5]]
        leader_strength = np.mean(leader_scores[:3]) if len(leader_scores) >= 3 else np.mean(leader_scores)
        best_leader = leaders[0]
        leader_risk = best_leader['score_details'].get('冲高回落概率', 0)
        second_wave = best_leader['score_details'].get('二波启动概率', 0)
    else:
        leader_strength = 0
        leader_risk = 0
        second_wave = 0
    
    breadth_score = _clip_score(stats['up_ratio'] * 0.65 + stats['strong_ratio'] * 0.8)
    momentum_score = _clip_score(50 + stats['avg_3d'] * 4 + stats['avg_5d'] * 2)
    limit_score = _clip_score(stats['limit_up_count'] * 12)
    
    vol_ratio = stats['avg_volume_ratio']
    if 1.05 <= vol_ratio <= 1.8:
        volume_score = 80
    elif 0.8 <= vol_ratio < 1.05 or 1.8 < vol_ratio <= 2.5:
        volume_score = 60
    elif vol_ratio > 2.5:
        volume_score = 45
    else:
        volume_score = 35
    
    rank_change = summary.get('rank_change', 0)
    trend_bonus = 0
    if summary.get('score_trend') == '上升':
        trend_bonus += 8
    elif summary.get('score_trend') == '下降':
        trend_bonus -= 8
    trend_bonus += _clip_score(rank_change * 2, -10, 10)
    
    risk_penalty = 0
    if stats['avg_ma5_biased'] > 8:
        risk_penalty += 8
    if stats['avg_ma5_biased'] > 12:
        risk_penalty += 8
    if stats['big_drop_ratio'] >= 15:
        risk_penalty += 12
    if leader_risk >= 70:
        risk_penalty += 10
    elif leader_risk >= 50:
        risk_penalty += 5
    
    score = (
        leader_strength * 0.30 +
        breadth_score * 0.18 +
        momentum_score * 0.18 +
        limit_score * 0.12 +
        volume_score * 0.10 +
        second_wave * 0.12 +
        trend_bonus -
        risk_penalty
    )
    
    return round(_clip_score(score), 2), stats

# =========================
# 今日主题评分与轮动分析
# =========================
def output_theme_analysis(ranked_themes, theme_summary, theme_leaders):
    print("\n\n" + "="*100)
    print("【今日主题评分与轮动分析 - 游资风格】")
    print("="*100)
    
    print("\n📊 今日主题完整排名:")
    for rank, (theme, today_score) in enumerate(ranked_themes, 1):
        summary = theme_summary.get(theme, {})
        avg_10d = summary.get('avg_score_10d', 0)
        avg_rank = summary.get('avg_rank_10d', 0)
        trend = summary.get('score_trend', '未知')
        rank_change = summary.get('rank_change', 0)
        
        trend_icon = "📈" if trend == "上升" else "📉" if trend == "下降" else "➡️"
        rank_change_icon = "⬆️" if rank_change > 0 else "⬇️" if rank_change < 0 else "➖"
        
        zt_stocks = []
        if theme in theme_leaders:
            for leader in theme_leaders[theme]:
                if leader['limit_up_count'] > 0:
                    zt_stocks.append(f"{leader['name']}({leader['limit_up_count']}次)")
        zt_str = f" | 涨停: {', '.join(zt_stocks[:3])}" if zt_stocks else ""
        
        print(f"\n{rank}. 【{theme}】")
        print(f"  今日评分: {today_score:.1f} | 近10日平均分: {avg_10d:.1f}")
        rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
        print(f"  近10日平均排名: {avg_rank:.1f} | 排名变化: {rank_change_icon}{rank_change_text}{zt_str}")
        print(f"  趋势方向: {trend_icon} {trend}")
    
    print("\n\n" + "="*100)
    print("🔥 主题轮动分析")
    print("="*100)
    
    print("\n⬆️ 上升趋势板块（评分高且趋势向上）:")
    rising_themes = [(t, s) for t, s in ranked_themes if theme_summary.get(t, {}).get('score_trend') == "上升"]
    if rising_themes:
        for rank, (theme, score) in enumerate(rising_themes[:5], 1):
            rank_change = theme_summary.get(theme, {}).get('rank_change', 0)
            rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
            print(f"   {rank}. {theme} - 今日评分: {score:.1f} (排名{rank_change_text})")
    else:
        print("   暂无可推荐板块")
    
    print("\n⬇️ 下降趋势板块（评分下降趋势）:")
    falling_themes = [(t, s) for t, s in ranked_themes if theme_summary.get(t, {}).get('score_trend') == "下降"]
    if falling_themes:
        for rank, (theme, score) in enumerate(falling_themes[:5], 1):
            rank_change = theme_summary.get(theme, {}).get('rank_change', 0)
            rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
            print(f"   {rank}. {theme} - 今日评分: {score:.1f} (排名{rank_change_text})")
    else:
        print("   暂无可推荐板块")

# =========================
# 今日龙头评分与轮动分析（游资风格）
# =========================
def output_leader_analysis(theme_leaders):
    print("\n\n" + "="*100)
    print("【今日龙头评分与轮动分析 - 游资风格】")
    print("="*100)
    
    leaders_dict = {}
    for theme, leaders in theme_leaders.items():
        for leader in leaders:
            ts_code = leader['ts_code']
            if ts_code not in leaders_dict:
                leaders_dict[ts_code] = leader.copy()
                leaders_dict[ts_code]['themes'] = []
            leaders_dict[ts_code]['themes'].append(theme)
    
    all_leaders = sorted(leaders_dict.values(), key=lambda x: x['total_score'], reverse=True)
    
    print("\n🏆 全市场综合 TOP 10 龙头（游资评分）:")
    for rank, leader in enumerate(all_leaders[:10], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        themes_str = "、".join(leader['themes'][:3])
        if len(leader['themes']) > 3:
            themes_str += f" 等{len(leader['themes'])}个主题"
        
        print(f"\n{rank}. {leader['name']:10s} ({leader['ts_code']:10s})")
        print(f"   所属主题: {themes_str}")
        print(f"   游资评分: {leader['total_score']:.1f} | 5日涨幅: {leader['change_5']:+.1f}% | 20日涨幅: {leader['change_20']:+.1f}%")
        print(f"   5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
        print(f"   {details['回落风险等级']} | {details['二波信号等级']} | 涨停次数: {leader['limit_up_count']}次")
    
    print("\n📈 二波信号龙头（强信号）:")
    strong_wave_leaders = [l for l in all_leaders if l['score_details']['二波启动概率'] >= 70]
    if strong_wave_leaders:
        for rank, leader in enumerate(strong_wave_leaders[:5], 1):
            print(f"   {rank}. {leader['name']:10s} ({leader['ts_code']:10s}) - 二波概率: {leader['score_details']['二波启动概率']:.0f}%")
    else:
        print("   暂无可推荐龙头")

# =========================
# 三种策略推荐函数
# =========================
def get_strategy_recommendations(ranked_themes, theme_leaders, theme_summary):
    today_theme_scores = dict(ranked_themes)
    all_leaders = []
    for theme, leaders in theme_leaders.items():
        for leader in leaders:
            leader_copy = leader.copy()
            leader_copy['theme'] = theme
            leader_copy['theme_today_score'] = today_theme_scores.get(theme, 0)
            leader_copy['theme_avg_score'] = theme_summary.get(theme, {}).get('avg_score_10d', 50)
            leader_copy['theme_rank_change'] = theme_summary.get(theme, {}).get('rank_change', 0)
            all_leaders.append(leader_copy)
    
    # 按ts_code去重（防止同一股票因跨主题重复出现）
    seen_codes = set()
    deduped_leaders = []
    for l in all_leaders:
        if l['ts_code'] not in seen_codes:
            seen_codes.add(l['ts_code'])
            deduped_leaders.append(l)
    all_leaders = deduped_leaders
    
    strong_leaders = [
        l for l in all_leaders
        if l['total_score'] > 80
        and l['score_details']['二波启动概率'] >= 70
        and l['ma_data']['ma5_biased'] < 10
    ]
    strong_leaders.sort(key=lambda x: (x['total_score'], x['score_details']['二波启动概率']), reverse=True)
    strategy1 = strong_leaders[:3]
    
    pullback_leaders = [
        l for l in all_leaders
        if -5 <= l['ma_data']['ma5_biased'] <= 2
        and l['score_details']['二波启动概率'] >= 40
        and l['ma_data']['volume_ratio'] <= 1.3
        and l['limit_up_count'] == 0
    ]
    pullback_leaders.sort(key=lambda x: x['score_details']['二波启动概率'], reverse=True)
    strategy2 = pullback_leaders[:3]
    
    rotation_leaders = [
        l for l in all_leaders
        if l['theme_rank_change'] > 0
        and l['theme_today_score'] >= 55
        and l['score_details']['二波启动概率'] >= 50
        and l['score_details']['冲高回落概率'] < 70
    ]
    rotation_leaders.sort(
        key=lambda x: (x['theme_today_score'], x['theme_rank_change'], x['score_details']['二波启动概率']),
        reverse=True
    )
    strategy3 = rotation_leaders[:3]
    
    return {
        'strategy1': strategy1,
        'strategy2': strategy2,
        'strategy3': strategy3
    }

# =========================
# 明日中低风险主题和龙头推荐
# =========================
def output_tomorrow_recommendation(ranked_themes, theme_leaders, theme_summary, market_emotion=None):
    print("\n\n" + "="*100)
    print("【明日中低风险主题和龙头推荐】")
    print("="*100)
    
    print("\n🎯 推荐原则:")
    print("   ✅ 回落风险等级: 📊低风险 或 ✅安全")
    print("   ✅ 二波信号等级: 🚀强信号 或 📈概率高")
    print("   ✅ 5日乖离率适中 | 量比健康")
    
    low_risk_themes = []
    for theme, leaders in theme_leaders.items():
        safe_leaders = [l for l in leaders if l['score_details']['冲高回落概率'] < 70]
        if safe_leaders:
            avg_score = np.mean([l['total_score'] for l in safe_leaders])
            low_risk_themes.append({
                'theme': theme,
                'leaders': safe_leaders,
                'avg_score': avg_score
            })
    
    low_risk_themes.sort(key=lambda x: x['avg_score'], reverse=True)
    
    print(f"\n📊 推荐 TOP {min(5, len(low_risk_themes))} 个中低风险主题:")
    for rank, theme_info in enumerate(low_risk_themes[:5], 1):
        print(f"\n{rank}. 【{theme_info['theme']}】")
        
        safe_leaders = theme_info['leaders'][:3]
        print(f"   推荐龙头:")
        for i, leader in enumerate(safe_leaders, 1):
            ma_data = leader['ma_data']
            details = leader['score_details']
            
            print(f"     {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
            print(f"        游资评分: {leader['total_score']:.1f} | 5日: {leader['change_5']:+.1f}% | 20日: {leader['change_20']:+.1f}%")
            print(f"        回落风险: {details['回落风险等级']} | 二波信号: {details['二波信号等级']}")
            print(f"        5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
    
    print("\n\n" + "="*100)
    print("【三种操盘策略 TOP 3 推荐】")
    print("="*100)
    
    strategies = get_strategy_recommendations(ranked_themes, theme_leaders, theme_summary)
    
    print("\n🚀 策略一：强者恒强（追涨）")
    print("   选股条件: 游资评分>80 + 二波概率>=70% + 乖离率<10%")
    print("   适用场景: 市场情绪高涨，热点明确，捕捉持续强势龙头")
    for i, leader in enumerate(strategies['strategy1'], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        print(f"   {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
        print(f"      游资评分: {leader['total_score']:.1f} | 二波概率: {details['二波启动概率']:.0f}%")
        print(f"      5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
    
    print("\n📉 策略二：低吸潜伏（抄底）")
    print("   选股条件: 乖离率支撑位(-5%~+2%) + 二波概率>=40% + 量比<=1.3")
    print("   适用场景: 强势板块回调，寻找支撑位低吸机会")
    for i, leader in enumerate(strategies['strategy2'], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        print(f"   {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
        print(f"      游资评分: {leader['total_score']:.1f} | 二波概率: {details['二波启动概率']:.0f}%")
        print(f"      5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
    
    print("\n🔄 策略三：轮动切换（高抛低吸）")
    print("   选股条件: 主题排名上升 + 二波概率>=50% + 回落风险<70%")
    print("   适用场景: 热点轮动，把握板块切换节奏")
    for i, leader in enumerate(strategies['strategy3'], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        print(f"   {i}. {leader['name']:10s} ({leader['ts_code']:10s}) 【{leader['theme']}】")
        print(f"      游资评分: {leader['total_score']:.1f} | 二波概率: {details['二波启动概率']:.0f}%")
        print(f"      5日乖离: {ma_data['ma5_biased']:+.1f}% | 回落风险: {details['冲高回落概率']:.0f}%")
    
    # ========== Qwen千问AI验证 ==========
    if market_emotion:
        all_selected_stocks = strategies['strategy1'] + strategies['strategy2'] + strategies['strategy3']
        theme_info = {'ranked_themes': ranked_themes, 'theme_summary': theme_summary}
        
        validated_stocks, ai_report = qwen_stock_validator(all_selected_stocks, market_emotion, theme_info)
        
        if ai_report:
            print("\n\n" + "="*100)
            print("【Qwen千问AI深度验证报告】")
            print("="*100)
            print(ai_report)
            report_file = os.path.join(CACHE_DIR, f"daily_review_hotmoney_AI{datetime.now().strftime('%Y%m%d')}.txt")
            
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(ai_report)
                
    print("\n💡 风险提示:")
    print("   1. 以上推荐基于历史数据，不构成投资建议")
    print("   2. 注意控制仓位，做好止损止盈")
    print("   3. 密切关注市场整体环境变化")

# =========================
# 保存文本复盘报告
# =========================
def save_text_report(ranked_themes, theme_leaders, theme_summary, trade_dates, market_emotion=None, fastest_rising=None):
    report_lines = []
    report_lines.append("="*100)
    report_lines.append("每日盘后复盘报告 - 游资风格优化版")
    report_lines.append(f"日期: {trade_dates[-1]}")
    report_lines.append("="*100)
    
    # ========== 大盘情绪分析 ==========
    if market_emotion:
        report_lines.append("\n\n【大盘情绪分析】")
        report_lines.append("="*100)
        report_lines.append(f"📊 大盘点位: {market_emotion.get('大盘点位', 'N/A')} | 涨跌幅: {market_emotion.get('大盘涨跌幅', 'N/A')}%")
        report_lines.append(f"🌡️ 市场情绪: {market_emotion.get('情绪指数', 'N/A')} ({market_emotion.get('市场阶段', 'N/A')})")
        report_lines.append(f"📈 指数环境: {market_emotion.get('指数环境', 'N/A')} | 风险等级: {market_emotion.get('风险等级', 'N/A')}")
        report_lines.append(f"💰 全市场成交额: {market_emotion.get('全市场成交额（亿元）', 'N/A')}亿元")
        report_lines.append(f"🔴 涨停家数: {market_emotion.get('涨停家数', 'N/A')} | 🔽 跌停家数: {market_emotion.get('跌停家数', 'N/A')}")
        report_lines.append(f"💥 炸板率: {market_emotion.get('炸板率', 'N/A')}% | 📊 连板高度: {market_emotion.get('连板高度', 'N/A')}")
        report_lines.append(f"📊 上涨占比: {market_emotion.get('上涨占比', 'N/A')}% | 强势股占比: {market_emotion.get('强势股占比', 'N/A')}%")
        report_lines.append(f"📉 20日偏离率: {market_emotion.get('20日偏离率', 'N/A')}% | MA20方向: {market_emotion.get('MA20方向', 'N/A')}")
        report_lines.append(f"🎯 建议仓位: {market_emotion.get('最终建议仓位', 'N/A')}")
        report_lines.append(f"📊 均线状态: {market_emotion.get('均线状态', 'N/A')} | MA5:{market_emotion.get('站上MA5', 'N/A')} MA20:{market_emotion.get('站上MA20', 'N/A')} MA60:{market_emotion.get('站上MA60', 'N/A')}")
    
    report_lines.append("\n\n【今日主题评分与轮动分析 - 游资风格】")
    report_lines.append("="*100)
    
    # 全部主题排名
    report_lines.append("\n📊 今日主题完整排名:")
    for rank, (theme, today_score) in enumerate(ranked_themes, 1):
        summary = theme_summary.get(theme, {})
        avg_10d = summary.get('avg_score_10d', 0)
        avg_rank = summary.get('avg_rank_10d', 0)
        trend = summary.get('score_trend', '未知')
        rank_change = summary.get('rank_change', 0)
        
        trend_icon = "📈" if trend == "上升" else "📉" if trend == "下降" else "➡️"
        rank_change_icon = "⬆️" if rank_change > 0 else "⬇️" if rank_change < 0 else "➖"
        
        zt_stocks = []
        if theme in theme_leaders:
            for leader in theme_leaders[theme]:
                if leader['limit_up_count'] > 0:
                    zt_stocks.append(f"{leader['name']}({leader['limit_up_count']}次)")
        zt_str = f" | 涨停: {', '.join(zt_stocks[:3])}" if zt_stocks else ""
        
        report_lines.append(f"\n{rank}. 【{theme}】")
        report_lines.append(f"  今日评分: {today_score:.1f} | 近10日平均分: {avg_10d:.1f}")
        rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
        report_lines.append(f"  近10日平均排名: {avg_rank:.1f} | 排名变化: {rank_change_icon}{rank_change_text}{zt_str}")
        report_lines.append(f"  趋势方向: {trend_icon} {trend}")
    
    # 当日上升最快的主题
    if fastest_rising:
        report_lines.append("\n\n" + "="*100)
        report_lines.append("🔥 当日上升最快的热点主题 TOP 5")
        report_lines.append("="*100)
        
        for rank, (theme, today_score, rank_change, score_change) in enumerate(fastest_rising, 1):
            summary = theme_summary.get(theme, {})
            avg_10d = summary.get('avg_score_10d', 0)
            
            report_lines.append(f"\n{'='*80}")
            report_lines.append(f"第{rank}名: 【{theme}】")
            report_lines.append(f"{'='*80}")
            report_lines.append(f"  ⬆️ 排名变化: {rank_change:+d} 位 | 评分变化: {score_change:+.1f}")
            report_lines.append(f"  今日评分: {today_score:.1f} | 近10日平均分: {avg_10d:.1f}")
            report_lines.append(f"  趋势: {summary.get('score_trend', '未知')}")
            
            if theme in theme_leaders and theme_leaders[theme]:
                report_lines.append(f"\n  🚀 当日最强龙头:")
                leader = theme_leaders[theme][0]
                ma_data = leader['ma_data']
                report_lines.append(f"    {leader['name']:10s} ({leader['ts_code']:10s})")
                report_lines.append(f"       游资评分:{leader['total_score']:.1f} | 5日:{leader['change_5']:+.1f}% | 20日:{leader['change_20']:+.1f}%")
                report_lines.append(f"       {leader['score_details']['回落风险等级']} | {leader['score_details']['二波信号等级']}")
                
                report_lines.append(f"\n  💡 主题成分股 TOP 3:")
                for i, stock in enumerate(theme_leaders[theme][:3], 1):
                    report_lines.append(f"    {i}. {stock['name']:10s} ({stock['ts_code']:10s})")
    
    report_lines.append("\n\n【今日龙头评分与轮动分析 - 游资风格】")
    report_lines.append("="*100)
    all_leaders = []
    for theme, leaders in theme_leaders.items():
        for leader in leaders:
            leader_copy = leader.copy()
            leader_copy['theme'] = theme
            all_leaders.append(leader_copy)
    all_leaders.sort(key=lambda x: x['total_score'], reverse=True)
    
    report_lines.append("\n🏆 全市场综合 TOP 10 龙头（游资评分）:")
    for rank, leader in enumerate(all_leaders[:10], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        report_lines.append(f"\n{rank}. {leader['name']:10s} ({leader['ts_code']:10s}) 【{leader['theme']}】")
        report_lines.append(f"   游资评分: {leader['total_score']:.1f} | 5日涨幅: {leader['change_5']:+.1f}% | 20日涨幅: {leader['change_20']:+.1f}%")
        report_lines.append(f"   风险等级: {details['回落风险等级']} | 二波信号: {details['二波信号等级']}")
    
    report_lines.append("\n\n【明日中低风险主题和龙头推荐】")
    report_lines.append("="*100)
    report_lines.append("\n🎯 推荐原则: 中低风险 + 强二波信号 + 均线健康")
    
    low_risk_themes = []
    for theme, leaders in theme_leaders.items():
        safe_leaders = [l for l in leaders if l['score_details']['冲高回落概率'] < 70]
        if safe_leaders:
            avg_score = np.mean([l['total_score'] for l in safe_leaders])
            low_risk_themes.append({
                'theme': theme,
                'leaders': safe_leaders,
                'avg_score': avg_score
            })
    low_risk_themes.sort(key=lambda x: x['avg_score'], reverse=True)
    
    for rank, theme_info in enumerate(low_risk_themes[:5], 1):
        report_lines.append(f"\n{rank}. 【{theme_info['theme']}】")
        safe_leaders = theme_info['leaders'][:3]
        for i, leader in enumerate(safe_leaders, 1):
            ma_data = leader['ma_data']
            details = leader['score_details']
            report_lines.append(f"     {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
            report_lines.append(f"        游资评分: {leader['total_score']:.1f} | 回落风险: {details['冲高回落概率']:.0f}% | 二波概率: {details['二波启动概率']:.0f}%")
    
    report_text = "\n".join(report_lines)
    report_file = os.path.join(CACHE_DIR, f"daily_review_hotmoney_{trade_dates[-1]}.txt")
    
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    print(f"\n✓ 文本复盘报告已保存: {report_file}")

# =========================
# 保存结果
# =========================
def save_final_results(ranked_themes, theme_leaders, theme_summary, trade_dates):
    ranking_data = []
    for rank, (theme, today_score) in enumerate(ranked_themes, 1):
        summary = theme_summary.get(theme, {})
        ranking_data.append({
            '排名': rank,
            '主题': theme,
            '今日评分': round(today_score, 2),
            '近10日平均分': round(summary.get('avg_score_10d', 0), 2),
            '近10日平均排名': round(summary.get('avg_rank_10d', 0), 1),
            '趋势': summary.get('score_trend', '未知'),
            '排名变化': summary.get('rank_change', 0)
        })
    
    ranking_df = pd.DataFrame(ranking_data)
    ranking_file = os.path.join(CACHE_DIR, f"theme_ranking_hotmoney_{trade_dates[-1]}.csv")
    ranking_df.to_csv(ranking_file, index=False, encoding='utf-8-sig')
    print(f"\n✓ 主题排名已保存: {ranking_file}")
    
    leaders_data = []
    for theme, leaders in theme_leaders.items():
        for leader in leaders:
            details = leader['score_details']
            ma_data = leader['ma_data']
            
            leaders_data.append({
                '主题': theme,
                '股票代码': leader['ts_code'],
                '股票名称': leader['name'],
                '游资评分': round(leader['total_score'], 2),
                '5日涨幅': round(leader['change_5'], 2),
                '20日涨幅': round(leader['change_20'], 2),
                '涨停次数': leader['limit_up_count'],
                '5日乖离率': round(ma_data['ma5_biased'], 2) if ma_data else 0,
                '20日乖离率': round(ma_data['ma20_biased'], 2) if ma_data else 0,
                '量比': round(leader['volume_ratio'], 2),
                '冲高回落概率': round(details['冲高回落概率'], 0),
                '回落风险等级': details['回落风险等级'],
                '二波启动概率': round(details['二波启动概率'], 0),
                '二波信号等级': details['二波信号等级']
            })
    
    leaders_df = pd.DataFrame(leaders_data)
    leaders_file = os.path.join(CACHE_DIR, f"theme_leaders_hotmoney_{trade_dates[-1]}.csv")
    leaders_df.to_csv(leaders_file, index=False, encoding='utf-8-sig')
    print(f"✓ 龙头股列表已保存: {leaders_file}")

# =========================
# 生成复盘报告
# =========================
def generate_report(theme_stocks_map, name_map, trade_dates, market_emotion=None):
    print("\n" + "="*100)
    print("每日盘后复盘报告 - 游资风格优化版")
    print("="*100)
    
    # 收集所有股票代码
    all_ts_codes = set()
    for theme_stocks in theme_stocks_map.values():
        all_ts_codes.update(list(theme_stocks)[:50])
    
    # 批量预加载所有日线数据（大幅提速）
    preload_bulk_daily_data(list(all_ts_codes), trade_dates[0], trade_dates[-1])
    
    theme_summary = calculate_theme_historical_rankings(theme_stocks_map, trade_dates)
    
    theme_scores = {}
    theme_leaders = {}
    theme_market_stats = {}
    
    for theme_name, theme_stocks in theme_stocks_map.items():
        print(f"\n处理主题: {theme_name}")
        
        leaders = identify_theme_leaders(list(theme_stocks), name_map)
        theme_leaders[theme_name] = leaders
        optimized_score, market_stats = calculate_optimized_theme_score(
            theme_name,
            theme_stocks,
            leaders,
            theme_summary
        )
        theme_scores[theme_name] = optimized_score
        theme_market_stats[theme_name] = market_stats
    
    ranked_themes = sorted(theme_scores.items(), key=lambda x: x[1], reverse=True)
    
    theme_rank_changes = []
    for theme, today_score in theme_scores.items():
        summary = theme_summary.get(theme, {})
        rank_change = summary.get('rank_change', 0)
        score_change = today_score - summary.get('avg_score_10d', 0)
        theme_rank_changes.append((theme, today_score, rank_change, score_change))
    
    fastest_rising = sorted(theme_rank_changes, key=lambda x: x[2], reverse=True)[:5]
    
    print("\n\n" + "="*100)
    print("主题热点排名（按游资评分）")
    print("="*100)
    
    for rank, (theme, today_score) in enumerate(ranked_themes, 1):
        summary = theme_summary.get(theme, {})
        avg_10d = summary.get('avg_score_10d', 0)
        avg_rank = summary.get('avg_rank_10d', 0)
        
        print(f"\n第{rank}名: 【{theme}】")
        print(f"  今日评分: {today_score:.1f} | 近10日平均分: {avg_10d:.1f} | 近10日平均排名: {avg_rank:.1f}")
        print(f"  趋势: {summary.get('score_trend', '未知')} | 排名变化: {summary.get('rank_change', 0):+d}")
        
        stats = theme_market_stats.get(theme, {})
        if stats:
            print(
                f"  主题广度: 上涨占比{stats.get('up_ratio', 0):.1f}% | "
                f"强势股占比{stats.get('strong_ratio', 0):.1f}% | "
                f"涨停{stats.get('limit_up_count', 0)}家 | "
                f"平均量比{stats.get('avg_volume_ratio', 1):.2f}"
            )
        
        if theme in theme_leaders and theme_leaders[theme]:
            print(f"  龙头股 TOP 3:")
            for i, leader in enumerate(theme_leaders[theme][:3], 1):
                ma_data = leader['ma_data']
                print(f"    {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
                print(f"       游资评分:{leader['total_score']:.1f} | 5日:{leader['change_5']:+.1f}% | 20日:{leader['change_20']:+.1f}%")
                print(f"       {leader['score_details']['回落风险等级']} | {leader['score_details']['二波信号等级']}")
    
    output_theme_analysis(ranked_themes, theme_summary, theme_leaders)
    output_leader_analysis(theme_leaders)
    output_tomorrow_recommendation(ranked_themes, theme_leaders, theme_summary, market_emotion)
    save_final_results(ranked_themes, theme_leaders, theme_summary, trade_dates)
    save_text_report(ranked_themes, theme_leaders, theme_summary, trade_dates, market_emotion, fastest_rising)
    
    return ranked_themes, theme_leaders, theme_summary

# =========================
# 主函数
# =========================
def main():
    print("="*100)
    print("每日盘后复盘和热点轮动分析系统 - 游资风格优化版")
    print("主题排名 + 近10日平均分 + 游资风格评分优化 + 大盘情绪分析")
    print("优化内容: 短期爆发力优先 + 量能确认 + 涨停基因检测 + 均线多头优先")
    print("="*100)
    
    trade_dates = get_trade_dates(30)
    time.sleep(0.5)
    print(f"\n分析周期: {trade_dates[0]} ~ {trade_dates[-1]}")
    print(f"共 {len(trade_dates)} 个交易日")
    
    print("\n" + "="*60)
    print("📊 大盘情绪分析")
    print("="*60)
    market_emotion = analyze_market_emotion_simple()
    if market_emotion:
        print(f"大盘情绪: {market_emotion.get('情绪指数', 'N/A')}")
        print(f"市场阶段: {market_emotion.get('市场阶段', 'N/A')}")
        print(f"涨停家数: {market_emotion.get('涨停家数', 'N/A')}")
    else:
        print("大盘情绪分析失败")
    
    theme_stocks_map, name_map = load_theme_portfolio_from_csv()
    if not theme_stocks_map:
        print("未获取到主题投资组合数据")
        return
    
    print(f"\n加载了 {len(theme_stocks_map)} 个主题")
    
    ranked_themes, theme_leaders, theme_summary = generate_report(
        theme_stocks_map, name_map, trade_dates, market_emotion
    )
    
    print("\n\n" + "="*100)
    print("游资风格复盘分析完成！")
    print("="*100)
    
    # ── 构建微信推送内容 ──
    push_title = f"📊 盘后复盘 {TRADE_DATE}"
    push_lines = []
    if market_emotion:
        push_lines.append(f"【大盘情绪】{market_emotion.get('情绪指数','N/A')}分 {market_emotion.get('市场阶段','N/A')}")
        push_lines.append(f"涨停{market_emotion.get('涨停家数',0)}家 跌停{market_emotion.get('跌停家数',0)}家 建议仓位{market_emotion.get('最终建议仓位','N/A')}")
        push_lines.append("")
    push_lines.append(f"【主题 TOP 5】")
    for rank, (theme, score) in enumerate(ranked_themes[:5], 1):
        leaders = theme_leaders.get(theme, [])
        leader_names = "、".join([l['name'] for l in leaders[:3]])
        push_lines.append(f"{rank}. {theme}({score:.0f}分) {leader_names}")
    push_lines.append("")
    strategies = get_strategy_recommendations(ranked_themes, theme_leaders, theme_summary)
    push_lines.append("【策略一·强者恒强】")
    for s in strategies['strategy1'][:3]:
        push_lines.append(f"  {s['name']}({s['ts_code'][:6]}) 评分{s['total_score']:.0f}")
    push_lines.append("【策略二·低吸潜伏】")
    for s in strategies['strategy2'][:3]:
        push_lines.append(f"  {s['name']}({s['ts_code'][:6]}) 评分{s['total_score']:.0f}")
    push_lines.append("【策略三·轮动切换】")
    for s in strategies['strategy3'][:3]:
        push_lines.append(f"  {s['name']}({s['ts_code'][:6]}) 评分{s['total_score']:.0f}")
    send_serverchan_push(push_title, "\n".join(push_lines))

if __name__ == "__main__":
    main()
