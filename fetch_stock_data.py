# -*- coding: utf-8 -*-
import tushare as ts
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

stocks = [
    ('688551.SH', 'KEWEIER'),
    ('301183.SZ', 'DONGTIANWEI'),
    ('002222.SZ', 'FUJING'),
    ('002428.SZ', 'YUNNANGE'),
    ('002975.SZ', 'BOJIE'),
]

for ts_code, name in stocks:
    print(f'\n========== {name} ({ts_code}) ==========')

    df_quote = pro.daily_basic(ts_code=ts_code, trade_date='20260522')
    if len(df_quote) > 0:
        row = df_quote.iloc[0]
        close = row.get('close', 0) or 0
        pe = row.get('pe_ttm', 0) or 0
        pb = row.get('pb', 0) or 0
        mktcap = row.get('total_mv', 0) or 0
        circ_mv = row.get('circ_mv', 0) or 0
        ps = row.get('ps_ttm', 0) or 0
        print(f'CLOSE:{close:.2f}')
        print(f'PE:{pe:.2f}')
        print(f'PB:{pb:.2f}')
        print(f'PS:{ps:.2f}')
        print(f'MKTCAP:{mktcap/10000:.2f}')
        print(f'CIRC_MV:{circ_mv/10000:.2f}')

    df_ind = pro.fina_indicator(ts_code=ts_code, start_date='20250101')
    if len(df_ind) > 0:
        r = df_ind.iloc[0]
        print(f'EPS:{r["eps"]:.4f}')
        print(f'ROE:{r["roe"]:.2f}')
        print(f'GROSS_MARGIN:{r["grossprofit_margin"]:.2f}')
        print(f'NET_MARGIN:{r["netprofit_margin"]:.2f}')
        print(f'DEBT_RATIO:{r["debt_to_assets"]:.2f}')
        print(f'EPS_YOY:{r["basic_eps_yoy"]:.2f}')
        print(f'NP_YOY:{r["netprofit_yoy"]:.2f}')

    # also get income statement
    df_inc = pro.income(ts_code=ts_code, start_date='20250101', period_type='Q')
    if len(df_inc) > 0:
        for i, row in df_inc.head(2).iterrows():
            print(f'INC_END:{row["end_date"]} REV:{row["total_revenue"]} NP:{row["net_profit"]}')

    # get cash flow
    df_cf = pro.cashflow(ts_code=ts_code, start_date='20250101', period_type='Q')
    if len(df_cf) > 0:
        r = df_cf.iloc[0]
        print(f'CFO:{r["free_cashflow"]}')