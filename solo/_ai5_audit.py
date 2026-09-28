# -*- coding: utf-8 -*-
"""五主题重构审计：原 AI算力 81 只迁移表 + 新主题成分 + 污染检查"""
import json, io, sys, os

LOG = open('_ai5_audit.log', 'w', encoding='utf-8')
sys.stdout = LOG

NEW5 = ['CPO', '液冷', 'AI服务器', '算力运营', 'AI应用']
# 主题名 → spec 全名（用户裁定保留短名）
SPEC_NAME = {
    'CPO': 'CPO/光通信',
    '液冷': '液冷/温控',
    'AI服务器': 'AI服务器/算力设备',
    '算力运营': '算力运营',
    'AI应用': 'AI应用',
}

# 原 AI算力 81 只（来源 _ai_split.log L73-153）
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
old = {}
for ln in OLD81.strip().split('\n'):
    c, n = ln.split()
    old[c] = n
assert len(old) == 81, len(old)

m = json.load(open('report_daily/theme_stock_map_latest_v2.json', encoding='utf-8'))
themes = m['themes']
stocks = m['stocks']

print('=' * 70)
print('一、新五大主题成分数量')
print('=' * 70)
for t in NEW5:
    print(f'{SPEC_NAME[t]:<16}({t}) : {len(themes[t])}')

print()
print('=' * 70)
print('二、新五大主题成分明细')
print('=' * 70)
new5_codes = {}
for t in NEW5:
    codes = [x['code'] for x in themes[t]]
    new5_codes[t] = codes
    print(f'\n【{SPEC_NAME[t]}】 n={len(codes)}')
    for x in themes[t]:
        tag = '★新增' if x['code'] not in old else ' 原81'
        print(f"  {x['code']}  {x['name']:<8} via={x.get('via'):<18} {tag}")

print()
print('=' * 70)
print('三、原 AI算力 81 只迁移表')
print('=' * 70)
print(f"{'代码':<11}{'名称':<9}{'原主题':<8}{'新主题(5主题内)':<16}{'其他主题':<24}{'动作'}")
mig = {'RETAIN': 0, 'RECLASSIFY': 0, 'REMOVE': 0}
rows = []
for c, n in old.items():
    st = stocks.get(c)
    th = st['themes'] if st else []
    hit = [t for t in NEW5 if t in th]
    other = [t for t in th if t not in NEW5]
    if hit:
        act = 'RETAIN'
        newt = '+'.join(hit)
    elif other:
        act = 'RECLASSIFY'
        newt = ''
    else:
        act = 'REMOVE'
        newt = ''
    mig[act] += 1
    rows.append((c, n, 'AI算力', newt, ','.join(other), act))
    print(f"{c:<11}{n:<9}{'AI算力':<8}{newt:<16}{','.join(other):<24}{act}")
print()
print('动作统计:', mig)

print()
print('=' * 70)
print('四、池外新增成员（不在原 81 之内）')
print('=' * 70)
for t in NEW5:
    add = [x for x in themes[t] if x['code'] not in old]
    print(f'\n【{SPEC_NAME[t]}】 新增 {len(add)} 只')
    for x in add:
        print(f"  {x['code']}  {x['name']:<8} via={x.get('via')} industry={x.get('industry')}")

print()
print('=' * 70)
print('五、污染检查')
print('=' * 70)
# 检查1：同一股票是否被复制到多个一级主题
multi = {t: [] for t in NEW5}
for t in NEW5:
    for x in themes[t]:
        c = x['code']
        cnt = sum(1 for t2 in NEW5 if c in new5_codes[t2])
        if cnt > 1:
            multi[t].append((c, x['name'], cnt))
print('检查1 同一股票跨多个新主题:')
any_multi = False
for t in NEW5:
    if multi[t]:
        any_multi = True
        print('  ', t, multi[t])
if not any_multi:
    print('   PASS — 无股票同时出现在多个新主题')

# 检查2：算力运营是否混入设备厂商
EQUIP = {'科华数据', '曙光数创', '朗威股份', '亚康股份', '恒润股份', '广合科技', '群兴玩具',
         '工业富联', '浪潮信息', '中科曙光', '紫光股份', '英维克', '高澜股份', '同飞股份',
         '申菱环境', '佳力图', '中兴通讯', '烽火通信'}
bad2 = [x['name'] for x in themes['算力运营'] if x['name'] in EQUIP]
print('检查2 算力运营混入设备/零部件厂商:', bad2 or 'PASS — 无')

# 检查3：CPO 是否混入普通通信企业
bad3 = []
for x in themes['CPO']:
    cps = x.get('concepts', [])
    pass
print('检查3 CPO 成员:', [x['name'] for x in themes['CPO']], '(人工核验是否均为光模块/光器件/光芯片)')

# 检查5：液冷是否混入传统空调/制冷
print('检查5 液冷成员:', [x['name'] for x in themes['液冷']])
# 检查6：AI服务器是否混入普通电子零部件
print('检查6 AI服务器成员:', [x['name'] for x in themes['AI服务器']])
print('检查4 AI应用成员:', [x['name'] for x in themes['AI应用']])

print()
print('=' * 70)
print('六、新主题成员在其它主题的 SECONDARY 落点')
print('=' * 70)
for t in NEW5:
    print(f'\n【{SPEC_NAME[t]}】')
    for x in themes[t]:
        st = stocks.get(x['code'])
        other = [tt for tt in (st['themes'] if st else []) if tt not in NEW5]
        if other:
            print(f"  {x['code']} {x['name']:<8} 亦属: {','.join(other)}")

LOG.close()
print('DONE')
