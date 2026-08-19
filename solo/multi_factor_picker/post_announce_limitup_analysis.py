# -*- coding: utf-8 -*-
"""中报公告后涨停走势统计研究
问题: 发布中报(业绩预告)后涨停, 未来会如何?
样本: 近5年中报季(2022-2026 公告日 6/1~8/31)业绩预告, 公告后 ANNO_WINDOW 个交易日内首个涨停
收益: 涨停日收盘买入, +1/+3/+5/+10/+20 交易日收益与期间峰值(最高点到涨停日收盘)
分桶: 业绩方向(正/负) / 连板数(1板 vs ≥2板) / 追涨vs回踩 / 市值分位(样本内三等分)
口径说明:
  - 涨停=limit_list_d 收盘涨停(U), 连板数=limit_times
  - 回踩样本: 涨停后第2~5交易日内出现 close<涨停日收盘 的第一天(买入日)
  - 一字板(涨停日 open==close) 记买不进, 单独标注
  - 过滤: 北交所(8/4开头), 上市<60天次新(无涨跌幅限制)
"""
import os, time, math
import pandas as pd
import numpy as np
import tushare as ts

# ---------- 配置 ----------
REPORT_DIR = r"D:\mystock\solo\report_daily"
YEARS = [2022, 2023, 2024, 2025, 2026]
ANNO_WINDOW = 10          # 公告后10个交易日内涨停算"公告驱动"
HOLD = [1, 3, 5, 10, 20]  # 观察周期(交易日)
GAP_LIMIT = 2             # 公告间隔阀门: 涨停日距公告日≤2交易日 才保留(立即涨停=强者恒强;
                          #   间隔≥3日的滞后涨停/反抽型实测负期望, 见 20260817 统计)
POSITIVE = {'预增', '略增', '扭亏', '续盈'}
NEGATIVE = {'预减', '略减', '首亏', '续亏', '减亏'}

_env = r"D:\mystock\config\.env"
with open(_env, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line.startswith("TUSHARE_TOKEN="):
            os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()
ts.set_token(os.environ.get('TUSHARE_TOKEN', ''))
pro = ts.pro_api()


def _sleep():
    time.sleep(0.15)


def load_limit_days(year):
    """当年中报季(6/1~8/31)全部涨停板"""
    cal = pro.trade_cal(exchange='SSE', start_date=f'{year}0601',
                        end_date=f'{year}0831', is_open='1')['cal_date'].tolist()
    rows = []
    for d in cal:
        _sleep()
        try:
            ld = pro.limit_list_d(trade_date=d, fields='ts_code,name,close,open,high,low,pct_chg,limit_times,trade_date')
            if ld is not None and len(ld):
                rows.append(ld)
        except Exception as e:
            print(f'  limit_list_d {d} fail: {e}')
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def load_forecasts(year):
    """当年中报季业绩预告(逐交易日拉取; forecast 区间参数静默返回空, 必须单日)"""
    cal = pro.trade_cal(exchange='SSE', start_date=f'{year}0601',
                        end_date=f'{year}0831', is_open='1')['cal_date'].tolist()
    rows = []
    for d in cal:
        _sleep()
        try:
            fc = pro.forecast(ann_date=d, fields='ts_code,ann_date,type,p_change_min,p_change_max')
            if fc is not None and len(fc):
                rows.append(fc)
        except Exception as e:
            print(f'  forecast {d} fail: {e}')
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def get_daily_basic_cap(year):
    """当年 7/31 前后全市场流通市值(用于样本内市值分位)"""
    for d in [f'{year}0731', f'{year}0729', f'{year}0730']:
        _sleep()
        try:
            db = pro.daily_basic(trade_date=d, fields='ts_code,circ_mv')
            if db is not None and len(db):
                return db
        except Exception:
            continue
    return pd.DataFrame()


def main():
    all_ld, all_fc, all_cap = [], [], []
    for y in YEARS:
        print(f'[{y}] 拉取中报季涨停/预告/市值...')
        ld = load_limit_days(y)
        fc = load_forecasts(y)
        cap = get_daily_basic_cap(y)
        print(f'  [{y}] 涨停 {len(ld)} 条 / 预告 {len(fc)} 条 / 市值 {len(cap)} 条')
        all_ld.append(ld); all_fc.append(fc); all_cap.append(cap)
    ld = pd.concat(all_ld, ignore_index=True)
    fc = pd.concat(all_fc, ignore_index=True)
    cap = pd.concat(all_cap, ignore_index=True)

    # 过滤北交所
    ld = ld[~ld['ts_code'].str.startswith(('8', '4'))].copy()
    fc = fc[~fc['ts_code'].str.startswith(('8', '4'))].copy()

    # 公告驱动涨停匹配: 涨停日 ∈ (ann_date, ann_date+ANNO_WINDOW 交易日内]
    # 先给每个预告算出"下一个交易日历"太繁, 用日历天数近似窗口后精确卡交易窗口:
    # 做法: 涨停日 - 公告日 <= 21 自然日 且 用 trade_cal 校验处于同一窗口的交易日序号
    cal = pro.trade_cal(exchange='SSE', start_date='20220601', end_date='20260831',
                        is_open='1')['cal_date'].tolist()
    cal_idx = {d: i for i, d in enumerate(cal)}

    fc = fc.merge(cap, on='ts_code', how='left')
    # 每只股票预告公告日排序(同年可能多次预告, 取当年首条)
    fc = fc.sort_values(['ts_code', 'ann_date']).drop_duplicates(['ts_code'], keep='first')

    merged = ld.merge(fc, on='ts_code', how='inner')
    merged['gap_days'] = merged.apply(
        lambda r: (cal_idx[r['trade_date']] - cal_idx[r['ann_date']]) if
        (r['ann_date'] in cal_idx and r['trade_date'] in cal_idx) else np.nan, axis=1)
    merged = merged[(merged['gap_days'] > 0) & (merged['gap_days'] <= ANNO_WINDOW)].copy()
    # 同股票同窗口内多天涨停 → 取首个涨停(连板也算同一事件, 保留 limit_times 最大那天作为事件日)
    merged['first_limit'] = merged['trade_date'].astype(int)
    merged = merged.sort_values(['ts_code', 'first_limit'])
    merged = merged.drop_duplicates(['ts_code'], keep='first')

    print(f'\n===== 匹配样本: 公告后{ANNO_WINDOW}交易日内首个涨停 =====')
    print(f'预告总数 {len(fc)} / 涨停总数 {len(ld)} / 匹配 {len(merged)}')
    if merged.empty:
        print('无样本, 退出'); return

    # 业绩方向
    merged['dir'] = merged['type'].apply(
        lambda t: '业绩正' if t in POSITIVE else ('业绩负' if t in NEGATIVE else '其他'))
    # 连板数(公告驱动窗口内的连板)
    merged['limit_group'] = np.where(merged['limit_times'] >= 2, '≥2连板', '首板')

    # ---------- 拉日线算收益 ----------
    def calc(row):
        code, lim_date = row['ts_code'], str(row['trade_date'])
        s = str(int(lim_date) - 800)
        e = str(int(lim_date) + 4000)
        _sleep()
        try:
            d = pro.daily(ts_code=code, start_date=s, end_date=e).sort_values('trade_date').reset_index(drop=True)
        except Exception:
            return pd.Series({})
        if d.empty:
            return pd.Series({})
        # 定位涨停日
        idx = d[d['trade_date'] == lim_date].index
        if len(idx) == 0:
            return pd.Series({})
        i0 = idx[0]
        base = d.loc[i0, 'close']
        # 一字板判定(涨停日 open==high==close, 买不进)
        res = {'收盘': base, '一字': d.loc[i0, 'open'] == d.loc[i0, 'close'] == d.loc[i0, 'high'] == base}
        after = d.iloc[i0 + 1:].reset_index(drop=True)
        # 涨停后第1~5日与 base 比: 出现 <base 的第一天为回踩买入日
        pull = None
        for j in range(len(after)):
            if after.loc[j, 'close'] < base:
                pull = j
                break
        res['回踩日序号'] = pull + 1 if pull is not None else None  # 1-based 交易日
        # 追涨: 涨停日收盘买入
        for h in HOLD:
            if len(after) >= h:
                res[f'追涨+{h}日'] = (after.loc[h - 1, 'close'] / base - 1) * 100
                res[f'峰值+{h}日'] = (after.loc[:h - 1, 'high'].max() / base - 1) * 100
        # 回踩: 回踩日收盘买入(若在+5日内发生)
        if pull is not None and pull < 5:
            pb = after.loc[pull, 'close']
            rest = after.iloc[pull + 1:].reset_index(drop=True)
            res['回踩买入价'] = pb
            for h in HOLD:
                if len(rest) >= h:
                    res[f'回踩+{h}日'] = (rest.loc[h - 1, 'close'] / pb - 1) * 100
                    res[f'回踩峰值+{h}日'] = (rest.loc[:h - 1, 'high'].max() / pb - 1) * 100
        return pd.Series(res)

    out = merged.apply(calc, axis=1)
    df_all = pd.concat([merged.reset_index(drop=True), out.reset_index(drop=True)], axis=1)
    # 市值分位(样本内三等分)
    df_all['cap_rank'] = df_all['circ_mv'].rank(pct=True)
    df_all['cap_group'] = pd.cut(df_all['cap_rank'], bins=3, labels=['小市值', '中市值', '大市值'])
    # 公告间隔阀门: 只保留公告驱动浓度最高的立即涨停(gap<=GAP_LIMIT)
    df_all['valve_ok'] = df_all['gap_days'] <= GAP_LIMIT
    df = df_all[df_all['valve_ok']].copy()

    csv_path = os.path.join(REPORT_DIR, 'post_announce_limitup_samples.csv')
    df_all.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'样本明细已存: {csv_path}  (含 valve_ok 标记)\n')

    # ---------- 统计输出 ----------
    def stat(sub, name):
        sub = sub[sub['收盘'].notna()]
        if len(sub) == 0:
            print(f'[{name}] 无有效样本\n'); return
        print(f'===== {name}  n={len(sub)} =====')
        win = lambda c: (sub[c].dropna() > 0).mean() * 100
        for c in [f'追涨+{h}日' for h in HOLD]:
            s = sub[c].dropna()
            if len(s):
                print(f'  {c:8s} 均值{s.mean():+.2f}%  中位{s.median():+.2f}%  胜率{win(c):.0f}%  峰{ (sub[c.replace("追涨","峰值")].dropna().mean()):+.2f}%')
        if '回踩+5日' in sub.columns:
            s = sub['回踩+5日'].dropna()
            print(f'  回踩+5日  均值{s.mean():+.2f}%  胜率{win("回踩+5日"):.0f}%  (回踩样本n={len(s)})')
        if '回踩+10日' in sub.columns:
            s = sub['回踩+10日'].dropna()
            print(f'  回踩+10日 均值{s.mean():+.2f}%  胜率{win("回踩+10日"):.0f}%')
        print('')

    print(f'===== 公告间隔阀门 GAP_LIMIT={GAP_LIMIT} 通过 {df_all["valve_ok"].sum()} / 过滤 {int((~df_all["valve_ok"]).sum())} =====\n')
    stat(df_all, f'全样本(公告后10日内涨停, n={len(df_all)})')
    stat(df, f'通过阀门(公告后≤{GAP_LIMIT}日涨停, 主样本)')
    stat(df_all[~df_all['valve_ok']], f'被阀门过滤(公告后>{GAP_LIMIT}日涨停)')
    # 分档展示(诊断用)
    gap = df_all['gap_days']
    stat(df_all[gap <= 2], '公告间隔1-2日(立即涨停)')
    stat(df_all[(gap >= 3) & (gap <= 5)], '公告间隔3-5日')
    stat(df_all[gap >= 6], '公告间隔6-10日')
    # 主样本内的业务分桶(全部基于通过阀门的 df)
    for d in ['业绩正', '业绩负']:
        stat(df[df['dir'] == d], d)
    stat(df[df['limit_group'] == '首板'], '首板')
    stat(df[df['limit_group'] == '≥2连板'], '≥2连板')
    stat(df[(df['dir'] == '业绩正') & (df['gap_days'] <= 2)], '业绩正+公告间隔1-2日')
    # 回踩样本(通过阀门且涨停后5日内曾跌破涨停收盘)
    pull_sub = df[df['回踩日序号'].notna() & (df['回踩日序号'] <= 5)]
    stat(pull_sub, '通过阀门+涨停后回踩过(5日内跌破涨停价)')
    for g in ['小市值', '中市值', '大市值']:
        stat(df[df['cap_group'] == g], f'市值-{g}')
    # 一字板
    stat(df[df['一字']], '一字涨停')


if __name__ == '__main__':
    main()
