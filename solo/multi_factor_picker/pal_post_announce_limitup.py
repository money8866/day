# -*- coding: utf-8 -*-
"""
PAL (Post-Announcement Limit-up) v1.2 - 中报公告涨停首波策略
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
次日确认实证(20260819, 主口径73样本, 涨停日收盘买入):
  - 次日守住涨停价 +10日+9.03%/胜率64% vs 回落跌破 -0.09%/41% → 方向是最强单因子
  - 次日量能×方向: 放量阳(续攻)+15.49%/60% > 缩量阳+5.48%/73% > 放量阴(出货)+2.78%/44% > 缩量阴(回踩)-1.71%/40%
  - 次日振幅10~15%配合放量阳=续攻(胜率78%), 6~10%不上不下最钝化
信号规则:
  - 涨停日距公告日 ≤ 2 交易日  → 公告驱动首波
  - 首板(limit_times==1) + 非一字(可买入) + 收盘涨停确认(pct_chg>=9, 剔天地板)
  - 业绩正(预告: 预增/略增/扭亏/续盈; 正式: 净利润同比>0)
  - 非北交所 + 反闸门(涨停巨量量比≥3 后次日巨量阴线≥1.3×涨停量=出货, 剔除)
评分(100): 市值甜区45 + 业绩强度35 + 涨停质量20
次日预案: 公告日次日开盘承接(公告确认后不追涨停), 成本低于涨停价3~15%
        持有周期: 峰值均值+21%/期末+6.5%(7/16回测17只), 止损=公告日收盘-5%
次日确认(--confirm): 读候选池CSV拉次日数据打标, 只保留"守住涨停价+放量阳=续攻",
        弱攻(守住缩量阳)观察, 出货/回踩/滞涨/反抽 剔除——避免预案日直接买入的伪信号
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


# ---- 本地通达信日线(反闸门用): 涨停次日巨量阴线剔除 ----
import struct as _st

TDX_PATH = r'C:\new_tdx\vipdoc'

def _tdx_file(ts_code):
    sym, mkt = ts_code.split('.')
    return os.path.join(TDX_PATH, 'sh' if mkt == 'SH' else 'sz', 'lday',
                        f"{'sh' if mkt == 'SH' else 'sz'}{sym}.day")

def read_tdx_day(ts_code):
    """本地通达信 .day -> DataFrame(trade_date,open,high,low,close,vol) 升序"""
    f = _tdx_file(ts_code)
    if not os.path.exists(f):
        return None
    rec = []
    with open(f, 'rb') as fh:
        while True:
            c = fh.read(32)
            if not c or len(c) < 32:
                break
            rec.append({
                'trade_date': str(_st.unpack('<i', c[0:4])[0]),
                'open': _st.unpack('<i', c[4:8])[0] / 100.0,
                'high': _st.unpack('<i', c[8:12])[0] / 100.0,
                'low': _st.unpack('<i', c[12:16])[0] / 100.0,
                'close': _st.unpack('<i', c[16:20])[0] / 100.0,
                'vol': _st.unpack('<i', c[24:28])[0] / 100.0,
            })
    if not rec:
        return None
    return pd.DataFrame(rec).sort_values('trade_date').reset_index(drop=True)


def tdx_next_bar_after(ts_code, trade_date):
    """trade_date 之后的第一根K线(次日), 本地缺失回退 tushare daily"""
    df = read_tdx_day(ts_code)
    if df is not None:
        after = df[df['trade_date'] > trade_date]
        if not after.empty:
            return after.iloc[0]
    # 回退 tushare: 拉信号日及之后, 取第二根
    _p(None)
    try:
        d = pro.daily(ts_code=ts_code, start_date=str(int(trade_date)+1),
                      fields='trade_date,open,high,low,close,vol').sort_values('trade_date')
        if d is not None and len(d):
            return d.iloc[0]
    except Exception:
        pass
    return None


def tdx_vol_ratio(ts_code, trade_date):
    """本地数据中 trade_date 当日量/前5日均量; 本地缺失回退 tushare daily 计算"""
    df = read_tdx_day(ts_code)
    if df is not None:
        try:
            i = df.index[df['trade_date'] == trade_date][0]
        except IndexError:
            df = None
        else:
            if i < 5:
                return None
            pre = df.iloc[i-5:i]['vol'].mean()
            if pre:
                return df.iloc[i]['vol'] / pre
            return None
    # 回退 tushare: 拉前10日算量比
    _p(None)
    try:
        d = pro.daily(ts_code=ts_code, start_date=str(int(trade_date) - 10), end_date=trade_date,
                      fields='trade_date,vol').sort_values('trade_date').reset_index(drop=True)
        hit = d.index[d['trade_date'] == trade_date]
        if len(hit) == 0 or hit[0] < 5:
            return None
        i = hit[0]
        pre = d.iloc[i-5:i]['vol'].mean()
        return d.iloc[i]['vol'] / pre if pre else None
    except Exception:
        return None


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
    # 涨停质量 20: 首板满分(连板是出货信号); 涨停巨量(量比≥3)=分歧信号扣5分
    s += 20 if r.get('limit_times') == 1 else 0
    vr = r.get('涨停量比')
    if vr is not None and pd.notna(vr) and vr >= 3.0:
        s -= 5  # 巨量涨停=强分歧, 次日易回落(实证: 次日巨量阴线10日胜率仅35%)
    return round(min(100, max(0, s)), 1)


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


def run_confirm(date, mode):
    """次日确认模式: 读信号日候选池CSV, 拉次日数据打标, 只保留"守住+放量阳=续攻"
    判定(20260819 实证 73样本):
      续攻=守住涨停价+放量阳(+10日+15.49%/胜率60%)
      弱攻=守住+缩量阳(+5.48%/胜率73%, 观察)
      出货=回落+放量阴(+2.78%/44%) / 回踩=回落+缩量阴(-1.71%/40%)
    """
    src = os.path.join(REPORT_DIR, f'pal_post_announce_limitup_{mode}_{date}.csv')
    if not os.path.exists(src):
        print(f'无候选池CSV: {src} (先运行 --date {date} --mode {mode} 生成)')
        return
    df = pd.read_csv(src, dtype={'代码': str})
    if df.empty:
        print('候选池为空'); return
    # 次日交易日
    nxt_day = None
    cal = build_calendar(date, str(int(date) + 30))
    for d in sorted(cal):
        if d > date:
            nxt_day = d
            break
    if nxt_day is None:
        print('未找到次日交易日'); return
    print(f'\n===== PAL 次日确认 (信号日 {date} → 次日 {nxt_day}) =====')

    results = []
    for _, r in df.iterrows():
        code = r['代码']
        zt_close = float(r['现价'])
        # 涨停日量
        zt_vol = None
        _p(None)
        try:
            zt = pro.daily(ts_code=code, start_date=date, end_date=date, fields='vol')
            if zt is not None and len(zt):
                zt_vol = float(zt.iloc[0]['vol'])
        except Exception:
            pass
        # 次日 OHLCV
        nb = None
        _p(None)
        try:
            n = pro.daily(ts_code=code, start_date=str(int(date) + 1),
                          fields='trade_date,open,high,low,close,vol').sort_values('trade_date')
            if n is not None and len(n):
                nb = n.iloc[0]
        except Exception:
            pass
        row = {**r.to_dict()}
        if nb is None:
            row['确认'] = '无次日数据'
            results.append(row)
            continue
        n_close = float(nb['close'])
        hold = '守住' if n_close >= zt_close else '回落'
        direc = '阳' if n_close >= float(nb['open']) else '阴'
        vr = float(nb['vol']) / zt_vol if zt_vol else None
        combo = f"{'放量' if vr is not None and vr >= 1.3 else '缩量'}{direc}"
        if hold == '守住' and combo == '放量阳':
            verdict = '续攻'
        elif hold == '守住' and combo == '缩量阳':
            verdict = '弱攻'
        elif hold == '守住':
            verdict = '滞涨'
        elif combo == '放量阴':
            verdict = '出货'
        elif combo == '缩量阴':
            verdict = '回踩'
        else:
            verdict = '反抽'
        row.update({'次日': nxt_day, '次日收盘': n_close, '守涨停价': hold,
                    '次日量比': round(vr, 2) if vr is not None else None,
                    '次日振幅%': round((float(nb['high']) - float(nb['low'])) / zt_close * 100, 1),
                    '次日组合': combo, '确认': verdict})
        results.append(row)

    out = pd.DataFrame(results)
    order = {'续攻': 0, '弱攻': 1, '反抽': 2, '滞涨': 3, '回踩': 4, '出货': 5}
    out = out.sort_values('确认', key=lambda s: s.map(order)).reset_index(drop=True)
    mark = {'续攻': '✅', '弱攻': '⚠️', '出货': '❌', '回踩': '↘', '反抽': '↗', '滞涨': '—'}
    for _, r in out.iterrows():
        if pd.isna(r['次日']):
            print(f"  {r['名称']}({r['代码']}) 无次日数据")
            continue
        vr_s = f"{r['次日量比']:.1f}×" if pd.notna(r['次日量比']) else "?"
        print(f"{mark.get(r['确认'], '?')} {r['确认']} {r['名称']}({r['代码']}) "
              f"次日收{r['次日收盘']:.2f} {r['守涨停价']}涨停价 | {r['次日组合']}({vr_s}) | 振幅{r['次日振幅%']}%")
    keep = out[out['确认'] == '续攻']
    print(f"\n确认保留 {len(keep)} 只(续攻): "
          + (' '.join(str(r['名称']) for _, r in keep.iterrows()) if len(keep) else '无'))
    save = os.path.join(REPORT_DIR, f'pal_confirm_{mode}_{date}.csv')
    out.to_csv(save, index=False, encoding='utf-8-sig')
    print(f'确认明细已存: {save}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True, help='交易日 YYYYMMDD')
    ap.add_argument('--period', default=None, help='报告期, 默认自动(8月前=上年年报, 6-8月=当年中报)')
    ap.add_argument('--mode', default='forecast', choices=['forecast', 'report'],
                    help='公告源: forecast=业绩预告(默认) / report=正式半年度报告')
    ap.add_argument('--confirm', action='store_true',
                    help='次日确认模式: 读信号日候选池CSV拉次日数据打标, 只保留续攻(守住涨停价+放量阳)')
    args = ap.parse_args()
    date = args.date
    mode = args.mode
    if args.confirm:
        run_confirm(date, mode)
        return
    # 报告期推断: 中报季(6-8月)用当年0630
    period = args.period or (f"{date[:4]}0630" if 6 <= int(date[4:6]) <= 8 else f"{int(date[:4])-1}1231")

    src = '业绩预告' if mode == 'forecast' else '正式半年报'
    print(f'== PAL 公告涨停首波策略 v1.2 | 交易日 {date} | 报告期 {period} | 公告源: {src} ==')
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

    # ---- 反闸门(本地通达信): 涨停巨量(量比≥3)后次日巨量阴线(收阴且vol≥1.3×涨停vol) 剔除 ----
    kept, dropped = [], []
    for r in rows:
        vr = tdx_vol_ratio(r['代码'], date)
        r['涨停量比'] = round(vr, 1) if vr is not None else None
        nxt = tdx_next_bar_after(r['代码'], date)
        heavy_limit = vr is not None and vr >= 3.0
        if nxt is not None and heavy_limit:
            is_neg = float(nxt['close']) < float(nxt['open'])   # 次日收阴
            # 涨停日量(本地或tushare回退后的 nxt 反推不可行, 用量比判定: 次日量≥1.3×涨停量)
            # 直接用 vol: tdx 与 tushare 的 vol 单位不同, 统一用"次日量/涨停日量"比值需同日源
            sig_vol = None
            tdx = read_tdx_day(r['代码'])
            if tdx is not None:
                sig_rows = tdx[tdx['trade_date'] == date]
                if len(sig_rows):
                    sig_vol = float(sig_rows.iloc[0]['vol'])
            if sig_vol is None:
                # tushare 涨停日量
                _p(None)
                try:
                    sd = pro.daily(ts_code=r['代码'], start_date=date, end_date=date, fields='vol')
                    if sd is not None and len(sd):
                        sig_vol = float(sd.iloc[0]['vol'])
                except Exception:
                    pass
            heavy_next = bool(sig_vol) and float(nxt['vol']) >= sig_vol * 1.3  # 次日量≥1.3×涨停量
            if is_neg and heavy_next:
                r['反闸门'] = f"剔除:涨停量比{vr:.1f}→次日{nxt['trade_date']}收阴量{float(nxt['vol'])/sig_vol:.1f}×涨停量"
                dropped.append(r)
                continue
        r['反闸门'] = ''
        kept.append(r)
    rows = kept
    if dropped:
        print(f'\n[反闸门] 剔除 {len(dropped)} 只(涨停巨量次日巨量阴线):')
        for r in dropped:
            print(f"  {r['名称']}({r['代码']}) {r['反闸门']}")

    df = pd.DataFrame(rows)
    df['评分'] = df.apply(lambda r: score_stock({
        'type': r['公告类型'], 'p_min': r['业绩幅度%'], 'p_max': r['预增上限'],
        'limit_times': r['连板数'], '涨停量比': r['涨停量比']}, r['流通市值亿']), axis=1)
    df['次日预案'] = df.apply(lambda r: build_plan({
        'ann_close': r['ann_close'], 'close': r['现价'], 'limit_times': r['连板数']}, r['流通市值亿']), axis=1)
    df = df.sort_values('评分', ascending=False).reset_index(drop=True)

    out = os.path.join(REPORT_DIR, f'pal_post_announce_limitup_{mode}_{date}.csv')
    df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n===== 公告涨停候选池 (n={len(df)}) =====\n')
    for _, r in df.iterrows():
        vr = r['涨停量比']
        vr_s = f" 涨停量比{vr:.1f}" if pd.notna(vr) else ""
        print(f"{r['评分']:.0f}分 {r['名称']}({r['代码']}) {r['公告类型']} 幅度{r['业绩幅度%']}% "
              f"公告{r['公告日']}→涨停{r['涨停日']}(间隔{r['公告间隔']}日) 现价{r['现价']:.2f} +{r['涨幅%']:.1f}%{vr_s}")
        print(f"    预案: {r['次日预案']}")
    print(f'\n明细已存: {out}')


if __name__ == '__main__':
    main()
