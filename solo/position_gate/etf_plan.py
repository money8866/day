# -*- coding: utf-8 -*-
"""CSI2000 ETF execution plan.

把 Position Gate 的风险暴露百分比翻译成 ETF 次日执行计划。
原则：Gate 决定总风险暴露；ETF 执行层只做份额换算、波动止损和调仓纪律。
"""
import glob
import json
import os
from typing import Optional

import numpy as np
import pandas as pd

from position_gate import config as C
from position_gate.data import GateData
from position_gate.regime import quantize_position


def _norm_code(code: str) -> str:
    code = str(code or '').strip().upper()
    if '.' in code:
        return code
    if code.startswith(('15', '16', '18')):
        return f'{code}.SZ'
    if code.startswith(('5', '6')):
        return f'{code}.SH'
    return code


def _safe_code(code: str) -> str:
    return _norm_code(code).replace('.', '_')


def load_etf_ohlc(etf_code: str, date: str = None) -> tuple[pd.DataFrame, str]:
    """从本地 ETF CSV 缓存读取 OHLC；没有 ETF 缓存时回退到中证2000指数代理。"""
    code = _norm_code(etf_code)
    safe = _safe_code(code)
    fund_dir = os.path.join(os.path.dirname(C.BASE_DIR), '..', 'cache_daily', 'etf_fund')
    fund_dir = os.path.abspath(fund_dir)

    frames = []
    daily_path = os.path.join(fund_dir, f'{safe}_fund_daily.csv')
    if os.path.exists(daily_path):
        frames.append(pd.read_csv(daily_path))

    pat = os.path.join(fund_dir, f'{code}_*.csv')
    files = sorted(glob.glob(pat))
    if date:
        files = [p for p in files if os.path.basename(p).endswith(f'_{date}.csv')] or files
    if files:
        frames.append(pd.read_csv(files[-1]))

    if frames:
        df = pd.concat(frames, ignore_index=True)
        need = ['trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
        keep = [c for c in need if c in df.columns]
        df = df[keep].copy()
        df['trade_date'] = df['trade_date'].astype(str)
        for c in ['open', 'high', 'low', 'close', 'vol', 'amount']:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['close']).sort_values('trade_date')
        df = df.drop_duplicates('trade_date', keep='last').reset_index(drop=True)
        if date:
            df = df[df['trade_date'] <= str(date)]
        if len(df):
            return df, 'ETF_CACHE'

    idx = GateData().index_df(C.CORE_INDEX)
    idx = idx[['trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']].copy()
    if date:
        idx = idx[idx['trade_date'].astype(str) <= str(date)]
    return idx.sort_values('trade_date').reset_index(drop=True), 'INDEX_PROXY'


def _atr(df: pd.DataFrame, n: int = None) -> float:
    n = int(n or C.ATR_WINDOW)
    h = df['high'].astype(float)
    l = df['low'].astype(float)
    c = df['close'].astype(float)
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    val = tr.rolling(n, min_periods=max(3, n // 2)).mean().iloc[-1]
    return float(val) if np.isfinite(val) else float((h.iloc[-1] - l.iloc[-1]))


def _action(delta_pct: float, gate_row: pd.Series) -> str:
    if abs(delta_pct) < C.MIN_REBALANCE_PCT:
        return 'HOLD'
    if delta_pct > 0:
        if int(gate_row.get('riskoff_count', 0)) > 0:
            return 'WAIT_CONFIRM'
        return 'BUY'
    return 'SELL'


def build_plan(gate: pd.DataFrame, date: str = None, etf_code: str = None,
               account_size: Optional[float] = None,
               current_shares: Optional[int] = None,
               current_position_pct: Optional[float] = None) -> dict:
    """生成单日 ETF 仓位与次日操作计划。"""
    date = str(date or gate.index[-1])
    if date not in set(map(str, gate.index)):
        date = str(gate.index[-1])
    row = gate.loc[date]

    etf_code = _norm_code(etf_code or C.DEFAULT_ETF_CODE)
    ohlc, source = load_etf_ohlc(etf_code, date=date)
    last = ohlc.iloc[-1]
    close = float(last['close'])
    atr = _atr(ohlc)
    stop = max(0.0, close - C.ATR_MULTIPLIER * atr)
    atr_pct = atr / close * 100 if close > 0 else np.nan

    target_pct = quantize_position(float(row['position']))
    band_min = float(row['position_min'])
    band_max = float(row['position_max'])

    if current_position_pct is None:
        if account_size and current_shares is not None:
            current_position_pct = current_shares * close / float(account_size) * 100
        else:
            current_position_pct = target_pct
    current_position_pct = float(current_position_pct)

    raw_delta_pct = target_pct - current_position_pct
    delta_pct = max(-C.MAX_SINGLE_ADJUST_PCT, min(C.MAX_SINGLE_ADJUST_PCT, raw_delta_pct))
    action = _action(delta_pct, row)
    if action in ('HOLD', 'WAIT_CONFIRM'):
        delta_pct = 0.0

    target_value = None
    current_value = None
    target_shares = None
    trade_shares = None
    trade_lots = None
    if account_size:
        target_value = float(account_size) * target_pct / 100.0
        current_value = float(account_size) * current_position_pct / 100.0
    if account_size and source == 'ETF_CACHE':
        lot = int(C.ETF_LOT_SIZE)
        target_shares = int(target_value / close / lot) * lot
        current_shares = int(current_shares or int(current_value / close / lot) * lot)
        planned_value = float(account_size) * (current_position_pct + delta_pct) / 100.0
        planned_shares = int(planned_value / close / lot) * lot
        trade_shares = planned_shares - current_shares
        trade_lots = int(trade_shares / lot)
        if trade_shares == 0 and action in ('BUY', 'SELL'):
            action = 'HOLD'

    logic_bad = []
    if bool(row.get('INDEX_BREADTH_DIVERGENCE')):
        logic_bad.append('指数上涨但内部宽度背离，不追高加仓')
    if int(row.get('riskoff_count', 0)) >= 2:
        logic_bad.append('Risk-Off>=2，优先降风险')
    if float(row.get('breadth_change_5', 0)) <= -15:
        logic_bad.append('宽度5日快速恶化')
    if float(row.get('close', close)) < float(row.get('ma20', close)):
        logic_bad.append('中证2000收盘跌破MA20')

    return {
        'schema_version': '1.0',
        'date': date,
        'next_trade_date': 'next_open',
        'instrument': {'code': etf_code, 'name': C.DEFAULT_ETF_NAME, 'price_source': source},
        'market_gate': {
            'regime': str(row['regime']),
            'target_position_pct': round(target_pct, 1),
            'allowed_min_pct': round(band_min, 1),
            'allowed_max_pct': round(band_max, 1),
            'full_exposure_eligible': bool(row['full_exposure_eligible']),
            'riskoff_count': int(row['riskoff_count']),
            'reason': str(row['reason']),
        },
        'execution': {
            'action': action,
            'current_position_pct': round(current_position_pct, 1),
            'raw_delta_pct': round(raw_delta_pct, 1),
            'planned_delta_pct': round(delta_pct, 1),
            'min_rebalance_pct': C.MIN_REBALANCE_PCT,
            'max_single_adjust_pct': C.MAX_SINGLE_ADJUST_PCT,
            'account_size': account_size,
            'close': round(close, 4),
            'target_value': round(target_value, 2) if target_value is not None else None,
            'current_value': round(current_value, 2) if current_value is not None else None,
            'target_shares': target_shares,
            'trade_shares': trade_shares,
            'trade_lots': trade_lots,
        },
        'risk_control': {
            'atr_window': C.ATR_WINDOW,
            'atr': round(atr, 4),
            'atr_pct': round(float(atr_pct), 2) if np.isfinite(atr_pct) else None,
            'atr_multiplier': C.ATR_MULTIPLIER,
            'volatility_stop': round(stop, 4),
            'risk_per_trade_pct': C.RISK_PER_TRADE_PCT,
            'logic_invalidations': logic_bad,
            'price_note': ('ETF本地行情可用，份额按ETF收盘价换算'
                           if source == 'ETF_CACHE'
                           else 'ETF本地行情缺失，使用中证2000指数代理；不换算ETF份额/手数'),
        },
        'checklist': _checklist(action, row),
    }


def _checklist(action: str, row: pd.Series) -> list[str]:
    base = [
        '开盘前确认 ETF 溢折价和成交额，避免流动性明显异常时一次性成交',
        '只按计划差额调仓；若开盘跳空超过 1.5*ATR，等待 15~30 分钟重新确认',
        '收盘后用新的 Gate 信号复核，不因盘中情绪临时扩大仓位',
    ]
    if action == 'BUY':
        return ['加仓仅在价格未显著高开且 Risk-Off 未新增时执行'] + base
    if action == 'SELL':
        return ['先降到计划仓位，若盘中跌破波动止损或 Risk-Off 扩大，继续降一档'] + base
    if action == 'WAIT_CONFIRM':
        return ['目标仓位高于当前，但存在风险事件，次日只观察不追高'] + base
    return ['仓位偏差未达到调仓阈值，保持当前仓位'] + base


def format_plan(plan: dict) -> str:
    e = plan['execution']
    g = plan['market_gate']
    r = plan['risk_control']
    lines = [
        '### ETF次日执行计划',
        f'交易对象：{plan["instrument"]["code"]}（{plan["instrument"]["price_source"]}）',
        f'结论：{e["action"]}；当前 {e["current_position_pct"]:.1f}% → 目标 {g["target_position_pct"]:.1f}%',
        f'状态：{g["regime"]}，允许区间 {g["allowed_min_pct"]:.0f}%～{g["allowed_max_pct"]:.0f}%，Risk-Off={g["riskoff_count"]}',
        f'参考收盘价：{e["close"]:.4f}；ATR{r["atr_window"]}={r["atr"]:.4f}（{r["atr_pct"]}%）；波动止损 {r["volatility_stop"]:.4f}',
    ]
    if e.get('account_size'):
        if e.get('target_shares') is not None:
            lines.append(f'目标市值：{e["target_value"]:.2f}；目标份额：{e["target_shares"]}；本次差额：{e["trade_shares"]} 份（{e["trade_lots"]} 手）')
        else:
            lines.append(f'目标市值：{e["target_value"]:.2f}；ETF份额：本地ETF行情缺失，暂不换算')
    lines.append(f'价格说明：{r["price_note"]}')
    if r['logic_invalidations']:
        lines += ['失效条件：'] + [f'- {x}' for x in r['logic_invalidations']]
    lines += ['执行检查：'] + [f'- {x}' for x in plan['checklist']]
    return '\n'.join(lines)


def save_plan(plan: dict, path: str = None) -> str:
    path = path or os.path.join(C.OUT_DIR, C.PLAN_OUT_FILE)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return path
