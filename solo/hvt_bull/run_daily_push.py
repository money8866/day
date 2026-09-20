# -*- coding: utf-8 -*-
"""HVT-BULL 每日盘后定时任务入口（每工作日 17:00）

数据补全 → 全市场扫描 → 选股落库(stock_pick_db) → AI 自然语言复盘 → 微信推送。
由 Windows 任务计划程序调用，也可手动运行：
    python hvt_bull/run_daily_push.py [--date 20260830]
"""
import os
import sys
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hvt_bull.push import _load_env, push_daily_report

# 选股跟踪库（幂等落库；导入失败不影响主流程）
try:
    from stock_pick_db import DB_PATH as PICK_DB_PATH, record_picks
except Exception:
    PICK_DB_PATH = None
    record_picks = None

# ── 落库档位口径：一个 strategy_id 只记一个档，不混档 ──
# stock_pick_db 以「选股日收盘」为基准跟踪 T+N 收益，落库记录等价于"买入信号"，
# 所以只落 next_day_action=BUY（当日收盘即可直接执行）。BUY_ON_CONFIRM /
# WAIT / WAIT_PULLBACK / NO_CHASE / SKIP 都是当日不可直接执行的档，混进同一
# strategy_id 会让胜率统计失去意义：实测把 BUY_ON_CONFIRM 混在 hvt_bull_te 里，
# 5 日收益从 +1.06%（纯 BUY）被拖到 +0.06%，5 日胜率从 55% 降到 37.5%。
DB_ACTIONS = ('BUY',)


def _filter_rows(pool, actions) -> list:
    """按档位过滤并重排 rank_no"""
    kept = [it for it in pool if (it.get('next_day_action') or '') in actions]
    return [dict(it, rank_no=i + 1) for i, it in enumerate(kept)]


def _executable_rows(pool) -> list:
    """可执行档（next_day_action ∈ DB_ACTIONS，当日收盘即可直接下单）"""
    return _filter_rows(pool, DB_ACTIONS)


def _watch_rows(pool) -> list:
    """结构观察档（可执行档之外的其余档位，具体档位见 action 列）"""
    kept = [it for it in pool if (it.get('next_day_action') or '') not in DB_ACTIONS]
    return [dict(it, rank_no=i + 1) for i, it in enumerate(kept)]


def _ensure_data(trade_date: str):
    """三张普通日线缓存表（daily / daily_basic / adj_factor）当日数据不足时自动补全

    与 data_loader 一致，改用窄表口径；各自缺失时调 stock_cache 的
    daily_market / daily_basic_market / adj_factor_market（缓存优先+API 兜底回写）。
    """
    import stock_cache as sc
    min_cnt = sc.UDC_MARKET_MIN_COUNT
    checks = {
        'daily_cache':       (sc.get_daily_by_date_count,       sc.daily_market),
        'daily_basic_cache': (sc.get_daily_basic_valuation_count, sc.daily_basic_market),
        'adj_factor_cache':  (sc.get_adj_factor_by_date_count,  sc.adj_factor_market),
    }
    for name, (count_fn, fill_fn) in checks.items():
        try:
            cnt = count_fn(trade_date)
        except Exception:
            cnt = 0
        if cnt >= min_cnt:
            print(f'[RUN-PUSH] {name} {trade_date} 数据已就绪（{cnt}条），无需补全')
            continue
        print(f'[RUN-PUSH] {name} {trade_date} 数据不足（{cnt}条），开始补全...')
        try:
            fill_fn(trade_date)
        except Exception as e:
            print(f'[RUN-PUSH] {name} 数据补全异常: {e}')


def _record_picks_to_db(trade_date: str, report: dict):
    """te_buy_pool 接入 stock_pick_db 选股跟踪（幂等，失败不阻塞推送）

    按记录表标准列映射: close=current_close(跟踪收益基准), stop_price/target1
    供 STOP_HIT/TARGET_HIT 命中判定; 其余策略私有字段（reentry_streak/
    entry_trigger/buy_zone/invalidation…）自动进 indicators JSON 列。

    落库口径 = te_buy_pool_kept（R1/R2 再入规则剔除后的当日显示池）中
    **仅 next_day_action=BUY** 的可直接执行档，与报告「① 次日买入候选」
    里能直接下单的部分一致；BUY_ON_CONFIRM 等不可执行档不落库。
    te_buy_pool（剔除前）仅作 streak 递推源、不落库。
    """
    if record_picks is None or not report:
        return
    try:
        pool = report.get('te_buy_pool_kept')
        if pool is None:
            pool = report.get('te_buy_pool') or []
        if not pool:
            print('[RUN-PUSH] te_buy_pool_kept 为空（当日无剔除后保留候选），跳过选股落库')
            return
        rows = _executable_rows(pool)
        if not rows:
            print(f'[RUN-PUSH] te_buy_pool_kept {len(pool)} 条均非可执行档 '
                  f'{DB_ACTIONS}，跳过选股落库（不混档）')
            return
        if len(rows) < len(pool):
            print(f'[RUN-PUSH] te 落库档位过滤 {len(pool)} → {len(rows)} 条 '
                  f'（剔除 {len(pool) - len(rows)} 条不可执行档）')
        if PICK_DB_PATH:
            os.makedirs(os.path.dirname(PICK_DB_PATH), exist_ok=True)
        n = record_picks('hvt_bull_te', 'HVT-BULL TE 执行池', rows, pick_date=trade_date,
                         field_map={'next_day_action': 'action',
                                    'te_decision_point': 'signal',
                                    'execution_score': 'score',
                                    'current_close': 'close',
                                    'stop_loss': 'stop_price',
                                    'target1': 'target_price',
                                    'sector_name': 'industry',
                                    'execution_reason': 'reason'})
        print(f'[RUN-PUSH] stock_pick_db 写入 {n}/{len(rows)} 条 '
              f'(strategy=hvt_bull_te pick_date={trade_date})')
    except Exception as e:
        print(f'[RUN-PUSH] stock_pick_db 写入失败(不影响推送): {e}')


def _record_first_echelon_to_db(trade_date: str, report: dict):
    """第一梯队（PRIMARY_BUY 结构层）单独跟踪：按档位拆成两个 strategy_id，互不混档

    - hvt_bull_fe       第一梯队·可执行档：next_day_action=BUY（当日收盘即可下单）
    - hvt_bull_fe_watch 第一梯队·结构观察档：NO_CHASE / WAIT / WAIT_PULLBACK /
      BUY_ON_CONFIRM 等当日不可直接执行的档

    拆分原因：第一梯队池天然同时含"可执行"和"结构达标但当日不可执行"两类，
    合成一个 strategy_id 会让 T+N 胜率被两档对冲（实测其 T+1 正收益全部来自
    NO_CHASE/WAIT 档，唯一可执行的 BUY_ON_CONFIRM 只有 +1.31%）。两个 id 各自
    档位同质，与 hvt_bull_te（执行池）也各自独立；档位值仍保留在 action 列。
    结构成立与否看报告「★ 第一梯队重点解读」。
    """
    if record_picks is None or not report:
        return
    try:
        pool = report.get('first_echelon_pool') or []
        if not pool:
            print('[RUN-PUSH] first_echelon_pool 为空（当日无第一梯队），跳过落库')
            return
        if PICK_DB_PATH:
            os.makedirs(os.path.dirname(PICK_DB_PATH), exist_ok=True)
        for sid, sname, rows in (
            ('hvt_bull_fe', 'HVT-BULL 第一梯队·可执行档', _executable_rows(pool)),
            ('hvt_bull_fe_watch', 'HVT-BULL 第一梯队·结构观察档', _watch_rows(pool)),
        ):
            if not rows:
                print(f'[RUN-PUSH] {sid} 当日无可落库档位，跳过')
                continue
            n = record_picks(sid, sname, rows, pick_date=trade_date,
                             field_map={'next_day_action': 'action',
                                        'te_decision_point': 'signal',
                                        'execution_score': 'score',
                                        'current_close': 'close',
                                        'stop_loss': 'stop_price',
                                        'target1': 'target_price',
                                        'sector_name': 'industry'})
            print(f'[RUN-PUSH] stock_pick_db 写入 {n}/{len(rows)} 条 '
                  f'(strategy={sid} pick_date={trade_date})')
    except Exception as e:
        print(f'[RUN-PUSH] hvt_bull_fe 写入失败(不影响推送): {e}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    _load_env()
    import stock_cache as sc
    trade_date = args.date or sc.get_effective_date()
    print(f'[RUN-PUSH] 目标交易日: {trade_date}')
    _ensure_data(trade_date)
    from hvt_bull.daily import run_daily
    report = run_daily(trade_date=trade_date)
    _record_picks_to_db(trade_date, report)
    _record_first_echelon_to_db(trade_date, report)
    push_daily_report(trade_date=trade_date)


if __name__ == '__main__':
    main()
