#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
跨主题动量轮动策略 V1.0

核心逻辑：
  1. 加载 V6 引擎输出 → 计算轮动分 rotation_score = composite × 0.4 + continuation × 0.6
  2. 选 top N 主题作为持仓主题
  3. 在每个主题中选龙头股（V6 leader，找不到则取 portfolio DB 中评分最高的）
  4. 每日输出调仓信号：买卖什么、仓位多少、止损在哪

使用方式：
  python rotation.py                              # 运行今日轮动
  python rotation.py --date 20260703              # 运行指定日期
  python rotation.py --top 5                      # 轮动 top 5 主题（默认3）

依赖：
  - theme_alpha_v6_result.json (V6 引擎输出)
  - theme_portfolio.db (主题核心股池)
  - tushare 日线数据
"""
import os, sys, json, sqlite3, argparse
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

# =========================
# 路径配置
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
V6_RESULT_PATH = os.path.join(BASE_DIR, 'cache', 'theme_alpha_v6_result.json')
PORTFOLIO_DB_PATH = os.path.join(
    os.path.dirname(BASE_DIR), 'cache_backbone_tushare', 'theme_portfolio.db'
)
STOCK_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(BASE_DIR)), 'cache_daily', 'theme_stock_map_latest.json'
)

# Tushare 配置
sys.path.insert(0, os.path.dirname(BASE_DIR))
from dotenv import load_dotenv
load_dotenv("d:/mystock/config/.env")
import tushare as ts
pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))


# =========================
# 1. 加载 V6/V8 引擎输出
# =========================
def load_v6_results(path=None):
    """加载 V8/V6 引擎输出的主题评分数据（优先 V8）"""
    if path:
        pass
    else:
        # 尝试找今天的 V8 结果
        today_str = datetime.now().strftime("%Y%m%d")
        v8_path = V6_RESULT_PATH.replace('.json', f'_v8_{today_str}.json')
        if os.path.exists(v8_path):
            path = v8_path
        else:
            path = V6_RESULT_PATH
    if not os.path.exists(path):
        print(f"[错误] 引擎结果不存在: {path}")
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    source = "V8" if '_v8_' in path else "V6"

    # V8字段兼容映射
    if source == "V8":
        for r in data:
            if '主题' in r:
                r['theme'] = r.get('主题', '')
                r['composite_score'] = r.get('V7综合得分', 0)
                r['stage'] = r.get('D阶段', '')
                r['continuation_score'] = 0
                r['trend_score'] = r.get('趋势分', 0)
                r['capital_score'] = r.get('资金分', 0)
                r['trade_signal'] = r.get('策略动作', '')

    print(f"[{source}] 加载 {len(data)} 个主题评分")
    return data


# =========================
# 2. 计算轮动分 & 选主题
# =========================
def compute_rotation_scores(v6_data, w_composite=0.4, w_continuation=0.6):
    """
    计算每个主题的轮动分。

    rotation_score = composite × w_composite + continuation × w_continuation

    高 continuation 权重：延续概率比当前强度更重要。
    divergence_buy 标记的主题额外 +5 分（分歧买点奖励）。
    """
    scores = []
    for r in v6_data:
        theme = r['theme']
        composite = r.get('composite_score', 0)
        continuation = r.get('continuation_score', 0)

        # 信号为"回避"的主题直接排除
        signal = r.get('trade_signal', '')
        if signal == '回避':
            continue

        rot_score = composite * w_composite + continuation * w_continuation

        # 分歧买点奖励：趋势未破但暂时分歧，是低吸机会
        if r.get('divergence_buy'):
            rot_score += 5

        scores.append({
            'theme': theme,
            'rotation_score': round(rot_score, 1),
            'composite_score': composite,
            'continuation_score': continuation,
            'trade_signal': signal,
            'stage': r.get('stage', ''),
            'leader': r.get('leader', ''),
            'continuation_tag': r.get('continuation_tag', ''),
            'divergence_buy': r.get('divergence_buy', ''),
            'confidence': r.get('confidence', 0),
        })

    scores.sort(key=lambda x: -x['rotation_score'])
    return scores


def select_top_themes(rotation_scores, top_n=3, min_score=50):
    """按轮动分选 top N 主题，过滤过低分主题"""
    selected = [s for s in rotation_scores if s['rotation_score'] >= min_score]
    return selected[:top_n]


# =========================
# 3. 获取主题内龙头股
# =========================
def load_portfolio_db(path=None):
    """
    从 theme_portfolio.db 加载每个主题的核心股池。

    返回: { theme_name: [(ts_code, name, composite_score), ...] }
    按 composite_score 降序排序。
    """
    path = path or PORTFOLIO_DB_PATH
    if not os.path.exists(path):
        print(f"[警告] 投资组合数据库不存在: {path}")
        return {}

    conn = sqlite3.connect(path)
    query = """
        SELECT theme_name, ts_code, name, purity, mcap, turnover
        FROM portfolio
        WHERE layer = 'core'
        ORDER BY theme_name, purity DESC, mcap DESC
    """
    df = pd.read_sql(query, conn)
    conn.close()

    theme_stocks = defaultdict(list)
    for _, row in df.iterrows():
        theme_stocks[row['theme_name']].append({
            'ts_code': row['ts_code'],
            'name': row['name'],
            'score': row['purity'],
            'mcap': row['mcap'],
        })

    print(f"[股池] 加载 {len(theme_stocks)} 个主题的核心股池, 共 {len(df)} 只")
    return dict(theme_stocks)


def load_stock_name_map(path=None):
    """加载 theme_stock_map_latest.json 中的 stock name -> code 映射"""
    path = path or STOCK_MAP_PATH
    if not os.path.exists(path):
        print(f"[警告] 主题映射文件不存在: {path}")
        return {}, {}

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    name_to_code = {}
    code_to_name = {}
    for code, info in data.get('stocks', {}).items():
        name = info.get('name', '')
        if name:
            name_to_code[name] = code
            code_to_name[code] = name

    return name_to_code, code_to_name


def get_leader_stock(theme_info, theme_stocks, name_to_code):
    """
    获取主题的龙头股。

    优先级：
      1. V6 leader（龙头公司名 → 查 code）
      2. 核心股池中评分最高的股票
    """
    leader_name = theme_info.get('leader', '')
    if leader_name:
        code = name_to_code.get(leader_name)
        if code:
            return code, leader_name, 'v6_leader'

    # 降级：从核心股池取评分最高
    stocks = theme_stocks.get(theme_info['theme'], [])
    if stocks:
        s = stocks[0]
        return s['ts_code'], s['name'], 'pool_top1'

    return None, None, 'none'


# =========================
# 4. 获取个股行情 & 计算信号
# =========================
def get_stock_daily(ts_code, trade_date, lookback=60):
    """获取个股日线数据，返回 DataFrame（V2: 优先 daily_cache 表）"""
    try:
        df = None
        try:
            from stock_cache import get_daily_cache, get_daily_cache_range, batch_insert_daily_cache
            _, _max_date = get_daily_cache_range(ts_code)
            if _max_date is not None and str(_max_date) >= str(trade_date):
                df = get_daily_cache(ts_code, '20250101', trade_date)
                if df is not None and not df.empty:
                    df['trade_date'] = df['trade_date'].astype(str)
        except Exception:
            pass
        if df is None or df.empty:
            df = pro.daily(ts_code=ts_code, start_date='', end_date=trade_date)
            if df is not None and not df.empty:
                try:
                    from stock_cache import batch_insert_daily_cache
                    batch_insert_daily_cache(df)
                except Exception:
                    pass
        if df is None or df.empty:
            return None
        df = df.sort_values('trade_date').tail(lookback)
        return df
    except Exception as e:
        print(f"    [错误] 获取 {ts_code} 数据失败: {e}")
        return None


def compute_entry_signal(df, ts_code, name):
    """
    计算个股入场信号。

    返回：
      entry_price: 建议入场价（最新收盘价）
      stop_loss:   止损价（MA20 或 -7%）
      position_pct: 建议仓位权重（1/3 等权）
      signal:      买入/持有/观望
    """
    if df is None or len(df) < 20:
        return None

    close = df['close'].values
    latest_close = close[-1]
    ma20 = np.mean(close[-20:])
    ma10 = np.mean(close[-10:])
    ma5 = np.mean(close[-5:])

    # 止损价 = min(MA20, -7%)
    stop_ma20 = ma20
    stop_fixed = latest_close * 0.93
    stop_loss = max(stop_ma20, stop_fixed)  # 取较高的（宽松止损）

    # 入场信号判断
    if latest_close > ma5:  # 在 MA5 以上 = 强势
        signal = '买入'
        entry_price = latest_close
    elif latest_close > ma10:  # MA5-MA10 = 偏强
        signal = '关注'
        entry_price = latest_close
    elif latest_close > ma20:  # MA10-MA20 = 回调
        signal = '低吸'
        entry_price = latest_close
    else:
        signal = '观望'
        entry_price = latest_close

    return {
        'ts_code': ts_code,
        'name': name,
        'close': latest_close,
        'ma5': ma5,
        'ma10': ma10,
        'ma20': ma20,
        'entry_price': entry_price,
        'stop_loss': round(stop_loss, 2),
        'pct_chg': float(df['pct_chg'].values[-1]) if 'pct_chg' in df.columns else 0,
        'signal': signal,
        'position_pct': round(1.0 / 3 * 100, 1),  # 等权 1/3
    }


# =========================
# 5. 轮动信号比较（与昨日持仓对比）
# =========================
def load_previous_positions(cache_dir):
    """加载昨日持仓"""
    path = os.path.join(cache_dir, 'rotation_positions.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_current_positions(positions, cache_dir):
    """保存今日持仓"""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, 'rotation_positions.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def compare_positions(current, previous):
    """比较新旧持仓，生成调仓动作"""
    if not previous:
        return [{'action': '开仓', 'theme': p['theme'], 'stock': p['name'],
                 'code': p['ts_code'], 'reason': '新开仓'} for p in current]

    prev_map = {p['ts_code']: p for p in previous}
    curr_map = {p['ts_code']: p for p in current}

    actions = []
    # 新买入
    for p in current:
        if p['ts_code'] not in prev_map:
            actions.append({'action': '买入', 'theme': p['theme'], 'stock': p['name'],
                            'code': p['ts_code'], 'reason': f"轮动分排名晋升"})
        elif prev_map[p['ts_code']].get('theme') != p['theme']:
            actions.append({'action': '切换主题', 'theme': p['theme'], 'stock': p['name'],
                            'code': p['ts_code'],
                            'reason': f"从 {prev_map[p['ts_code']]['theme']} 切换到 {p['theme']}"})

    # 卖出
    for p in previous:
        if p['ts_code'] not in curr_map:
            actions.append({'action': '卖出', 'theme': p['theme'], 'stock': p['name'],
                            'code': p['ts_code'], 'reason': '轮动分跌出 top N'})

    return actions


# =========================
# 6. 主流程
# =========================
def run_rotation(trade_date=None, top_n=3, w_composite=0.4, w_continuation=0.6):
    """运行跨主题动量轮动策略"""
    print("=" * 70)
    print("  跨主题动量轮动策略 V1.0")
    print("=" * 70)
    print()

    if trade_date is None:
        now = datetime.now()
        if now.hour < 15:
            trade_date = (now - timedelta(days=1)).strftime('%Y%m%d')
        else:
            trade_date = now.strftime('%Y%m%d')
        # 用交易日历校准
        try:
            cal = pro.trade_cal(exchange='', start_date='20250101', end_date=trade_date)
            cal = cal[cal['is_open'] == 1]
            trade_date = str(cal[cal['cal_date'] <= trade_date]['cal_date'].max())
        except:
            pass
    print(f"  交易日: {trade_date}")
    print()

    # --- 1. 加载 V6 结果 ---
    print("[1/5] 加载 V6 引擎评分...")
    v6_data = load_v6_results()
    if not v6_data:
        print("[错误] 无 V6 数据，退出")
        return

    # --- 2. 计算轮动分 & 选主题 ---
    print(f"[2/5] 计算轮动分 (composite×{w_composite} + continuation×{w_continuation})...")
    rotation_scores = compute_rotation_scores(v6_data, w_composite, w_continuation)

    print(f"\n  轮动分 TOP 10:")
    print(f"  {'#':<3} {'主题':<16} {'轮动分':<8} {'综合':<6} {'延续':<6} {'信号':<6} {'阶段':<8} {'分歧':<6}")
    print(f"  {'-'*70}")
    for i, s in enumerate(rotation_scores[:10]):
        div = '★' if s['divergence_buy'] else ''
        print(f"  {i+1:<3} {s['theme']:<16} {s['rotation_score']:<8.1f} "
              f"{s['composite_score']:<6.1f} {s['continuation_score']:<6.1f} "
              f"{s['trade_signal']:<6} {s['stage']:<8} {div:<6}")

    selected = select_top_themes(rotation_scores, top_n)
    print(f"\n  → 选中 top {len(selected)} 个持仓主题:")
    for s in selected:
        print(f"    {s['theme']:<16} 轮动分={s['rotation_score']:.1f} 信号={s['trade_signal']}")

    if not selected:
        print("[警告] 无主题满足轮动条件，空仓")
        print(json.dumps([], ensure_ascii=False, indent=2))
        return []

    # --- 3. 加载核心股池 & 龙头映射 ---
    print(f"\n[3/5] 加载核心股池 & 龙头映射...")
    theme_stocks = load_portfolio_db()
    name_to_code, code_to_name = load_stock_name_map()

    # --- 4. 确定每只持仓股 ---
    print(f"[4/5] 确定持仓个股...")
    positions = []
    for s in selected:
        code, name, source = get_leader_stock(s, theme_stocks, name_to_code)
        if not code:
            print(f"  [警告] {s['theme']}: 无可用龙头股，跳过")
            continue
        print(f"  {s['theme']}: {name}({code}) [来源={source}]")

        # 获取行情
        df = get_stock_daily(code, trade_date)
        signal_info = compute_entry_signal(df, code, name)
        if not signal_info:
            print(f"    [警告] {code}: 行情数据不足，跳过")
            continue

        signal_info['theme'] = s['theme']
        signal_info['rotation_score'] = s['rotation_score']
        signal_info['theme_stage'] = s['stage']
        signal_info['theme_signal'] = s['trade_signal']
        signal_info['continuation_tag'] = s['continuation_tag']
        signal_info['divergence_buy'] = s.get('divergence_buy', '')
        signal_info['composite_score'] = s.get('composite_score', 0)
        signal_info['continuation_score'] = s.get('continuation_score', 0)
        positions.append(signal_info)

    # --- 5. 输出轮动信号 ---
    print(f"\n[5/5] 输出轮动信号...")
    previous = load_previous_positions(os.path.join(BASE_DIR, 'cache'))
    actions = compare_positions(positions, previous)
    save_current_positions(positions, os.path.join(BASE_DIR, 'cache'))

    print(f"\n{'='*70}")
    print(f"  ★ 跨主题动量轮动信号 — {trade_date}")
    print(f"{'='*70}")

    # 信号汇总
    print(f"\n  【调仓动作】")
    if actions:
        for a in actions:
            sym = {'买入': '🟢', '卖出': '🔴', '开仓': '🟢', '切换主题': '🟡', '持有': '⏺'}
            print(f"    {sym.get(a['action'], '•')} {a['action']} {a['stock']}({a['code']}) | {a['reason']}")
    else:
        print("    ⏺ 无变动（持仓不变）")

    print(f"\n  【持仓明细】共 {len(positions)} 只")
    if positions:
        print(f"  {'主题':<16} {'股票':<10} {'代码':<12} {'现价':<8} {'信号':<6} {'止损':<8} {'仓位':<6} {'轮动分':<8}")
        print(f"  {'-'*80}")
        total_risk = 0
        for p in positions:
            div = '★' if p.get('divergence_buy') else ''
            print(f"  {p['theme']:<16} {p['name']:<10} {p['ts_code']:<12} "
                  f"{p['close']:<8.2f} {p['signal']:<6} {p['stop_loss']:<8.2f} "
                  f"{p['position_pct']:<6} {p['rotation_score']:<8.1f}{div}")
            total_risk += (p['close'] - p['stop_loss']) / p['close'] * p['position_pct'] / 100

        avg_price = sum(p['close'] for p in positions) / len(positions)
        print(f"\n  组合均价: {avg_price:.2f}")
        print(f"  组合风险(等权): {total_risk * 100 / len(positions) * (1/3):.1f}% (平均单股最大回撤)")
        print(f"  总仓位: {sum(p['position_pct'] for p in positions):.0f}%")
    else:
        print("  (空仓)")

    # 各主题详情
    print(f"\n  【主题持仓理由】")
    for p in positions:
        print(f"\n    {p['theme']}")
        print(f"      ├ 股票: {p['name']}({p['ts_code']})")
        print(f"      ├ 轮动分: {p['rotation_score']:.1f} (综合{p.get('composite_score',0)}×0.4+延续{p.get('continuation_score',0)}×0.6)")
        print(f"      ├ 主题信号: {p.get('theme_signal','')} | 阶段: {p.get('theme_stage','')} | 延续标签: {p.get('continuation_tag','')}")
        print(f"      ├ 入场价: {p['entry_price']:.2f} | 止损价: {p['stop_loss']:.2f} (-{(1-p['stop_loss']/p['entry_price'])*100:.1f}%)")
        print(f"      ├ 仓位: {p['position_pct']:.0f}%")
        if p.get('divergence_buy'):
            print(f"      └ ★ 分歧买点: 综合分低但延续分极高，低吸机会！")

    # JSON 输出（供其他程序消费）
    output = {
        'trade_date': trade_date,
        'strategy': '跨主题动量轮动 V1',
        'params': {'top_n': top_n, 'w_composite': w_composite, 'w_continuation': w_continuation},
        'actions': actions,
        'positions': [{
            'theme': p['theme'],
            'ts_code': p['ts_code'],
            'name': p['name'],
            'entry_price': p['entry_price'],
            'stop_loss': p['stop_loss'],
            'position_pct': p['position_pct'],
            'signal': p['signal'],
            'rotation_score': p['rotation_score'],
        } for p in positions],
    }
    output_path = os.path.join(BASE_DIR, 'cache', f'rotation_signal_{trade_date}.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  信号已保存: {output_path}")
    print(f"{'='*70}")

    # --- 6. 导出通达信板块文件 ---
    # 通达信板块文件格式：每行一个股票代码，上海加SH，深圳加SZ
    # 文件放在 d:\mystock\solo\theme_alpha_v6\cache\ 下
    if positions:
        blk_path = os.path.join(BASE_DIR, 'cache', f'rotation_blk_{trade_date}.blk')
        with open(blk_path, 'w', encoding='gbk') as f:
            for p in positions:
                code = p['ts_code']
                # 通达信格式：SH600958 或 SZ002046
                if code.endswith('.SH'):
                    tdx_code = f"SH{code.replace('.SH','')}"
                elif code.endswith('.SZ'):
                    tdx_code = f"SZ{code.replace('.SZ','')}"
                else:
                    tdx_code = code
                f.write(tdx_code + '\n')
        print(f"  通达信板块已保存: {blk_path}")

    return positions


# =========================
# 命令行入口
# =========================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='跨主题动量轮动策略')
    parser.add_argument('--date', type=str, default=None, help='交易日 (YYYYMMDD)')
    parser.add_argument('--top', type=int, default=3, help='轮动主题数量 (默认3)')
    parser.add_argument('--wc', type=float, default=0.4, help='综合分权重 (默认0.4)')
    parser.add_argument('--wct', type=float, default=0.6, help='延续分权重 (默认0.6)')
    args = parser.parse_args()

    run_rotation(trade_date=args.date, top_n=args.top,
                 w_composite=args.wc, w_continuation=args.wct)
