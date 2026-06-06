#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题评分趋势曲线生成器
- 读取60天周期的趋势分、情绪分、综合分的曲线图表
- 独立程序测试
"""
import os
import sys
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import rcParams
import matplotlib.dates as mdates
from datetime import datetime, timedelta
from collections import defaultdict

# 配置matplotlib显示中文
try:
    rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    rcParams['axes.unicode_minus'] = False
except Exception as e:
    print(f"中文字体配置失败: {e}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
OUTPUT_DB = os.path.join(CACHE_DIR, "theme_trend_sentiment.db")

def get_theme_history(n_days=60):
    """
    从数据库获取主题历史评分数据
    Args:
        n_days: 获取最近多少天的数据
    Returns:
        (themes_data: dict, {theme_name: list of scores}
        all_dates: list, sorted date list
    """
    if not os.path.exists(OUTPUT_DB):
        print(f"数据库不存在: {OUTPUT_DB}")
        return {}, []

    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()

    # 获取所有可用的交易日期（按倒序）
    cur.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC")
    all_dates = [row[0] for row in cur.fetchall()]
    
    if not all_dates:
        print("数据库中无数据")
        conn.close()
        return {}, []
    
    # 取最近n_days天
    recent_dates = all_dates[:n_days]
    recent_dates = sorted(recent_dates)  # 按日期顺序排列
    
    print(f"读取 {len(recent_dates)} 个交易日的数据（{recent_dates[0]} ~ {recent_dates[-1]}")

    # 查询所有主题在这些日期的数据
    cur.execute("""
        SELECT trade_date, theme, trend_score, sentiment_score, composite_score 
        FROM theme_scores 
        WHERE trade_date IN ({})
    """.format(','.join(['?'] * len(recent_dates))), recent_dates)

    rows = cur.fetchall()
    conn.close()

    # 按主题整理数据
    themes_data = defaultdict(lambda: {
        'dates': [],
        'trend_scores': [],
        'sentiment_scores': [],
        'composite_scores': []
    })

    for td, theme, ts, ss, cs in rows:
        themes_data[theme]['dates'].append(td)
        themes_data[theme]['trend_scores'].append(ts)
        themes_data[theme]['sentiment_scores'].append(ss)
        themes_data[theme]['composite_scores'].append(cs)

    # 清理数据：确保每个主题的数据是连续的日期序列
    themes_data_clean = {}
    for theme, data in themes_data.items():
        df = pd.DataFrame({
            'date': data['dates'],
            'trend': data['trend_scores'],
            'sentiment': data['sentiment_scores'],
            'composite': data['composite_scores']
        })
        df = df.sort_values('date').drop_duplicates('date')
        
        # 保留至少有30天数据的主题
        if len(df) >= 30:
            themes_data_clean[theme] = df
    
    print(f"共 {len(themes_data_clean)} 个主题有足够数据（≥30天）")
    
    return themes_data_clean, recent_dates

def plot_trend_curves(themes_data, output_dir='theme_trend_charts'):
    """
    绘制主题趋势曲线图
    Args:
        themes_data: 主题数据
        output_dir: 输出目录
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 1. 综合分TOP5主题的趋势曲线
    print("\n1. 生成综合分TOP5主题的趋势曲线...")
    composite_top5 = []
    for theme, df in themes_data.items():
        if len(df) > 0:
            latest_composite = df['composite'].iloc[-1]
            composite_top5.append((-latest_composite, theme))

    composite_top5.sort()
    composite_top5 = [theme for (score, theme) in composite_top5[:10]]  # 取TOP10，实际画前5

    plt.figure(figsize=(16, 10))
    
    colors = ['red', 'blue', 'green', 'orange', 'purple', 'brown', 'pink', 'gray', 'olive', 'cyan']
    for idx, theme in enumerate(composite_top5[:5]):
        df = themes_data[theme]
        # 转换日期
        dates = pd.to_datetime(df['date'], format='%Y%m%d')
        plt.plot(dates, df['composite'], marker='o', markersize=4, label=theme, color=colors[idx])

    plt.title(f'主题综合分TOP5趋势曲线（60天）', fontsize=16, fontweight='bold')
    plt.xlabel('日期', fontsize=14)
    plt.ylabel('综合分', fontsize=14)
    plt.legend(loc='upper left', fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    plt.gca().xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.xticks(rotation=45)
    plt.tight_layout()

    output_path = os.path.join(output_dir, 'top5_composite_trends.png')
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"   ✓ 已保存: {output_path}")

    # 2. 趋势分vs情绪分对比图（TOP3主题）
    print("\n2. 生成趋势分vs情绪分对比图...")
    plt.figure(figsize=(18, 12))
    
    for idx, theme in enumerate(composite_top5[:3]):
        plt.subplot(3, 1, idx + 1)
        df = themes_data[theme]
        dates = pd.to_datetime(df['date'], format='%Y%m%d')
        
        plt.plot(dates, df['trend'], marker='s', label='趋势分', linewidth=2)
        plt.plot(dates, df['sentiment'], marker='o', label='情绪分', linewidth=2)
        plt.plot(dates, df['composite'], marker='^', label='综合分', linewidth=2, alpha=0.7)
        
        plt.title(f'{theme} - 趋势分/情绪分/综合分', fontsize=14)
        plt.xlabel('日期', fontsize=12)
        plt.ylabel('评分', fontsize=12)
        plt.legend(loc='best', fontsize=10)
        plt.grid(True, alpha=0.3)
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
        plt.gca().xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        plt.xticks(rotation=30)
    
    plt.tight_layout()
    output_path = os.path.join(output_dir, 'top3_trend_sentiment.png')
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"   ✓ 已保存: {output_path}")

    # 3. 各主题独立图（TOP10）
    print("\n3. 生成各主题独立趋势图...")
    for idx, theme in enumerate(composite_top5[:10]):
        plt.figure(figsize=(14, 7))
        df = themes_data[theme]
        dates = pd.to_datetime(df['date'], format='%Y%m%d')
        
        plt.plot(dates, df['trend'], marker='s', label='趋势分', linewidth=2)
        plt.plot(dates, df['sentiment'], marker='o', label='情绪分', linewidth=2)
        plt.plot(dates, df['composite'], marker='^', label='综合分', linewidth=2)
        
        plt.title(f'{theme} - 60天评分趋势', fontsize=16)
        plt.xlabel('日期', fontsize=14)
        plt.ylabel('评分', fontsize=14)
        plt.legend(loc='best', fontsize=12)
        plt.grid(True, alpha=0.3)
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
        plt.gca().xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        output_path = os.path.join(output_dir, f'trend_{theme}.png')
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"   ✓ 已保存: {output_path}")

    # 4. 热力图（所有主题综合分汇总
    print("\n4. 生成热力图...")
    # 构建综合分数据矩阵
    all_df_list = []
    for theme in composite_top5[:10]:
        df = themes_data[theme].copy()
        df['theme'] = theme
        all_df_list.append(df)
    
    all_df = pd.concat(all_df_list)
    pivot_df = all_df.pivot(index='date', columns='theme', values='composite').fillna(0)
    pivot_df = pivot_df.sort_index()

    plt.figure(figsize=(16, 10))
    dates = pd.to_datetime(pivot_df.index, format='%Y%m%d')
    plt.imshow(pivot_df.T, aspect='auto', cmap='YlOrRd', interpolation='nearest')
    
    plt.yticks(range(len(pivot_df.columns)), pivot_df.columns)
    plt.xticks(range(0, len(pivot_df.index), 5), [dates[i].strftime('%m-%d') for i in range(0, len(dates), 5)])
    plt.colorbar(label='综合分')
    plt.title('主题综合分热力图（60天）', fontsize=16)
    plt.tight_layout()
    
    output_path = os.path.join(output_dir, 'composite_heatmap.png')
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"   ✓ 已保存: {output_path}")

    print(f"\n📊 共生成 {5 + 3 + 10 + 1} 个图表")

def main():
    print("=" * 80)
    print("主题评分趋势曲线生成器")
    print("=" * 80)

    print("\n1. 读取历史数据...")
    themes_data, dates = get_theme_history(n_days=60)

    if not themes_data:
        print("   ❌ 没有足够数据")
        return

    print("\n2. 生成趋势曲线...")
    plot_trend_curves(themes_data)

    print("\n" + "=" * 80)
    print("完成")

if __name__ == '__main__':
    main()
