#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =========================================================
# ETF-to-Stock Pipeline (ETF→板块→个股)
# =========================================================
# 策略逻辑：
# 1. 分析最强ETF（多维度评分）
# 2. 映射ETF到行业分类
# 3. 获取行业成分股
# 4. 个股量化筛选（基本面+技术面）
# 5. 输出TOP5推荐个股
# =========================================================

import os
import sys
import io
import time
import json
import pickle
import sqlite3
import requests
import numpy as np
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta
from dotenv import load_dotenv

# =========================
# 编码修复（Windows PowerShell）
# =========================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# =========================================================
# 环境变量
# =========================================================
load_dotenv("config/.env")

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERVERCHAN_KEY = os.getenv("WECHAT_SCKEY")

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# =========================================================
# 路径配置
# =========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

# =========================================================
# ETF池（37只）
# =========================================================
ETF_POOL = {
    '半导体': '512480.SH',
    '人工智能': '159819.SZ',
    '算力': '561210.SH',
    '机器人': '562500.SH',
    '软件': '515230.SH',
    '通信': '515880.SH',
    '新能源': '516160.SH',
    '光伏': '515790.SH',
    '储能': '159566.SZ',
    '军工': '512660.SH',
    '创新药': '159992.SZ',
    '消费电子': '159732.SZ',
    '黄金': '518880.SH',
    '证券': '512880.SH',
    '红利': '515180.SH',
    '银行': '512800.SH',
    '消费': '159928.SZ',
    '酒': '512690.SH',
    '电池': '159755.SZ',
    '有色金属': '516650.SH',
    '芯片': '159995.SZ',
    '化工': '159870.SZ',
    '半导体设备': '159516.SZ',
    '煤炭': '515220.SH',
    '游戏': '159869.SZ',
    '金融科技': '159851.SZ',
    '电力': '159611.SZ',
    '电网设备': '561380.SH',
    '新能源车': '515030.SH',
    '航空航天': '159227.SZ',
    '医疗器械': '159883.SZ',
    '食品饮料': '159736.SZ',
    '钢铁': '515210.SH',
}

# =========================================================
# ETF → 行业映射（用于获取成分股）
# =========================================================
ETF_TO_INDUSTRY = {
    '512480.SH': '半导体',
    '159819.SZ': '人工智能',
    '561210.SH': '算力',
    '562500.SH': '机器人',
    '515790.SH': '光伏',
    '512660.SH': '军工',
    '159992.SZ': '创新药',
    '515230.SH': '软件',
    '515880.SH': '通信',
    '516160.SH': '新能源',
    '159566.SZ': '储能',
    '159755.SZ': '电池',
    '515030.SH': '新能源车',
    '159732.SZ': '消费电子',
    '159928.SZ': '消费',
    '512690.SH': '酒',
    '159995.SZ': '芯片',
    '159870.SZ': '化工',
    '159516.SZ': '半导体设备',
    '515220.SH': '煤炭',
    '159869.SZ': '游戏',
    '159851.SZ': '金融科技',
    '159611.SZ': '电力',
    '561380.SH': '电网设备',
    '159227.SZ': '航空航天',
    '159883.SZ': '医疗器械',
    '159736.SZ': '食品饮料',
    '515210.SH': '钢铁',
    '516650.SH': '有色金属',
    '518880.SH': '黄金',
    '512880.SH': '证券',
    '512800.SH': '银行',
    '515180.SH': '红利',
}

# =========================================================
# 交易日获取
# =========================================================
def get_last_trade_date():
    """获取最近交易日"""
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    cal = pro.trade_cal(exchange='', start_date='20240101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    return str(cal[cal['cal_date'] <= query_date]['cal_date'].max())


TRADE_DATE = get_last_trade_date()


# =========================================================
# Step 1: 分析最强ETF
# =========================================================
def analyze_strongest_etf():
    """
    分析最强ETF（简化版etf_quant_v2.py逻辑）
    
    返回:
        dict: 最强ETF信息
    """
    print("\n" + "="*60)
    print("Step 1: 分析最强ETF")
    print("="*60)
    
    all_result = []
    
    for industry, ts_code in ETF_POOL.items():
        try:
            # 获取ETF数据
            df = pro.fund_daily(
                ts_code=ts_code,
                start_date=(datetime.now() - timedelta(days=60)).strftime('%Y%m%d'),
                fields='ts_code,trade_date,close,pct_chg,vol,amount'
            )
            
            if df is None or len(df) < 20:
                continue
            
            df = df.sort_values('trade_date')
            
            # 计算技术指标
            df['ma5'] = df['close'].rolling(5).mean()
            df['ma10'] = df['close'].rolling(10).mean()
            df['ma20'] = df['close'].rolling(20).mean()
            df['pct5'] = (df['close'] / df['close'].shift(5) - 1) * 100
            df['pct10'] = (df['close'] / df['close'].shift(10) - 1) * 100
            df['pct20'] = (df['close'] / df['close'].shift(20) - 1) * 100
            
            latest = df.iloc[-1]
            
            # 简化评分
            score = 0
            score += latest['pct5'] * 2 if pd.notna(latest['pct5']) else 0
            score += latest['pct10'] if pd.notna(latest['pct10']) else 0
            
            if pd.notna(latest['ma5']) and pd.notna(latest['ma10']) and pd.notna(latest['ma20']):
                if latest['ma5'] > latest['ma10'] > latest['ma20']:
                    score += 20
            
            all_result.append({
                '行业': industry,
                'ETF代码': ts_code,
                '收盘价': round(latest['close'], 2),
                '涨跌幅': round(latest['pct_chg'], 2),
                '总评分': round(score, 2)
            })
            
            time.sleep(0.1)
            
        except Exception as e:
            print(f"  ⚠️  {industry} ({ts_code}) 分析失败: {e}")
            continue
    
    if not all_result:
        return None
    
    result_df = pd.DataFrame(all_result)
    result_df = result_df.sort_values('总评分', ascending=False)
    
    print(f"\n最强ETF TOP 3:")
    print(result_df.head(3).to_string(index=False))
    
    return result_df.iloc[0].to_dict()


# =========================================================
# Step 2: 获取行业成分股
# =========================================================
def get_industry_stocks(industry, max_stocks=50):
    """
    获取行业成分股
    
    参数:
        industry: 行业名称
        max_stocks: 最大返回数量
    
    返回:
        list: 股票代码列表
    """
    print(f"\n" + "="*60)
    print(f"Step 2: 获取 {industry} 行业成分股")
    print("="*60)
    
    stocks = []
    
    try:
        # 方法A：申万行业分类（更权威）
        # 行业代码映射（部分示例）
        industry_code_map = {
            '半导体': '851950.SI',
            '人工智能': '851743.SI',
            '光伏': '851611.SI',
            '军工': '851730.SI',
            '创新药': '851631.SI',
            '软件': '851723.SI',
            '通信': '851724.SI',
            '新能源': '851711.SI',
            '电池': '851712.SI',
            '新能源车': '851713.SI',
            '消费电子': '851722.SI',
            '消费': '851111.SI',
            '酒': '851121.SI',
            '芯片': '851950.SI',
            '化工': '851621.SI',
            '煤炭': '851131.SI',
            '游戏': '851741.SI',
            '电力': '851141.SI',
            '航空航天': '851731.SI',
            '医疗器械': '851632.SI',
            '食品饮料': '851121.SI',
            '钢铁': '851151.SI',
            '有色金属': '851141.SI',
            '证券': '851151.SI',
            '银行': '851151.SI',
        }
        
        index_code = industry_code_map.get(industry)
        
        if index_code:
            # 使用申万行业指数成分股
            df_members = pro.index_member(index_code=index_code)
            stocks = df_members['con_code'].tolist()
            print(f"  ✅ 申万行业分类: {len(stocks)} 只")
        else:
            # 方法B：Tushare行业分类
            df_basic = pro.stock_basic(exchange='', fields='ts_code,industry')
            df_industry = df_basic[df_basic['industry'] == industry]
            stocks = df_industry['ts_code'].tolist()
            print(f"  ✅ Tushare行业分类: {len(stocks)} 只")
    
    except Exception as e:
        print(f"  ⚠️  获取成分股失败: {e}")
        # 降级方案：返回空列表
        stocks = []
    
    if not stocks:
        print(f"  ⚠️  未找到 {industry} 的成分股")
        return []
    
    # 限制数量
    stocks = stocks[:max_stocks]
    
    print(f"  分析范围: {len(stocks)} 只")
    print(f"  股票列表: {', '.join(stocks[:10])}...")
    
    return stocks


# =========================================================
# Step 3: 个股量化筛选
# =========================================================
def analyze_stock(ts_code, trade_date):
    """
    个股量化分析（简化版tushare_quant.py逻辑）
    
    参数:
        ts_code: 股票代码
        trade_date: 交易日期
    
    返回:
        dict: 分析结果
    """
    try:
        # 获取日线数据
        df = pro.daily(
            ts_code=ts_code,
            start_date=(datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=120)).strftime('%Y%m%d'),
            end_date=trade_date
        )
        
        if df is None or len(df) < 60:
            return None
        
        df = df.sort_values('trade_date')
        
        # 基本面数据
        df_basic = pro.stock_basic(ts_code=ts_code, fields='ts_code,name,industry')
        stock_name = df_basic.iloc[0]['name'] if not df_basic.empty else ts_code
        
        # 技术指标
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma10'] = df['close'].rolling(10).mean()

        df['ma20'] = df['close'].rolling(20).mean()
        df['pct5'] = (df['close'] / df['close'].shift(5) - 1) * 100
        df['pct20'] = (df['close'] / df['close'].shift(20) - 1) * 100
        
        latest = df.iloc[-1]
        
        # 简化评分
        score = 0
        
        # 趋势评分
        if pd.notna(latest['pct5']):
            score += latest['pct5'] * 2
        if pd.notna(latest['pct20']):
            score += latest['pct20']
        
        # 多头排列
        if pd.notna(latest['ma5']) and pd.notna(latest['ma10']) and pd.notna(latest['ma20']):
            if latest['ma5'] > latest['ma10'] > latest['ma20']:
                score += 20
        
        # 信号识别
        signal = '观察'
        if pd.notna(latest['pct5']) and latest['pct5'] > 5:
            signal = '主升浪'
        elif pd.notna(latest['pct20']) and latest['pct20'] > 20:
            signal = '趋势衰竭'
        
        return {
            '代码': ts_code,
            '名称': stock_name,
            '收盘价': round(latest['close'], 2),
            '涨跌幅': round(latest['pct_chg'], 2),
            '总评分': round(score, 2),
            '信号': signal
        }
    
    except Exception as e:
        return None


def screen_stocks(stocks, trade_date, top_n=5):
    """
    批量筛选股票
    
    参数:
        stocks: 股票代码列表
        trade_date: 交易日期
        top_n: 返回TOP N
    
    返回:
        list: TOP N股票
    """
    print(f"\n" + "="*60)
    print(f"Step 3: 个股量化筛选（{len(stocks)} 只）")
    print("="*60)
    
    results = []
    
    for i, ts_code in enumerate(stocks):
        print(f"  [{i+1}/{len(stocks)}] 分析 {ts_code}")
        
        result = analyze_stock(ts_code, trade_date)
        
        if result:
            results.append(result)
        
        time.sleep(0.05)  # Tushare限速
    
    if not results:
        return []
    
    # 排序
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('总评分', ascending=False)
    
    print(f"\n筛选结果 TOP {top_n}:")
    print(results_df.head(top_n).to_string(index=False))
    
    return results_df.head(top_n).to_dict('records')


# =========================================================
# Step 4: 生成报告
# =========================================================
def generate_report(strongest_etf, top_stocks):
    """
    生成分析报告
    
    参数:
        strongest_etf: 最强ETF信息
        top_stocks: TOP个股列表
    
    返回:
        str: 报告内容
    """
    print(f"\n" + "="*60)
    print("Step 4: 生成报告")
    print("="*60)
    
    report = f"""
# ETF-to-Stock Pipeline 分析报告
生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
交易日: {TRADE_DATE}

---

## 一、最强ETF分析

**行业**: {strongest_etf['行业']}
**ETF代码**: {strongest_etf['ETF代码']}
**收盘价**: {strongest_etf['收盘价']}
**涨跌幅**: {strongest_etf['涨跌幅']:+.2f}%
**总评分**: {strongest_etf['总评分']}

---

## 二、推荐个股 TOP 5

"""
    
    for i, stock in enumerate(top_stocks, 1):
        report += f"""
### {i}. {stock['名称']} ({stock['代码']})

- **收盘价**: {stock['收盘价']}
- **涨跌幅**: {stock['涨跌幅']:+.2f}%
- **总评分**: {stock['总评分']}
- **信号**: {stock['信号']}

"""
    
    report += """
---

## 三、操作建议

1. **ETF层面**: 关注最强ETF的回调买点
2. **个股层面**: 优先选择评分>80的个股
3. **仓位管理**: 单只个股不超过20%
4. **止损设置**: -5%严格止损

---

## 四、风险提示

1. 板块轮动风险
2. 个股黑天鹅风险
3. 市场系统性风险

---

**免责声明**: 本报告仅供参考，不构成投资建议。
"""
    
    return report


# =========================================================
# Step 5: 保存和推送
# =========================================================
def save_report(report):
    """保存报告"""
    report_file = os.path.join(REPORT_DIR, f"ETF_Stock_Pipeline_{TRADE_DATE}.md")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n✅ 报告已保存: {report_file}")
    return report_file


def send_wechat(report):
    """微信推送"""
    if not SERVERCHAN_KEY:
        print("\n⚠️  未配置Server酱，跳过推送")
        return
    
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    data = {
        "title": f"ETF-to-Stock Pipeline {TRADE_DATE}",
        "desp": report
    }
    
    try:
        response = requests.post(url, data=data, timeout=30)
        if response.status_code == 200:
            print("\n✅ 微信推送成功")
        else:
            print(f"\n❌ 微信推送失败: {response.status_code}")
    except Exception as e:
        print(f"\n❌ 微信推送失败: {e}")


# =========================================================
# 主程序
# =========================================================
def main():
    print("\n" + "="*60)
    print("ETF-to-Stock Pipeline (ETF→板块→个股)")
    print("="*60)
    print(f"交易日: {TRADE_DATE}")
    
    # Step 1: 分析最强ETF
    strongest_etf = analyze_strongest_etf()
    if not strongest_etf:
        print("\n❌ 无法识别最强ETF，退出")
        return
    
    # Step 2: 获取行业成分股
    industry = strongest_etf['行业']
    stocks = get_industry_stocks(industry, max_stocks=50)
    
    if not stocks:
        print(f"\n❌ 未找到 {industry} 的成分股，退出")
        return
    
    # Step 3: 个股量化筛选
    top_stocks = screen_stocks(stocks, TRADE_DATE, top_n=5)
    
    if not top_stocks:
        print("\n❌ 未筛选出符合条件的个股，退出")
        return
    
    # Step 4: 生成报告
    report = generate_report(strongest_etf, top_stocks)
    
    # Step 5: 保存和推送
    save_report(report)
    send_wechat(report)
    
    print("\n" + "="*60)
    print("✅ Pipeline 执行完成")
    print("="*60)
    print(report)


# =========================================================
# 快速模式（仅分析TOP1 ETF的前10只股票）
# =========================================================
def quick_mode():
    """快速模式"""
    print("\n" + "="*60)
    print("快速模式（仅分析TOP1 ETF的前10只股票）")
    print("="*60)
    
    # 分析最强ETF
    strongest_etf = analyze_strongest_etf()
    if not strongest_etf:
        return
    
    # 获取成分股（仅前10）
    industry = strongest_etf['行业']
    stocks = get_industry_stocks(industry, max_stocks=10)
    
    # 筛选
    top_stocks = screen_stocks(stocks, TRADE_DATE, top_n=3)
    
    # 生成报告
    if top_stocks:
        report = generate_report(strongest_etf, top_stocks)
        save_report(report)
        print(report)


if __name__ == '__main__':
    # 选择运行模式
    if len(sys.argv) > 1 and sys.argv[1] == 'quick':
        quick_mode()
    else:
        main()
