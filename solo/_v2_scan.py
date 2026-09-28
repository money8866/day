# -*- coding: utf-8 -*-
"""V2.0 召回扫描：五主题现有成员主营 + 全市场AI产业链候选 + 旧81只主营"""
import json, sys, os, io

LOG = open('_v2_scan.log', 'w', encoding='utf-8')
sys.stdout = LOG

SOLO = r'd:\mystock\solo'
MAINBIZ = r'd:\mystock\cache_daily\stock_company_mainbiz.json'
MAPFILE = os.path.join(SOLO, 'report_daily', 'theme_stock_map_latest_v2.json')

mainbiz = json.load(open(MAINBIZ, encoding='utf-8'))
mp = json.load(open(MAPFILE, encoding='utf-8'))
themes = mp['themes']
stocks = mp['stocks']
print('== mapping trade_date=%s n_themes=%s n_stocks=%s' % (mp.get('trade_date'), mp.get('n_themes'), mp.get('n_stocks')))

TARGETS = ['CPO', '液冷', 'AI服务器', '算力运营', 'AI应用']
# 找五主题在 mapping 中的真实键名
print('\n== 主题键名匹配 ==')
allk = list(themes.keys())
for t in TARGETS:
    hits = [k for k in allk if t in k or (t == '液冷' and '液冷' in k) or (t == 'AI服务器' and '服务器' in k) or (t == '算力运营' and '算力' in k)]
    print(t, '->', hits)

print('\n== 五个主题当前成分（含主营摘要）==')
for t in TARGETS:
    hits = [k for k in allk if k == t or t in k]
    for k in hits:
        lst = themes[k]
        print('\n--- %s (n=%d) ---' % (k, len(lst)))
        for r in lst:
            code = r['code']
            mb = (mainbiz.get(code) or '')[:70].replace('\n', ' ')
            print('%s %-8s via=%-16s irs=%-6s | %s' % (code, r.get('name', ''), r.get('via', ''), r.get('irs_score', ''), mb))

OLD81 = """300308.SZ 中际旭创
300502.SZ 新易盛
002281.SZ 光迅科技
300394.SZ 天孚通信
301202.SZ 朗威股份
000977.SZ 浪潮信息
300476.SZ 胜宏科技
603019.SH 中科曙光
688041.SH 海光信息
688256.SH 寒武纪
301205.SZ 联特科技
600498.SH 烽火通信
920045.BJ 蘅东光
300017.SZ 网宿科技
301666.SZ 大普微
600589.SH 大位科技
688795.SH 摩尔线程
688802.SH 沐曦股份
920808.BJ 曙光数创
002815.SZ 崇达技术
300442.SZ 润泽科技
300570.SZ 太辰光
300739.SZ 明阳电路
301366.SZ 一博科技
600487.SH 亨通光电
600522.SH 中天科技
688205.SH 德科立
688313.SH 仕佳光子
300548.SZ 长芯博创
000815.SZ 美利云
300383.SZ 光环新网
300738.SZ 奥飞数据
300895.SZ 铜牛信息
603881.SH 数据港
000938.SZ 紫光股份
002212.SZ 天融信
002436.SZ 兴森科技
300602.SZ 飞荣达
300609.SZ 汇纳科技
300684.SZ 中石科技
301248.SZ 杰创智能
301489.SZ 思泉新材
600100.SH 同方股份
600797.SH 浙大网新
603220.SH 中贝通信
603803.SH 瑞斯康达
688227.SH 品高股份
601869.SH 长飞光纤
688143.SH 长盈通
688635.SH 长进光子
000063.SZ 中兴通讯
000070.SZ 特发信息
300098.SZ 高新兴
300698.SZ 万马科技
301176.SZ 逸豪新材
301282.SZ 金禄电子
603920.SH 世运电路
002148.SZ 北纬科技
301314.SZ 科瑞思
000725.SZ 京东方A
002938.SZ 鹏鼎控股
300053.SZ 航宇微
300814.SZ 中富电路
300964.SZ 本川智能
301041.SZ 金百泽
301132.SZ 满坤科技
301251.SZ 威尔高
603228.SH 景旺电子
603936.SH 博敏电子
920060.BJ 万源通
920821.BJ 则成电子
002575.SZ 群兴玩具
603328.SH 依顿电子
000586.SZ 汇源通信
002491.SZ 通鼎互联
300292.SZ 吴通控股
300578.SZ 会畅科技
301419.SZ 阿莱德
300736.SZ 百邦科技
920036.BJ 觅睿科技
920790.BJ 联迪信息"""

print('\n\n== 旧81只 主营文本 ==')
oldmap = {}
for ln in OLD81.strip().split('\n'):
    p = ln.split()
    if len(p) >= 2:
        oldmap[p[0]] = p[1]
for code, nm in oldmap.items():
    mb = (mainbiz.get(code) or '')[:110].replace('\n', ' ')
    inthemes = stocks.get(code, {}).get('themes', [])
    print('%s %-7s | %-60s | %s' % (code, nm, mb, ','.join(inthemes)))

# 全市场关键词候选
KW = ['服务器', '光模块', '光器件', '光芯片', '液冷', '冷板', '浸没', 'CDU', '算力', '智算', '人工智能', 'AI',
      '数据中心', 'IDC', '机柜', '散热', '温控', 'GPU', '超算', '云计算', '大模型', '智能体', 'AIGC', '光通信', '光互联']
print('\n\n== 全市场关键词候选（不在五主题现有成分内）==')
existing = set()
for t in TARGETS:
    for k in allk:
        if k == t or t in k:
            for r in themes[k]:
                existing.add(r['code'])
n = 0
for code, mb in mainbiz.items():
    if code in existing:
        continue
    hit = [k for k in KW if k in mb]
    if hit:
        n += 1
        print('%s %-8s | %-4s | %s' % (code, stocks.get(code, {}).get('name', ''), ','.join(hit[:4]), mb[:100].replace('\n', ' ')))
print('候选总数=%d' % n)

LOG.close()
