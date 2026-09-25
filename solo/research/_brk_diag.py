# -*- coding: utf-8 -*-
"""§17 §19 关键特征 TRAIN/VALID/OOS 三期一致性诊断 + §31 硬性 FAIL 预检"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
S = pd.read_parquet(os.path.join(OUT, 'brk_stat.parquet'))
pd.set_option('display.width', 300)
pd.set_option('display.max_rows', 400)

C = S[S.feature != 'B1_AllCandidates']
cols = ['ic_mean', 'ic_pos', 'icir', 'auc', 'rank_sep', 'std_gap', 'spread10', 'spread20',
        'top10_ret', 'bot10_ret', 'n_days']


def show(feats, H):
    t = C[(C.horizon == H) & (C.feature.isin(feats))].pivot_table(
        index='feature', columns='period', values=cols, aggfunc='mean')
    t = t.swaplevel(0, 1, axis=1).sort_index(axis=1)
    print('\n===== H=%d 三期一致性 =====' % H)
    for f in feats:
        if f not in t.index:
            continue
        print('--', f)
        sub = t.loc[f]
        out = pd.DataFrame({
            'ALL': [sub.get(('ic_mean', 'ALL')), sub.get(('auc', 'ALL')),
                    sub.get(('spread10', 'ALL')), sub.get(('ic_pos', 'ALL')),
                    sub.get(('std_gap', 'ALL'))],
            'TRAIN': [sub.get(('ic_mean', 'TRAIN')), sub.get(('auc', 'TRAIN')),
                      sub.get(('spread10', 'TRAIN')), sub.get(('ic_pos', 'TRAIN')),
                      sub.get(('std_gap', 'TRAIN'))],
            'VALID': [sub.get(('ic_mean', 'VALID')), sub.get(('auc', 'VALID')),
                      sub.get(('spread10', 'VALID')), sub.get(('ic_pos', 'VALID')),
                      sub.get(('std_gap', 'VALID'))],
            'OOS': [sub.get(('ic_mean', 'OOS')), sub.get(('auc', 'OOS')),
                    sub.get(('spread10', 'OOS')), sub.get(('ic_pos', 'OOS')),
                    sub.get(('std_gap', 'OOS'))]},
            index=['ic_mean', 'auc', 'spread10', 'ic_pos', 'std_gap'])
        print(out.round(4).to_string())


KEY = ['ATR10_pct', 'ATR5_pct', 'ATR20_pct', 'STD10', 'STD20', 'K_BodyAbs20',
       'Turnover_1', 'Turnover_5', 'Turnover_20', 'Turnover_per_MV', 'Turnover_Shock',
       'Turnover_Stability', 'Volume_Stability', 'Volume_Shock', 'K_LargeBodyFreq20',
       'Close_vs_MA60', 'Close_vs_MA10', 'Close_Position_60', 'Distance_to_R10',
       'Distance_to_R20', 'Ret_5', 'Ret_10', 'Ret_20', 'RelativeStrength_5',
       'RelativeStrength_20', 'Industry_Return_10', 'Efficiency_20', 'TrendEfficiency_20',
       'K_Gap', 'K_NegCandleRatio20', 'LogAmount', 'LogMV', 'MA_Align',
       'B2_RandomOrder', 'B3_Momentum_Ret20', 'B4_DistToResistance', 'B5_Volume_VR20',
       'B6_SimpleBreakoutFlag', 'PriceVolumeEff_5', 'MA5_slope']

for H in (3, 5):
    show(KEY, H)

# ── 符号一致性统计（全特征）
print('\n===== 全特征 三期符号一致性统计 =====')
for H in (3, 5):
    t = C[C.horizon == H].pivot_table(index='feature', columns='period',
                                      values='ic_mean', aggfunc='mean')
    t = t.dropna(subset=['ALL', 'TRAIN', 'VALID', 'OOS'])
    s = np.sign(t)
    same4 = (s['ALL'] == s['TRAIN']) & (s['ALL'] == s['VALID']) & (s['ALL'] == s['OOS'])
    same3 = (s['TRAIN'] == s['VALID']) & (s['TRAIN'] == s['OOS'])
    print('H=%d  总特征 %d ; TRAIN=VALID=OOS 同号 %d (%.1f%%) ; 含 ALL 四期同号 %d (%.1f%%) ; '
          'OOS 与 ALL 反号 %d'
          % (H, len(t), same3.sum(), 100 * same3.mean(), same4.sum(), 100 * same4.mean(),
             (s['OOS'] != s['ALL']).sum()))

# ── §21 基准对照
print('\n===== §21 Baseline 对照（H=5, spread10 / mean）=====')
b = C[(C.horizon == 5) & (C.feature.isin(['B2_RandomOrder', 'B3_Momentum_Ret20',
                                          'B4_DistToResistance', 'B5_Volume_VR20',
                                          'B6_SimpleBreakoutFlag']))]
print(b.pivot_table(index='feature', columns='period', values=['ic_mean', 'spread10', 'auc'],
                    aggfunc='mean').round(5).to_string())

# ── §31 预检: 按 |ALL IC| top20 特征，看 OOS 是否反号
print('\n===== |ALL IC| Top20 的 OOS 表现（H=5）=====')
t = C[C.horizon == 5].pivot_table(index='feature', columns='period',
                                  values=['ic_mean', 'spread10', 'auc'], aggfunc='mean')
t['absic'] = t[('ic_mean', 'ALL')].abs()
top = t.sort_values('absic', ascending=False).head(20)
print(top[[('ic_mean', 'ALL'), ('ic_mean', 'TRAIN'), ('ic_mean', 'VALID'), ('ic_mean', 'OOS'),
           ('spread10', 'ALL'), ('spread10', 'TRAIN'), ('spread10', 'VALID'), ('spread10', 'OOS'),
           ('auc', 'ALL'), ('auc', 'OOS')]].round(4).to_string())
print('\n同表（H=3）')
t = C[C.horizon == 3].pivot_table(index='feature', columns='period',
                                  values=['ic_mean', 'spread10', 'auc'], aggfunc='mean')
t['absic'] = t[('ic_mean', 'ALL')].abs()
top = t.sort_values('absic', ascending=False).head(20)
print(top[[('ic_mean', 'ALL'), ('ic_mean', 'TRAIN'), ('ic_mean', 'VALID'), ('ic_mean', 'OOS'),
           ('spread10', 'ALL'), ('spread10', 'TRAIN'), ('spread10', 'VALID'), ('spread10', 'OOS'),
           ('auc', 'ALL'), ('auc', 'OOS')]].round(4).to_string())
