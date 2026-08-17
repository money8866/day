# -*- coding: utf-8 -*-
"""EGPT 绿灯B 触发自动化 v1.0 —— 建议1落地
对每笔绿灯B 信号，用信号日报告里的 VWAP/止损价 + 后续行情判定触发状态：
  - 回踩触发: 后续某日 low<=VWAP*1.01 且 close>=VWAP(收回上方) 且量比<=1.5(缩量企稳)
  - 突破触发: 后续某日 close>=VWAP 且量比>=1.2(放量站上)
  - 止损失效: 触发前某日 low<=止损价(报告止损)
  - 未触发:   观察期结束仍未满足任一
分桶统计: 已触发(回踩/突破) vs 未触发 vs 止损失效 的跟踪绩效
"""
import os, sys, time, pandas as pd, tushare as ts
from green_b_tracker import extract_green_b, classify_trigger

_env_path = r"D:\mystock\config\.env"
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()
ts.set_token(os.environ.get('TUSHARE_TOKEN', ''))
pro = ts.pro_api()

REPORT_DIR = r"D:\mystock\solo\report_daily"
DATES = ['20260806', '20260807', '20260810', '20260811', '20260812', '20260813', '20260814']
END_DATE = '20260818'   # 观察截止（8/17 收盘数据）

# 收集信号
signals = []
for d in DATES:
    path = os.path.join(REPORT_DIR, f'enhanced_timing_bull_all_{d}.csv')
    if not os.path.exists(path):
        continue
    df = pd.read_csv(path, encoding='utf-8-sig')
    gb = extract_green_b(df)
    for _, r in gb.iterrows():
        signals.append({
            '信号日': d, '名称': r['名称'], '代码': r['代码'],
            '评级': r['修正后胜率分级'], '回踩买点分': r['回踩买点分'],
            '主题': r.get('主题', ''), 'VWAP': float(r['VWAP']),
            '止损价': float(r.get('ATR动态止损价', r.get('止损价', float('nan')))) if 'ATR动态止损价' in df.columns or '止损价' in df.columns else float('nan'),
        })

# 名称->代码
basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
name2code = dict(zip(basic['name'], basic['ts_code']))

rows = []
for s in signals:
    code = s['代码']
    if not isinstance(code, str) or '.' not in code:
        code = name2code.get(s['名称'], None)
    time.sleep(0.15)
    try:
        k = pro.daily(ts_code=code, start_date='20260701', end_date=END_DATE).sort_values('trade_date')
    except Exception as e:
        print(f"{s['信号日']} {s['名称']}: 拉取失败 {e}")
        continue
    if k.empty:
        continue
    status, trig_date, trig_type, trig_close, ret, peak = classify_trigger(k, s)
    # 未触发/止损: 收益用信号日收盘到最新收盘衡量（未触发纪律=不买，理论收益0；此处仅展示）
    rows.append({
        '信号日': s['信号日'], '名称': s['名称'], '评级': s['评级'],
        '主题': s['主题'], 'VWAP': round(s['VWAP'], 2), '止损价': round(s['止损价'], 2) if pd.notna(s['止损价']) else '',
        '触发状态': status, '触发日': trig_date or '', '触发类型': trig_type or '',
        '触发收盘': round(trig_close, 2) if trig_close else '',
        '触发后收益%': ret if ret is not None else '', '触发后峰值%': peak if peak is not None else '',
    })

out = pd.DataFrame(rows)
out.to_csv(os.path.join(REPORT_DIR, 'egpt_green_b_trigger_review.csv'), index=False, encoding='utf-8-sig')
pd.set_option('display.width', 200)
print(out.to_string(index=False))

print('\n===== 分桶统计 =====')
triggered = out[out['触发状态'].isin(['回踩触发', '突破触发'])]
untouched = out[out['触发状态'] == '未触发']
stopped = out[out['触发状态'] == '止损失效']
print(f"已触发 {len(triggered)} 笔: 胜率 {round((pd.to_numeric(triggered['触发后收益%'],errors='coerce').fillna(-999)>0).mean()*100,1)}% "
      f"均值 {round(pd.to_numeric(triggered['触发后收益%'],errors='coerce').mean(),1)}% 峰值均值 {round(pd.to_numeric(triggered['触发后峰值%'],errors='coerce').mean(),1)}%")
print(f"  其中 突破 {len(triggered[triggered['触发类型']=='突破'])} 笔 / 回踩 {len(triggered[triggered['触发类型']=='回踩'])} 笔")
print(f"未触发 {len(untouched)} 笔 (纪律=不买, 理论收益0)")
print(f"止损失效 {len(stopped)} 笔 (若信号日误买则亏损)")
print('\n已保存: report_daily/egpt_green_b_trigger_review.csv')
