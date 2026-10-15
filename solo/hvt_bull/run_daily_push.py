# -*- coding: utf-8 -*-
"""HVT-BULL 每日盘后定时任务入口（每工作日 17:00）

数据补全 → 全市场扫描 → 选股落库(stock_pick_db) → AI 自然语言复盘 → 邮件推送。
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


# ── 20261014 第一梯队落库质量门（依据 _fe_ret_analysis.py 对 70 条 fe_watch 跟踪记录的分类收益率分析）──
# ① SKIP 剔除：12 条 3日 -4.46%、止损率 83%，引擎硬风控已否决（突破失效），不构成任何买点；
# ② HVT 等级只留 A：A 档 3日 +0.24% vs B -5.35%（止损率69%）、C -6.49%（10日 -20%）；
# ③ 执行分 ≥75：唯一正期望档（3日 +2.82%/胜67%）vs 70~75 -4.62%、<70 -5.22%（止损率88%）。
# 三道全过才落库（宁缺毋滥，与第一梯队定位一致）。离线验证：70 条历史门后保留 11 条
# 3日 +7.24%（胜88%）、止损率 0%、maxDD 2.8%；门外 59 条 3日 -5.02%（胜20%）。
# 报告「★ 第一梯队重点解读」展示口径不变，仅落库/跟踪口径收窄。
FE_GATE_HVT_GRADES = ('A',)
FE_GATE_MIN_EXEC = 75.0


def _fe_gate_rows(pool) -> tuple:
    """第一梯队落库质量门：返回 (通过行, [(淘汰行, 原因)])"""
    kept, dropped = [], []
    for it in pool:
        nda = (it.get('next_day_action') or '')
        st = (it.get('execution_state') or '')
        try:
            exec_s = float(it.get('execution_score') or 0.0)
        except (TypeError, ValueError):
            exec_s = 0.0
        hg = (it.get('hvt_grade') or '').upper()
        if 'SKIP' in nda or 'SKIP' in st:
            dropped.append((it, 'SKIP硬风控已否决（历史止损率83%）'))
        elif hg not in FE_GATE_HVT_GRADES:
            dropped.append((it, f'HVT等级{hg or "?"}非A（历史B/C 3日-5.4%/-6.5%）'))
        elif exec_s < FE_GATE_MIN_EXEC:
            dropped.append((it, f'执行分{exec_s:.1f}<75（历史<75档3日-4.6%~-5.2%）'))
        else:
            kept.append(it)
    return kept, dropped


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

    20261014 落库质量门（_fe_gate_rows）：①剔除 SKIP ②HVT 等级只留 A ③执行分≥75，
    三道全过才落库；报告展示口径不变。落库改 replace=True：质量门使当日池缩小的
    重跑场景，被剔除标的须从 stock_pick 与 pick_tracking 一并清除（含门后为 0 的日期）。
    """
    if record_picks is None or not report:
        return
    try:
        pool = report.get('first_echelon_pool') or []
        if not pool:
            print('[RUN-PUSH] first_echelon_pool 为空（当日无第一梯队），跳过落库')
            return
        kept, dropped = _fe_gate_rows(pool)
        for it, why in dropped:
            print(f"[RUN-PUSH] 第一梯队质量门剔除 {it.get('ts_code')} {it.get('name')} "
                  f"({it.get('next_day_action') or '-'}/{it.get('execution_state') or '-'}/"
                  f"HVT-{it.get('hvt_grade') or '?'}/exec{it.get('execution_score')}): {why}")
        print(f"[RUN-PUSH] 第一梯队质量门: {len(pool)} → {len(kept)} 只（拦下 {len(dropped)} 只）")
        pool = kept
        if PICK_DB_PATH:
            os.makedirs(os.path.dirname(PICK_DB_PATH), exist_ok=True)
        for sid, sname, rows in (
            ('hvt_bull_fe', 'HVT-BULL 第一梯队·可执行档', _executable_rows(pool)),
            ('hvt_bull_fe_watch', 'HVT-BULL 第一梯队·结构观察档', _watch_rows(pool)),
        ):
            # 20261014：replace=True —— 门后池（含空池）完整替换当日记录，旧残留一并清除
            n = record_picks(sid, sname, rows, pick_date=trade_date, replace=True,
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


def _record_right_bottom_to_db(trade_date: str, report: dict):
    """右底低吸（全市场单列）独立落库：strategy_id=hvt_bull_rb

    20261008 用户口径：删除 sli_v2 龙头池/热点主题扩池准入后对全市场扫描，独立成节
    （报告「I. 右底低吸信号」）。该池当日收盘即低吸买点（现价即买区），故 action 统一记
    'RIGHT_BOTTOM'；与前两池 hvt_bull_te（TE 执行池）/ hvt_bull_fe（第一梯队）互不混档。
    stop_price 取结构失效位=左底（前低，收盘跌破即离场）；其余右底结构字段经 record_picks
    自动打包进 indicators（不改表结构）。
    """
    if record_picks is None or not report:
        return
    try:
        pool = report.get('rb_offpool_pool') or []
        if not pool:
            print('[RUN-PUSH] rb_offpool_pool 为空（当日无右底低吸），跳过落库')
            return
        rows = []
        for i, it in enumerate(pool, 1):
            try:
                rise = float(it.get('rb_prior_rise') or 0.0) * 100
                ratio = float(it.get('rb_ratio') or 0.0)
                volr = float(it.get('volr') or 0.0)
                mve = float(it.get('circ_mv') or 0.0) / 10000.0
            except (TypeError, ValueError):
                rise, ratio, volr, mve = 0.0, 0.0, 0.0, 0.0
            rows.append(dict(it, rank_no=i, action='RIGHT_BOTTOM',
                             stop_price=it.get('rb_left_low'),
                             reason=(f"右底低吸：前波涨幅{rise:.1f}%、右底/左底{ratio:.3f}、"
                                     f"缩量{volr:.2f}×20日均量、流通市值{mve:.1f}亿")))
        if PICK_DB_PATH:
            os.makedirs(os.path.dirname(PICK_DB_PATH), exist_ok=True)
        n = record_picks('hvt_bull_rb', 'HVT-BULL 右底低吸（全市场单列）', rows,
                         pick_date=trade_date)
        print(f'[RUN-PUSH] stock_pick_db 写入 {n}/{len(rows)} 条 '
              f'(strategy=hvt_bull_rb pick_date={trade_date})')
    except Exception as e:
        print(f'[RUN-PUSH] hvt_bull_rb 写入失败(不影响推送): {e}')


def _record_midline_hold_to_db(trade_date: str, report: dict):
    """中位低吸（全市场单列）独立落库：strategy_id=hvt_bull_mh

    20261009 用户口径：HVT-BULL 新增「低吸分支」——与右底低吸并列的第二种低吸结构，
    判定复用 W7 的 midline_hold（放量长阳 ≥7% + 其后缩量回踩不破半分位），全市场扫描、
    流通市值≥50亿、且须基于 HVT 入选（近 40 个交易日内存在合格天量事件）。该池当日收盘
    即低吸买点（现价即买区），故 action 统一记 'MIDLINE_HOLD'；与 hvt_bull_te（TE 执行池）/
    hvt_bull_fe（第一梯队）/ hvt_bull_rb（右底低吸）互不混档。
    stop_price 取结构失效位=长阳半分位（收盘跌破即离场）；其余中位低吸字段经 record_picks
    自动打包进 indicators（不改表结构）。
    """
    if record_picks is None or not report:
        return
    try:
        pool = report.get('mh_offpool_pool') or []
        if not pool:
            print('[RUN-PUSH] mh_offpool_pool 为空（当日无中位低吸），跳过落库')
            return
        rows = []
        for i, it in enumerate(pool, 1):
            try:
                up = float(it.get('mid_day_pct') or 0.0)
                volr = float(it.get('volr') or 0.0)
                mve = float(it.get('circ_mv') or 0.0) / 10000.0
            except (TypeError, ValueError):
                up, volr, mve = 0.0, 0.0, 0.0
            rows.append(dict(it, rank_no=i, action='MIDLINE_HOLD',
                             stop_price=it.get('mid_line'),
                             reason=(f"中位低吸：长阳日{it.get('mid_date')} 涨幅{up:.1f}%、"
                                     f"缩量{volr:.2f}×长阳量、流通市值{mve:.1f}亿")))
        if PICK_DB_PATH:
            os.makedirs(os.path.dirname(PICK_DB_PATH), exist_ok=True)
        n = record_picks('hvt_bull_mh', 'HVT-BULL 中位低吸（全市场单列）', rows,
                         pick_date=trade_date)
        print(f'[RUN-PUSH] stock_pick_db 写入 {n}/{len(rows)} 条 '
              f'(strategy=hvt_bull_mh pick_date={trade_date})')
    except Exception as e:
        print(f'[RUN-PUSH] hvt_bull_mh 写入失败(不影响推送): {e}')


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
    _record_right_bottom_to_db(trade_date, report)
    _record_midline_hold_to_db(trade_date, report)
    push_daily_report(trade_date=trade_date)


if __name__ == '__main__':
    main()
