# -*- coding: utf-8 -*-
"""VSW 策略跟踪记录分类收益率分析
================================================
样本: stock_pick WHERE strategy_id='vsw' JOIN pick_tracking
口径: 收益基准=选股日收盘价; 第N日=选股日之后第N个交易日收盘 (同 stock_pick_db 全局口径)
分类维度: signal 主类/修饰、action、Rating、EntryTimingGrade、T1Risk、
          量能爆发评分、FinalEntryScore、距MA20、5日涨幅、主题、基本面Gate、ChipSuggestion、逐日
"""
import sys, sqlite3, json
from collections import defaultdict, Counter

sys.stdout.reconfigure(encoding='utf-8')

DB = r'd:\mystock\solo\picks_db\stock_picks.db'


def _f(v, d=None):
    try:
        if v is None or v == '':
            return d
        return float(v)
    except (TypeError, ValueError):
        return d


def load():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute("""
        SELECT p.pick_date, p.ts_code, p.stock_name, p.close, p.pct_chg, p.signal, p.action,
               p.score, p.stop_price, p.target_price, p.reason, p.indicators,
               t.ret_1d, t.ret_3d, t.ret_5d, t.ret_10d, t.ret_20d,
               t.max_gain, t.max_drawdown, t.status
        FROM stock_pick p LEFT JOIN pick_tracking t
          ON t.pick_date=p.pick_date AND t.strategy_id=p.strategy_id AND t.ts_code=p.ts_code
        WHERE p.strategy_id='vsw'
        ORDER BY p.pick_date, p.rank_no""").fetchall()
    db.close()
    recs = []
    for r in rows:
        ind = {}
        try:
            ind = json.loads(r['indicators'] or '{}')
        except Exception:
            pass
        sig = r['signal'] or ''
        recs.append(dict(
            date=r['pick_date'], code=r['ts_code'], name=r['stock_name'],
            close=r['close'], pct=r['pct_chg'], sig=sig,
            main='扩大' if '扩大' in sig else ('优选' if '优选' in sig else '其他'),
            tags=''.join([t for t in ('主题效应', 'F2优先', '主线共振', 'L3共振') if t in sig]),
            n_tag=sum(1 for t in ('主题效应', 'F2优先', '主线共振', 'L3共振') if t in sig),
            action=r['action'] or '', score=_f(r['score']),
            reason=r['reason'] or '',
            rating=ind.get('Rating', '') or '', etg=ind.get('EntryTimingGrade', '') or '',
            ets=_f(ind.get('EntryTimingScore')), risk=ind.get('T1Risk', '') or '',
            volscore=_f(ind.get('量能爆发评分')), fes=_f(ind.get('FinalEntryScore')),
            dma20=_f(ind.get('距MA20')), chg5=_f(ind.get('5日涨幅')),
            theme=(ind.get('所属主题') or '').strip(),
            stage=(ind.get('非一日游阶段') or '').strip(),
            chip=(ind.get('ChipSuggestion') or '').strip(),
            fgate=('有' if ind.get('基本面Gate') not in (None, '', '否', 'False') else '无'),
            r1=_f(r['ret_1d']), r3=_f(r['ret_3d']), r5=_f(r['ret_5d']),
            r10=_f(r['ret_10d']), r20=_f(r['ret_20d']),
            mg=_f(r['max_gain']), mdd=_f(r['max_drawdown']), status=r['status'] or ''))
    return recs


def agg(sub, label, w=0):
    n = len(sub)
    if not n:
        print(f"{' ' * w}{label:<34} n=0")
        return
    def s(k):
        v = [x[k] for x in sub if x[k] is not None]
        if not v:
            return None
        m = sum(v) / len(v)
        win = sum(1 for a in v if a > 0) / len(v) * 100
        return m, win, len(v)
    def pl(k):
        v = [x[k] for x in sub if x[k] is not None]
        wins = [a for a in v if a > 0]
        loss = [a for a in v if a < 0]
        if not wins or not loss:
            return '-'
        return f"{sum(wins)/len(wins)/abs(sum(loss)/len(loss)):.2f}"
    cells = []
    for k in ('r1', 'r3', 'r5', 'r10', 'r20'):
        r = s(k)
        cells.append(f"{k[1:]}d: {'--' if r is None else f'{r[0]:+.2f}%({r[1]:.0f}%,n={r[2]})'}")
    stopn = sum(1 for x in sub if x['status'] == 'STOP_HIT')
    tgtn = sum(1 for x in sub if x['status'] == 'TARGET_HIT')
    dd = [x['mdd'] for x in sub if x['mdd'] is not None]
    g = [x['mg'] for x in sub if x['mg'] is not None]
    extra = f"| 盈亏比3d={pl('r3')} 止损{stopn/n*100:.0f}% 达标{tgtn/n*100:.0f}%"
    extra += f" maxDD{sum(dd)/len(dd):.1f}% maxG{sum(g)/len(g):+.1f}%" if dd and g else ''
    print(f"{' ' * w}{label:<34} n={n:<3} " + ' '.join(cells) + ' ' + extra)


def group(recs, keyfn, title, order=None, w=0):
    print(f"\n{title}")
    g = defaultdict(list)
    for x in recs:
        g[keyfn(x)].append(x)
    keys = order if order else sorted(g, key=lambda k: -len(g[k]))
    for k in keys:
        if k in g:
            agg(g[k], str(k), w)


def bucket(v, cuts, labels):
    if v is None:
        return '未知'
    for i, c in enumerate(cuts):
        if v < c:
            return labels[i]
    return labels[-1]


def main():
    recs = load()
    print('=' * 100)
    print('VSW 策略跟踪记录分类收益率分析')
    print('=' * 100)
    print(f"总记录: {len(recs)}  日期: {min(x['date'] for x in recs)} ~ {max(x['date'] for x in recs)}")
    tracked = [x for x in recs if x['r1'] is not None or x['r3'] is not None]
    print(f"已有跟踪收益: {len(tracked)} 条 (未回填 {len(recs)-len(tracked)} 条)")
    print(f"status 分布: {dict(Counter(x['status'] for x in recs))}")
    print(f"重复(同日同股>1): "
          f"{len(recs) - len(set((x['date'], x['code']) for x in recs))} 条")
    print(f"唯一标的数: {len(set(x['code'] for x in recs))}")

    print('\n' + '=' * 100)
    print('【总体基准】')
    print('=' * 100)
    agg(recs, '全部 154 条')

    print('\n' + '=' * 100)
    print('【分类维度】')
    print('=' * 100)

    group(recs, lambda x: x['action'] or '(空)', '① 按 action（买点档）')
    group(recs, lambda x: x['main'], '② 按 signal 主类', order=['优选', '扩大', '其他'])
    group(recs, lambda x: x['tags'] or '(无修饰)', '③ 按 signal 修饰标签')
    group(recs, lambda x: f"{x['main']}+{x['action'].replace('可开仓·','')}", '④ 主类 × 买点档（交叉）')
    group(recs, lambda x: f"修饰数{x['n_tag']}", '⑤ 按修饰标签个数', order=['修饰数0', '修饰数1', '修饰数2'])

    print('\n' + '=' * 100)
    print('【引擎评分维度】')
    print('=' * 100)
    group(recs, lambda x: f"Rating {x['rating'] or '?'}", '⑥ 按 Rating',
          order=['Rating A', 'Rating B', 'Rating C', 'Rating D'])
    group(recs, lambda x: f"Timing {x['etg'] or '?'}", '⑦ 按 EntryTimingGrade',
          order=['Timing S', 'Timing A', 'Timing B', 'Timing C'])
    group(recs, lambda x: f"T1Risk {x['risk'] or '?'}", '⑧ 按 T1Risk')
    group(recs, lambda x: bucket(x['volscore'], [70, 80, 85, 90],
                                 ['<70', '70~80', '80~85', '85~90', '>=90']),
          '⑨ 按 量能爆发评分', order=['>=90', '85~90', '80~85', '70~80', '<70', '未知'])
    group(recs, lambda x: bucket(x['fes'], [60, 65, 70, 75],
                                 ['<60', '60~65', '65~70', '70~75', '>=75']),
          '⑩ 按 FinalEntryScore（=score）', order=['>=75', '70~75', '65~70', '60~65', '<60', '未知'])
    group(recs, lambda x: bucket(x['ets'], [80, 90, 95],
                                 ['<80', '80~90', '90~95', '>=95']),
          '⑪ 按 EntryTimingScore', order=['>=95', '90~95', '80~90', '<80', '未知'])

    print('\n' + '=' * 100)
    print('【结构位置维度】')
    print('=' * 100)
    group(recs, lambda x: bucket(x['dma20'], [0, 3, 6, 10],
                                 ['<0', '0~3', '3~6', '6~10', '>=10']),
          '⑫ 按 距MA20(%)', order=['<0', '0~3', '3~6', '6~10', '>=10', '未知'])
    group(recs, lambda x: bucket(x['chg5'], [-5, 0, 5, 10],
                                 ['<-5', '-5~0', '0~5', '5~10', '>=10']),
          '⑬ 按 5日涨幅(%)', order=['<-5', '-5~0', '0~5', '5~10', '>=10', '未知'])

    print('\n' + '=' * 100)
    print('【标签/属性维度】')
    print('=' * 100)
    group(recs, lambda x: '有主题' if x['theme'] else '无主题', '⑭ 按 所属主题', order=['有主题', '无主题'])
    group(recs, lambda x: x['stage'] or '(空)', '⑮ 按 非一日游阶段')
    group(recs, lambda x: '有基本面Gate' if x['fgate'] == '有' else '无基本面Gate',
          '⑯ 按 基本面Gate', order=['有基本面Gate', '无基本面Gate'])
    group(recs, lambda x: x['chip'] or '(空)', '⑰ 按 ChipSuggestion')

    print('\n' + '=' * 100)
    print('【逐日】')
    print('=' * 100)
    print(f"{'日期':<10}{'n':>4} {'1d':>22} {'3d':>22} {'5d':>22} 胜3d 止损")
    g = defaultdict(list)
    for x in recs:
        g[x['date']].append(x)
    for d in sorted(g):
        sub = g[d]
        def s(k):
            v = [x[k] for x in sub if x[k] is not None]
            if not v:
                return '--'
            return f"{sum(v)/len(v):+.2f}%({sum(1 for a in v if a>0)/len(v)*100:.0f}%,n={len(v)})"
        stopn = sum(1 for x in sub if x['status'] == 'STOP_HIT')
        print(f"{d:<10}{len(sub):>4} {s('r1'):>22} {s('r3'):>22} {s('r5'):>22} "
              f"{stopn/len(sub)*100:>4.0f}%")

    print('\n' + '=' * 100)
    print('【逐条明细（按3日收益升序，看最亏/最赚）】')
    print('=' * 100)
    print(f"{'日期':<10}{'代码':<11}{'名称':<9}{'主类':<5}{'买点档':<12}{'Rat':<4}{'Tim':<4}"
          f"{'量能':>5}{'1d':>7}{'3d':>7}{'5d':>7}{'10d':>7}{'maxG':>6}{'maxDD':>6} 状态")
    shown = [x for x in recs if x['r3'] is not None]
    for x in sorted(shown, key=lambda x: x['r3']):
        print(f"{x['date']:<10}{x['code']:<11}{x['name']:<9}{x['main']:<5}"
              f"{x['action'].replace('可开仓·',''):<12}{x['rating']:<4}{x['etg']:<4}"
              f"{x['volscore'] or 0:>5.0f}{x['r1'] if x['r1'] is not None else float('nan'):>7.2f}"
              f"{x['r3']:>7.2f}{x['r5'] if x['r5'] is not None else float('nan'):>7.2f}"
              f"{x['r10'] if x['r10'] is not None else float('nan'):>7.2f}"
              f"{x['mg'] if x['mg'] is not None else float('nan'):>6.1f}"
              f"{x['mdd'] if x['mdd'] is not None else float('nan'):>6.1f} {x['status']}")


if __name__ == '__main__':
    main()
