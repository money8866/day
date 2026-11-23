"""命令行入口：每日运行 / 历史重算 / 回测 / 报告重生成 / 定时调度。

用法
    python main.py --mode daily
    python main.py --mode daily --date 20261120
    python main.py --mode backtest
    python main.py --mode report --date 20261120
    python main.py --mode schedule
    python main.py --mode init-account
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import indicators
import portfolio
import report as report_mod
import risk_manager
import strategy
from database import Database
from data_provider import (DataError, DataNotReady, DataPermissionError,
                           DataQualityError, TushareProvider, _fmt_date)
from settings import (VERSION, get_logger, get_tushare_token, load_config,
                      now_cn, resolve_path, setup_logging)

LOG = get_logger()

EXIT_OK = 0
EXIT_DATA_NOT_READY = 2
EXIT_ERROR = 3
EXIT_SKIPPED = 0


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _db(cfg: dict) -> Database:
    path = resolve_path(cfg, cfg.get("project", {}).get("db_path", "data/etf159531.db"))
    return Database(path)


def _row_to_float(row: pd.Series, key: str) -> Optional[float]:
    v = row.get(key)
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return float(v)


def _index_features(index_df: pd.DataFrame) -> pd.DataFrame:
    """指数特征（仅用于熔断恢复的门控条件）。"""
    if index_df is None or index_df.empty:
        return pd.DataFrame()
    d = index_df.sort_values("trade_date").reset_index(drop=True).copy()
    d["index_close"] = d["close"].astype(float)
    d["index_ma20"] = indicators.sma(d["index_close"], 20)
    d["index_ma60"] = indicators.sma(d["index_close"], 60)
    d["index_vol20"] = indicators.annualized_vol(d["index_close"], 20)
    return d[["trade_date", "index_close", "index_ma20", "index_ma60", "index_vol20"]]


def _extend_snapshot(snapshot: pd.DataFrame, target_date: str) -> tuple[pd.DataFrame, bool]:
    """若账户快照早于目标交易日，按「持仓不变」假设顺延至目标日（并提示）。"""
    if snapshot.empty:
        return snapshot, False
    last = str(snapshot["trade_date"].max())
    if last >= target_date:
        return snapshot, False
    row = snapshot.sort_values("trade_date").iloc[-1].to_dict()
    row["trade_date"] = target_date
    out = pd.concat([snapshot, pd.DataFrame([row])], ignore_index=True)
    return out, True


# --------------------------------------------------------------------------- #
# 核心：单个交易日的决策
# --------------------------------------------------------------------------- #
def build_decision(features: pd.DataFrame, market: pd.DataFrame, events: dict,
                   cfg: dict, trade_date: str, next_trade_date: Optional[str],
                   account: Optional[portfolio.AccountData],
                   nav_df: Optional[pd.DataFrame], account_warn: list[str],
                   quality: dict, status: str = "OK",
                   executable: bool = True) -> dict:
    """组装每日决策（纯函数，便于测试与复核）。"""
    idx = len(features) - 1
    # market 是 features 的超集（含趋势/超卖/危机/波动率上限列）
    row = market.iloc[idx]
    max_pos = float(cfg.get("risk", {}).get("max_position", 0.65))

    # ---- 趋势 ----
    trend = strategy.trend_result(features, idx, cfg)
    trend_dict = trend.as_dict()
    trend_dict["dirs_str"] = trend_dict["dirs"] and " / ".join(
        f"{k}:{v}" for k, v in trend_dict["dirs"].items()
    )

    # ---- 超卖 ----
    os_weight = float(row["os_weight"])
    os_state = str(row["os_state"])
    ev_obj = strategy.event_at_date(events["_oversold_objects"], trade_date)
    cooldown_until = None
    if ev_obj is not None and ev_obj.exit_idx is not None:
        cd = int(cfg.get("oversold", {}).get("cooldown_days", 20))
        try:
            pos = features.index[features["trade_date"] == ev_obj.exit_date][0]
            j = features.index.get_loc(pos) + cd
            if j < len(features):
                cooldown_until = str(features.iloc[j]["trade_date"])
        except Exception:
            cooldown_until = None
    oversold_dict = {
        "state": os_state,
        "event_id": ev_obj.event_id if ev_obj else None,
        "trigger_date": ev_obj.trigger_date if ev_obj else None,
        "trigger_price": ev_obj.trigger_price if ev_obj else None,
        "lowest_close": (float(row["os_lowest"]) if not np.isnan(row["os_lowest"]) else None),
        "activated": os_weight > 0,
        "weight": os_weight,
        "exit_reason": ev_obj.exit_reason if (ev_obj and ev_obj.exit_reason) else None,
        "cooldown_until": cooldown_until,
    }

    # ---- 危机 ----
    crisis = strategy.crisis_state_at(features, market, idx, cfg)
    crisis_dict = crisis.as_dict()

    # ---- 波动率上限 ----
    v_cap = float(row["vol_cap"])

    # ---- 账户层 ----
    index_feat = _index_features(events.get("_index_df")) if events.get("_index_df") is not None else None
    if index_feat is not None and not index_feat.empty:
        trig = market[["trade_date", "crisis_trigger"]].copy()
        index_feat = index_feat.merge(trig, on="trade_date", how="left")
        index_feat["crisis_trigger"] = index_feat["crisis_trigger"].fillna(False).astype(bool)

    if nav_df is not None and not nav_df.empty:
        risk_df = risk_manager.account_risk(nav_df, cfg, index_feat)
        limits = risk_manager.latest_limits(risk_df, cfg)
        account_mode = True
    else:
        risk_df = pd.DataFrame()
        limits = risk_manager.latest_limits(pd.DataFrame(), cfg)
        account_mode = False

    # ---- 最终仓位 ----
    breaker_cap = limits.breaker_cap
    final, binding = strategy.binding_constraint(
        trend.base_weight, os_weight, v_cap, crisis.cap,
        limits.dd_cap, max_pos, breaker_cap,
    )
    caps = {
        "raw_weight": float(trend.base_weight + os_weight),
        "vol_cap": v_cap,
        "crisis_cap": float(crisis.cap),
        "dd_cap": float(limits.dd_cap),
        "breaker_cap": breaker_cap,
        "max_position": max_pos,
        "final_target": float(final),
        "binding": binding,
    }

    # ---- 账户状态与交易建议 ----
    acc_dict = {
        "mode": "account" if account_mode else "market_only",
        "drawdown": limits.drawdown,
        "dd_cap": limits.dd_cap,
        "account_cap": limits.account_cap,
        "breaker_stage": limits.breaker_stage,
        "breaker_stage_cn": risk_manager.STAGE_CN.get(limits.breaker_stage, limits.breaker_stage),
        "note": limits.note,
        "warnings": account_warn,
    }
    trade_dict: dict = {
        "action": "N/A",
        "delta_weight": None,
        "exec_shares": 0,
        "est_amount": 0.0,
        "ref_price": float(row["close"]),
        "exec_date": next_trade_date,
        "note": "仅市场信号模式，需账户数据才能计算交易份额",
        "rebalance_reason": "",
    }

    if account_mode:
        state = portfolio.latest_account_state(account, float(row["close"]))
        acc_dict.update(state)
        acc_dict["drawdown"] = limits.drawdown
        plan = portfolio.plan_trade(final, state, float(row["close"]), cfg)
        trade_dict.update(plan.as_dict())
        trade_dict["delta_weight"] = float(final - state["current_weight"])
        trade_dict["exec_date"] = next_trade_date
        trade_dict["note"] = plan.note

        # 周度再平衡 + 5pp 阈值；降风险指令优先
        is_week_end = portfolio.is_week_last(trade_date, next_trade_date)
        force = bool(final < state["current_weight"] - 1e-9 and
                     (crisis.active or limits.breaker_stage != "NORMAL"))
        ok, why = portfolio.should_rebalance(final, state["current_weight"], cfg, force=force)
        weekly = bool(cfg.get("trading", {}).get("weekly_rebalance", True))
        if weekly and not is_week_end and not force:
            ok, why = False, "非本周最后一个交易日，按周度再平衡规则今日不交易"
        trade_dict["rebalance_reason"] = why
        if not ok:
            trade_dict["action"] = "HOLD"
            trade_dict["exec_shares"] = 0
            trade_dict["est_amount"] = 0.0
            trade_dict["note"] = why
        trade_dict["is_week_end"] = is_week_end
        trade_dict["force"] = force

    # ---- 信号解释 ----
    reasons = [
        f"趋势层：{trend.change_reason}；当前 {trend.level} 条均线确认在上"
        f"（{', '.join(f'{k}={v}' for k, v in trend.dirs.items())}），基础仓位 {trend.base_weight:.0%}。",
        f"波动率层：vol20={_row_to_float(row, 'vol20') or 0:.2%}，"
        f"vol60={_row_to_float(row, 'vol60') or 0:.2%}，有效波动率 "
        f"{_row_to_float(row, 'effective_vol') or 0:.2%}，"
        f"目标波动率 {float(cfg.get('volatility', {}).get('target', 0.12)):.0%}，"
        f"对应上限 {v_cap:.0%}。",
        f"超卖层：状态 {os_state}，战术仓位 {os_weight:.0%}"
        + (f"（事件 {ev_obj.event_id}）" if ev_obj else "（无活动事件）") + "。",
        f"危机层：{crisis.reason}；危机上限 {crisis.cap:.0%}。",
    ]
    if account_mode:
        reasons.append(
            f"账户层：单位净值口径回撤 {limits.drawdown:.2%}，分层上限 {limits.dd_cap:.0%}，"
            f"熔断状态「{acc_dict['breaker_stage_cn']}」。"
        )
    else:
        reasons.append("账户层：未提供账户快照，回撤上限不生效，本报告仅为市场信号。")
    reasons.append(
        f"取最严格上限后，最终仓位 {final:.0%}，由「{binding}」决定"
        f"（策略组合 {trend.base_weight + os_weight:.0%} / 波动率 {v_cap:.0%} / "
        f"危机 {crisis.cap:.0%} / 账户 {limits.dd_cap:.0%} / 系统 {max_pos:.0%}）。"
    )
    if account_mode:
        reasons.append(
            f"交易建议：{trade_dict['action']}，目标 {final:.0%} vs 当前 "
            f"{acc_dict.get('current_weight', 0):.0%}；{trade_dict['rebalance_reason']}。"
        )

    # ---- 风险提示 ----
    disclaimers = [
        "盘后生成的目标仓位仅作为下一交易日指令，不以信号日收盘价成交；实际成交价不同需重算份额与现金约束。",
        "模型未连接券商，不会自动下单；所有交易需人工确认。",
        "历史回测与回撤控制不构成未来收益保证；极端行情下回撤可能突破 20%。",
        "ETF 存在折溢价、流动性冲击与滑点，实际成交可能劣于估算。",
    ]
    if quality:
        if quality.get("warnings"):
            disclaimers.append("数据提示：" + "；".join(quality["warnings"][:4]))
        if quality.get("missing_trade_days"):
            disclaimers.append(
                f"行情相对交易日历缺少 {len(quality['missing_trade_days'])} 个交易日，"
                "可能为停牌或数据延迟，指标按实际存在的交易日计算。"
            )
    if account_warn:
        disclaimers.append("账户数据提示：" + "；".join(account_warn))
    if account_mode:
        disclaimers.append(
            "账户净值为按快照 + 未复权收盘价估算，未包含未登记资产；出入金已按外部现金流剔除。"
        )
    else:
        disclaimers.append("未提供账户数据：无法计算真实账户回撤，也未给出账户风控通过结论。")

    return {
        "meta": {
            "version": VERSION,
            "trade_date": trade_date,
            "data_date": str(row["trade_date"]),
            "next_trade_date": next_trade_date,
            "generated_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "executable": executable,
        },
        "market": {
            "close": _row_to_float(row, "close"),
            "pct_chg": _row_to_float(row, "pct_chg") / 100.0 if _row_to_float(row, "pct_chg") is not None else None,
            "ma5": _row_to_float(row, "ma5"),
            "ma10": _row_to_float(row, "ma10"),
            "ma20": _row_to_float(row, "ma20"),
            "ma60": _row_to_float(row, "ma60"),
            "ma120": _row_to_float(row, "ma120"),
            "ma250": _row_to_float(row, "ma250"),
            "rsi6": _row_to_float(row, "rsi6"),
            "rsi14": _row_to_float(row, "rsi14"),
            "vol20": _row_to_float(row, "vol20"),
            "vol60": _row_to_float(row, "vol60"),
            "effective_vol": _row_to_float(row, "effective_vol"),
            "ret20": _row_to_float(row, "ret20"),
            "ret60": _row_to_float(row, "ret60"),
            "ret120": _row_to_float(row, "ret120"),
        },
        "trend": trend_dict,
        "oversold": oversold_dict,
        "crisis": crisis_dict,
        "caps": caps,
        "account": acc_dict,
        "trade": trade_dict,
        "reasons": reasons,
        "disclaimers": disclaimers,
        "quality": quality,
    }


# --------------------------------------------------------------------------- #
# 每日运行
# --------------------------------------------------------------------------- #
def _resolve_target_date(provider: TushareProvider, date: Optional[str]) -> tuple[Optional[str], str]:
    """返回 (目标交易日, 说明)。非交易日返回 (None, 原因)。"""
    today = now_cn().strftime("%Y%m%d")
    d = _fmt_date(date) if date else today
    if not provider.is_trade_date(d):
        return None, f"{d} 不是 A 股交易日"
    return d, ""


def _load_account_state(cfg: dict, trade_date: str, features: pd.DataFrame):
    """返回 (nav_df, account, warnings)。account 内的 snapshot 已顺延到目标交易日。"""
    account = portfolio.load_account(cfg)
    if account is None:
        return None, None, []
    warns = list(account.warnings)
    snap, extended = _extend_snapshot(account.snapshot, trade_date)
    if extended:
        warns.append(f"账户快照最后日期为 {account.snapshot['trade_date'].max()}，"
                     f"按持仓不变假设顺延至 {trade_date} 估值")
    warns.extend(portfolio.validate_trades_consistency(account))
    price = features[["trade_date", "close"]]
    nav_df = risk_manager.build_account_nav(snap, price, account.flows)
    account = portfolio.AccountData(snapshot=snap, flows=account.flows,
                                    trades=account.trades, source_dir=account.source_dir)
    return nav_df, account, warns


def run_daily(cfg: dict, date: Optional[str] = None, allow_fetch: bool = True,
              provider: Optional[TushareProvider] = None, db: Optional[Database] = None,
              save: bool = True) -> tuple[int, Optional[dict]]:
    """执行一次每日决策。返回 (退出码, 决策字典)。"""
    logger = setup_logging(cfg)
    db = db or _db(cfg)
    provider = provider or TushareProvider(cfg, db)
    run_at = now_cn().strftime("%Y-%m-%d %H:%M:%S")

    try:
        provider.ensure_calendar()
    except Exception as exc:
        logger.error("交易日历更新失败：%s", exc)
        db.save_run(run_at, "daily", date, None, "ERROR", VERSION, f"日历失败：{exc}")
        return EXIT_ERROR, None

    target, msg = _resolve_target_date(provider, date)
    if target is None:
        logger.info("跳过：%s", msg)
        db.save_run(run_at, "daily", _fmt_date(date), None, "SKIPPED_NON_TRADING", VERSION, msg)
        print(f"[SKIPPED] {msg}，未生成新信号")
        return EXIT_SKIPPED, None

    next_td = provider.next_trade_date(target)
    logger.info("目标交易日 %s，下一交易日 %s", target, next_td)

    # ---- 数据同步（含重试与就绪判断） ----
    ready = False
    ready_msg = ""
    if allow_fetch:
        max_retry = int(cfg.get("data", {}).get("max_retry", 3))
        wait = float(cfg.get("data", {}).get("retry_backoff_sec", 15))
        factor = float(cfg.get("data", {}).get("retry_backoff_factor", 2))
        for attempt in range(1, max_retry + 1):
            try:
                provider.verify_index_code()
                provider.update_etf(end_date=target, full=(db.latest_etf_date() is None))
                try:
                    provider.sync_index(end_date=target)
                except DataError as exc:
                    logger.warning("指数同步失败（不影响 ETF 信号，但熔断恢复门控将不可用）：%s", exc)
                ready, ready_msg = provider.check_ready(target)
            except DataPermissionError:
                raise
            except (DataNotReady, DataError) as exc:
                ready, ready_msg = False, str(exc)
                logger.warning("第 %d/%d 次数据检查未就绪：%s", attempt, max_retry, exc)
            if ready:
                break
            if attempt < max_retry:
                time.sleep(wait)
                wait *= factor
    else:
        ready, ready_msg = provider.check_ready(target)

    if not ready:
        logger.error("DATA_NOT_READY：%s", ready_msg)
        # 使用最后有效数据生成「仅供展示」报告，不写入 signals
        last_date = db.latest_etf_date()
        ctx = None
        if last_date:
            ctx = _build_from_db(cfg, db, provider, last_date, target,
                                 status="DATA_NOT_READY", executable=False,
                                 quality={"ok": False, "errors": [ready_msg],
                                          "warnings": ["目标交易日数据未就绪，展示最近有效数据"]})
        db.save_run(run_at, "daily", target, last_date, "DATA_NOT_READY", VERSION, ready_msg)
        print(f"[DATA_NOT_READY] {ready_msg}")
        return EXIT_DATA_NOT_READY, ctx

    # ---- 构建决策 ----
    try:
        quality = _validate(cfg, db, provider, target)
        if not quality["ok"]:
            raise DataQualityError("；".join(quality["errors"]))
        ctx = _build_from_db(cfg, db, provider, target, target, quality=quality)
    except DataQualityError as exc:
        logger.error("数据质量校验失败：%s", exc)
        db.save_run(run_at, "daily", target, db.latest_etf_date(), "ERROR", VERSION, str(exc))
        print(f"[ERROR] 数据质量校验失败：{exc}")
        return EXIT_ERROR, None
    except Exception as exc:
        logger.exception("每日决策失败")
        db.save_run(run_at, "daily", target, db.latest_etf_date(), "ERROR", VERSION, str(exc))
        return EXIT_ERROR, None

    # ---- 落库 + 报告 ----
    paths = report_mod.save_report(ctx, cfg, ctx.get("_features"))
    ctx["_paths"] = paths
    if save:
        payload = {k: v for k, v in ctx.items() if not k.startswith("_")}
        db.upsert_signal(target, ctx["meta"]["generated_at"], ctx["meta"]["executable"],
                         ctx["caps"]["final_target"], payload)
        db.replace_oversold_events(ctx.get("_oversold_events", []))
        db.replace_crisis_events(ctx.get("_crisis_events", []))
    db.save_run(run_at, "daily", target, ctx["meta"]["data_date"], "OK", VERSION,
                f"最终仓位 {ctx['caps']['final_target']:.2%}")

    _print_summary(ctx)
    logger.info("报告已生成：%s", paths)
    return EXIT_OK, ctx


def _validate(cfg: dict, db: Database, provider: TushareProvider, target: str) -> dict:
    etf = db.load_etf_daily(end_date=target)
    quality = provider.validate(etf, target)
    return quality.as_dict() if hasattr(quality, "as_dict") else quality


def _build_from_db(cfg: dict, db: Database, provider: TushareProvider,
                   data_date: str, target: str, status: str = "OK",
                   executable: bool = True, quality: Optional[dict] = None) -> dict:
    """从库内数据计算一次决策（数据日期 = data_date，报告日期 = target）。"""
    etf = db.load_etf_daily(end_date=data_date)
    if etf.empty:
        raise DataNotReady("库内无 ETF 数据")
    features = indicators.compute_features(etf, cfg)
    market, events = strategy.compute_market(features, cfg)

    index_df = db.load_index_daily(end_date=data_date)
    events["_index_df"] = index_df
    nav_df, account, warns = _load_account_state(cfg, data_date, features)

    min_hist = int(cfg.get("data", {}).get("min_history_days", 260))
    if len(features) < min_hist:
        warns = warns + [f"历史样本仅 {len(features)} 个交易日，少于 {min_hist}，指标可能不稳定"]
        executable = False
        status = "INSUFFICIENT_HISTORY" if status == "OK" else status

    ctx = build_decision(
        features, market, events, cfg,
        trade_date=target,
        next_trade_date=provider.next_trade_date(data_date),
        account=account,
        nav_df=nav_df,
        account_warn=warns,
        quality=quality or {},
        status=status,
        executable=executable,
    )
    ctx["meta"]["data_date"] = str(features.iloc[-1]["trade_date"])
    ctx["_features"] = features
    ctx["_oversold_events"] = events.get("oversold", [])
    ctx["_crisis_events"] = events.get("crisis", [])
    return ctx


def _print_summary(ctx: dict) -> None:
    caps = ctx["caps"]
    print("=" * 68)
    print(f"159531 每日动态仓位报告  交易日 {ctx['meta']['trade_date']}")
    print("-" * 68)
    print(f"收盘价 {ctx['market']['close']:.3f}   趋势 {ctx['trend']['trend_level']} 条   "
          f"超卖 {ctx['oversold']['state']}   危机 {'是' if ctx['crisis']['active'] else '否'}")
    print(f"策略组合 {caps['raw_weight']:.0%} | 波动率上限 {caps['vol_cap']:.0%} | "
          f"危机上限 {caps['crisis_cap']:.0%} | 账户上限 {caps['dd_cap']:.0%}")
    print(f"最终目标仓位 {caps['final_target']:.0%}（由「{caps['binding']}」决定）")
    if ctx["account"]["mode"] == "account":
        tr = ctx["trade"]
        print(f"当前仓位 {ctx['account']['current_weight']:.1%} | 建议 {tr['action']} "
              f"{tr['exec_shares']:+.0f} 份 | 最早执行 {tr['exec_date']}")
    else:
        print("仅市场信号模式：未提供账户数据，不计算交易份额与账户回撤")
    print("=" * 68)


# --------------------------------------------------------------------------- #
# 其他模式
# --------------------------------------------------------------------------- #
def run_report(cfg: dict, date: str) -> int:
    db = _db(cfg)
    d = _fmt_date(date)
    sig = db.load_signal(d)
    if sig is None:
        print(f"[ERROR] 数据库中没有 {d} 的信号记录，请先运行 --mode daily --date {d}")
        return EXIT_ERROR
    payload = sig["payload"]
    payload.pop("_paths", None)
    payload.pop("_features", None)
    paths = report_mod.save_report(payload, cfg, None)
    print(f"已重新生成报告：{paths}")
    return EXIT_OK


def init_account(cfg: dict) -> int:
    created = portfolio.ensure_templates(cfg)
    base = resolve_path(cfg, cfg.get("account", {}).get("dir", "account"))
    if created:
        for p in created:
            print(f"已创建模板：{p}")
    else:
        print(f"模板已存在：{base}")
    print("请复制 template_snapshot.csv -> snapshot.csv 并填入真实数据，程序才会进入账户模式。")
    return EXIT_OK


def backtest_cmd(cfg: dict, start: Optional[str], end: Optional[str],
                 allow_fetch: bool) -> int:
    import backtest as bt

    db = _db(cfg)
    provider = TushareProvider(cfg, db)
    if allow_fetch:
        provider.ensure_calendar()
        try:
            provider.verify_index_code()
        except DataError as exc:
            LOG.warning("指数校验失败：%s", exc)
        provider.update_etf(end_date=end or now_cn().strftime("%Y%m%d"))
        try:
            provider.sync_index(end_date=end or now_cn().strftime("%Y%m%d"))
        except DataError as exc:
            LOG.warning("指数同步失败：%s", exc)

    etf = db.load_etf_daily(end_date=end)
    if etf.empty:
        print("[ERROR] 库内无 ETF 数据，无法回测")
        return EXIT_ERROR
    index_df = db.load_index_daily(end_date=end)
    result = bt.run_all(cfg, etf, index_df, start=start, end=end)
    print(result["summary_text"])
    return EXIT_OK


def run_schedule(cfg: dict) -> int:
    import scheduler

    scheduler.start(cfg)
    return EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="159531 每日盘后动态仓位管理系统")
    parser.add_argument("--mode", required=True,
                        choices=["daily", "backtest", "report", "schedule", "init-account"])
    parser.add_argument("--date", help="YYYYMMDD，指定历史日期重算/重生成")
    parser.add_argument("--start", help="回测开始日期 YYYYMMDD")
    parser.add_argument("--end", help="回测结束日期 YYYYMMDD")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--no-fetch", action="store_true", help="不访问 TuShare，只用本地库")
    parser.add_argument("--quiet", action="store_true", help="减少日志")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg)

    try:
        if args.mode == "daily":
            code, _ = run_daily(cfg, date=args.date, allow_fetch=not args.no_fetch)
            return code
        if args.mode == "report":
            if not args.date:
                print("[ERROR] --mode report 需要 --date YYYYMMDD")
                return EXIT_ERROR
            return run_report(cfg, args.date)
        if args.mode == "init-account":
            return init_account(cfg)
        if args.mode == "backtest":
            return backtest_cmd(cfg, args.start, args.end, allow_fetch=not args.no_fetch)
        if args.mode == "schedule":
            return run_schedule(cfg)
        return EXIT_ERROR
    except DataPermissionError as exc:
        print(f"[ERROR] TuShare 权限/Token 问题：{exc}")
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001
        LOG.exception("运行失败")
        print(f"[ERROR] {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
