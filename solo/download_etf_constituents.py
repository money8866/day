# -*- coding: utf-8 -*-
"""
下载ETF主线策略中所有ETF的成份股对照关系表，存为CSV文件
数据源：Tushare API (etf_sh_cons / etf_sz_cons)
输出：report_daily/etf_constituents_YYYYMMDD.csv
"""
import os
import sys
import json
import time
import datetime
from dotenv import load_dotenv
import pandas as pd
import tushare as ts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DATA_DIR = os.path.dirname(BASE_DIR)
REPORT_DIR = os.path.join(STOCK_DATA_DIR, "report_daily")
os.makedirs(REPORT_DIR, exist_ok=True)
CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")

load_dotenv(os.path.join(STOCK_DATA_DIR, "config", ".env"))
TS_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TS_TOKEN)
pro = ts.pro_api()


def get_last_trade_date():
    now = datetime.datetime.now()
    if now.hour < 15:
        query_date = (now - datetime.timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')

    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


TRADE_DATE = get_last_trade_date()
print("当前交易日:", TRADE_DATE)

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
    '沪深300': '510300', '创业板': '159915',
    '上证50': '510050', '双创ETF': '588300', '科创ETF': '588050', '科创半导体': '588170',
}


def get_etf_suffix(ts_code):
    if ts_code.startswith('1') or ts_code.startswith('15'):
        return ts_code + '.SZ'
    else:
        return ts_code + '.SH'


def get_etf_constituents(ts_code, trade_date: str = None):
    full_code = get_etf_suffix(ts_code)
    prefix = ts_code[0]
    if trade_date is None:
        trade_date = TRADE_DATE
    try:
        if prefix == '1':
            time.sleep(0.12)
            probe = pro.etf_sz_cons(
                ts_code=full_code,
                fields=["trade_date"]
            )
            if probe is None or probe.empty:
                return [], None
            sz_latest = probe['trade_date'].max()
            time.sleep(0.12)
            df = pro.etf_sz_cons(
                ts_code=full_code,
                trade_date=sz_latest,
                fields=["trade_date", "ts_code", "con_code", "con_name", "qty", "cpr"]
            )
            cons_date = sz_latest
        else:
            time.sleep(0.12)
            df = pro.etf_sh_cons(
                ts_code=full_code,
                trade_date=trade_date,
                fields=["trade_date", "ts_code", "con_code", "con_name", "qty", "cpr"]
            )
            cons_date = trade_date
        if df is None or df.empty:
            return [], None
        cons_list = df.to_dict('records')

        weight_map = _get_fund_weights(full_code, trade_date)

        for c in cons_list:
            code = c.get('con_code', '')
            w = weight_map.get(code, {})
            c['weight'] = w.get('weight', 0)
            c['mkv'] = w.get('mkv', 0)
            if not c.get('con_name'):
                c['con_name'] = w.get('name', '')

        return cons_list, cons_date
    except Exception as e:
        print(f"  [WARN] 获取{full_code}成份股失败: {e}")
        return [], None


def _get_fund_weights(ts_code, trade_date):
    try:
        time.sleep(0.12)
        df = pro.fund_portfolio(ts_code=ts_code)
        if df is None or df.empty:
            return {}
        df = df[df['end_date'] <= trade_date]
        if df.empty:
            return {}
        latest_end = df['end_date'].max()
        df = df[df['end_date'] == latest_end]
        weight_map = {}
        for _, row in df.iterrows():
            code = row.get('symbol', row.get('con_code', ''))
            if not code:
                continue
            weight_map[code] = {
                'weight': float(row.get('stk_mkv_ratio', 0)) or 0,
                'mkv': float(row.get('mkv', 0)) or 0,
                'name': row.get('name', ''),
            }
        return weight_map
    except Exception as e:
        print(f"  [WARN] 获取{ts_code}基金权重失败: {e}")
        return {}


def main():
    all_rows = []
    etf_count = len(ETF_POOL)
    success_count = 0

    print(f"\n开始下载 {etf_count} 只ETF的成份股数据...\n")

    for idx, (etf_name, etf_code) in enumerate(ETF_POOL.items(), 1):
        print(f"[{idx}/{etf_count}] 正在获取 {etf_name}({etf_code}) 的成份股...")
        constituents, cons_date = get_etf_constituents(etf_code)
        if not constituents:
            print(f"  未获取到成份股数据")
            continue

        success_count += 1
        for c in constituents:
            all_rows.append({
                'etf_name': etf_name,
                'etf_code': get_etf_suffix(etf_code),
                'con_code': c.get('con_code', ''),
                'con_name': c.get('con_name', ''),
                'qty': c.get('qty', 0),
                'cpr': c.get('cpr', 0),
                'weight': c.get('weight', 0),
                'mkv': c.get('mkv', 0),
                'trade_date': cons_date or TRADE_DATE,
            })
        w_count = sum(1 for c in constituents if c.get('weight', 0) > 0)
        print(f"  共 {len(constituents)} 只成份股 (权重数据 {w_count} 只)")

    if not all_rows:
        print("\n未获取到任何成份股数据！")
        return

    df = pd.DataFrame(all_rows)
    df = df.sort_values(['etf_name', 'weight'], ascending=[True, False])

    output_file = os.path.join(REPORT_DIR, f"etf_constituents_{TRADE_DATE}.csv")
    df.to_csv(output_file, index=False, encoding='utf-8-sig')

    def _safe_float(v):
        try:
            if v is None or v == '' or v == '-' or pd.isna(v):
                return 0.0
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    cons_json = {}
    for etf_name, grp in df.groupby('etf_name'):
        etf_code = grp['etf_code'].iloc[0]
        trade_date = grp['trade_date'].iloc[0]
        constituents = []
        for _, row in grp.sort_values('weight', ascending=False).iterrows():
            constituents.append({
                'con_code': row['con_code'],
                'con_name': row['con_name'],
                'weight': _safe_float(row['weight']),
                'qty': _safe_float(row['qty']),
            })
        cons_json[etf_code] = {
            'trade_date': str(trade_date),
            'constituents': constituents,
        }
    json_path = os.path.join(CACHE_DIR, 'etf_constituents_all.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(cons_json, f, ensure_ascii=False, indent=2)
    print(f"  JSON映射: {json_path} ({len(cons_json)} 个ETF)")

    total_stocks = df['con_code'].nunique()
    print(f"\n{'='*60}")
    print(f"下载完成！")
    print(f"  ETF数量: {success_count}/{etf_count}")
    print(f"  成份股记录数: {len(df)}")
    print(f"  去重后股票数: {total_stocks}")
    print(f"  输出文件: {output_file}")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
