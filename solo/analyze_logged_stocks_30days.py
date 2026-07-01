"""
姣忔棩鍒嗘瀽鏃ュ織涓彁鍒颁釜鑲＄殑鏈€鏂板叕鍛婂拰璧勮

淇敼锛氭壂鎻忔渶杩?0澶╋紝纭繚鏈夋暟鎹?"""

import os
import json
from datetime import datetime, timedelta

BASE_DIR = r'D:\mystock'
NEWS_CACHE_DIR = r'D:\mystock\news_cache'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

TUSHARE_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

def get_tushare_pro():
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    return ts.pro_api()

def extract_stocks_from_logs(days=30):
    """浠庢棩蹇椾腑鎻愬彇涓偂 - 榛樿30澶?""
    print(f'鎵弿鏈€杩?{days} 澶╃殑鏃ュ織...')
    print(f'鐩綍: {NEWS_CACHE_DIR}')
    
    if not os.path.exists(NEWS_CACHE_DIR):
        print('閿欒: 鐩綍涓嶅瓨鍦?')
        return []
    
    # 鑾峰彇鎵€鏈夋棩蹇楁枃浠?    files = [f for f in os.listdir(NEWS_CACHE_DIR) 
             if f.startswith('ai_analysis_') and f.endswith('.json')]
    
    print(f'鎵惧埌 {len(files)} 涓棩蹇楁枃浠?)
    
    # 杩囨护鏈€杩慛澶╃殑
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    print(f'Cutoff: {cutoff_date} (鏈€杩憑days}澶?')
    
    stocks = []
    recent_count = 0
    
    for filename in files:
        try:
            # 鎻愬彇鏃ユ湡
            date_str = filename.split('_')[-1].replace('.json', '')
            
            if date_str >= cutoff_date:
                recent_count += 1
                
                # 璇诲彇鏂囦欢
                filepath = os.path.join(NEWS_CACHE_DIR, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                code = data.get('code', '')
                name = data.get('name', '')
                response = data.get('response', '')
                
                # 鎻愬彇璇勫垎锛堟渶鍚庝竴琛岋級
                score = 50  # 榛樿
                if response:
                    lines = response.strip().split('\n')
                    # 浠庢渶鍚庝竴琛屾彁鍙栨暟瀛?                    for line in reversed(lines):
                        line = line.strip()
                        if line.isdigit() and 0 <= int(line) <= 100:
                            score = int(line)
                            break
                
                stocks.append({
                    'ts_code': code,
                    'name': name,
                    'date': date_str,
                    'score': score,
                    'response': response
                })
        except Exception as e:
            print(f'  澶勭悊 {filename} 澶辫触: {e}')
    
    print(f'鏈€杩憑days}澶╁唴鏈?{recent_count} 涓棩蹇楁枃浠?)
    print(f'鎻愬彇鍒?{len(stocks)} 涓偂')
    
    # 鍘婚噸锛堜繚鐣欐渶鏂版棩鏈燂級
    seen = {}
    for stock in stocks:
        code = stock['ts_code']
        if code not in seen or stock['date'] > seen[code]['date']:
            seen[code] = stock
    
    stocks = list(seen.values())
    print(f'鍘婚噸鍚? {len(stocks)} 涓偂')
    
    # 鎸夎瘎鍒嗘帓搴?    stocks.sort(key=lambda x: x['score'], reverse=True)
    
    return stocks

def get_announcements(pro, ts_code):
    """鑾峰彇鏈€鏂板叕鍛婏紙鏈€杩?0澶╋級"""
    try:
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
        end_date = datetime.now().strftime('%Y%m%d')
        
        df = pro.anns(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df is not None and len(df) > 0:
            # 绛涢€夐噸瑕佸叕鍛?            important_keywords = ['閲嶇粍', '鏀惰喘', '瀹氬', '鑲℃潈婵€鍔?, '楂橀€佽浆', 
                                  '涓氱哗棰勫憡', '涓氱哗蹇姤', '骞存姤', '瀛ｆ姤', '鍋滅墝', '澶嶇墝', '閲嶅ぇ浜嬮」']
            
            important = df[df['title'].str.contains('|'.join(important_keywords), na=False, regex=True)]
            
            if len(important) > 0:
                return important.head(10).to_dict('records')
            else:
                return df.head(5).to_dict('records')
    except Exception as e:
        print(f'  鑾峰彇鍏憡澶辫触: {e}')
    
    return []

def get_news(pro, ts_code):
    """鑾峰彇鏈€鏂版柊闂伙紙鏈€杩?澶╋級"""
    try:
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')
        end_date = datetime.now().strftime('%Y%m%d')
        
        df = pro.news(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df is not None and len(df) > 0:
            return df.head(10).to_dict('records')
    except Exception as e:
        print(f'  鑾峰彇鏂伴椈澶辫触: {e}')
    
    return []

def generate_report(stocks_with_info):
    """鐢熸垚鎶ュ憡"""
    report = f"""# 鏃ュ織涓偂鏈€鏂板叕鍛婁笌璧勮鍒嗘瀽

鐢熸垚鏃堕棿: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 姒傝

鏈鍒嗘瀽 {len(stocks_with_info)} 鍙釜鑲★紝鍧囦负杩戞湡 AI 鍒嗘瀽鏃ュ織涓彁鍒扮殑鑲＄エ銆?
鎸夋儏缁瘎鍒嗕粠楂樺埌浣庢帓搴忋€?
## 涓偂璇︽儏

"""
    
    for i, stock in enumerate(stocks_with_info, 1):
        ts_code = stock['ts_code']
        name = stock['name']
        score = stock['score']
        announcements = stock.get('announcements', [])
        news = stock.get('news', [])
        
        report += f"### {i}. {name} ({ts_code}) - 鎯呯华璇勫垎: {score}\n\n"
        
        # AI鍒嗘瀽鎽樿
        response = stock.get('response', '')
        if response:
            # 鍙栫涓€琛屼綔涓烘憳瑕?            summary = response.strip().split('\n')[0]
            report += f"**AI鍒嗘瀽鎽樿:** {summary}\n\n"
        
        # 鍏憡
        if announcements:
            report += "**鏈€鏂板叕鍛?**\n\n"
            for ann in announcements[:5]:
                report += f"- {ann.get('datetime', '')[:10]} [{ann.get('type', '')}] {ann.get('title', '')}\n"
            report += "\n"
        else:
            report += "**鏈€鏂板叕鍛?** 鏃犻噸瑕佸叕鍛奬n\n"
        
        # 鏂伴椈
        if news:
            report += "**鏈€鏂拌祫璁?**\n\n"
            for n in news[:5]:
                report += f"- {n.get('datetime', '')[:10]} {n.get('title', '')}\n"
            report += "\n"
        else:
            report += "**鏈€鏂拌祫璁?** 鏃燶n\n"
        
        report += "---\n\n"
    
    return report

def main():
    print('=' * 70)
    print('姣忔棩鍒嗘瀽鏃ュ織涓彁鍒颁釜鑲＄殑鏈€鏂板叕鍛婂拰璧勮')
    print('=' * 70)
    print()
    
    # 1. 鎻愬彇涓偂锛堟渶杩?0澶╋級
    stocks = extract_stocks_from_logs(days=30)
    
    if not stocks:
        print('鏃犳暟鎹紝閫€鍑?)
        return
    
    print()
    print('=' * 70)
    print(f'灏嗗垎鏋?{len(stocks)} 鍙釜鑲?)
    print('=' * 70)
    print()
    
    # 鏄剧ず鍓?0鍙?    print('Top 10 涓偂:')
    for i, stock in enumerate(stocks[:10], 1):
        print(f'  {i}. {stock["name"]} ({stock["ts_code"]}) - 璇勫垎: {stock["score"]}')
    print()
    
    # 2. 鑾峰彇 Tushare Pro API
    try:
        pro = get_tushare_pro()
        print('Tushare Pro API 杩炴帴鎴愬姛')
    except Exception as e:
        print(f'Tushare Pro API 杩炴帴澶辫触: {e}')
        return
    
    # 3. 鑾峰彇姣忓彧涓偂鐨勬渶鏂板叕鍛婂拰璧勮
    stocks_with_info = []
    
    for i, stock in enumerate(stocks):
        ts_code = stock['ts_code']
        name = stock['name']
        
        print(f'[{i+1}/{len(stocks)}] 澶勭悊 {name} ({ts_code})...')
        
        # 鑾峰彇鍏憡
        announcements = get_announcements(pro, ts_code)
        
        # 鑾峰彇鏂伴椈
        news = get_news(pro, ts_code)
        
        stocks_with_info.append({
            'ts_code': ts_code,
            'name': name,
            'score': stock['score'],
            'response': stock.get('response', ''),
            'announcements': announcements,
            'news': news
        })
        
        # 闄愰
        import time
        time.sleep(0.1)
    
    # 4. 鐢熸垚鎶ュ憡
    print()
    print('=' * 70)
    print('鐢熸垚鎶ュ憡...')
    print('=' * 70)
    
    report = generate_report(stocks_with_info)
    
    # 淇濆瓨鎶ュ憡
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(OUTPUT_DIR, f'logged_stocks_analysis_{timestamp}.md')
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f'鎶ュ憡宸蹭繚瀛? {report_path}')
    print()
    print('=' * 70)
    print('瀹屾垚锛?)
    print('=' * 70)
    
    return report_path

if __name__ == '__main__':
    main()
