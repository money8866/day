# -*- coding: utf-8 -*-
"""V2.0 定向候选核查：已知AI产业链公司 mainbiz + 现有主题归属"""
import json, sys, os
LOG = open('_v2_scan2.log', 'w', encoding='utf-8')
sys.stdout = LOG
SOLO = r'd:\mystock\solo'
mainbiz = json.load(open(r'd:\mystock\cache_daily\stock_company_mainbiz.json', encoding='utf-8'))
mp = json.load(open(os.path.join(SOLO, 'report_daily', 'theme_stock_map_latest_v2.json'), encoding='utf-8'))
stocks = mp['stocks']

CAND = {
 'AI服务器/整机系': ['002261.SZ','000034.SZ','000628.SZ','600839.SH','000066.SZ','600100.SH','603296.SH','001339.SZ','000977.SZ','603019.SH','000938.SZ','601138.SH','301236.SZ','688787.SH','002195.SZ','300474.SZ'],
 'GPU/计算芯片(半导体)': ['688041.SH','688256.SH','688795.SH','688802.SH','688008.SH','300474.SZ'],
 '液冷/温控': ['002837.SZ','300499.SZ','300990.SZ','301018.SZ','603912.SH','920808.BJ','301202.SZ','300249.SZ','002335.SZ','300602.SZ','300684.SZ','301489.SZ','301626.SZ','300647.SZ','603124.SH','002937.SZ','002272.SZ','300492.SZ','300811.SZ'],
 '算力运营/IDC/云': ['300442.SZ','300383.SZ','300738.SZ','300846.SZ','600797.SH','603220.SH','603881.SH','688158.SH','920493.BJ','002229.SZ','603985.SH','600186.SH','603629.SH','300857.SZ','002929.SZ','002757.SZ','301396.SZ','301085.SZ','300895.SZ','603887.SH','600845.SH','600602.SH','300017.SZ','600589.SH','002575.SZ','000815.SZ','688227.SH','603003.SH','002335.SZ'],
 'CPO/光通信': ['300308.SZ','300502.SZ','002281.SZ','300394.SZ','301205.SZ','920045.BJ','300570.SZ','688205.SH','688313.SH','300548.SZ','300620.SZ','688498.SH','000988.SZ','688195.SH','600487.SH','600522.SH','601869.SH','000063.SZ','600498.SH','688498.SH','688807.SH','300502.SZ','688400.SH','002222.SZ'],
 'AI应用': ['002230.SZ','002362.SZ','300033.SZ','300229.SZ','300418.SZ','300559.SZ','300624.SZ','688088.SH','688111.SH','688327.SH','688343.SH','688787.SH','601360.SH','300766.SZ','688615.SH','688207.SH','603859.SH','300451.SZ','002195.SZ'],
}

seen = set()
for grp, codes in CAND.items():
    print('\n===== %s =====' % grp)
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        mb = (mainbiz.get(c) or '(无主营)').replace('\n', ' ')[:130]
        th = stocks.get(c, {}).get('themes', [])
        nm = stocks.get(c, {}).get('name', '')
        ind = stocks.get(c, {}).get('industry', '')
        print('%s %-8s [%s] | %s | %s' % (c, nm, ind, ','.join(th), mb))
LOG.close()
