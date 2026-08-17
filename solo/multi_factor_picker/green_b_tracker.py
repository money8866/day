# -*- coding: utf-8 -*-
"""EGPT 绿灯B 触发跟踪器 —— 每日推送接入组件
将历史绿灯B 信号持久化到 CSV，每日增量检查触发状态，供 push_washout_recovery.py
在推送消息中生成"🔥 绿灯B 触发提醒"段落。触发规则(v1.0, 20260817):
  - 突破触发: 信号日后某日 close>=VWAP 且量比>=1.2(量比=当日vol/信号日前5日均量, 排除信号日放量污染)
  - 回踩触发: low<=VWAP*1.01 且 close>=VWAP 且量比<=1.5(缩量企稳收回)
  - 止损失效: 触发前某日 low<=报告ATR止损价
  - 未触发:   观察期结束仍未满足任一(纪律=不买)
"""
import os, re, time
from datetime import datetime, timedelta
import pandas as pd

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily')
STATE_FILE = os.path.join(REPORT_DIR, 'egpt_green_b_tracking.csv')
STATE_COLS = ['信号日', '名称', '代码', '评级', '主题', 'VWAP', '止损价',
              '触发状态', '触发日', '触发类型', '触发收盘',
              '触发后收益%', '触发后峰值%', '更新时间']


def parse_growth(v):
    s = str(v).replace('%', '').replace('+', '').strip()
    try:
        return float(s)
    except Exception:
        return float('nan')


def extract_green_b(df):
    """v1.3.2 口径绿灯B(与 push_washout_recovery.build_wechat_msg 保持一致)"""
    if '次日操作' not in df.columns:
        return pd.DataFrame()
    if '中报业绩亮点' in df.columns:
        growth = pd.to_numeric(df['中报业绩亮点'].apply(parse_growth), errors='coerce')
    else:
        growth = pd.Series(float('nan'), index=df.index)
    buy_mask = (
        (df['次日操作'] == '✅ 次日可买入') &
        (df['兑现冲击过滤'].astype(str).str.contains('✅', na=False)) &
        (pd.to_numeric(df.get('修正后评分'), errors='coerce').fillna(0) > 0) &
        (df['修正后胜率分级'].isin(['S', 'A', 'B']) | (growth.fillna(-1) > 0)) &
        (df['修正后胜率分级'] != 'E')
    )
    buy = df[buy_mask]
    cond_mask = df['次日操作'].astype(str).isin(['⚠️ 观察', '⚠️ 次日观察等回踩'])
    pullback_days = (pd.to_numeric(df['回踩天数'], errors='coerce').fillna(0)
                     if '回踩天数' in df.columns else pd.Series(0, index=df.index))
    green_b = df[
        cond_mask &
        (pullback_days >= 1) &
        (df['兑现冲击过滤'].astype(str).str.contains('✅', na=False)) &
        (pd.to_numeric(df.get('修正后评分'), errors='coerce').fillna(0) > 0) &
        (df['修正后胜率分级'].isin(['S', 'A', 'B']) & (growth.fillna(-1) > 0)) &
        (~df.index.isin(buy.index))
    ].sort_values('回踩买点分', ascending=False).head(3)
    return green_b


def classify_trigger(k, sig):
    """判定一笔绿灯B 信号在 k(含信号日及其后完整日线)上的触发状态。
    返回 (状态, 触发日, 触发类型, 触发收盘, 触发后收益%, 触发后峰值%)"""
    sig_date = sig['信号日']
    vwap = sig['VWAP']
    stop = sig['止损价']
    pre = k[k['trade_date'] <= sig_date].tail(5)
    base_vol = pre['vol'].mean() if len(pre) else float('nan')
    k = k[k['trade_date'] > sig_date].reset_index(drop=True)   # 信号日之后
    if k.empty:
        return ('未触发', '', '', None, None, None)
    for _, bar in k.iterrows():
        vr = bar['vol'] / base_vol if base_vol and base_vol > 0 else float('nan')
        # 止损失效(优先级最高): 触发前某日 low<=止损价
        if pd.notna(stop) and stop > 0 and bar['low'] <= stop:
            return ('止损失效', bar['trade_date'], '止损', None, None, None)
        # 突破触发: close>=VWAP 且量比>=1.2
        if bar['close'] >= vwap and pd.notna(vr) and vr >= 1.2:
            after = k[k['trade_date'] > bar['trade_date']]
            if after.empty:
                return ('突破触发', bar['trade_date'], '突破', bar['close'], 0.0, 0.0)
            last = after['close'].iloc[-1]; peak = after['high'].max()
            return ('突破触发', bar['trade_date'], '突破', bar['close'],
                    round((last/bar['close']-1)*100, 1), round((peak/bar['close']-1)*100, 1))
        # 回踩触发: low<=VWAP*1.01 且 close>=VWAP 且量比<=1.5
        if bar['low'] <= vwap * 1.01 and bar['close'] >= vwap and pd.notna(vr) and vr <= 1.5:
            after = k[k['trade_date'] > bar['trade_date']]
            if after.empty:
                return ('回踩触发', bar['trade_date'], '回踩', bar['close'], 0.0, 0.0)
            last = after['close'].iloc[-1]; peak = after['high'].max()
            return ('回踩触发', bar['trade_date'], '回踩', bar['close'],
                    round((last/bar['close']-1)*100, 1), round((peak/bar['close']-1)*100, 1))
    return ('未触发', '', '', None, None, None)


# ---------- 状态持久化 ----------

def _load_state():
    if os.path.exists(STATE_FILE):
        df = pd.read_csv(STATE_FILE, encoding='utf-8-sig', dtype={'代码': str})
        for c in STATE_COLS:
            if c not in df.columns:
                df[c] = ''
        return df
    return pd.DataFrame(columns=STATE_COLS)


def _save_state(df):
    df.to_csv(STATE_FILE, index=False, encoding='utf-8-sig')


def _load_kline(pro, ts_code, start_date, end_date):
    time.sleep(0.15)   # tushare 限速 120ms+
    try:
        return pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date).sort_values('trade_date')
    except Exception:
        return pd.DataFrame()


def _start_of(sig_date):
    """信号日往前推15天作为行情起点(覆盖前5日均量基准)"""
    try:
        return (datetime.strptime(str(sig_date), '%Y%m%d') - timedelta(days=15)).strftime('%Y%m%d')
    except Exception:
        return '20260701'


def _days_between(sig_date, today):
    try:
        return max((datetime.strptime(today, '%Y%m%d') - datetime.strptime(str(sig_date), '%Y%m%d')).days, 0)
    except Exception:
        return ''


def _fmt_num(v, nd=2):
    """数值格式化(含 NaN/异常兜底)"""
    try:
        fv = float(v)
        if pd.isna(fv):
            return '—'
        return f'{fv:.{nd}f}'
    except Exception:
        return '—'


# ---------- 每日增量扫描 ----------

def scan_and_update(pro, today=None):
    """扫描全部增强报告提取绿灯B 信号(新增并入状态)，对未触发信号增量判定触发。
    返回 (state, newly, watching):
      newly   : 今日新触发(触发日==today)的行
      watching: 仍在观察(未触发)的行
    """
    today = today or time.strftime('%Y%m%d')
    state = _load_state()
    files = sorted(f for f in os.listdir(REPORT_DIR)
                   if re.match(r'enhanced_timing_bull_all_\d{8}\.csv', f))

    # 1. 新增信号(幂等: 以 信号日+代码 为键)
    for fn in files:
        d = re.search(r'_(\d{8})\.csv', fn).group(1)
        try:
            df = pd.read_csv(os.path.join(REPORT_DIR, fn), encoding='utf-8-sig', dtype={'代码': str})
        except Exception:
            continue
        gb = extract_green_b(df)
        for _, r in gb.iterrows():
            code = str(r['代码'])
            if not state.empty and ((state['信号日'] == d) & (state['代码'] == code)).any():
                continue
            try:
                stop = (float(r['ATR动态止损价']) if pd.notna(r.get('ATR动态止损价'))
                        else float(r['止损价']) if '止损价' in df.columns and pd.notna(r.get('止损价')) else float('nan'))
                sig = {
                    '信号日': d, '名称': r['名称'], '代码': code,
                    '评级': r['修正后胜率分级'], '主题': r.get('主题', ''),
                    'VWAP': float(r['VWAP']), '止损价': stop,
                    '触发状态': '未触发', '触发日': '', '触发类型': '', '触发收盘': '',
                    '触发后收益%': '', '触发后峰值%': '', '更新时间': d,
                }
            except Exception as e:
                print(f'⚠️ 信号解析失败 {d} {r.get("名称", "")}: {e}')
                continue
            state = pd.concat([state, pd.DataFrame([sig])], ignore_index=True)

    if state.empty:
        _save_state(state)
        return state, pd.DataFrame(), pd.DataFrame()

    # 2. 增量触发检查(仅未触发)
    state = state.reset_index(drop=True)
    newly, watching = [], []
    for i, row in state.iterrows():
        if str(row['触发状态']) not in ('未触发', ''):
            continue
        code = str(row['代码'])
        if '.' not in code:
            continue
        k = _load_kline(pro, code, _start_of(row['信号日']), today)
        if k.empty:
            continue
        status, tdate, ttype, tclose, ret, peak = classify_trigger(k, row)
        state.at[i, '触发状态'] = status
        state.at[i, '触发日'] = str(tdate)
        state.at[i, '触发类型'] = str(ttype)
        state.at[i, '触发收盘'] = _fmt_num(tclose) if tclose else ''
        state.at[i, '触发后收益%'] = ret if ret is not None else ''
        state.at[i, '触发后峰值%'] = peak if peak is not None else ''
        state.at[i, '更新时间'] = today
        if status == '未触发':
            watching.append(row)
        elif str(tdate) == today:
            newly.append(state.loc[i])
    _save_state(state)
    nf = pd.DataFrame(newly, columns=state.columns) if newly else pd.DataFrame(columns=state.columns)
    wf = pd.DataFrame(watching, columns=state.columns) if watching else pd.DataFrame(columns=state.columns)
    return state, nf, wf


# ---------- 推送段落 ----------

def build_trigger_block(today, newly, watching):
    """生成"🔥 绿灯B 触发提醒" Markdown 段落"""
    today = str(today)
    lines = ['## 🔥 绿灯B 触发提醒（历史信号实时跟踪）',
             '> 突破=收盘≥VWAP且量比≥1.2 | 回踩=触及VWAP收回且量比≤1.5 | 止损失效=跌破报告止损价',
             '> 量比基准=信号日前5日均量(排除信号日放量污染)；未触发纪律=不买。', '']
    if len(newly):
        lines.append('### 今日新触发')
        lines.append('| 股票 | 信号日 | 评级 | 类型 | 触发收盘 | VWAP | 止损价 |')
        lines.append('|------|:----:|:----:|:----:|:---:|:---:|:---:|')
        for _, r in newly.iterrows():
            name = f"{r['名称']}({str(r['代码']).replace('.SZ','').replace('.SH','')})"
            lines.append(f"| {name} | {r['信号日']} | {r['评级']} | {r['触发类型']} | "
                         f"{_fmt_num(r['触发收盘'])} | {_fmt_num(r['VWAP'])} | {_fmt_num(r['止损价'])} |")
        lines.append('')
    if len(watching):
        lines.append('### 仍在观察（未触发·纪律=不买）')
        lines.append('| 股票 | 信号日 | 评级 | 主题 | VWAP | 止损价 | 观察天数 |')
        lines.append('|------|:----:|:----:|------|:---:|:---:|:---:|')
        for _, r in watching.head(10).iterrows():
            name = f"{r['名称']}({str(r['代码']).replace('.SZ','').replace('.SH','')})"
            theme = str(r['主题']) if pd.notna(r.get('主题')) else '-'
            lines.append(f"| {name} | {r['信号日']} | {r['评级']} | {theme} | "
                         f"{_fmt_num(r['VWAP'])} | {_fmt_num(r['止损价'])} | {_days_between(r['信号日'], today)} |")
        lines.append('')
    if not len(newly) and not len(watching):
        lines.append('> 当前无绿灯B 观察标的。')
        lines.append('')
    return '\n'.join(lines)
