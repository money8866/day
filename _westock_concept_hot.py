# -*- coding: utf-8 -*-
"""
跨行业概念榜 · 腾讯自选股（westock CLI）口径

用法：
    python _westock_concept_hot.py [--date YYYYMMDD] [--limit N] [--no-zt] [--json]

作用：
    取 westock hot sector 榜，只保留 BK 类型的「题材概念」，
    剔除申万一级(BK-HY-1)/二级(BK-HY-2) 与「昨日连板/昨日涨停」等属性型板块，
    可选叠加东财涨停池计算每个概念的当日涨停家数。

为什么要单独做这个：
    物理AI、人形机器人、覆铜板这类题材是「跨行业风格概念」，
    在申万一级行业结构诊断里会被拆散稀释掉，行业层面完全看不见。
    这一节专门补这个盲区。

与 _finchx_hotlist.py（同花顺概念热榜）的关系：
    - 同花顺榜：有热度绝对值 + 涨停家数，但只有 TOP20、且概念收录偏保守（无物理AI）
    - 腾讯榜：  有 rankdelta 名次变动、50 条样本、涨跌幅与 westock 口径一致，但无热度值
    两者是互补，不互相替代。
"""
import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

WESTOCK = r"C:\Users\kongx\.local\bin\westock.exe"

# 属性型 / 技术型板块：它们是「结果的集合」而非「题材」，必须剔除
NOISE_KW = [
    '昨日连板', '昨日涨停', '昨日跌停', '昨日触板', '昨日首板', '近期新高', '近期新低',
    '昨日高振幅', '昨日地量', '龙头股', '强势股', '活跃A股',
    '融资融券', '转融券', '两网及退市', '预盈预增', '预亏预减', '业绩',
    '标准普尔', 'MSCI', '富时罗素', '标普', '沪股通', '深股通', '北交所',
    '科创板', '创业板综', '注册制次新股', '次新股', '新股与次新股',
    '破净股', 'ST股', '低价股', '高价股', '百元股', '送转', '高送转', '含可转债',
    '机构重仓', '基金重仓', 'QFII', '社保重仓', '养老金', '险资', '外资',
    '创业成份', '深圳国资', '上海国资', '央企', '中字头', '国企改革', '举牌',
    '股权转让', '壳资源', '参股新三板', '参股保险', '参股银行', '参股券商',
    '专精特新', '标准普尔', 'AH股', 'B股', 'H股', '独角兽', '增持', '回购',
    '大盘', '中盘', '小盘', '微盘股', '活跃股', '换手', '振幅',
]
NOISE_RE = re.compile('|'.join(NOISE_KW))


def run(cmd, timeout=45):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, shell=False)
    return p.stdout.decode('utf-8', 'ignore')


def parse_md_table(text):
    """解析 westock 的 markdown 表格，返回 dict 列表"""
    rows = []
    for line in text.splitlines():
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 2 or set(''.join(cells)) <= set('-: '):
            continue
        rows.append(cells)
    if not rows:
        return []
    header = rows[0]
    out = []
    for r in rows[1:]:
        if len(r) != len(header):
            continue
        out.append(dict(zip(header, r)))
    return out


def fetch_sector_board(limit=50):
    txt = run([WESTOCK, 'hot', 'sector', '--limit', str(limit)])
    rows = parse_md_table(txt)
    if not rows:
        raise RuntimeError('hot sector 返回为空，westock 可能未登录或上游故障')
    return rows


def fetch_zt_pool(datestr):
    """东财涨停池，返回 {6位纯数字代码: (名称, 连板数)}

    ⚠️ 关键坑：东财 push2ex 返回的 code 是「6 位纯数字」('600838')，
       而 westock 成分股是「带市场前缀」('sh600838')。
       直接做集合交集永远为空 → 涨停家数全算成 0 的假阴性。
       必须统一到 6 位纯数字再比。
    """
    import requests
    url = ('https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989'
           '&dpt=wz.ztzt&Pageindex=0&pagesize=300&sort=fbt:asc&date=' + datestr)
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        pool = r.json()['data']['pool']
    except Exception as e:
        print('  [warn] 涨停池拉取失败: %s' % e, file=sys.stderr)
        return None
    return {('%06d' % int(x['c'])): (x['n'], x['lbc']) for x in pool}


def fetch_members(symbol):
    txt = run([WESTOCK, 'sector', 'constituent', symbol, '--limit', '0'])
    codes = re.findall(r'^\|\s*((?:sh|sz|bj)\d{6})\s*\|', txt, re.M)
    return list(dict.fromkeys(codes))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYYMMDD，用于涨停池')
    ap.add_argument('--limit', type=int, default=10, help='输出条数，默认 10')
    ap.add_argument('--scan', type=int, default=50, help='扫描榜内前 N 条，默认 50')
    ap.add_argument('--no-zt', action='store_true', help='跳过涨停家数计算（更快）')
    ap.add_argument('--json', action='store_true', help='额外输出 JSON')
    a = ap.parse_args()

    if not a.date:
        import datetime
        a.date = datetime.date.today().strftime('%Y%m%d')

    board = fetch_sector_board(a.scan)

    # 只保留 BK 类型（题材概念），剔除申万一/二级与属性型板块
    concepts = []
    for r in board:
        st = r.get('stock_type', '')
        name = r.get('name', '')
        if st != 'BK':
            continue
        if NOISE_RE.search(name):
            continue
        try:
            rank = int(r.get('rank'))
            zdf = float(r.get('zdf'))
            rd = int(r.get('rankdelta', 0))
        except (TypeError, ValueError):
            continue
        concepts.append({
            'rank': rank, 'name': name, 'symbol': r.get('symbol', ''),
            'zdf': zdf, 'rankdelta': rd,
        })

    # 按榜内顺序取前 N（榜本身就是热度序）
    concepts.sort(key=lambda x: x['rank'])
    concepts = concepts[:a.limit]

    zt_map = None
    if not a.no_zt:
        zt_map = fetch_zt_pool(a.date)
        if zt_map is not None:
            with ThreadPoolExecutor(max_workers=6) as ex:
                mems = list(ex.map(lambda c: fetch_members(c['symbol']), concepts))
            for c, m in zip(concepts, mems):
                c['members'] = len(m)
                # 成分股 'sz002048' → '002048'，与涨停池 6 位码对齐
                hits = [k[2:] for k in m if k[2:] in zt_map]
                c['zt'] = len(hits)
                c['zt_names'] = [zt_map[k][0] for k in hits][:4]
                c['max_lbc'] = max([zt_map[k][1] for k in hits], default=0)
        else:
            for c in concepts:
                c.update(members=None, zt=None, zt_names=[], max_lbc=0)

    # ---------- 输出 Markdown ----------
    ts = board[0].get('date', '') if board else ''
    print('### 🧩 跨行业概念 TOP%d（腾讯自选股 · 取数 %s）' % (len(concepts), ts[11:16] if ts else '—'))
    print()
    if zt_map is not None:
        print('| # | 概念 | 涨跌幅 | 名次变动 | 涨停 | 成分股(强) |')
        print('|---|------|------|------|------|------|')
        for c in concepts:
            rd = c['rankdelta']
            rds = '—' if rd == 0 else ('↑%d' % rd if rd > 0 else '↓%d' % abs(rd))
            strong = ('　'.join(c['zt_names']) + ('…' if c['zt'] > 4 else '')) if c['zt'] else '—'
            print('| %d | %s | %+.2f%% | %s | %d | %s |' % (
                c['rank'], c['name'], c['zdf'], rds, c['zt'], strong))
    else:
        print('| # | 概念 | 涨跌幅 | 名次变动 |')
        print('|---|------|------|------|')
        for c in concepts:
            rd = c['rankdelta']
            rds = '—' if rd == 0 else ('↑%d' % rd if rd > 0 else '↓%d' % abs(rd))
            print('| %d | %s | %+.2f%% | %s |' % (c['rank'], c['name'], c['zdf'], rds))
    print()

    # ---------- 自动观察 ----------
    print('**自动观察**')
    ups = [c for c in concepts if c['rankdelta'] > 0]
    downs = [c for c in concepts if c['rankdelta'] < 0]
    if ups:
        print('· 名次上升：%s' % '、'.join('%s ↑%d' % (c['name'], c['rankdelta']) for c in ups))
    if downs:
        print('· 名次下降：%s' % '、'.join('%s ↓%d' % (c['name'], abs(c['rankdelta'])) for c in downs))
    if zt_map is not None:
        tot = sum(c['zt'] for c in concepts)
        red = [c for c in concepts if c['zdf'] > 0]
        print('· BK 概念 %d 个合计涨停 %d 家，其中 %d 个收红' % (len(concepts), tot, len(red)))
        hot = [c for c in concepts if c['rank'] <= 5 and c['zt'] == 0]
        if hot:
            print('· 热度在前 5 但零涨停：%s（有热度无封板）' % '、'.join(c['name'] for c in hot))
        hi = max(concepts, key=lambda c: c['max_lbc']) if concepts else None
        if hi and hi['max_lbc'] >= 2:
            print('· 概念内最高连板：%s %d 板（%s）' % (
                hi['name'], hi['max_lbc'], '、'.join(hi['zt_names'])))

    if a.json:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'concept_hot_westock_%s.json' % a.date)
        with open(out, 'w', encoding='utf-8') as f:
            json.dump({'date': a.date, 'snapshot': ts, 'rows': concepts},
                      f, ensure_ascii=False, indent=1)
        print('\n[json] %s' % out)


if __name__ == '__main__':
    main()
