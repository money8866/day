# -*- coding: utf-8 -*-
"""HVE V1 回测入口（§37 / §38 / §39 / §40）

用法：
    python hve_v1/backtest_run.py --start 20220101 --end 20260930
    python hve_v1/backtest_run.py --start 20220101 --end 20260930 --cost-bp 30
    python hve_v1/backtest_run.py --start 20250101 --end 20260930 --limit 300   # 冒烟

说明：
  - 全市场全区间回测极慢（每只股票都要读全历史序列），冒烟请用 --limit/--stride；
  - 成本梯度取 hve_config.json 的 backtest.cost_bp，--cost-bp 只改「重点成本档」
    （用于 economic_edge 判定，默认 30bp）；
  - 不做任何参数寻优（§40/§50）。
"""
import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hve_v1 import load_config                          # noqa: E402
from hve_v1.backtest import GROUPS, run, save, summary_lines  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description='HVE V1 回测（事件驱动信号的历史表现）')
    ap.add_argument('--start', required=True, help='起始交易日 YYYYMMDD')
    ap.add_argument('--end', required=True, help='结束交易日 YYYYMMDD')
    ap.add_argument('--cost-bp', type=float, default=None, help='重点成本档（默认取配置 30）')
    ap.add_argument('--horizon', type=int, default=None, help='摘要展示的档位（默认最大档）')
    ap.add_argument('--group', default=None, choices=list(GROUPS), help='只展示某一 signal_type')
    ap.add_argument('--limit', type=int, default=None, help='只扫前 N 只（冒烟用）')
    ap.add_argument('--stride', type=int, default=1, help='按步长抽样股票（冒烟用）')
    ap.add_argument('--no-save', action='store_true')
    args = ap.parse_args()

    cfg = load_config()
    if args.cost_bp is not None:
        cfg.setdefault('backtest', {})['primary_cost_bp'] = float(args.cost_bp)
    if args.limit or args.stride > 1:
        print(f'[HVE-BT] 冒烟模式：limit={args.limit} stride={args.stride}（结果非全样本）')

    res = run(args.start, args.end, cfg=cfg, limit=args.limit, stride=args.stride)
    print('\n'.join(summary_lines(res, group=args.group, horizon=args.horizon)))
    if not args.no_save:
        p = save(res)
        print(f'[HVE-BT] 已落盘: {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
