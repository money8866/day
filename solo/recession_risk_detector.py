#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主线退潮风险检测模块 V3
专注于检测最强主线的退潮风险
"""
import os
import sys
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")


def load_theme_scores():
    """
    从主题轮动数据库加载当天主题评分
    :return: {theme_name: score}
    """
    db_path = os.path.join(BASE_DIR, "theme_rotation.db")
    
    if not os.path.exists(db_path):
        return {}
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 获取最新日期
        cursor.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT 1")
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return {}
        
        latest_date = result[0]
        
        # 查询当天主题评分
        query = """
            SELECT theme_name, today_score 
            FROM theme_scores 
            WHERE trade_date = ?
            ORDER BY today_score DESC
        """
        df = pd.read_sql_query(query, conn, params=(latest_date,))
        conn.close()
        
        if df.empty:
            return {}
        
        theme_scores = {}
        for _, row in df.iterrows():
            theme_scores[row['theme_name']] = float(row['today_score'])
        
        return theme_scores
        
    except Exception as e:
        print(f"⚠️ 加载主题评分失败: {e}")
        return {}


def load_theme_stocks_data():
    """
    从主题投资组合数据库加载股票数据并获取涨跌信息
    :return: {theme_name: [stock_dict_list]}
    """
    db_path = os.path.join(BASE_DIR, "theme_portfolio.db")
    
    if not os.path.exists(db_path):
        print("⚠️ 主题投资组合数据库不存在")
        return {}
    
    try:
        conn = sqlite3.connect(db_path)
        
        # 获取最新日期的数据
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT trade_date FROM theme_stocks ORDER BY trade_date DESC LIMIT 1")
        result = cursor.fetchone()
        
        if not result:
            print("⚠️ 数据库中没有主题成份股数据")
            conn.close()
            return {}
        
        latest_date = result[0]
        
        # 查询最新日期的成份股数据
        query = """
            SELECT theme_name, ts_code, name, change_5, change_20
            FROM theme_stocks 
            WHERE trade_date = ?
        """
        df = pd.read_sql_query(query, conn, params=(latest_date,))
        conn.close()
        
        if df.empty:
            print("⚠️ 无法加载主题投资组合数据")
            return {}
        
        theme_stocks_map = {}
        
        for _, row in df.iterrows():
            theme = row['theme_name']
            change = float(row.get('change_5', 0) or 0)
            stock = {
                'ts_code': row['ts_code'],
                'name': row['name'],
                'change': change,
                'volume_ratio': 1.0,
                'change_20': float(row.get('change_20', 0) or 0),
            }
            
            if theme not in theme_stocks_map:
                theme_stocks_map[theme] = []
            theme_stocks_map[theme].append(stock)
        
        print(f"✅ 成功加载 {len(theme_stocks_map)} 个主题的股票数据")
        return theme_stocks_map
        
    except Exception as e:
        print(f"⚠️ 加载主题数据失败: {e}")
        import traceback
        traceback.print_exc()
        return {}


def calculate_theme_health(stocks):
    """
    计算主题健康度指标
    """
    if not stocks:
        return None
    
    changes = [s['change'] for s in stocks]
    up_stocks = [s for s in stocks if s['change'] > 0]
    down_stocks = [s for s in stocks if s['change'] < 0]
    strong_stocks = [s for s in stocks if s['change'] >= 3]
    weak_stocks = [s for s in stocks if s['change'] <= -3]
    
    total = len(stocks)
    
    return {
        'total': total,
        'up_count': len(up_stocks),
        'down_count': len(down_stocks),
        'strong_count': len(strong_stocks),
        'weak_count': len(weak_stocks),
        'up_ratio': len(up_stocks) / total,
        'weak_ratio': len(weak_stocks) / total,
        'avg_change': np.mean(changes),
        'max_change': max(changes),
        'min_change': min(changes),
        'change_spread': max(changes) - min(changes),
        'net_momentum': (len(up_stocks) - len(down_stocks)) / total * 100
    }


def detect_mainline_recession_risk_v3(theme_stocks_map=None):
    """
    V3版本：专注于最强主线的退潮风险检测
    :param theme_stocks_map: 主题数据，为None时自己从数据库加载
    :return: 风险分析结果
    """
    print("\n" + "="*60)
    print("🌊 主线退潮风险检测 V3 (最强主题专检)")
    print("="*60)
    
    # 如果传入参数为None，自己从数据库加载数据
    if theme_stocks_map is None:
        print("📊 从数据库加载主题数据...")
        theme_stocks_map = load_theme_stocks_data()
    
    if not theme_stocks_map:
        print("⚠️ 没有主题数据")
        return {
            'risk_level': '安全',
            'risk_score': 0,
            'mainlines': [],
            'signals': [],
            'analysis_summary': '数据不足，无法分析'
        }
    
    # 1. 加载主题评分
    print("📊 加载主题评分...")
    theme_scores = load_theme_scores()
    
    if not theme_scores:
        print("⚠️ 无法获取主题评分，使用股票数量代替")
        # 如果没有评分数据，使用股票数量作为权重
        theme_scores = {theme: len(stocks) for theme, stocks in theme_stocks_map.items()}
    
    # 2. 找出TOP 10最强主题
    sorted_themes = sorted(theme_scores.items(), key=lambda x: x[1], reverse=True)
    top_themes = [t[0] for t in sorted_themes[:10]]
    
    print(f"📊 TOP 10最强主题: {', '.join(top_themes[:5])}...")
    
    # 3. 分析最强主题的健康度
    print("\n📊 正在分析最强主题健康度...")
    mainline_analysis = []
    all_signals = []
    risk_score = 0
    
    for theme_name in top_themes:
        if theme_name not in theme_stocks_map:
            continue
        
        stocks = theme_stocks_map[theme_name]
        health = calculate_theme_health(stocks)
        
        if not health:
            continue
        
        theme_score = theme_scores.get(theme_name, 0)
        
        analysis = {
            'theme': theme_name,
            'score': theme_score,
            'health': health
        }
        mainline_analysis.append(analysis)
        
        # 4. 检测退潮信号
        theme_signals = []
        
        # 信号1: 弱势股过多 (>30%的股票下跌>=3%)
        if health['weak_ratio'] > 0.3:
            theme_signals.append(f"弱势股过多({health['weak_count']}只跌幅≥3%)")
            risk_score += 30
        
        # 信号2: 上涨家数严重不足 (<40%)
        if health['up_ratio'] < 0.4:
            theme_signals.append(f"上涨家数严重不足({health['up_ratio']*100:.0f}%)")
            risk_score += 25
        
        # 信号3: 动量严重衰竭 (<-50)
        if health['net_momentum'] < -50:
            theme_signals.append(f"动量严重衰竭({health['net_momentum']:.0f})")
            risk_score += 25
        
        # 信号4: 上涨家数不足 (<60%)
        if health['up_ratio'] < 0.6:
            theme_signals.append(f"上涨家数偏少({health['up_ratio']*100:.0f}%)")
            risk_score += 10
        
        # 信号5: 弱势股比例偏高 (>15%)
        if health['weak_ratio'] > 0.15:
            theme_signals.append(f"弱势股比例偏高({health['weak_ratio']*100:.0f}%)")
            risk_score += 15
        
        # 信号6: 动量偏弱 (<-20)
        if health['net_momentum'] < -20:
            theme_signals.append(f"动量偏弱({health['net_momentum']:.0f})")
            risk_score += 10
        
        if theme_signals:
            signal_text = f"⚠️ {theme_name}: " + ", ".join(theme_signals)
            all_signals.append(signal_text)
    
    # 5. 综合评估最强主题整体状态
    if mainline_analysis:
        avg_up_ratio = np.mean([a['health']['up_ratio'] for a in mainline_analysis])
        avg_momentum = np.mean([a['health']['net_momentum'] for a in mainline_analysis])
        total_weak = sum([a['health']['weak_count'] for a in mainline_analysis])
        total_stocks = sum([a['health']['total'] for a in mainline_analysis])
        
        # 整体弱势信号
        if avg_up_ratio < 0.4:
            all_signals.append(f"⚠️ 主线整体弱势: 上涨家数均{avg_up_ratio*100:.0f}%")
            risk_score += 25
        elif avg_up_ratio < 0.6:
            all_signals.append(f"⚠️ 主线整体偏弱: 上涨家数均{avg_up_ratio*100:.0f}%")
            risk_score += 10
        
        if avg_momentum < -30:
            all_signals.append(f"⚠️ 主线动量严重衰竭: 均{avg_momentum:.0f}")
            risk_score += 25
        
        if total_weak / total_stocks > 0.2:
            all_signals.append(f"⚠️ 主线内弱势股偏多: {total_weak}只({total_weak/total_stocks*100:.0f}%)")
            risk_score += 20
    
    # 6. 判断风险等级
    if risk_score >= 80:
        risk_level = '严重风险'
    elif risk_score >= 60:
        risk_level = '高风险'
    elif risk_score >= 40:
        risk_level = '中等风险'
    elif risk_score >= 20:
        risk_level = '低风险'
    else:
        risk_level = '安全'
    
    # 7. 生成分析摘要
    analysis_summary = generate_summary_v3(risk_level, risk_score, mainline_analysis, all_signals)
    
    return {
        'risk_level': risk_level,
        'risk_score': risk_score,
        'mainlines': mainline_analysis,
        'signals': all_signals[:10],
        'analysis_summary': analysis_summary
    }


def generate_summary_v3(risk_level, risk_score, mainline_analysis, signals):
    """生成V3版本分析摘要"""
    summary_parts = []
    
    if risk_score < 20:
        summary_parts.append("✅ 最强主线运行健康")
        summary_parts.append("   - TOP主题整体上涨，动能充沛")
        summary_parts.append("   - 建议：继续持有最强主线仓位")
    elif risk_score < 40:
        summary_parts.append("⚠️ 最强主线出现轻微退潮信号")
        summary_parts.append("   - 部分主线内部分化")
        summary_parts.append("   - 建议：关注弱势主题，准备轮动")
    elif risk_score < 60:
        summary_parts.append("⚠️ 中等风险：主线开始走弱")
        summary_parts.append("   - 多个主线出现退潮迹象")
        summary_parts.append("   - 建议：逐步减仓，降低风险敞口")
    elif risk_score < 80:
        summary_parts.append("🚨 高风险警报：主线明显退潮")
        summary_parts.append("   - 最强主题集体走弱")
        summary_parts.append("   - 建议：大幅减仓，规避回调风险")
    else:
        summary_parts.append("🚨 严重风险警报：主线全面退潮")
        summary_parts.append("   - 热点主题全线回调")
        summary_parts.append("   - 建议：清仓观望，等待企稳信号")
    
    # 添加主线健康度统计
    if mainline_analysis:
        avg_up_ratio = np.mean([a['health']['up_ratio'] for a in mainline_analysis])
        avg_momentum = np.mean([a['health']['net_momentum'] for a in mainline_analysis])
        
        summary_parts.append(f"\n📊 主线健康度:")
        summary_parts.append(f"   平均上涨家数比: {avg_up_ratio*100:.1f}%")
        summary_parts.append(f"   平均动量: {avg_momentum:.1f}")
        
        # 显示最强主题的状态
        summary_parts.append(f"\n🔥 最强TOP 5主题状态:")
        for i, analysis in enumerate(mainline_analysis[:5], 1):
            health = analysis['health']
            up_pct = health['up_ratio'] * 100
            summary_parts.append(f"   {i}. {analysis['theme']} 上涨{up_pct:.0f}%({health['up_count']}/{health['total']})")
    
    return '\n'.join(summary_parts)


def save_risk_report(report_data):
    """保存风险报告"""
    report_file = os.path.join(CACHE_DIR, f"recession_risk_report_{datetime.now().strftime('%Y%m%d')}.txt")
    
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("主线退潮风险检测报告 V3\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")
        
        f.write(f"风险等级: {report_data['risk_level']}\n")
        f.write(f"风险得分: {report_data['risk_score']}/100\n\n")
        
        f.write("风险信号:\n")
        for signal in report_data['signals']:
            f.write(f"  {signal}\n")
        
        if report_data['mainlines']:
            f.write("\n主线健康度:\n")
            for analysis in report_data['mainlines'][:10]:
                health = analysis['health']
                f.write(f"  {analysis['theme']}:\n")
                f.write(f"    评分: {analysis['score']:.1f}\n")
                f.write(f"    涨跌幅: {health['avg_change']:+.2f}%\n")
                f.write(f"    上涨: {health['up_count']}/{health['total']}\n")
        
        f.write("\n" + report_data['analysis_summary'] + "\n")
    
    print(f"✅ 风险报告已保存: {report_file}")
    return report_file


if __name__ == "__main__":
    print("="*80)
    print("主线退潮风险检测 V3 - 最强主题专检")
    print("="*80)
    
    # 执行风险检测
    report_data = detect_mainline_recession_risk_v3()
    
    print(f"\n风险等级: {report_data['risk_level']}")
    print(f"风险得分: {report_data['risk_score']}/100\n")
    
    if report_data['signals']:
        print("风险信号:")
        for signal in report_data['signals']:
            print(f"  {signal}")
    
    print(f"\n{report_data['analysis_summary']}")
    
    # 保存报告
    save_risk_report(report_data)
