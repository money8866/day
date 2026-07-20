"""
Chip Alpha Engine V5 — 批量扫描脚本
从 bull_stocks_qualified.csv 读取候选股，运行 V5 全引擎分析
输出 V5 Profile（含 Alpha 三维度、Risk、Trend State、Transition、Decision）
==============================================================
"""
import os
import sys
import json
import time
import pickle
import threading
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv("d:/mystock/config/.env")

# 防止反复 import 出错
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chip_alpha_v5 import ChipAlphaV5Engine, calc_opportunity_score
from chip_alpha_engine_v2 import ChipAlphaEngineV2, _get_trade_dates


# =========================
# V2分析结果缓存（pickle）
# 与 scan_etf_alpha_v5.py 共用缓存目录
# =========================
_V2_CACHE_LOCK = threading.Lock()

def _get_v2_cache_dir(engine_cache_dir: str) -> str:
    d = os.path.join(engine_cache_dir, 'v2_analysis')
    os.makedirs(d, exist_ok=True)
    return d

def _v2_cache_path(cache_dir: str, ts_code: str, end_date: str) -> str:
    return os.path.join(cache_dir, f"v2_{ts_code}_{end_date}.pkl")

def _load_v2_cache(cache_dir: str, ts_code: str, end_date: str) -> Optional[Dict]:
    p = _v2_cache_path(cache_dir, ts_code, end_date)
    if not os.path.exists(p):
        return None
    try:
        with _V2_CACHE_LOCK:
            with open(p, 'rb') as f:
                return pickle.load(f)
    except Exception:
        return None

def _save_v2_cache(cache_dir: str, ts_code: str, end_date: str, result: Dict):
    p = _v2_cache_path(cache_dir, ts_code, end_date)
    try:
        with _V2_CACHE_LOCK:
            with open(p + '.tmp', 'wb') as f:
                pickle.dump(result, f)
            os.replace(p + '.tmp', p)
    except Exception:
        pass


def load_candidates(csv_path: str) -> list:
    """从bull_stocks_qualified.csv中加载候选股列表"""
    df = pd.read_csv(csv_path, dtype=str).fillna('')
    candidates = []
    for _, row in df.iterrows():
        code = str(row.get('code') or row.get('代码', '')).strip()
        name = str(row.get('name') or row.get('名称', '')).strip()
        if code:
            # 补全后缀
            if not code.endswith('.SZ') and not code.endswith('.SH') and not code.endswith('.BJ'):
                if code.startswith('6') or code.startswith('9'):
                    code += '.SH'
                elif code.startswith('8') or code.startswith('4'):
                    code += '.BJ'
                else:
                    code += '.SZ'
            candidates.append({'代码': code, '名称': name})
    return candidates


def scan_v5_batch(v2_engine, v5_engine, candidates: list,
                  lookback_days: int = 20,
                  end_date: Optional[str] = None,
                  output_csv: str = '',
                  max_workers: int = 8) -> pd.DataFrame:
    """
    批量扫描流程 — 日线批量预取 + 线程池并行加速。

    与 scan_etf_alpha_v5.py 共用芯片缓存目录，第二次运行更快。

    Parameters
    ----------
    max_workers : int
        线程池并发数，默认8。设为1退化为串行。
    """
    t0 = time.time()

    # Step 0: 准备日期参数
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    end_date = str(end_date).replace('-', '')
    trade_dates = _get_trade_dates(end_date, lookback_days)

    # Step 1: 按交易日批量预取日线 + 基本面数据（写入引擎缓存）
    from scan_etf_alpha_v5 import batch_prefetch_daily_by_date
    print("[ChipAlphaV5] 按交易日批量预取日线/基本面数据...")
    batch_prefetch_daily_by_date(candidates, v2_engine.cache_dir,
                                 end_date, lookback_days)
    print(f"[ChipAlphaV5] 日线预取完成，耗时 {time.time()-t0:.0f}s")

    # Step 2: 线程池并行扫描
    total = len(candidates)
    rows = []
    results_lock = threading.Lock()
    pbar_lock = threading.Lock()
    completed = [0]

    def _process_one(ts_code: str, name: str) -> Optional[dict]:
        try:
            kw = {'lookback_days': lookback_days}
            if end_date:
                kw['end_date'] = end_date
            # V2分析结果缓存（避免重复CPU因子计算）
            v2_cache_dir = _get_v2_cache_dir(v2_engine.cache_dir)
            v2_result = _load_v2_cache(v2_cache_dir, ts_code, end_date)
            if v2_result is None:
                v2_result = v2_engine.analyze(ts_code, **kw)
                _save_v2_cache(v2_cache_dir, ts_code, end_date, v2_result)
            v5_profile = v5_engine.analyze_from_v2(v2_result)
            _os = calc_opportunity_score(v5_profile)
            v5_profile['Opportunity_Score'] = _os['score']

            a = v5_profile.get('alpha', {})
            r = v5_profile.get('risk', {})
            t = v5_profile.get('trend', {})
            d = v5_profile.get('decision', {})
            tr = t.get('transition', {})
            fs = v5_profile.get('raw_factors', {})

            return {
                '代码': ts_code,
                '名称': name,
                '现价': v5_profile.get('current_price', 0),
                '筹码质心': v5_profile.get('chip_center', 0),
                'V2_Score': v5_profile.get('v2_score', 50),
                'V2_Grade': v5_profile.get('v2_grade', 'C'),
                '结构分': a.get('Structure', 50),
                '资金分': a.get('Flow', 50),
                '动量分': a.get('Momentum', 50),
                '复合Alpha': a.get('Composite', 50),
                'Alpha等级': a.get('Grade', 'C'),
                '风险分': r.get('Composite', 50),
                '风险等级': r.get('Level', 'Medium'),
                '趋势阶段': t.get('current_state', ''),
                '阶段描述': t.get('description', ''),
                '下一阶段': tr.get('primary_next', ''),
                '转移概率': tr.get('primary_prob', 0),
                '操作建议': d.get('action', ''),
                '信心度': d.get('confidence', 50),
                '买入质量分': d.get('buy_quality_score', 50),
                'Opportunity_Score': v5_profile.get('Opportunity_Score', 50),
                'CostResilience': fs.get('Resilience', 50),
                'PressureDecay': fs.get('PressureDecay', 50),
                'CRE': fs.get('CRE', 50),
                'ChipMomentum': fs.get('ChipMomentum', 50),
                '20日涨幅': v2_result.get('price_return_20d', 0),
            }
        except Exception as e:
            return None

    def _scan_with_progress(ts_code, name):
        row = _process_one(ts_code, name)
        with pbar_lock:
            completed[0] += 1
            done = completed[0]
            elapsed = time.time() - t0
            avg = elapsed / done if done > 0 else 0
            eta = avg * (total - done)
            name_str = name or ts_code
            if row:
                print(f"[{done}/{total}] {name_str}({ts_code}) "
                      f"V5={row['复合Alpha']:.1f}({row['Alpha等级']}) "
                      f"OS={row['Opportunity_Score']:.0f} "
                      f"风险={row['风险分']:.0f} "
                      f"阶段={row['趋势阶段']}→{row['下一阶段']}({row['转移概率']*100:.0f}%) "
                      f"建议={row['操作建议']} "
                      f"ETA={eta:.0f}s")
            else:
                print(f"[{done}/{total}] {name_str}({ts_code}) 失败 ETA={eta:.0f}s")
        if row:
            with results_lock:
                rows.append(row)

    print(f"[ChipAlphaV5] 线程池并行扫描 {total} 只 (workers={max_workers})...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = []
        for stock in candidates:
            ts_code = stock['代码']
            name = stock.get('名称', '')
            futures.append(pool.submit(_scan_with_progress, ts_code, name))
        for f in as_completed(futures):
            pass  # 异常已在内部处理

    total_time = time.time() - t0
    print(f"\n完成! 共 {len(rows)}/{total} 只成功, 总耗时 {total_time:.0f}s")

    df = pd.DataFrame(rows)
    if output_csv:
        df.to_csv(output_csv, index=False, encoding='utf-8-sig')
        print(f"结果已保存至: {output_csv}")
    return df


def format_v5_report(df: pd.DataFrame) -> str:
    """生成 V5 批量扫描结果摘要报告 (终端输出)"""
    lines = []
    lines.append("")
    lines.append("═" * 75)
    lines.append("  Chip Alpha V5 — 批量扫描结果摘要")
    lines.append("═" * 75)
    lines.append(f"  扫描日期: {pd.Timestamp.now().strftime('%Y%m%d')}")
    lines.append(f"  样本数:   {len(df)}")
    lines.append("")

    # 按复合Alpha排序
    df_sorted = df.sort_values('复合Alpha', ascending=False)

    # 按操作建议分组统计
    action_counts = df_sorted['操作建议'].value_counts()
    lines.append("  ── 操作建议分布 ──")
    for act, cnt in action_counts.items():
        pct = cnt / len(df_sorted) * 100
        lines.append(f"    {act:<16s}  {cnt:3d} 只 ({pct:.0f}%)")

    # 按趋势阶段分组
    stage_counts = df_sorted['趋势阶段'].value_counts()
    lines.append("\n  ── 趋势阶段分布 ──")
    for stage, cnt in stage_counts.items():
        lines.append(f"    {stage:<14s}  {cnt:3d} 只")

    # 按风险等级分组
    risk_counts = df_sorted['风险等级'].value_counts()
    lines.append("\n  ── 风险等级分布 ──")
    for risk_lv, cnt in risk_counts.items():
        lines.append(f"    {risk_lv:<10s}  {cnt:3d} 只")

    lines.append("")

    # Top 10 列表
    lines.append("  ── Top 10 高Alpha股票 ──")
    lines.append("")
    for i, (_, s) in enumerate(df_sorted.head(10).iterrows(), 1):
        act = s.get('操作建议', '')
        conf = s.get('信心度', 50)
        lines.append(f"  【{i}】{s['名称']}({s['代码']}) "
                     f"Alpha={s['复合Alpha']:.1f}({s['Alpha等级']}) "
                     f"结构/资金/动量={s['结构分']:.0f}/{s['资金分']:.0f}/{s['动量分']:.0f}")
        lines.append(f"       风险={s['风险分']:.0f}({s['风险等级']}) "
                     f"阶段={s['趋势阶段']}→{s['下一阶段']}({s['转移概率']*100:.0f}%) "
                     f"建议={act}({conf:.0f}%)")
        lines.append("")

    # 低风险+高Alpha筛选
    high_alpha_low_risk = df_sorted[
        (df_sorted['复合Alpha'] >= 70) &
        (df_sorted['风险分'] <= 40)
    ]
    if len(high_alpha_low_risk) > 0:
        lines.append(f"  ── 高Alpha(≥70)+低风险(≤40) 优质标的 ({len(high_alpha_low_risk)}只) ──")
        for i, (_, s) in enumerate(high_alpha_low_risk.iterrows(), 1):
            lines.append(f"  {i}. {s['名称']}({s['代码']}) "
                         f"Alpha={s['复合Alpha']:.1f} 风险={s['风险分']:.0f} "
                         f"阶段={s['趋势阶段']}→{s['下一阶段']}({s['转移概率']*100:.0f}%) "
                         f"建议={s['操作建议']}({s['信心度']:.0f}%)")

    lines.append("")
    lines.append("═" * 75)
    lines.append("")

    return '\n'.join(lines)


# ============================================================
# 主题聚合
# ============================================================
_THEME_MAP = None  # 缓存: {theme_name: [stock_dict, ...]}
_STOCK_THEMES = None  # 缓存: {stock_code: [theme_name, ...]}

def load_theme_map(cache_path: str = "D:/mystock/cache_daily/theme_stock_map_latest.json"):
    """
    加载主题映射表，建立两种索引：
    1. theme→stocks  {主题名: [{code, name, score}, ...]}
    2. stock→themes {股票代码: [主题名, ...]}
    """
    global _THEME_MAP, _STOCK_THEMES
    if _THEME_MAP is not None and _STOCK_THEMES is not None:
        return _THEME_MAP, _STOCK_THEMES
    if not os.path.exists(cache_path):
        print(f"[主题聚合] 文件不存在: {cache_path}")
        _THEME_MAP, _STOCK_THEMES = {}, {}
        return {}, {}
    with open(cache_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    _THEME_MAP = data.get('themes', {})
    # 构建反向索引
    _STOCK_THEMES = {}
    for theme_name, stocks in _THEME_MAP.items():
        for s in stocks:
            code = s.get('code', '')
            if code:
                _STOCK_THEMES.setdefault(code, []).append(theme_name)
    print(f"[主题聚合] 加载 {len(_THEME_MAP)} 个主题, {len(_STOCK_THEMES)} 只股票")
    return _THEME_MAP, _STOCK_THEMES


def inject_theme_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    为扫描结果 DataFrame 注入主题列（逗号分隔的主题名）
    返回新增'主题'列的 DataFrame
    """
    _, stock_themes = load_theme_map()
    themes_col = []
    for _, row in df.iterrows():
        code = row.get('代码', '')
        tlist = stock_themes.get(code, [])
        themes_col.append(','.join(tlist) if tlist else '')
    df = df.copy()
    df['主题'] = themes_col
    return df


def aggregate_by_theme(df: pd.DataFrame, high_score_threshold: float = 80) -> pd.DataFrame:
    """
    将扫描结果按主题聚合。
    要求 df 已有'主题'列（逗号分隔，每只股票可能属多个主题）。
    
    Parameters
    ----------
    df : DataFrame
        扫描结果，含'主题'列
    high_score_threshold : float
        高分判定阈值（OS >= 此值视为高分），默认80
    
    返回 DataFrame:
        ['主题', '股票数', '主题总股数', '高分个股数', '高分占比',
         '平均20日涨幅', '平均Alpha', '平均风险', '平均OS', '平均结构', '平均资金', '平均动量',
         '推荐买入数', '推荐买入率', '星级']
    """
    # 获取主题总股数（从主题映射表中读取）
    theme_map, _ = load_theme_map()
    
    # 先将单行的多个主题拆成多行
    rows = []
    for _, s in df.iterrows():
        themes_str = s.get('主题', '')
        if not themes_str:
            continue
        os_val = s.get('Opportunity_Score', 0)
        is_high = 1 if os_val >= high_score_threshold else 0
        for theme in themes_str.split(','):
            theme = theme.strip()
            if not theme:
                continue
            rows.append({
                '主题': theme,
                '复合Alpha': s.get('复合Alpha', 50),
                '风险分': s.get('风险分', 50),
                'Opportunity_Score': os_val,
                '结构分': s.get('结构分', 50),
                '资金分': s.get('资金分', 50),
                '动量分': s.get('动量分', 50),
                '操作建议': s.get('操作建议', ''),
                'is_high': is_high,
                '20日涨幅': s.get('20日涨幅', 0),
            })
    if not rows:
        return pd.DataFrame()

    exp_df = pd.DataFrame(rows)
    # 聚合
    agg = exp_df.groupby('主题').agg(
        股票数=('复合Alpha', 'count'),
        高分个股数=('is_high', 'sum'),
        平均Alpha=('复合Alpha', 'mean'),
        平均风险=('风险分', 'mean'),
        平均OS=('Opportunity_Score', 'mean'),
        平均结构=('结构分', 'mean'),
        平均资金=('资金分', 'mean'),
        平均动量=('动量分', 'mean'),
        平均20日涨幅=('20日涨幅', 'mean'),
        推荐买入数=('操作建议', lambda x: sum(
            1 for a in x if a in ('Buy', 'Strong Buy', 'Buy on Pullback'))),
    ).reset_index()

    # 注入主题总股数
    def get_theme_total(theme_name):
        stocks = theme_map.get(theme_name, [])
        return len(stocks)
    
    agg['主题总股数'] = agg['主题'].apply(get_theme_total)
    # 高分占比 = 高分个股数 / 主题总股数 * 100
    agg['高分占比'] = agg.apply(
        lambda r: round(r['高分个股数'] / r['主题总股数'] * 100, 2)
        if r['主题总股数'] > 0 else 0, axis=1)

    agg['推荐买入率'] = (agg['推荐买入数'] / agg['股票数'] * 100).round(1)
    agg['平均Alpha'] = agg['平均Alpha'].round(1)
    agg['平均风险'] = agg['平均风险'].round(1)
    agg['平均OS'] = agg['平均OS'].round(1)
    agg['平均结构'] = agg['平均结构'].round(1)
    agg['平均资金'] = agg['平均资金'].round(1)
    agg['平均动量'] = agg['平均动量'].round(1)
    agg['平均20日涨幅'] = agg['平均20日涨幅'].round(1)

    # 星级：基于 Alpha 和 风险的组合
    def calc_stars(row):
        a = row['平均Alpha']
        r = row['平均风险']
        if a >= 75 and r <= 25:
            return '★★★★★'
        if a >= 70 and r <= 30:
            return '★★★★☆'
        if a >= 65 and r <= 35:
            return '★★★☆☆'
        if a >= 60 and r <= 40:
            return '★★☆☆☆'
        return '★☆☆☆☆'

    agg['星级'] = agg.apply(calc_stars, axis=1)

    # 排序：高分占比降序 → 平均20日涨幅降序 → 平均OS降序
    agg = agg.sort_values(['高分占比', '平均20日涨幅', '平均OS'],
                          ascending=[False, False, False]).reset_index(drop=True)
    return agg


def format_theme_report(theme_df: pd.DataFrame) -> str:
    """生成主题聚合终端报告"""
    if theme_df.empty:
        return ""
    lines = []
    lines.append("")
    lines.append("═" * 75)
    lines.append("  主题聚合 — 按共振强度排序（高分占比↓ + 20日强度↓）")
    lines.append("═" * 75)

    for i, (_, t) in enumerate(theme_df.iterrows(), 1):
        name = t['主题']
        stock_cnt = int(t['股票数'])
        total_cnt = int(t['主题总股数'])
        high_cnt = int(t['高分个股数'])
        high_pct = t['高分占比']
        alpha = t['平均Alpha']
        risk = t['平均风险']
        os_val = t['平均OS']
        ret20 = t['平均20日涨幅']
        stars = t['星级']
        buy_pct = t['推荐买入率']
        # 高分共振标记
        resonance_tag = f">> {high_cnt}/{total_cnt}({high_pct:.1f}%)" if high_pct >= 10 else f"   {high_cnt}/{total_cnt}({high_pct:.1f}%)"
        # 20日强度标记
        ret_tag = f"📈 +{ret20:.1f}%" if ret20 > 0 else f"📉 {ret20:.1f}%"
        # 标注推荐买入占比
        buy_tag = f" | 推荐 {t['推荐买入数']:.0f}/{stock_cnt}({buy_pct:.0f}%)"
        lines.append(f"")
        lines.append(f"  ═══ {name} ═══")
        lines.append(f"  股票数: {stock_cnt}/{total_cnt} | 高分共振: {resonance_tag} | 20日强度: {ret_tag}")
        lines.append(f"  平均Alpha: {alpha:.1f} | 平均风险: {risk:.1f} | OS: {os_val:.1f}")
        lines.append(f"  结构/资金/动量: {t['平均结构']:.0f}/{t['平均资金']:.0f}/{t['平均动量']:.0f}")
        lines.append(f"  {stars}{buy_tag}")

    lines.append("")
    lines.append("═" * 75)
    return '\n'.join(lines)


def save_theme_csv(theme_df: pd.DataFrame, output_path: str):
    """保存主题聚合 CSV"""
    if theme_df.empty:
        print("[主题聚合] 无数据，跳过保存")
        return
    # 排序：沿用聚合时的排序（高分占比降序 → 平均OS降序）
    out = theme_df
    out.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"[主题聚合] 已保存: {output_path} ({len(out)} 个主题)")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Chip Alpha V5 批量扫描')
    parser.add_argument('--input', default='report_daily/bull_stocks_qualified.csv',
                        help='候选股CSV路径 (默认 report_daily/bull_stocks_qualified.csv)')
    parser.add_argument('--output', default='report_daily/chip_alpha_v5_scan_result.csv',
                        help='输出CSV路径 (默认 report_daily/chip_alpha_v5_scan_result.csv)')
    parser.add_argument('--days', type=int, default=20, help='回看天数 (默认20)')
    parser.add_argument('--date', type=str, default='',
                        help='回溯截止日期 YYYYMMDD (默认当天)')
    parser.add_argument('--summary', action='store_true', help='仅输出摘要不保存')
    parser.add_argument('--workers', type=int, default=8,
                        help='线程池并发数 (默认8, 设为1退化为串行)')
    args = parser.parse_args()

    # 路径处理
    base_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = args.input if os.path.isabs(args.input) else os.path.join(base_dir, args.input)
    output_path = args.output if os.path.isabs(args.output) else os.path.join(base_dir, args.output)

    # 如果指定了日期，自动修改输出文件名
    end_date = args.date.strip() or None
    if end_date:
        # 在 .csv 前插入 _YYYYMMDD
        base, ext = os.path.splitext(output_path)
        output_path = f"{base}_{end_date}{ext}"
        print(f"[ChipAlphaV5] 回溯日期: {end_date}, 输出: {output_path}")

    if not os.path.exists(input_path):
        print(f"错误: 输入文件不存在 {input_path}")
        sys.exit(1)

    print(f"[ChipAlphaV5] 加载候选股: {input_path}")
    candidates = load_candidates(input_path)
    print(f"[ChipAlphaV5] 候选股数量: {len(candidates)}")

    # 初始化引擎
    print("[ChipAlphaV5] 初始化 V2 引擎...")
    v2 = ChipAlphaEngineV2()
    print("[ChipAlphaV5] 初始化 V5 引擎...")
    v5 = ChipAlphaV5Engine()

    # 批量扫描
    if args.summary:
        output_csv = ''
    else:
        output_csv = output_path

    df = scan_v5_batch(v2, v5, candidates,
                       lookback_days=args.days,
                       end_date=end_date,
                       output_csv=output_csv,
                       max_workers=args.workers)

    # 输出摘要
    report = format_v5_report(df)
    print(report)

    # 主题聚合
    print("[ChipAlphaV5] 主题聚合...")
    try:
        df_with_theme = inject_theme_column(df)
        theme_df = aggregate_by_theme(df_with_theme)
        theme_report = format_theme_report(theme_df)
        print(theme_report)
        if output_csv:
            theme_csv_path = output_csv.replace('.csv', '_theme.csv')
            save_theme_csv(theme_df, theme_csv_path)
    except Exception as e:
        print(f"[ChipAlphaV5] 主题聚合失败: {e}")
        import traceback
        traceback.print_exc()

    return df


if __name__ == '__main__':
    df = main()
