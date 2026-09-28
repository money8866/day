# -*- coding: utf-8 -*-
"""TL-01 趋势状态识别：Model A 规则状态（预注册阈值，唯一实现）

§16 要求状态不得由未来收益反推；本模块的每一条规则只使用决策日 t 及之前的
已观测信息。tl_build.py 与 tl_grid.py 共用同一实现，保证参数扰动时状态定义同源。

状态阶梯（优先级由低到高，后应用者覆盖前者）：
  S0 非趋势 / S1 萌芽 / S2 确认 / S3 扩张 / S4 加速 / S5 高位拥挤
  S6 衰减 / S7 正常回撤 / S8 再启动 / S9 趋势破坏 / S10 趋势终结

输入 F：dict[str, np.ndarray]（1 维，长度 n），必须包含：
  c_ma18 c_ma20 c_ma22 ma18_ma60 ma20_ma60 ma22_ma60 c_ma60
  ma60_slope slope_20 pos_60 pos_120
  ret_18 ret_20 ret_25 rs_18 rs_20 rs_25
  ts_raw tv tv_alt18 tv_alt25
  atr_pct_pct v_trend v_ma5_ma20 up_recent20 dd_min10 dd_60
"""
import numpy as np

from tl_common import PREREG


def default_cfg(**over):
    """预注册配置；grid 脚本通过 over 覆盖单个参数（§33）"""
    cfg = dict(
        ma_win=20,
        trend_win=20,
        rs_win=20,
        dd_mild=PREREG['th_dd_mild'],
        dd_deep=PREREG['th_dd_deep'],
        dd_term=PREREG['th_dd_term'],
        pos_expa_lo=PREREG['th_pos_expa_lo'],
        pos_expa_hi=PREREG['th_pos_expa_hi'],
        pos_mid=PREREG['th_pos_mid'],
        pos_hi=PREREG['th_pos_hi'],
        atr_acc_pct=PREREG['th_atr_acc_pct'],
        atr_hi_pct=PREREG['th_atr_hi_pct'],
        v_expand=PREREG['th_v_expand'],
    )
    cfg.update(over)
    return cfg


def assign_state(F, cfg=None):
    """返回 int8 状态数组；-1 表示该行未进入研究样本（由调用方置入）"""
    if cfg is None:
        cfg = default_cfg()
    mw, tw, rw = cfg['ma_win'], cfg['trend_win'], cfg['rs_win']
    n = len(F['ret_20'])
    S = np.zeros(n, dtype=np.int8)

    c_ma = F['c_ma%d' % mw]
    ma_ma60 = F['ma%d_ma60' % mw]
    ret = F['ret_%d' % tw]
    rs = F['rs_%d' % rw]
    tv = F['tv'] if tw == 20 else F['tv_alt%d' % tw]

    up = ma_ma60 > 0
    s20 = F['slope_20'] > 0
    pos60, pos120 = F['pos_60'], F['pos_120']
    dd = F['dd_60']
    atrp = F['atr_pct_pct']
    expanding = F['v_trend'] > cfg['v_expand']
    up_rec = F['up_recent20'] > 0
    dd_rec = F['dd_min10'] <= -cfg['dd_mild']
    above20 = c_ma > 0
    sl60 = F['ma60_slope'] < 0

    c1 = above20 & (~up) & s20
    c2 = up & s20 & (ret > 0)
    c3 = c2 & (pos60 >= cfg['pos_expa_lo']) & (pos60 <= cfg['pos_expa_hi']) & (rs > 0)
    c6 = up & (tv < 0) & (pos60 >= cfg['pos_mid'])
    c4 = up & (pos60 >= cfg['pos_mid']) & (tv >= 0) & (atrp >= cfg['atr_acc_pct']) & expanding
    c5 = up & (pos120 >= cfg['pos_hi']) & (atrp >= cfg['atr_hi_pct']) & expanding
    c7 = up & (dd <= -cfg['dd_mild'])
    c8 = (dd_rec & above20 & (tv > 0) & (F['v_ma5_ma20'] < 1.0)
          & (dd > -cfg['dd_mild']) & (dd > -cfg['dd_term']))
    c9 = (~up) & up_rec & (dd <= -cfg['dd_deep'])
    c10 = (~up) & up_rec & (dd <= -cfg['dd_term']) & sl60

    for cond, sid in ((c1, 1), (c2, 2), (c3, 3), (c6, 6), (c4, 4),
                      (c5, 5), (c8, 8), (c7, 7), (c9, 9), (c10, 10)):
        S[cond] = sid
    return S


def state_prep(F, valid, cfg=None):
    """便捷封装：无效样本置 -1"""
    S = assign_state(F, cfg)
    S[~np.asarray(valid, dtype=bool)] = -1
    return S
