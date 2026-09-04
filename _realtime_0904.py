# -*- coding: utf-8 -*-
import urllib.request, json

hdrs = {'Referer':'http://finance.sina.com.cn','User-Agent':'Mozilla/5.0'}

def sina(url):
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode('gbk')

etfs = {
    '512480':('半导体ETF',1),'159813':('芯片ETF',0),'588000':('科创50ETF',1),
    '159915':('创业板ETF',0),'159611':('电力ETF',0),'518880':('黄金ETF',1),
    '159667':('机器人ETF',0),'513500':('纳指ETF',1),'513100':('标普ETF',1),
    '159865':('医疗ETF',0),'512760':('芯片ETF国联安',1),
}
for code,(name,sc) in etfs.items():
    sh = f'sh{code}'; sz = f'sz{code}'
    for sym in [sh,sz]:
        d = sina(f'http://hq.sinajs.cn/list={sym}')
        if '"' in d:
            parts = d.split('"')[1].split(',')
            if len(parts)>3:
                try:
                    price = float(parts[3])
                    prev = float(parts[2])
                    chg = (price-prev)/prev*100
                    print(f'{name}: {price:.3f}  {chg:+.2f}%')
                except: pass
            break
