# -*- coding: utf-8 -*-
"""HVE V1 每日入口（§42 参数敏感性接口 / §47 日报 / 落库 / 邮件推送）

用法：
    python hve_v1/run_daily.py --date 20260930
    python hve_v1/run_daily.py --date 20260930 --hve-vr20 2.2 --hve-clv 0.70 --push

参数开关（§42，全部只覆盖本次运行的集中配置，不改 hve_config.json）：
    --hve-vr20 / --hve-clv / --hve-drawdown / --hve-volume-decay /
    --hve-digest-days / --hve-breakout-vr
"""
import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hve_v1 import STRATEGY_ID, load_config     # noqa: E402
from hve_v1.scanner import scan_day, save       # noqa: E402

# §42 CLI 开关 → 集中配置键
PARAM_SWITCHES = (
    ('hve_vr20', 'vr20_min', float),                # §6  高量阈值
    ('hve_clv', 'clv_min', float),                  # §6  收盘位置
    ('hve_drawdown', 'bull_max_drawdown', float),   # §13 HVE-BULL 回撤上限
    ('hve_volume_decay', 'volume_decay_max', float),  # §18 Volume Contraction 阈值
    ('hve_digest_days', 'min_digest_days', int),    # §19 最短整理天数
    ('hve_breakout_vr', 'breakout_volume_ratio', float),  # §21 突破量比
)

# 落库档位：仅「当日收盘即可执行」的 BUY（与 hvt_bull 同口径，不混档）
DB_SIGNALS = ('BUY',)


def _apply_overrides(cfg: dict, args) -> dict:
    """§42 参数扰动：返回 {cfg_key: value}，同时写回 cfg（就地）"""
    applied = {}
    for attr, key, cast in PARAM_SWITCHES:
        v = getattr(args, attr, None)
        if v is None:
            continue
        cfg[key] = cast(v)
        applied[key] = cfg[key]
    return applied


def _record_picks_to_db(trade_date: str, result: dict) -> int:
    """HVE 可执行 BUY 落库（strategy_id='hve_v1'，幂等；失败不阻塞推送）"""
    try:
        from stock_pick_db import DB_PATH as PICK_DB_PATH, record_picks
    except Exception as e:
        print(f'[HVE] stock_pick_db 不可用，跳过落库: {e}')
        return 0
    rows = []
    for key in ('HVE_BULL', 'HVE_2ND'):
        for r in result.get(key) or []:
            if r.get('signal') not in DB_SIGNALS or not r.get('executable'):
                continue
            rows.append({
                'ts_code': r.get('ts_code'),
                'stock_name': r.get('name') or '',
                'close': r.get('close'),
                'signal': r.get('signal_type'),
                'action': r.get('signal'),
                'score': None,                      # §26 无综合评分，不落分数
                'rank_no': len(rows) + 1,
                'industry': r.get('theme') or '',
                'reason': r.get('reason') or '',
                'stop_price': r.get('invalid_price'),
                'target_price': None,
                'details': {
                    'event_date': r.get('event_date'),
                    'days_since_hve': r.get('days_since_hve'),
                    'entry_price': r.get('entry_price'),
                    'trigger_price': r.get('trigger_price'),
                    'invalid_rule': r.get('invalid_rule'),
                    'vr20': r.get('vr20'),
                    'current_volume_ratio': r.get('current_volume_ratio'),
                    'drawdown_from_hve': r.get('drawdown_from_hve'),
                    'tradability_flag': r.get('tradability_flag'),
                    'market_regime': r.get('market_regime'),
                },
            })
    if not rows:
        print('[HVE] 当日无可执行 BUY（signal=BUY 且 executable），跳过落库')
        return 0
    try:
        if PICK_DB_PATH:
            os.makedirs(os.path.dirname(PICK_DB_PATH), exist_ok=True)
        n = record_picks(STRATEGY_ID, 'HVE V1 高量事件', rows, pick_date=trade_date)
        print(f'[HVE] stock_pick_db 写入 {n}/{len(rows)} 条 '
              f'(strategy={STRATEGY_ID} pick_date={trade_date})')
        return n
    except Exception as e:
        print(f'[HVE] stock_pick_db 写入失败(不影响推送): {e}')
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description='HVE V1 每日扫描')
    ap.add_argument('--date', default=None, help='交易日 YYYYMMDD（缺省取最近有效交易日）')
    ap.add_argument('--push', action='store_true', help='扫描后邮件推送日报')
    ap.add_argument('--no-save', action='store_true', help='不落盘 JSON/MD（--push 时强制落盘）')
    ap.add_argument('--top', type=int, default=10, help='展示限幅（保留接口，日报默认全量）')
    for attr, key, cast in PARAM_SWITCHES:
        ap.add_argument(f"--{attr.replace('_', '-')}", dest=attr, type=cast, default=None,
                        help=f'§42 参数扰动：覆盖 {key}')
    args = ap.parse_args()

    cfg = load_config()
    applied = _apply_overrides(cfg, args)

    trade_date = args.date
    if not trade_date:
        import stock_cache as sc
        trade_date = sc.get_effective_date()
    print(f'[HVE] 目标交易日: {trade_date}')
    if applied:
        print(f'[HVE] §42 参数扰动: {applied}')

    result = scan_day(trade_date, cfg=cfg)
    if applied:
        result['param_override'] = applied

    if not args.no_save or args.push:
        jp, mp = save(result)
        print(f'[HVE] 已落盘: {jp}')
        print(f'[HVE] 已落盘: {mp}')

    _record_picks_to_db(trade_date, result)

    if args.push:
        from hve_v1.push import push_daily_report
        push_daily_report(trade_date)
    return 0


if __name__ == '__main__':
    sys.exit(main())
