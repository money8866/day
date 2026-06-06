import os
import sys
import pandas as pd
from datetime import datetime, timedelta
import tushare as ts
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, '.env'))
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
pro = ts.pro_api(TS_TOKEN)

from advanced_buzhang_analysis import AdvancedBuzhangDetector


def get_market_cap_and_turnover(ts_code, trade_date):
    """获取市值和换手率"""
    try:
        df = pro.daily_basic(ts_code=ts_code, trade_date=trade_date)
        if not df.empty:
            total_mv = df['total_mv'].iloc[0] / 10000  # 万元转亿元
            turnover_rate = df['turnover_rate'].iloc[0] if 'turnover_rate' in df.columns else 0
            return total_mv, turnover_rate
    except Exception as e:
        pass
    return 0, 0


def get_stock_data(ts_code, start_date, end_date):
    cache_file = os.path.join(BASE_DIR, 'cache_backbone_tushare', f'{ts_code}_{end_date}.csv')
    if os.path.exists(cache_file):
        return pd.read_csv(cache_file)
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if len(df) >= 30:
            df = df.sort_values('trade_date').reset_index(drop=True)
            df.to_csv(cache_file, index=False)
            return df
    except Exception as e:
        print(f"获取{ts_code}数据失败: {e}")
    return None


def analyze_stock_detailed(ts_code, name, trade_date):
    td = datetime.strptime(trade_date, '%Y%m%d')
    start_date = (td - timedelta(days=100)).strftime('%Y%m%d')
    
    df = get_stock_data(ts_code, start_date, trade_date)
    if df is None or len(df) < 30:
        return None
    
    df_sorted = df.sort_values('trade_date').reset_index(drop=True)
    mc, turnover = get_market_cap_and_turnover(ts_code, trade_date)
    
    detector = AdvancedBuzhangDetector()
    result = detector.analyze_stock(df_sorted, market_cap=mc, turnover_rate=turnover)
    
    if not result.get('valid'):
        return None
    
    # 计算5日涨幅
    pct_5d = 0
    if len(df_sorted) >= 6:
        close_today = df_sorted.iloc[-1]['close']
        close_5d_ago = df_sorted.iloc[-6]['close']
        if close_5d_ago > 0:
            pct_5d = (close_today - close_5d_ago) / close_5d_ago * 100
    
    # 近20日平均成交额
    avg_amount_20 = 0
    if len(df_sorted) >= 21:
        recent_20 = df_sorted.iloc[-21:-1]
        avg_amount_20 = recent_20['amount'].astype(float).mean() / 100000
    
    return {
        'ts_code': ts_code,
        'name': name,
        'overall_score': result.get('overall_score', 0),
        'pct_5d': pct_5d,
        'mc': mc,
        'turnover': turnover,
        'avg_amount_20': avg_amount_20,
        'detected_patterns': result.get('detected_patterns', []),
        'metrics': result.get('metrics', {})
    }


def main():
    trade_date = '20260602'
    if len(sys.argv) > 1:
        trade_date = sys.argv[1]
    
    print(f"\n{'=' * 110}")
    print(f"补涨中军候选评分排序 - 日期: {trade_date}")
    print(f"{'=' * 110}")
    
    # 获取交易日信息
    try:
        td_df = pro.trade_cal(exchange='SSE', start_date=trade_date, end_date=trade_date)
        if not td_df.empty:
            actual_trade_date = td_df['cal_date'].iloc[0]
            print(f"[Init] 交易日: {actual_trade_date}")
            td = datetime.strptime(actual_trade_date, '%Y%m%d')
            start_date = (td - timedelta(days=90)).strftime('%Y%m%d')
            print(f"[Init] K线区间: {start_date} ~ {actual_trade_date}")
    except Exception as e:
        print(f"[Init] 获取交易日失败: {e}")
        td = datetime.strptime(trade_date, '%Y%m%d')
        start_date = (td - timedelta(days=90)).strftime('%Y%m%d')
    
    # 获取主题数据
    import theme_trend_sentiment_score as theme_score
    stock_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry,market,list_date')
    stock_basic = stock_basic[~stock_basic['name'].str.contains('ST|退', na=False)].copy()
    dc_df = theme_score.get_dc_members()
    hot_themes = theme_score.load_theme_json()
    
    # 匹配主题成分股
    theme_stock_map, name_map_basic, stock_industry, stock_concepts = theme_score.match_theme_stocks(hot_themes, dc_df, stock_basic)
    
    # 合并所有主题的成分股
    all_stocks = []
    seen_codes = set()
    
    themes_to_analyze = ['电力链', 'AI算力链', '华为鸿蒙', '半导体']
    
    for theme_name in themes_to_analyze:
        if theme_name not in hot_themes:
            continue
        
        theme_codes = list(theme_stock_map.get(theme_name, {}).keys())
        print(f"\n   分析主题: {theme_name} ({len(theme_codes)}只)")
        
        for code in theme_codes:
            if code in seen_codes:
                continue
            seen_codes.add(code)
            analysis = analyze_stock_detailed(code, name_map_basic.get(code, code), trade_date)
            if analysis:
                analysis['theme'] = theme_name
                all_stocks.append(analysis)
    
    # 筛选条件
    filtered_stocks = []
    for stock in all_stocks:
        pct_ok = stock['pct_5d'] <= 18
        mc_ok = 200 <= stock['mc'] <= 2000
        amount_ok = stock['avg_amount_20'] >= 8
        score_ok = stock['overall_score'] >= 50
        
        if pct_ok and mc_ok and amount_ok and score_ok:
            filtered_stocks.append(stock)
    
    # 按评分降序
    filtered_stocks.sort(key=lambda x: -x['overall_score'])
    
    # ========== 按主题分组输出Top5 ==========
    print(f"\n\n{'=' * 110}")
    print("各主题链补涨中军候选Top5")
    print(f"{'=' * 110}")
    
    for theme in themes_to_analyze:
        theme_stocks = [s for s in filtered_stocks if s['theme'] == theme][:5]
        if not theme_stocks:
            continue
            
        print(f"\n{'=' * 110}")
        print(f"{theme} - 补涨中军候选Top5")
        print(f"{'=' * 110}")
        print(f"{'排名':<6}{'代码':<14}{'名称':<10}{'综合评分':<10}{'5日涨幅%':<10}{'市值亿':<10}{'换手率%':<10}{'成交额亿':<12}")
        print('-' * 110)
        
        for idx, stock in enumerate(theme_stocks):
            print(f"{idx+1:<6}{stock['ts_code']:<14}{stock['name']:<10}{stock['overall_score']:<10.1f}{stock['pct_5d']:<10.1f}{stock['mc']:<10.1f}{stock['turnover']:<10.2f}{stock['avg_amount_20']:<12.1f}")
        
        # 显示该主题前3名的详细评分
        print(f"\n{theme} - 前3名详细评分（权重：大成交额35%、换手率20%、大市值15%、价格趋势15%、量价配合10%、技术面5%）")
        print(f"{'排名':<6}{'名称':<10}{'综合评分':<10}{'大成交额':<10}{'换手率':<10}{'大市值':<10}{'价格趋势':<10}{'量价配合':<10}{'技术面':<10}")
        print('-' * 110)
        
        for idx, stock in enumerate(theme_stocks[:3]):
            metrics = stock.get('metrics', {})
            print(f"{idx+1:<6}{stock['name']:<10}{stock['overall_score']:<10.1f}"
                  f"{metrics.get('big_amount', 0):<10.1f}{metrics.get('turnover_rate', 0):<10.1f}"
                  f"{metrics.get('big_market_cap', 0):<10.1f}"
                  f"{metrics.get('price_trend', 0):<10.1f}{metrics.get('volume_coordination', 0):<10.1f}"
                  f"{metrics.get('technicals', 0):<10.1f}")
    
    # ========== 全市场Top50 ==========
    display_stocks = filtered_stocks[:50]
    
    print(f"\n\n{'=' * 110}")
    print("全市场补涨中军候选TOP50（按综合评分排序）")
    print(f"{'=' * 110}")
    print(f"\n{'排名':<6}{'代码':<12}{'名称':<8}{'主题':<10}{'综合评分':<10}{'5日涨幅%':<10}{'市值亿':<10}{'换手率%':<10}{'成交额亿':<12}")
    print('-' * 110)
    
    for idx, stock in enumerate(display_stocks):
        print(f"{idx+1:<6}{stock['ts_code']:<12}{stock['name']:<8}{stock['theme']:<10}{stock['overall_score']:<10.1f}{stock['pct_5d']:<10.1f}{stock['mc']:<10.1f}{stock['turnover']:<10.2f}{stock['avg_amount_20']:<12.1f}")
    
    # 显示前20名的详细评分
    print(f"\n{'=' * 110}")
    print("前20名股票详细评分（权重：大成交额35%、换手率20%、大市值15%、价格趋势15%、量价配合10%、技术面5%）")
    print(f"{'=' * 110}")
    print(f"{'排名':<6}{'名称':<8}{'综合评分':<10}{'大成交额':<10}{'换手率':<10}{'大市值':<10}{'价格趋势':<10}{'量价配合':<10}{'技术面':<10}")
    print('-' * 110)
    
    for idx, stock in enumerate(display_stocks[:20]):
        metrics = stock.get('metrics', {})
        print(f"{idx+1:<6}{stock['name']:<8}{stock['overall_score']:<10.1f}"
              f"{metrics.get('big_amount', 0):<10.1f}{metrics.get('turnover_rate', 0):<10.1f}"
              f"{metrics.get('big_market_cap', 0):<10.1f}"
              f"{metrics.get('price_trend', 0):<10.1f}{metrics.get('volume_coordination', 0):<10.1f}"
              f"{metrics.get('technicals', 0):<10.1f}")
    
    print(f"\n共 {len(filtered_stocks)} 只候选股票（显示前50名）")


if __name__ == "__main__":
    main()
