#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个股深度分析 - 基本面+技术面+AI估值空间"""

import os, sys, io, time, json
import tushare as ts
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

load_dotenv("config/.env")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

STOCKS = ['300975.SZ', '688167.SH', '688195.SH']
STOCK_NAMES = {'300975.SZ': '商络电子', '688167.SH': '炬光科技', '688195.SH': '腾景科技'}

def get_latest_trade_date():
    """获取最近交易日"""
    df = pro.trade_cal(exchange='SSE', is_open=1, limit=1, end_date=datetime.now().strftime('%Y%m%d'))
    return df.iloc[0]['cal_date']

TRADE_DATE = get_latest_trade_date()
print(f"交易日: {TRADE_DATE}\n")

all_data = {}

for ts_code in STOCKS:
    name = STOCK_NAMES[ts_code]
    print(f"{'='*60}")
    print(f"分析: {name} ({ts_code})")
    print(f"{'='*60}")
    
    data = {'ts_code': ts_code, 'name': name}
    
    # 1. 基本面：最新财务指标
    try:
        df_fin = pro.fina_indicator(ts_code=ts_code, fields='ts_code,ann_date,end_date,roe,roe_dt,np_yoy,profit_yoy,or_yoy,eps,pe,pb,debt_to_assets,grossprofit_margin,netprofit_margin')
        if not df_fin.empty:
            latest = df_fin.iloc[0]
            data['roe'] = latest.get('roe_dt', latest.get('roe'))
            data['np_yoy'] = latest.get('np_yoy')
            data['or_yoy'] = latest.get('or_yoy')
            data['eps'] = latest.get('eps')
            data['debt_ratio'] = latest.get('debt_to_assets')
            data['gross_margin'] = latest.get('grossprofit_margin')
            data['net_margin'] = latest.get('netprofit_margin')
            data['fin_end_date'] = latest.get('end_date')
            print(f"  ROE: {data['roe']}%")
            print(f"  净利润同比: {data['np_yoy']}%")
            print(f"  营收同比: {data['or_yoy']}%")
            print(f"  EPS: {data['eps']}")
            print(f"  资产负债率: {data['debt_ratio']}%")
            print(f"  毛利率: {data['gross_margin']}%")
            print(f"  净利率: {data['net_margin']}%")
            print(f"  财报期: {data['fin_end_date']}")
    except Exception as e:
        print(f"  财务指标获取失败: {e}")
    time.sleep(0.3)
    
    # 2. 估值指标
    try:
        df_val = pro.daily_basic(ts_code=ts_code, trade_date=TRADE_DATE, 
                                  fields='ts_code,pe,pb,ps,dv_ratio,total_mv,circ_mv,turnover_rate')
        if not df_val.empty:
            row = df_val.iloc[0]
            data['pe'] = row.get('pe')
            data['pb'] = row.get('pb')
            data['ps'] = row.get('ps')
            data['total_mv'] = row.get('total_mv', 0) / 10000  # 亿
            data['circ_mv'] = row.get('circ_mv', 0) / 10000
            data['turnover_rate'] = row.get('turnover_rate')
            print(f"\n  PE: {data['pe']}")
            print(f"  PB: {data['pb']}")
            print(f"  PS: {data['ps']}")
            print(f"  总市值: {data['total_mv']:.1f}亿")
            print(f"  流通市值: {data['circ_mv']:.1f}亿")
            print(f"  换手率: {data['turnover_rate']}%")
    except Exception as e:
        print(f"  估值指标获取失败: {e}")
    time.sleep(0.3)
    
    # 3. K线技术面（60日）
    try:
        end = TRADE_DATE
        start = (datetime.strptime(end, '%Y%m%d') - timedelta(days=120)).strftime('%Y%m%d')
        df_k = pro.daily(ts_code=ts_code, start_date=start, end_date=end,
                          fields='ts_code,trade_date,open,high,low,close,vol,amount,pct_chg')
        if not df_k.empty:
            df_k = df_k.sort_values('trade_date')
            close = df_k['close'].values
            
            # MA
            if len(close) >= 5:
                data['ma5'] = close[-5:].mean()
            if len(close) >= 10:
                data['ma10'] = close[-10:].mean()
            if len(close) >= 20:
                data['ma20'] = close[-20:].mean()
            if len(close) >= 60:
                data['ma60'] = close[-60:].mean()
            
            data['current_price'] = close[-1]
            data['high_60d'] = df_k['high'].max()
            data['low_60d'] = df_k['low'].min()
            
            # 距高点
            data['from_high'] = (close[-1] - data['high_60d']) / data['high_60d'] * 100
            # 距低点
            data['from_low'] = (close[-1] - data['low_60d']) / data['low_60d'] * 100
            
            # 近期涨跌
            if len(close) >= 5:
                data['pct_5d'] = (close[-1] - close[-5]) / close[-5] * 100
            if len(close) >= 20:
                data['pct_20d'] = (close[-1] - close[-20]) / close[-20] * 100
            
            # 60日涨幅
            if len(close) >= 60:
                data['pct_60d'] = (close[-1] - close[-60]) / close[-60] * 100
            
            # 波动率（20日）
            if len(close) >= 21:
                rets = np.diff(close[-21:]) / close[-21:-1]
                data['volatility_20d'] = np.std(rets) * np.sqrt(250) * 100
            
            print(f"\n  现价: {data['current_price']}")
            print(f"  MA5/10/20/60: {data.get('ma5',0):.2f}/{data.get('ma10',0):.2f}/{data.get('ma20',0):.2f}/{data.get('ma60',0):.2f}")
            print(f"  60日最高: {data['high_60d']}")
            print(f"  60日最低: {data['low_60d']}")
            print(f"  距高点: {data['from_high']:.1f}%")
            print(f"  距低点: {data['from_low']:.1f}%")
            print(f"  5日涨幅: {data.get('pct_5d',0):.1f}%")
            print(f"  20日涨幅: {data.get('pct_20d',0):.1f}%")
            print(f"  60日涨幅: {data.get('pct_60d',0):.1f}%")
            print(f"  20日年化波动率: {data.get('volatility_20d',0):.1f}%")
    except Exception as e:
        print(f"  K线获取失败: {e}")
    time.sleep(0.3)
    
    # 4. 同行业对比（申万行业PE/PB中位数）
    try:
        df_basic = pro.stock_basic(ts_code=ts_code, fields='ts_code,industry')
        if not df_basic.empty:
            industry = df_basic.iloc[0]['industry']
            data['industry'] = industry
            print(f"\n  所属行业: {industry}")
            
            # 获取同行业股票
            df_ind = pro.stock_basic(exchange='', fields='ts_code,industry')
            peers = df_ind[df_ind['industry'] == industry]['ts_code'].tolist()
            
            # 同行业PE中位数（抽样）
            peer_pes = []
            for p in peers[:30]:
                try:
                    df_p = pro.daily_basic(ts_code=p, trade_date=TRADE_DATE, fields='pe')
                    if not df_p.empty and pd.notna(df_p.iloc[0]['pe']) and df_p.iloc[0]['pe'] > 0:
                        peer_pes.append(df_p.iloc[0]['pe'])
                except:
                    pass
                time.sleep(0.05)
            
            if peer_pes:
                data['industry_pe_median'] = np.median(peer_pes)
                data['industry_pe_mean'] = np.mean(peer_pes)
                print(f"  同行PE中位数: {data['industry_pe_median']:.1f}")
                print(f"  同行PE均值: {data['industry_pe_mean']:.1f}")
    except Exception as e:
        print(f"  行业对比失败: {e}")
    
    all_data[ts_code] = data
    print()

# 5. AI估值分析
print(f"\n{'='*60}")
print("AI估值空间分析（DeepSeek）")
print(f"{'='*60}\n")

if DEEPSEEK_API_KEY:
    for ts_code, data in all_data.items():
        prompt = f"""你是一位资深A股分析师，请对以下股票进行估值空间分析。

股票: {data['name']} ({ts_code})
行业: {data.get('industry', '未知')}

基本面:
- ROE: {data.get('roe', 'N/A')}%
- 净利润同比: {data.get('np_yoy', 'N/A')}%
- 营收同比: {data.get('or_yoy', 'N/A')}%
- 毛利率: {data.get('gross_margin', 'N/A')}%
- 净利率: {data.get('net_margin', 'N/A')}%
- 资产负债率: {data.get('debt_ratio', 'N/A')}%
- EPS: {data.get('eps', 'N/A')}
- 财报期: {data.get('fin_end_date', 'N/A')}

估值:
- PE: {data.get('pe', 'N/A')}
- PB: {data.get('pb', 'N/A')}
- PS: {data.get('ps', 'N/A')}
- 同行PE中位数: {data.get('industry_pe_median', 'N/A')}
- 总市值: {data.get('total_mv', 'N/A')}亿

技术面:
- 现价: {data.get('current_price', 'N/A')}
- 60日涨幅: {data.get('pct_60d', 'N/A')}%
- 距60日高点: {data.get('from_high', 'N/A')}%
- 20日波动率: {data.get('volatility_20d', 'N/A')}%

请分析：
1. 推荐理由（3条核心逻辑）
2. 主要风险（2条）
3. 合理估值区间（PE法+PEG法）
4. 目标价和上涨空间（保守/中性/乐观三个情景）
5. 综合评级（1-5星）

请用JSON格式输出：
{{
  "reasons": ["理由1", "理由2", "理由3"],
  "risks": ["风险1", "风险2"],
  "valuation_range": {{"pe_low": 0, "pe_high": 0, "peg": 0}},
  "target_price": {{"conservative": 0, "neutral": 0, "optimistic": 0}},
  "upside": {{"conservative": "0%", "neutral": "0%", "optimistic": "0%"}},
  "rating": 0
}}"""

        try:
            import requests as req
            resp = req.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 2000
                },
                timeout=60
            )
            result = resp.json()
            content = result['choices'][0]['message']['content']
            
            # 提取JSON
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                ai_result = json.loads(json_match.group())
                data['ai_analysis'] = ai_result
                
                print(f"📊 {data['name']} ({ts_code})")
                print(f"{'─'*40}")
                print(f"⭐ 推荐理由:")
                for i, r in enumerate(ai_result.get('reasons', []), 1):
                    print(f"  {i}. {r}")
                print(f"\n⚠️ 风险:")
                for i, r in enumerate(ai_result.get('risks', []), 1):
                    print(f"  {i}. {r}")
                tp = ai_result.get('target_price', {})
                up = ai_result.get('upside', {})
                print(f"\n🎯 目标价 & 上涨空间:")
                print(f"  保守: {tp.get('conservative','N/A')} → {up.get('conservative','N/A')}")
                print(f"  中性: {tp.get('neutral','N/A')} → {up.get('neutral','N/A')}")
                print(f"  乐观: {tp.get('optimistic','N/A')} → {up.get('optimistic','N/A')}")
                print(f"\n⭐ 综合评级: {ai_result.get('rating', 'N/A')}/5")
                print()
        except Exception as e:
            print(f"  AI分析失败: {e}")
        
        time.sleep(1)
else:
    print("⚠️ 未配置DEEPSEEK_API_KEY，跳过AI分析")

print("\n✅ 分析完成")
