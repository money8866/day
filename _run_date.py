# -*- coding: utf-8 -*-
import sys; sys.path.insert(0, r'C:\Users\kongx\mystock')
from zhongjun_v3 import *
from datetime import datetime, timedelta
import tushare as ts
env = Path(r'C:\Users\kongx\mystock\.env').read_text()
for line in env.splitlines():
    if line.startswith('TUSHARE_TOKEN='): ts.set_token(line.split('=',1)[1].strip())
pro = ts.pro_api()

cal = pro.trade_cal(exchange='SSE', is_open=1,
    start_date=(datetime.now()-timedelta(days=15)).strftime('%Y%m%d'),
    end_date=datetime.now().strftime('%Y%m%d'))
dates = sorted(cal['cal_date'].tolist())
trade_date = dates[-2]
print(f"交易日期: {trade_date}")

concept_map, theme_map, stock_concept_map, sw_df = load_maps()

print("\n=== Step1: 主线板块 ===")
hot = get_hot_sectors_from_db(top_n=10)
source = "block.py数据库"
if not hot:
    hot = get_hot_industries_fallback(trade_date, concept_map, theme_map, stock_concept_map, sw_df, top_n=8)
    source = "申万行业动量(备用)"
print(f"数据源: {source}")
for i, h in enumerate(hot[:5]):
    print(f"  #{i+1} {h['name']} | 强度={h['score']:.1f} | {len(h['stocks'])}只")

print("\n=== Step2: 中军筛选 ===")
zhongjun = screen_zhongjun(trade_date, hot)
print(f"技术合格: {len(zhongjun)}只")

print("\n=== Step3: AI评分 + 二次入选 ===")
repeat_counter, first_date = get_all_time_repeats()

all_picks = []
for _, r in zhongjun.iterrows():
    tc = r['ts_code']
    fin = get_fin(tc)
    fin_score, detail = ai_financial_score(fin, r['pe'], r['sector_count'], r['tech_score'])
    repeat = repeat_counter.get(tc, 0) + 1
    verdict = ai_generate_verdict(r['name'], tc, fin_score, detail, fin, r['pe'],
                                  r['sector_count'], repeat, r['tech_score'], r['close'], r['mv_yi'])
    all_picks.append({**r.to_dict(), 'fin_score': fin_score, 'detail': detail,
                        'repeat': repeat, 'verdict': verdict})

picks_df = pd.DataFrame(all_picks)
today_picks = [{'ts_code':r['ts_code'],'name':r['name']} for _,r in picks_df.iterrows()]
repeat_counter_new, first_date_new = record_selection(trade_date, today_picks)

grade_a = picks_df[(picks_df['repeat']>=MIN_REPEAT)&(picks_df['fin_score']>=FIN_SCORE_MIN)]
grade_a = grade_a.sort_values(['fin_score','repeat','sector_count'],ascending=False)
grade_b = picks_df[(picks_df['repeat']<MIN_REPEAT)&(picks_df['fin_score']>=FIN_SCORE_MIN)]
grade_b = grade_b.sort_values('fin_score', ascending=False)
grade_c = picks_df[picks_df['fin_score']<FIN_SCORE_MIN]

print(f"\n📊 汇总: 技术合格{len(picks_df)}只 | 🟢A级{len(grade_a)}只 | 🟡B级{len(grade_b)}只 | 🔴C级{len(grade_c)}只")

if len(grade_a)>0:
    print(f"\n🟢 A级({len(grade_a)}只):")
    for _, r in grade_a.iterrows():
        v = r['verdict']
        print(f"  {v['rating']} {r['name']}({r['ts_code']}) 现价{r['close']} PE={r['pe']:.0f} 基本面={r['fin_score']}分 第{r['repeat']}次入选")
        print(f"    评分: {r['detail']}")
        if v['highlights']:
            print("    ✅ " + " | ".join(v['highlights']))
        if v['risks']:
            print("    ⚠️ " + " | ".join(v['risks']))
        print(f"    💰 仓位:{v['position']} 止损:{v['stop_loss']}")

if len(grade_b)>0:
    print(f"\n🟡 B级({len(grade_b)}只):")
    for _, r in grade_b.iterrows():
        v = r['verdict']
        print(f"  {r['name']}({r['ts_code']}) 基本面={r['fin_score']} PE={r['pe']:.0f} 技术={r['tech_score']} {v['rating']}")

if len(grade_c)>0:
    names = [f"{r['name']}({r['fin_score']}分)" for _,r in grade_c.iterrows()][:10]
    print(f"\n🔴 C级: {', '.join(names)}")
