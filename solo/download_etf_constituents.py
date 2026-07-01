# -*- coding: utf-8 -*-
"""
下载ETF主线策略中所有ETF的成份股对照关系表，存为CSV文件
数据源：Tushare API (etf_sh_cons / etf_sz_cons)
输出：report_daily/etf_constituents_YYYYMMDD.csv
"""
import os
import sys
import time
import datetime
from dotenv import load_dotenv
import pandas as pd
import tushare as ts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DATA_DIR = os.path.dirname(BASE_DIR)
REPORT_DIR = os.path.join(STOCK_DATA_DIR, "report_daily")
os.makedirs(REPORT_DIR, exist_ok=True)

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
    '黄金': '518880', '沪深300': '510300', '创业板': '159915',
    '上证50': '510050', '双创ETF': '588300', '科创ETF': '588050',
}


def get_etf_suffix(ts_code):
    if ts_code.startswith('1') or ts_code.startswith('15'):
        return ts_code + '.SZ'
    else:
        return ts_code + '.SH'


def get_etf_constituents(ts_code):
    full_code = get_etf_suffix(ts_code)
    prefix = ts_code[0]
    try:
        time.sleep(0.12)
        if prefix == '1':
            df = pro.etf_sz_cons(
                ts_code=full_code,
                fields=["trade_date", "ts_code", "con_code", "con_name", "qty", "cpr"]
            )
        else:
            df = pro.etf_sh_cons(
                ts_code=full_code,
                fields=["trade_date", "ts_code", "con_code", "con_name", "qty", "cpr"]
            )
        if df is None or df.empty:
            return []
        latest_date = df['trade_date'].max()
        df = df[df['trade_date'] == latest_date]
        return df.to_dict('records')
    except Exception as e:
        print(f"  [WARN] 获取{full_code}成份股失败: {e}")
        return []


def main():
    all_rows = []
    etf_count = len(ETF_POOL)
    success_count = 0

    print(f"\n开始下载 {etf_count} 只ETF的成份股数据...\n")

    for idx, (etf_name, etf_code) in enumerate(ETF_POOL.items(), 1):
        print(f"[{idx}/{etf_count}] 正在获取 {etf_name}({etf_code}) 的成份股...")
        constituents = get_etf_constituents(etf_code)
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
                'trade_date': c.get('trade_date', TRADE_DATE),
            })
        print(f"  共 {len(constituents)} 只成份股")

    if not all_rows:
        print("\n未获取到任何成份股数据！")
        return

    df = pd.DataFrame(all_rows)
    df = df.sort_values(['etf_name', 'cpr'], ascending=[True, False])

    output_file = os.path.join(REPORT_DIR, f"etf_constituents_{TRADE_DATE}.csv")
    df.to_csv(output_file, index=False, encoding='utf-8-sig')

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
