# -*- coding: utf-8 -*-
"""H-EARN-FWD 前瞻版：给 2026Q3 造锚点 -> PIT 特征快照 -> 用冻结口径模型打分 -> 出排序。

与投研版 (hef_forecast.py) 的差别
--------------------------------
1. 锚点：PREREG 用「目标财报实际公告日」，但 2026Q3 尚未披露（报告生成于
   2026-09-26），所以这里改用**统一预期锚点 20260924**（数据可用末日）。
   这是对 PREREG anchor_rule 的有意偏离，只在「前瞻」场景使用，不回写历史产物。
2. 偏移：日历只到 20260924，故可取 P = 锚点-60/-30/-20/-10 =
   20260714 / 20260811 / 20260825 / 20260910。其中 10 属 PREREG
   的 offset_neighborhood（已声明参数）。P=20260910 时中报已全部披露，
   是最佳信息集，作为主排序口径；60/30/20 作为稳健性对照。
3. 产物全部写到 *_live_q3 新文件，不覆盖任何已验证产物。

用法:
  python -X utf8 hef_live_forward.py            # 全流程
  python -X utf8 hef_live_forward.py score      # 只重打分（前序产物已在）
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from hef_common import (DATA, PREREG, Log, ic_stats, load_stk_basic,  # noqa: E402
                        spearman_ic, auc_score, fmt)

ANCHOR = '20260924'          # 统一预期锚点 = 数据可用最后交易日
TARGET = '20260930'          # 2026Q3
OFFSETS = (60, 30, 20, 10)   # 60/30/20 主口径 + 10 邻域
PRIMARY_OFF = 10             # 主排序偏移（P=20260910，中报齐备）
SCHEMES = (('TRAINFIX', 2019, 2022, None), ('W6', 2024, 2025, 2026))
EV_LIVE = os.path.join(DATA, 'events_live_q3.parquet')
FT_LIVE = os.path.join(DATA, 'features_live_q3.parquet')
TG_LIVE = os.path.join(DATA, 'targets_live_q3.parquet')
TG_USE = os.path.join(DATA, 'targets_use_q3.parquet')
PR_LIVE = os.path.join(DATA, 'predictions_live_q3.parquet')
SKILL_TXT = os.path.join(HERE, '_live_skill.txt')
RANK_CSV = os.path.join(HERE, '..', '..', 'report_daily',
                        'q3_surprise_rank_2026.csv')

lg = Log('hef_live_forward')


def build_events():
    """历史 events + 2026Q3 合成行（宇宙 = 已披露 2026 中报的 4,9xx 只）。"""
    ev = pd.read_parquet(os.path.join(DATA, 'events.parquet'))
    ev['ts_code'] = ev['ts_code'].astype(str)
    ev['end_date'] = ev['end_date'].astype(str)
    ev['ann'] = ev['ann'].astype(str)

    uni = sorted(ev.loc[ev['end_date'] == '20260630', 'ts_code'].unique())
    syn = pd.DataFrame({
        'ts_code': uni,
        'end_date': TARGET,
        'q_str': '2026Q3',
        'ann': ANCHOR,
        'anchor_year': 2026,
        'phase': 'LIVE-LIKE',
        'np_sq': np.nan, 'rev_sq': np.nan, 'dp_sq': np.nan,
    })
    out = pd.concat([ev, syn], ignore_index=True)
    out.to_parquet(EV_LIVE, index=False)
    lg('合成 events: 历史 %d + 2026Q3 %d = %d  -> %s'
       % (len(ev), len(syn), len(out), EV_LIVE))


def build_features():
    import hef_build_features as B
    lg('=== LIVE FORWARD BUILD (anchor %s, offsets %s) ==='
       % (ANCHOR, OFFSETS))
    B.main(events_path=EV_LIVE, out_path=FT_LIVE, offsets=OFFSETS)


def build_targets():
    """offset 10 的历史标签原产物里没有，需要基于 live 特征补建。"""
    import hef_build_targets as T
    lg('=== LIVE TARGETS (offset 10 补齐) ===')
    T.main(feats_path=FT_LIVE, out_path=TG_LIVE)
    orig = pd.read_parquet(os.path.join(DATA, 'targets.parquet'))
    new = pd.read_parquet(TG_LIVE)
    add = new[new['offset'] == 10].copy()
    for d in (orig, add):
        d['ts_code'] = d['ts_code'].astype(str)
        d['target_end'] = d['target_end'].astype(str)
    use = pd.concat([orig, add], ignore_index=True)
    use.to_parquet(TG_USE, index=False)
    lg('targets_use: 原 %d (offset 60/30/20) + offset10 %d = %d -> %s'
       % (len(orig), len(add), len(use), TG_USE))


def score():
    """复用 hef_forecast 的 design()/mk_model()。预测历史测试集 + 前瞻集，
    前者用于报告 offset 10 的真实技能，后者用于排序。"""
    import hef_forecast as FC

    f = pd.read_parquet(FT_LIVE)
    t = pd.read_parquet(TG_USE)
    f['ts_code'] = f['ts_code'].astype(str)
    f['target_end'] = f['target_end'].astype(str)
    f['ann_year'] = f['ann_year'].astype(int)
    t['ts_code'] = t['ts_code'].astype(str)
    t['target_end'] = t['target_end'].astype(str)
    ycols = ['ts_code', 'target_end', 'offset', 'np_T'] + list(FC.MODELS.keys())
    f = f.merge(t[ycols], on=['ts_code', 'target_end', 'offset'], how='left')

    f['log_mv'] = np.log(pd.to_numeric(f['total_mv'], errors='coerce'))
    f['log_circ_mv'] = np.log(pd.to_numeric(f['circ_mv'], errors='coerce'))
    f['guide_present'] = (f['guide_mid'].notna()
                          & f['guide_is_target'].fillna(False).astype(bool)
                          ).astype('float64')
    miss = [c for c in FC.F_NUM if c not in f.columns]
    assert not miss, 'missing features: %s' % miss
    X = FC.design(f)
    Xv = X.to_numpy()
    lg('design matrix %d x %d' % X.shape)

    live = (f['target_end'] == TARGET).to_numpy()
    offs = f['offset'].to_numpy()
    ays = f['ann_year'].to_numpy()
    lg('2026Q3 行 %d  offsets %s' % (
        int(live.sum()), f.loc[live, 'offset'].value_counts().to_dict()))

    rows, skill = [], []
    for off in OFFSETS:
        for sch, a, b, test in SCHEMES:
            for tgt, kinds in FC.MODELS.items():
                yv = pd.to_numeric(f[tgt], errors='coerce').to_numpy()
                tr = ((offs == off) & (ays >= a) & (ays <= b)
                      & pd.notna(yv))
                te_h = ((offs == off)
                        & ((ays == test) if test is not None else (ays > b)))
                te = te_h | (live & (offs == off))
                if tr.sum() < 200 or te.sum() < 20:
                    lg('  skip off=%s %s %s (train %d / test %d)'
                       % (off, sch, tgt, tr.sum(), te.sum()))
                    continue
                for kind in kinds:
                    m = FC.mk_model(kind)
                    m.fit(Xv[tr], yv[tr])
                    pr = (m.predict_proba(Xv[te])[:, 1] if kind in
                          ('LOGIT', 'GBM_CLS', 'RF') else m.predict(Xv[te]))
                    r = f.loc[te, ['ts_code', 'target_end', 'offset',
                                   'sw_l1', 'board', 'total_mv', 'p_date',
                                   tgt]].copy()
                    r['scheme'] = sch
                    r['model'] = kind
                    r['target'] = tgt
                    r['pred'] = pr
                    rows.append(r)

                    h = r[(r['target_end'] != TARGET) & r[tgt].notna()]
                    ics, aucs = [], []
                    for _k, gc in h.groupby('target_end', sort=False):
                        if len(gc) < PREREG['ic_min_n']:
                            continue
                        ic, _n = spearman_ic(gc['pred'], gc[tgt])
                        if np.isfinite(ic):
                            ics.append(ic)
                        if tgt != 'model_surprise_pct':
                            a_ = auc_score(gc[tgt], gc['pred'])
                            if np.isfinite(a_):
                                aucs.append(a_)
                    mu, tt, icir, nc = ic_stats(
                        ics, PREREG['nw_lag_by_offset'].get(off, 2))
                    skill.append({'offset': off, 'scheme': sch, 'target': tgt,
                                  'model': kind, 'n_cohorts': nc,
                                  'ic': mu, 'ic_t': tt, 'icir': icir,
                                  'auc': float(np.mean(aucs)) if aucs else np.nan,
                                  'n_hist_test': len(h)})
                    lg('  off=%-2s %-8s %-20s %-8s tr=%-6d hist=%-6d '
                       'IC=%s t=%s AUC=%s'
                       % (off, sch, tgt, kind, tr.sum(), len(h),
                          fmt(mu, 4), fmt(tt, 2), fmt(np.mean(aucs) if aucs
                                                      else np.nan, 4)))
    P = pd.concat(rows, ignore_index=True)
    P.to_parquet(PR_LIVE, index=False)
    S = pd.DataFrame(skill)
    S.to_parquet(os.path.join(DATA, 'predictions_live_q3_skill.parquet'),
                 index=False)
    with open(SKILL_TXT, 'w', encoding='utf-8') as fh:
        fh.write(S.to_string(index=False))
    lg('saved %s rows %d ; skill %s' % (PR_LIVE, len(P), SKILL_TXT))
    return P


def rank_csv(P):
    """主排序：offset=10，strong_surprise 在 TRAINFIX/W6 上的均值。
    只取 2026Q3 前瞻行（target_end == TARGET），排除历史测试集。"""
    R = P[(P['offset'] == PRIMARY_OFF)
          & (P['target_end'] == TARGET)].copy()
    wide = R.pivot_table(index=['ts_code', 'sw_l1', 'board', 'total_mv'],
                         columns=['target', 'model', 'scheme'],
                         values='pred').reset_index()
    wide.columns = ['_'.join([str(x) for x in c if x]).strip('_')
                    if isinstance(c, tuple) else c for c in wide.columns]
    rename = {'ts_code': '代码', 'sw_l1': '申万一级', 'board': '板块',
              'total_mv': '总市值万元'}
    wide = wide.rename(columns=rename)

    def col(t, m, s):
        for c in wide.columns:
            if c.startswith('%s_%s_%s' % (t, m, s)):
                return c
        return None

    for t in ('strong_surprise', 'positive_surprise'):
        for m in ('GBM_CLS', 'LOGIT', 'RF'):
            cs = [c for c in (col(t, m, s) for s, _a, _b, _c in SCHEMES) if c]
            if cs:
                wide['%s|%s' % (t, m)] = wide[cs].mean(axis=1)
    cs = [c for c in (col('model_surprise_pct', 'GBM', s)
                      for s, _a, _b, _c in SCHEMES) if c]
    if cs:
        wide['surprise_pct|GBM'] = wide[cs].mean(axis=1)

    wide = wide.sort_values('strong_surprise|GBM_CLS', ascending=False)
    wide['排名'] = np.arange(1, len(wide) + 1)

    sb = load_stk_basic()
    nm = sb.set_index('ts_code')['name'].to_dict() if len(sb) else {}
    wide['名称'] = wide['代码'].map(nm)
    # 本地 sw_industry_map 只覆盖约 3000 只；未覆盖的用 stock_basic.industry
    # 作为备用行业标签（非申万口径，仅供分组参考）
    if len(sb) and 'industry' in sb.columns:
        ind = sb.set_index('ts_code')['industry'].to_dict()
    else:
        ind = {}
    wide['行业_备用'] = np.where(wide['申万一级'] == 'UNKNOWN',
                            wide['代码'].map(ind).fillna(''), '')
    wide['是否ST'] = wide['名称'].fillna('').str.contains('ST').map(
        {True: 'ST', False: ''})

    out = wide[['排名', '代码', '名称', '申万一级', '行业_备用', '是否ST', '板块',
                '总市值万元', 'strong_surprise|GBM_CLS',
                'positive_surprise|GBM_CLS', 'surprise_pct|GBM']].rename(
        columns={
            'strong_surprise|GBM_CLS': '强超预期概率',
            'positive_surprise|GBM_CLS': '正超预期概率',
            'surprise_pct|GBM': '预期偏离幅度'})
    out['总市值_亿'] = pd.to_numeric(out['总市值万元'], errors='coerce') / 1e4
    out = out.drop(columns='总市值万元')
    os.makedirs(os.path.dirname(RANK_CSV), exist_ok=True)
    out.to_csv(RANK_CSV, index=False, encoding='utf-8-sig')
    lg('ranking saved %s rows %d' % (RANK_CSV, len(out)))
    return out


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if mode in ('all', 'build'):
        build_events()
        build_features()
        build_targets()
    if mode in ('all', 'score'):
        rank_csv(score())
    if mode == 'rank':
        rank_csv(pd.read_parquet(PR_LIVE))
