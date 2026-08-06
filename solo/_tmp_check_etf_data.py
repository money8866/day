# -*- coding: utf-8 -*-
import sys, os, glob
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import pandas as pd

ETF_POOL = {
    '半导体': '512480', '芯片': '159995', '半导体设备': '159516',
    '人工智能': '159819', '软件': '515230', '通信': '515880',
    '消费电子': '159732', '金融科技': '159851', '游戏': '159869',
    '新能源': '516160', '光伏': '515790', '储能': '159566',
    '电池': '159755', '新能源车': '515030', '创新药': '159992',
    '医疗器械': '159883', '医药': '512010', '军工': '512660',
    '航空航天': '159227', '机器人': '562500', '有色金属': '516650',
    '化工': '159870', '煤炭': '515220', '钢铁': '515210',
    '电力': '159611', '电网设备': '561380', '消费': '159928',
    '食品饮料': '159736', '酒': '512690', '家电': '159996',
    '证券': '512880', '银行': '512800', '红利': '515180',
    '工业母机': '159667', '科创半导体': '588170',
}
CACHE = r'd:\mystock\cache_daily\etf_fund'

rows = []
for name, code in ETF_POOL.items():
    ts = f'{code}.SH' if code.startswith(('5', '6')) else f'{code}.SZ'
    for d in ['20260803', '20260731', '20260730', '20260729', '20260728']:
        p = os.path.join(CACHE, f'{ts}_{d}.csv')
        if os.path.exists(p):
            df = pd.read_csv(p)
            if not df.empty:
                df['trade_date'] = df['trade_date'].astype(str)
                latest = df['trade_date'].max()
                sub = df[df['trade_date'] == latest].iloc[-1]
                close = float(sub['close'])
                prev = None
                prev_rows = df[df['trade_date'] < latest]
                if not prev_rows.empty:
                    prev = float(prev_rows['close'].iloc[-1])
                day_chg = (close - prev) / prev * 100 if prev else None
                rows.append({'name': name, 'code': code, 'cache_date': d,
                             'latest': latest, 'close': close, 'day_chg': round(day_chg, 2) if day_chg is not None else None})
                break

df = pd.DataFrame(rows).sort_values('day_chg')
print(f"{'名称':<8}{'代码':<8}{'缓存日期':<10}{'最新日期':<10}{'收盘':>8}{'当日涨跌':>8}")
print('-' * 56)
for _, r in df.iterrows():
    print(f"{r['name']:<8}{r['code']:<8}{r['cache_date']:<10}{r['latest']:<10}{r['close']:>8.3f}{r['day_chg']:>8.2f}")
print()
print('缓存日期统计:', df['cache_date'].value_counts().to_dict())
