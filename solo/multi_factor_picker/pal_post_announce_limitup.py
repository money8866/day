# -*- coding: utf-8 -*-
"""
PAL (Post-Announcement Limit-up) v1.1 - 中报公告涨停首波策略
=========================================
公告涨停子策略：捕捉"业绩公告后立即涨停"的首波动量。
公告源二选一(--mode):
  forecast = 中报业绩预告公告(默认, type+幅度区间, 7月集中)
  report   = 正式半年度报告公告(fina_indicator.ann_date, 净利润同比, 8月集中)
实证依据(20260817 近5年中报季 453 样本统计):
  - 公告后≤2交易日涨停 才是真信号(+10日 +4.41%/胜率52%), 滞后涨停/反抽是负期望
  - 业绩正(预增/略增/扭亏/续盈) 公告即涨停 动量最足(+3日 +3.52%/胜率61%)
  - 中市值(样本内分位33-66%) 是甜区(+10日 +8.33%/胜率67%, 回踩买点胜率70-80%)
  - ≥2连板是出货信号(+10日 -7.64%/胜率6%), 小市值负期望, 一字板买不进
信号规则:
  - 涨停日距公告日 ≤ 2 交易日  → 公告驱动首波
  - 首板(limit_times==1) + 非一字(可买入) + 收盘涨停确认(pct_chg>=9, 剔天地板)
  - 业绩正(预告: 预增/略增/扭亏/续盈; 正式: 净利润同比>0)
  - 非北交所
评分(100): 市值甜区45 + 业绩强度35 + 涨停质量20
次日预案: 公告日次日开盘承接(公告确认后不追涨停), 成本低于涨停价3~15%
        持有周期: 峰值均值+21%/期末+6.5%(7/16回测17只), 止损=公告日收盘-5%
注意: 本策略输出为"次日可执行预案", 不跟踪历史信号(与 EGPT 相同口径)
"""
import os, sys, time, argparse
import pandas as pd
import numpy as np

_env = r"D:\mystock\config\.env"
with open(_env, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line.startswith("TUSHARE_TOKEN="):
            os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()
import tushare as ts
ts.set_token(os.environ.get('TUSHARE_TOKEN', ''))
pro = ts.pro_api()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')
GAP_LIMIT = 2          # 公告间隔阀门: 涨停日距公告日≤2交易日
POSITIVE = {'预增', '略增', '扭亏', '续盈'}
SWEET_CAP = (30, 150)  # 市值甜区(亿): 30-150亿 满分, 实证中市值最优
_SLEEP = 0.13


def _p(s):
    time.sleep(_SLEEP); return s


def load_limitups(trade_date):
    """当日涨停板列表(收盘涨停)"""
    _p(None)
    ld = pro.limit_list_d(trade_date=trade_date,
                          fields='ts_code,name,close,open,high,low,pct_chg,amount,limit_times')
    if ld is None or len(ld) == 0:
        return pd.DataFrame()
    return ld[~ld['ts_code'].str.startswith(('8', '4'))].copy()  # 过滤北交所


def load_ann_forecast(codes, period):
    """中报业绩预告公告日 {ts_code: (ann_date, type, p_min, p_max)}"""
    out = {}
    for c in codes:
        _p(None)
        try:
            fc = pro.forecast(ts_code=c, period=period,
                              fields='ts_code,ann_date,type,p_change_min,p_change_max')
            if fc is not None and len(fc):
                r = fc.sort_values('ann_date').iloc[0]
                out[c] = (str(r['ann_date']), str(r['type']), r.get('p_change_min'), r.get('p_change_max'))
        except Exception as e:
            print(f'  forecast {c} fail: {e}')
    return out


def load_ann_report(codes, period):
    """正式半年度报告公告日 {ts_code: (ann_date, type, netprofit_yoy, netprofit_yoy)}
    业绩正用净利润同比 netprofit_yoy>0; type 显示为'净利同比'"""
    out = {}
    for c in codes:
        _p(None)
        try:
            fi = pro.fina_indicator(ts_code=c, period=period,
                                    fields='ts_code,ann_date,end_date,netprofit_yoy')
            if fi is not None and len(fi):
                fi = fi[fi['end_date'] == period].sort_values('ann_date')
                if len(fi):
                    r = fi.iloc[-1]  # 取本报告期最后一条公告(正式报告)
                    yoy = r.get('netprofit_yoy')
                    out[c] = (str(r['ann_date']), '正式报告', yoy, yoy)
        except Exception as e:
            print(f'  fina_indicator {c} fail: {e}')
    return out


def load_circ_mv(codes, trade_date):
    """当日流通市值(亿)"""
    out = {}
    _p(None)
    try:
        db = pro.daily_basic(trade_date=trade_date, fields='ts_code,circ_mv')
        if db is not None and len(db):
            m = db.set_index('ts_code')['circ_mv']
            for c in codes:
                if c in m.index and pd.notna(m[c]):
                    out[c] = m[c] / 10000.0  # 万元->亿
    except Exception as e:
        print(f'  daily_basic fail: {e}')
    return out


def load_daily_ohlc(codes, dates):
    """查指定日 OHLC {ts_code: (open, close)}, codes 与 dates 等长"""
    out = {}
    for c, d in zip(codes, dates):
        _p(None)
        try:
            r = pro.daily(ts_code=c, start_date=d, end_date=d,
                          fields='ts_code,open,high,low,close')
            if r is not None and len(r):
                out[c] = (float(r.iloc[0]['open']), float(r.iloc[0]['close']))
        except Exception as e:
            print(f'  daily {c} fail: {e}')
    return out


def build_calendar(start='20260101', end='20261231'):
    _p(None)
    cal = pro.trade_cal(exchange='SSE', start_date=start, end_date=end, is_open='1')['cal_date'].tolist()
    cal = sorted(cal)  # trade_cal 返回倒序, 需升序后建索引
    return {d: i for i, d in enumerate(cal)}


def score_stock(r, mv):
    """评分(100) = 市值甜区45 + 业绩强度35 + 涨停质量20"""
    s = 0.0
    # 市值甜区 45: 30-150亿满分, 线性衰减, 极小/极大最低
    if mv is not None and pd.notna(mv):
        if SWEET_CAP[0] <= mv <= SWEET_CAP[1]:
            s += 45
        elif mv < SWEET_CAP[0]:
            s += 45 * max(0.2, mv / SWEET_CAP[0])
        else:
            s += 45 * max(0.2, 1 - (mv - SWEET_CAP[1]) / 500.0)
    else:
        s += 20  # 市值未知给中性分
    # 业绩强度 35: 预增/扭亏 25 > 略增/续盈 18, 正式报告按净利同比同档; 幅度越大分越高
    t = r.get('type', '')
    p_min, p_max = r.get('p_min'), r.get('p_max')
    if t in ('预增', '扭亏', '正式报告'):
        s += 25
    elif t in ('略增', '续盈'):
        s += 18
    else:
        s += 5
    try:
        amp = max([float(x) for x in (p_min, p_max) if x is not None and str(x) != 'nan'])
        s += min(10, max(0, amp / 30.0))  # 预增幅度: 30%+ 满分10分
    except Exception:
        pass
    # 涨停质量 20: 首板满分(连板是出货信号), 收盘封板强度(收盘/最高)
    s += 20 if r.get('limit_times') == 1 else 0
    return round(min(100, s), 1)


def build_plan(r, mv):
    """次日预案: 公告日次日开盘承接(成本优于追涨停), 止损=公告日收盘-5%"""
    a = float(r['ann_close'])     # 公告日收盘
    c = float(r['close'])         # 信号日(涨停)收盘
    buy = round(a * 1.01, 2)      # 公告次日承接买点: 公告收盘+1%(开盘承接)
    stop = round(a * 0.95, 2)     # 止损: 公告日收盘-5%
    tp1 = round(a * 1.10, 2)      # 止盈1: 公告+10%
    tp2 = round(a * 1.20, 2)      # 止盈2: 公告+20%
    mv_s = f"{mv:.0f}亿" if mv is not None and pd.notna(mv) else "?"
    return (f"公告次日开盘承接{buy}(不追涨停) | 止损{stop}(公告-5%) | "
            f"止盈{tp1}减半/{tp2}清仓 | 市值{mv_s}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True, help='交易日 YYYYMMDD')
    ap.add_argument('--period', default=None, help='报告期, 默认自动(8月前=上年年报, 6-8月=当年中报)')
    ap.add_argument('--mode', default='forecast', choices=['forecast', 'report'],
                    help='公告源: forecast=业绩预告(默认) / report=正式半年度报告')
    args = ap.parse_args()
    date = args.date
    mode = args.mode
    # 报告期推断: 中报季(6-8月)用当年0630
    period = args.period or (f"{date[:4]}0630" if 6 <= int(date[4:6]) <= 8 else f"{int(date[:4])-1}1231")

    src = '业绩预告' if mode == 'forecast' else '正式半年报'
    print(f'== PAL 公告涨停首波策略 v1.1 | 交易日 {date} | 报告期 {period} | 公告源: {src} ==')
    ld = load_limitups(date)
    if ld.empty:
        print('当日无涨停'); return
    print(f'当日涨停(非北交所): {len(ld)}')

    cal_idx = build_calendar(f"{int(date[:4])-1}1201", date)
    codes = ld['ts_code'].tolist()
    ann = load_ann_forecast(codes, period) if mode == 'forecast' else load_ann_report(codes, period)
    mv = load_circ_mv(codes, date)
    print(f'查得公告日 {len(ann)} / 市值 {len(mv)}')

    rows = []
    for _, r in ld.iterrows():
        c = r['ts_code']
        if c not in ann:
            continue
        ann_date, atype, p_min, p_max = ann[c]
        # 业绩方向阀门: 预告按类型, 正式报告按净利同比>0
        if mode == 'forecast':
            if atype not in POSITIVE:
                continue
        else:
            if p_min is None or pd.isna(p_min) or float(p_min) <= 0:
                continue
        if int(r['limit_times']) != 1 if pd.notna(r['limit_times']) else False:
            continue  # 首板阀门(≥2连板=出货信号, 统计负期望)
        # 收盘涨停确认: limit_list_d 可能含天地板(盘中涨停收盘跌停, pct_chg<0), 剔除
        if float(r['pct_chg']) < 9.0:
            continue
        # 公告间隔阀门: 0=公告当日涨停(盘后公告次日承接) / 1~2=公告后涨停; 负间隔(涨停在公告前)排除
        if ann_date not in cal_idx or date not in cal_idx:
            continue
        gap = cal_idx[date] - cal_idx[ann_date]
        if not (0 <= gap <= GAP_LIMIT):
            continue
        rows.append({
            '代码': c, '名称': r['name'], '行业': '', '主题': '',
            '公告日': ann_date, '涨停日': date, '公告间隔': gap,
            '公告类型': atype, '业绩幅度%': p_min, '预增上限': p_max,
            '现价': r['close'], '涨幅%': r['pct_chg'],
            '连板数': int(r['limit_times']) if pd.notna(r['limit_times']) else 1,
            '流通市值亿': mv.get(c, np.nan),
            '涨停质量分': 20, '次日预案': '',
        })
    if not rows:
        print(f'\n无符合阀门(公告后≤{GAP_LIMIT}日 + 业绩正 + 首板)的标的')
        return

    # 一字板判定 + 公告日收盘价: open==close 买不进剔除; 预案基准=公告收盘
    ohlc = load_daily_ohlc([r['代码'] for r in rows], [date]*len(rows))
    rows = [r for r in rows if not (r['代码'] in ohlc and ohlc[r['代码']][0] == r['现价'])]
    ann_closes = load_daily_ohlc([r['代码'] for r in rows], [r['公告日'] for r in rows])
    for r in rows:
        r['ann_close'] = ann_closes[r['代码']][1] if r['代码'] in ann_closes else r['现价']

    df = pd.DataFrame(rows)
    df['评分'] = df.apply(lambda r: score_stock({
        'type': r['公告类型'], 'p_min': r['业绩幅度%'], 'p_max': r['预增上限'],
        'limit_times': r['连板数']}, r['流通市值亿']), axis=1)
    df['次日预案'] = df.apply(lambda r: build_plan({
        'ann_close': r['ann_close'], 'close': r['现价'], 'limit_times': r['连板数']}, r['流通市值亿']), axis=1)
    df = df.sort_values('评分', ascending=False).reset_index(drop=True)

    out = os.path.join(REPORT_DIR, f'pal_post_announce_limitup_{mode}_{date}.csv')
    df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n===== 公告涨停候选池 (n={len(df)}) =====\n')
    for _, r in df.iterrows():
        print(f"{r['评分']:.0f}分 {r['名称']}({r['代码']}) {r['公告类型']} 幅度{r['业绩幅度%']}% "
              f"公告{r['公告日']}→涨停{r['涨停日']}(间隔{r['公告间隔']}日) 现价{r['现价']:.2f} +{r['涨幅%']:.1f}%")
        print(f"    预案: {r['次日预案']}")
    print(f'\n明细已存: {out}')


if __name__ == '__main__':
    main()
