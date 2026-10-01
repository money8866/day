# -*- coding: utf-8 -*-
"""HVE V1 与 HVT-BULL / W7 的交叉验证（§35 交集 / §36 四类 Case）

口径（必须在报告中一并标明，避免误读）：
  - HVE 侧「有信号」= 当日 HVE_BULL ∪ HVE_2ND（可操作两态）；HVE_WATCH 为观察态，
    单独计数但不计入交集（否则 573 条观察记录会淹没交集含义）。
  - HVT-BULL 侧 = te_buy_pool ∪ first_echelon_pool（可执行买点池 ∪ 第一梯队）。
  - W7 侧 = w7_today_action_{date}.json 的 signals[]（五态可操作榜）。
  三者「信号」定义层级不同（HVT 是执行层池、W7 是可操作榜、HVE 是事件驱动两态），
  故 overlap_ratio 同时给出「占 HVE 侧比例」与「占对方比例」两个方向。

输入（缺失一律 fail-soft，记为 exists=false，不报错）：
  report_daily/hve_daily_{date}.json
  report_daily/hvt_bull_{date}.json
  report_daily/w7_today_action_{date}.json
产物：report_daily/hve_cross_{date}.json
"""
import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hve_v1 import signals as S     # noqa: E402

OUT_DIR = os.path.join(BASE_DIR, 'report_daily')
CASE_CAP = 300                      # 每类 Case 最多落盘条数（超出只记 count）


def _load(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'[HVE-CROSS] 读取失败 {path}: {e}')
        return None


def _codes(records, code_key='ts_code') -> dict:
    """[{code, name}, ...] → {code: name}"""
    out = {}
    for r in (records or []):
        c = r.get(code_key)
        if c:
            out[str(c)] = r.get('name') or ''
    return out


def _pair(a: dict, b: dict) -> dict:
    """交集（a、b 均为 {code: name}）"""
    inter = sorted(set(a) & set(b))
    return {
        'count': len(inter),
        'ratio_of_a': (len(inter) / len(a)) if a else None,
        'ratio_of_b': (len(inter) / len(b)) if b else None,
        'codes': [{'ts_code': c, 'name': a.get(c) or b.get(c) or ''} for c in inter],
    }


def _case(codes: list, names: dict, extra: dict = None) -> dict:
    codes = sorted(codes)
    return {
        'count': len(codes),
        'truncated': len(codes) > CASE_CAP,
        'codes': [dict({'ts_code': c, 'name': names.get(c, '')},
                       **(extra.get(c) or {}) if extra else {})
                  for c in codes[:CASE_CAP]],
    }


def cross_check(trade_date: str, out_dir: str = OUT_DIR, verbose: bool = True) -> dict:
    td = str(trade_date)
    hve = _load(os.path.join(out_dir, f'hve_daily_{td}.json'))
    hvt = _load(os.path.join(out_dir, f'hvt_bull_{td}.json'))
    w7 = _load(os.path.join(out_dir, f'w7_today_action_{td}.json'))

    hve_bull = _codes((hve or {}).get('HVE_BULL'))
    hve_2nd = _codes((hve or {}).get('HVE_2ND'))
    hve_watch = _codes((hve or {}).get('HVE_WATCH'))
    hve_sig = {**hve_bull, **hve_2nd}

    hvt_pool = _codes((hvt or {}).get('te_buy_pool'))
    hvt_first = _codes((hvt or {}).get('first_echelon_pool'))
    hvt_sig = {**hvt_pool, **hvt_first}

    w7_sig = _codes((w7 or {}).get('signals'), code_key='code')

    overlap = {
        'HVE_BULL∩HVT': _pair(hve_bull, hvt_sig),
        'HVE_2ND∩HVT': _pair(hve_2nd, hvt_sig),
        'HVE_BULL∩W7': _pair(hve_bull, w7_sig),
        'HVE_2ND∩W7': _pair(hve_2nd, w7_sig),
    }

    names = {**hve_sig, **hvt_sig, **w7_sig}
    hvt_all, w7_all = set(hvt_sig), set(w7_sig)
    cases = {
        'case1_hvt_signal_hve_absent': _case(hvt_all - set(hve_sig), names),
        'case2_w7_signal_hve_absent': _case(w7_all - set(hve_sig), names),
        'case3_hve_signal_others_absent': _case(set(hve_sig) - hvt_all - w7_all, names),
        'case4_hve_and_others': _case(set(hve_sig) & (hvt_all | w7_all), names),
    }

    result = {
        'trade_date': td,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'sources': {
            'hve': {'path': f'hve_daily_{td}.json', 'exists': hve is not None},
            'hvt': {'path': f'hvt_bull_{td}.json', 'exists': hvt is not None},
            'w7': {'path': f'w7_today_action_{td}.json', 'exists': w7 is not None},
        },
        'hve': {'HVE_BULL': len(hve_bull), 'HVE_2ND': len(hve_2nd),
                'HVE_WATCH': len(hve_watch), 'signal_set': len(hve_sig),
                'universe': (hve or {}).get('universe'),
                'market_regime': (hve or {}).get('market_regime')},
        'hvt': {'te_buy_pool': len(hvt_pool), 'first_echelon_pool': len(hvt_first),
                'signal_set': len(hvt_sig)},
        'w7': {'signals': len(w7_sig)},
        'overlap': overlap,
        'cases': cases,
        'note': ('HVE 侧信号=HVE_BULL∪HVE_2ND（HVE_WATCH 为观察态，仅计数）；'
                 'HVT 侧=te_buy_pool∪first_echelon_pool；W7 侧=signals[]；'
                 '三者信号层级不同，ratio 已双向给出；缺文件按 exists=false 处理'),
    }
    if verbose:
        print(f'[HVE-CROSS] {td} ｜ HVE {len(hve_sig)} ｜ HVT {len(hvt_sig)} ｜ W7 {len(w7_sig)}')
        for k, v in overlap.items():
            print(f'[HVE-CROSS]   {k}: {v["count"]}')
        for k, v in cases.items():
            print(f'[HVE-CROSS]   {k}: {v["count"]}')
    return result


def save(result: dict, out_dir: str = OUT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"hve_cross_{result['trade_date']}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    return path


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='HVE V1 × HVT-BULL / W7 交叉验证')
    ap.add_argument('--date', required=True)
    ap.add_argument('--no-save', action='store_true')
    a = ap.parse_args()
    res = cross_check(a.date)
    if not a.no_save:
        print(f'[HVE-CROSS] 已落盘: {save(res)}')
