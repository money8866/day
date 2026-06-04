#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日综合分析汇总程序
功能：
1. 读取大盘分析结果
2. 读取主题分析结果
3. 读取个股分析结果
4. 合并成详细文本后用AI提炼总结
5. 通过Server酱发送到微信
"""
import os
import sys
import json
import sqlite3
import requests
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)
REPORT_DIR = os.path.join(PARENT_DIR, 'report_daily')
os.makedirs(REPORT_DIR, exist_ok=True)
load_dotenv(os.path.join(PARENT_DIR, 'config', '.env'))

def read_market_analysis():
    """读取大盘分析结果（新版包含市场总评分）"""
    db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'market_analysis.db')
    if not os.path.exists(db_path):
        return None
    
    conn = sqlite3.connect(db_path)
    
    df = pd.read_sql("SELECT * FROM index_analysis ORDER BY trade_date DESC LIMIT 10", conn)
    
    if df.empty:
        conn.close()
        return None
    
    trade_date = df['trade_date'].iloc[0]
    
    df = df[df['trade_date'] == trade_date]
    
    result = []
    for _, row in df.iterrows():
        result.append({
            'index_name': row['index_name'],
            'trend_status': row['trend_status'],
            'trend_score': row['trend_score'],
            'sentiment_status': row['sentiment_status'],
            'sentiment_score': row['sentiment_score'],
            'close_price': row['close_price'],
            'pct_change': row['pct_change']
        })
    
    # 读取总体分析（新版包含市场总评分）
    overall_info = {}
    try:
        df_overall = pd.read_sql(f"SELECT * FROM overall_analysis WHERE trade_date = '{trade_date}'", conn)
        if not df_overall.empty:
            row = df_overall.iloc[0]
            overall_info = {
                'position': row.get('total_position', row.get('position', 0)),
                'reason': row.get('position_reason', row.get('reason', '')),
                # 新增字段
                'trend_score': row.get('trend_score'),
                'index_trend': row.get('index_trend'),
                'theme_trend': row.get('theme_trend'),
                'market_status': row.get('market_status', '')
            }
    except:
        overall_info = {}
    
    conn.close()
    return {'indices': result, 'overall': overall_info, 'trade_date': trade_date}

def read_theme_analysis():
    """读取主题分析结果"""
    csv_file = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.csv')
    if os.path.exists(csv_file):
        df = pd.read_csv(csv_file, encoding='utf-8-sig')
        result = []
        for _, row in df.iterrows():
            theme_name = row.get('theme_name', row.get('name', row.get('theme', '')))
            trend_score = row.get('trend_score', 0)
            sentiment_score = row.get('sentiment_score', 0)
            
            if sentiment_score >= 70:
                sentiment_status = "情绪高涨"
            elif sentiment_score >= 50:
                sentiment_status = "情绪温和"
            elif sentiment_score >= 30:
                sentiment_status = "情绪低迷"
            else:
                sentiment_status = "情绪退潮"
            
            if trend_score >= 60:
                trend_status = "上升趋势"
            elif trend_score >= 45:
                trend_status = "震荡偏强"
            elif trend_score >= 30:
                trend_status = "震荡偏弱"
            else:
                trend_status = "下降趋势"
            
            result.append({
                'theme_name': theme_name,
                'trend_score': trend_score,
                'trend_status': trend_status,
                'sentiment_score': sentiment_score,
                'sentiment_status': sentiment_status,
                'change': row.get('t_avg_ret_5', row.get('change', 0)),
                'volume_ratio': row.get('s_avg_vol_ratio', row.get('volume_ratio', 0))
            })
        return {'themes': result}
    return None

def read_60day_avg_trend_scores():
    """读取60日趋势平均分"""
    db_path = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_trend_sentiment.db')
    if not os.path.exists(db_path):
        return None
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT theme, AVG(trend_score) as avg_trend_score, COUNT(*) as day_count
            FROM theme_scores
            GROUP BY theme
            HAVING day_count >= 10
            ORDER BY avg_trend_score DESC
        """)
        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                'theme_name': row[0],
                'avg_trend_score': row[1],
                'day_count': row[2]
            })
        conn.close()
        return {'themes': result}
    except Exception as e:
        print(f"⚠️ 读取60日趋势分失败: {e}")
        conn.close()
        return None

def read_stock_picker():
    """读取个股分析结果（新版包含theme_type字段）"""
    csv_file = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_pattern_stocks.csv')
    if os.path.exists(csv_file):
        df = pd.read_csv(csv_file, encoding='utf-8-sig')
        if not df.empty:
            result = []
            for _, row in df.iterrows():
                # 兼容两种字段命名：一种是旧的，一种是新的
                ts_code = row.get('code', row.get('ts_code', ''))
                close = row.get('price', row.get('close', 0))
                mcap = row.get('mcap', row.get('market_cap', 0))
                
                result.append({
                    'ts_code': ts_code,
                    'name': row.get('name', ''),
                    'close': close,
                    'pct_chg': row.get('pct_chg', 0),
                    'market_cap': mcap,
                    'theme': row.get('theme', ''),
                    'theme_type': row.get('theme_type', ''),
                    'buy_type': row.get('buy_type', ''),
                    'reason': row.get('reason', '')
                })
            return {'stocks': result}
    return None

def generate_summary(market_data, theme_data, stock_data, avg_trend_60_data, trade_date):
    """生成详细综合分析文本"""
    date_obj = datetime.strptime(trade_date, '%Y%m%d')
    date_str = date_obj.strftime('%Y年%m月%d日')
    summary = f"📊 {date_str} 每日综合分析报告\n"
    summary += "=" * 70 + "\n\n"
    
    if market_data:
        # 新增：市场趋势总评分
        overall = market_data.get('overall', {}) or {}
        # 检查是否有新增的评分字段（pd.read_sql 读取空值可能返回 None 或 NaN）
        has_new_fields = 'trend_score' in overall and overall['trend_score'] is not None and str(overall['trend_score']) != 'nan'
        
        if has_new_fields:
            summary += "【一、市场趋势总评分】\n"
            summary += "-" * 70 + "\n"
            market_status = overall.get('market_status', '未知') or '未知'
            status_icon = "🚀" if "主升浪" in market_status else ("📈" if "上升" in market_status or "良好" in market_status else ("⚠️" if "退潮" in market_status or "主跌" in market_status else "📊"))
            summary += f"  {status_icon} 市场状态: 【{market_status}】\n"
            summary += f"     总趋势分: {overall.get('trend_score', 0):.1f}\n"
            summary += f"     指数趋势 (IndexTrend): {overall.get('index_trend', 0):.1f}\n"
            summary += f"     主题趋势 (ThemeTrend): {overall.get('theme_trend', 0):.1f}\n"
            summary += f"     建议仓位: {overall.get('position', 0)}%\n"
            summary += "\n"
            summary += "【二、各指数分析】\n"
        else:
            summary += "【一、大盘分析】\n"
            summary += "-" * 70 + "\n"
        
        for idx in market_data['indices']:
            trend_icon = "📈" if "上升" in idx['trend_status'] else ("📉" if "下降" in idx['trend_status'] else "📊")
            summary += f"  {trend_icon} {idx['index_name']}\n"
            summary += f"     趋势: {idx['trend_status']} ({idx['trend_score']:.1f}分)\n"
            summary += f"     情绪: {idx['sentiment_status']} ({idx['sentiment_score']:.1f}分)\n"
            summary += f"     收盘: {idx['close_price']:.2f} | 涨跌: {idx['pct_change']:+.2f}%\n\n"
        
        if overall.get('position'):
            summary += f"  💡 总体仓位建议: {overall.get('position', 0)}%\n"
            if overall.get('reason'):
                summary += f"     {overall.get('reason', '')}\n"
        
        summary += "\n"
    
    if avg_trend_60_data and avg_trend_60_data.get('themes'):
        summary += "【三、60日趋势平均分排名（中线趋势主题）】\n"
        summary += "-" * 70 + "\n"
        summary += "  📊 主题名称     | 平均趋势分 | 数据天数\n"
        summary += "  " + "-" * 46 + "\n"
        
        top_avg_themes = avg_trend_60_data['themes'][:5]
        for theme in top_avg_themes:
            avg_score = theme.get('avg_trend_score', 0)
            trend_icon = "🟢" if avg_score >= 55 else ("🔴" if avg_score < 30 else "🟡")
            summary += f"  {trend_icon} {theme['theme_name']:<10}  {avg_score:>8.1f}   {theme.get('day_count', 0):>6}天\n"
        
        summary += "\n"
    
    if theme_data and theme_data.get('themes'):
        summary += "【四、今日主题趋势与情绪分析】\n"
        summary += "-" * 70 + "\n"
        
        themes = sorted(theme_data['themes'], key=lambda x: x.get('trend_score', 0) * 0.6 + x.get('sentiment_score', 0) * 0.4, reverse=True)
        
        summary += "  📊 主题名称     | 趋势状态   | 情绪状态   | 5日涨跌  | 量比\n"
        summary += "  " + "-" * 66 + "\n"
        
        for theme in themes:
            trend_icon = "🟢" if "上升" in theme.get('trend_status', '') else ("🔴" if "下降" in theme.get('trend_status', '') else "🟡")
            sentiment_icon = "🔥" if "高涨" in theme.get('sentiment_status', '') else ("❄️" if "退潮" in theme.get('sentiment_status', '') else "🌡️")
            
            change = theme.get('change', 0)
            change_str = f"{change:+.2f}%" if isinstance(change, (int, float)) else "N/A"
            
            summary += f"  {trend_icon} {theme['theme_name']:<10} {sentiment_icon} {theme.get('trend_status', ''):<8} {theme.get('sentiment_status', ''):<8} {change_str:>8} {theme.get('volume_ratio', 0):>6.2f}\n"
        
        summary += "\n"
    
    if theme_data and theme_data.get('themes'):
        summary += "【五、主题操盘策略建议】\n"
        summary += "-" * 70 + "\n"
        
        buy_signals = [t for t in theme_data['themes'] if t.get('trend_score', 0) >= 50 and t.get('sentiment_score', 0) < 50]
        sell_signals = [t for t in theme_data['themes'] if t.get('sentiment_score', 0) >= 70 or (t.get('trend_score', 0) < 40 and t.get('sentiment_score', 0) > 60)]
        focus_themes = [t for t in theme_data['themes'] if t.get('trend_score', 0) >= 60]
        
        if buy_signals:
            summary += "  ✅ 低吸博弈机会（趋势强+情绪回调）:\n"
            for t in sorted(buy_signals, key=lambda x: x.get('trend_score', 0), reverse=True):
                summary += f"     • {t['theme_name']}: 趋势{t.get('trend_score', 0):.1f} + 情绪{t.get('sentiment_score', 0):.1f}\n"
                summary += f"       → 趋势良好但情绪回调，可低吸博弈回升\n\n"
        
        if focus_themes:
            summary += "  🚀 重点关注（今日上升趋势主题）:\n"
            for t in sorted(focus_themes, key=lambda x: x.get('trend_score', 0), reverse=True):
                summary += f"     • {t['theme_name']}: 趋势{t.get('trend_score', 0):.1f} 情绪{t.get('sentiment_score', 0):.1f}\n"
            summary += "\n"
        
        if sell_signals:
            summary += "  ⚠️ 注意风险（情绪过热或趋势转弱）:\n"
            for t in sorted(sell_signals, key=lambda x: x.get('sentiment_score', 0), reverse=True):
                summary += f"     • {t['theme_name']}: 趋势{t.get('trend_score', 0):.1f} 情绪{t.get('sentiment_score', 0):.1f}\n"
                summary += f"       → 情绪过热，警惕冲高回落\n\n"
        
        summary += "\n"
    
    if stock_data and stock_data.get('stocks'):
        summary += "【六、精选个股列表】\n"
        summary += "-" * 70 + "\n"
        
        stocks = stock_data['stocks']
        summary += f"  共筛选出 {len(stocks)} 只符合条件的个股:\n\n"
        
        # 显示一下有多少种 theme_type（调试用）
        if stocks:
            type_counts = {}
            for s in stocks:
                t = s.get('theme_type', 'unknown')
                type_counts[t] = type_counts.get(t, 0) + 1
            summary += f"  主题类型分布: {type_counts}\n\n"
        
        # 按theme_type分组 - 更精确的匹配
        mid_term_stocks = [s for s in stocks if s.get('theme_type') == '中期趋势']
        short_term_stocks = [s for s in stocks if s.get('theme_type') == '短线主线']
        supplement_stocks = [s for s in stocks if s not in mid_term_stocks and s not in short_term_stocks]
        
        if mid_term_stocks:
            summary += "  📈 中期趋势主题（基于60日趋势平均分TOP2）\n"
            summary += "  " + "-" * 60 + "\n"
            
            # 分组显示中军和龙头
            zhongjun_mid = [s for s in mid_term_stocks if s.get('buy_type') == '中军' or s.get('buy_type') == '中军突破']
            if zhongjun_mid:
                summary += "  🏆 中军（中线布局）\n"
                for i, stock in enumerate(zhongjun_mid, 1):
                    pct_chg = stock.get('pct_chg', 0)
                    pct_str = f"{pct_chg:+.2f}%"
                    trend_icon = "🟢" if pct_chg > 0 else ("🔴" if pct_chg < 0 else "⚪")
                    summary += f"  {i}. {trend_icon} {stock.get('name', '')} ({stock.get('ts_code', '')})\n"
                    summary += f"     收盘价: {stock.get('close', 0):.2f} | 涨跌幅: {pct_str}\n"
                    summary += f"     所属主题: {stock.get('theme', '')}\n"
                    summary += f"     市值: {stock.get('market_cap', 0):.1f}亿\n"
                    if stock.get('reason'):
                        summary += f"     推荐理由: {stock.get('reason', '')}\n"
                    summary += "\n"
            
            longtou_mid = [s for s in mid_term_stocks if s.get('buy_type') in ['龙头首阴', '龙头']]
            if longtou_mid:
                summary += "  🔥 龙头（首阴/强势）\n"
                for i, stock in enumerate(longtou_mid, 1):
                    pct_chg = stock.get('pct_chg', 0)
                    pct_str = f"{pct_chg:+.2f}%"
                    trend_icon = "🟢" if pct_chg > 0 else ("🔴" if pct_chg < 0 else "⚪")
                    summary += f"  {i}. {trend_icon} {stock.get('name', '')} ({stock.get('ts_code', '')})\n"
                    summary += f"     收盘价: {stock.get('close', 0):.2f} | 涨跌幅: {pct_str}\n"
                    summary += f"     所属主题: {stock.get('theme', '')}\n"
                    summary += f"     市值: {stock.get('market_cap', 0):.1f}亿\n"
                    if stock.get('reason'):
                        summary += f"     推荐理由: {stock.get('reason', '')}\n"
                    summary += "\n"
        
        if short_term_stocks:
            summary += "  ⚡ 短线主题（基于今日趋势分TOP3）\n"
            summary += "  " + "-" * 60 + "\n"
            
            zhongjun_short = [s for s in short_term_stocks if s.get('buy_type') == '中军' or s.get('buy_type') == '中军突破']
            if zhongjun_short:
                summary += "  🏆 中军（短线操作）\n"
                for i, stock in enumerate(zhongjun_short, 1):
                    pct_chg = stock.get('pct_chg', 0)
                    pct_str = f"{pct_chg:+.2f}%"
                    trend_icon = "🟢" if pct_chg > 0 else ("🔴" if pct_chg < 0 else "⚪")
                    summary += f"  {i}. {trend_icon} {stock.get('name', '')} ({stock.get('ts_code', '')})\n"
                    summary += f"     收盘价: {stock.get('close', 0):.2f} | 涨跌幅: {pct_str}\n"
                    summary += f"     所属主题: {stock.get('theme', '')}\n"
                    summary += f"     市值: {stock.get('market_cap', 0):.1f}亿\n"
                    if stock.get('reason'):
                        summary += f"     推荐理由: {stock.get('reason', '')}\n"
                    summary += "\n"
            
            longtou_short = [s for s in short_term_stocks if s.get('buy_type') in ['龙头首阴', '龙头']]
            if longtou_short:
                summary += "  🔥 龙头（首阴/强势）\n"
                for i, stock in enumerate(longtou_short, 1):
                    pct_chg = stock.get('pct_chg', 0)
                    pct_str = f"{pct_chg:+.2f}%"
                    trend_icon = "🟢" if pct_chg > 0 else ("🔴" if pct_chg < 0 else "⚪")
                    summary += f"  {i}. {trend_icon} {stock.get('name', '')} ({stock.get('ts_code', '')})\n"
                    summary += f"     收盘价: {stock.get('close', 0):.2f} | 涨跌幅: {pct_str}\n"
                    summary += f"     所属主题: {stock.get('theme', '')}\n"
                    summary += f"     市值: {stock.get('market_cap', 0):.1f}亿\n"
                    if stock.get('reason'):
                        summary += f"     推荐理由: {stock.get('reason', '')}\n"
                    summary += "\n"
        
        if supplement_stocks:
            summary += "  🔄 补充主题\n"
            summary += "  " + "-" * 60 + "\n"
            
            for i, stock in enumerate(supplement_stocks[:6], 1):
                pct_chg = stock.get('pct_chg', 0)
                pct_str = f"{pct_chg:+.2f}%"
                trend_icon = "🟢" if pct_chg > 0 else ("🔴" if pct_chg < 0 else "⚪")
                summary += f"  {i}. {trend_icon} {stock.get('name', '')} ({stock.get('ts_code', '')})\n"
                summary += f"     收盘价: {stock.get('close', 0):.2f} | 涨跌幅: {pct_str}\n"
                summary += f"     所属主题: {stock.get('theme', '')} | 类型: {stock.get('buy_type', '')}\n"
                summary += f"     市值: {stock.get('market_cap', 0):.1f}亿\n"
                if stock.get('reason'):
                    summary += f"     推荐理由: {stock.get('reason', '')}\n"
                summary += "\n"
        
        summary += "\n"
    else:
        summary += "【六、精选个股】\n"
        summary += "-" * 70 + "\n"
        summary += "  暂无符合条件的个股\n\n"
    
    summary += "=" * 70 + "\n"
    return summary

def summarize_with_deepseek(text):
    """使用DeepSeek进行总结提炼"""
    try:
        DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
        if not DEEPSEEK_API_KEY:
            print("⚠️ 未配置DeepSeek API Key，跳过AI总结")
            return text
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        
        messages = [
            {
                "role": "system",
                "content": """你是一个专业的游资操盘手，请对以下大盘、主题、个股分析结果进行深度提炼总结。

分析报告包含以下内容：
1. 大盘分析：多个指数的趋势、情绪、收盘价和涨跌幅
2. 主题分析：多个主题的趋势、情绪、5日涨跌和量比
3. 操盘策略：低吸机会、重点关注主题、注意风险的主题
4. 精选个股：符合条件的个股列表，包括中军突破和龙头首阴两类

请给出：
1. 大盘核心观点（言简意赅，总结当前市场的主要趋势和风险,提示明日的操作建议）
2. 主题轮动机会分析（重点关注主题的持续和轮动趋势,说明主题轮动的原因和影响）
3. 个股机会分析（哪些个股值得重点关注，中期趋势主题和短线博弈的中军和龙头首阴分别说明）
   【重要】提到每只个股时，务必写出完整的股票代码（如：中际旭创 (300308.SZ)）！
4. 操作建议（仓位控制、方向选择、风险提示）

用简洁专业的语言输出，突出重点，便于快速阅读。"""
            },
            {
                "role": "user",
                "content": f"请深度总结以下股票分析报告：\n\n{text}"
            }
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4096
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        return result['choices'][0]['message']['content']
    
    except Exception as e:
        print(f"❌ AI总结失败: {e}")
        return text

def send_to_wechat(text):
    """通过Server酱发送到微信"""
    try:
        SERVERCHAN_SENDKEY = os.getenv('SERVERCHAN_SENDKEY', os.getenv('WECHAT_SCKEY'))
        if not SERVERCHAN_SENDKEY:
            print("⚠️ 未配置Server酱SendKey，跳过微信推送")
            return False
        
        url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
        title = f"{datetime.now().strftime('%Y-%m-%d')} 股票分析报告"
        
        if len(text) > 2000:
            text = text[:2000] + "\n...（内容过长，已截断）"
        
        data = {
            "title": title,
            "desp": text
        }
        
        response = requests.post(url, data=data, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        if result.get('code') == 0:
            print("✅ 微信推送成功")
            return True
        else:
            print(f"❌ 微信推送失败: {result.get('message', '未知错误')}")
            return False
    
    except Exception as e:
        print(f"❌ 微信推送失败: {e}")
        return False

def main():
    print("🔄 正在生成详细综合分析报告...")
    
    print("📥 读取大盘分析数据...")
    market_result = read_market_analysis()
    trade_date = market_result.get('trade_date', datetime.now().strftime('%Y%m%d')) if market_result else datetime.now().strftime('%Y%m%d')
    market_data = market_result if market_result else None
    
    print("📥 读取主题分析数据...")
    theme_data = read_theme_analysis()
    
    print("📥 读取60日趋势平均分数据...")
    avg_trend_60_data = read_60day_avg_trend_scores()
    
    print("📥 读取个股分析数据...")
    stock_data = read_stock_picker()
    
    print("📝 生成详细综合分析报告...")
    raw_report = generate_summary(market_data, theme_data, stock_data, avg_trend_60_data, trade_date)
    print("\n" + raw_report + "\n")
    
    print("🤖 AI深度总结...")
    summarized_report = summarize_with_deepseek(raw_report)
    print("\n--- AI深度总结 ---\n")
    print(summarized_report)
    print("\n-------------------\n")
    
    print("📤 发送到微信...")
    send_to_wechat(summarized_report)
    
    report_file = os.path.join(REPORT_DIR, f'daily_summary_{trade_date}.txt')
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write("=== 原始详细报告 ===\n\n")
        f.write(raw_report)
        f.write("\n\n=== AI深度总结 ===\n\n")
        f.write(summarized_report)
    
    print(f"✅ 报告已保存: {report_file}")

if __name__ == '__main__':
    main()
