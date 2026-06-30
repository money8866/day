"""
B浪策略优化版
==============
在原策略基础上添加三个过滤条件：
1. A浪涨幅 > 100% → 评分降权-10分
2. 底背离信号要求 DIF抬高 > 10%
3. 启动信号要求距A高 > 0%（未突破A浪高点）
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入原策略的所有函数
from bwave_strategy import *

def optimized_calc_bwave_score(awave: dict, bwave: dict, launch: dict) -> dict:
    """优化版B浪评分 — 添加A浪涨幅过滤"""
    score = calc_bwave_score(awave, bwave, launch)
    
    # 优化1：A浪涨幅 > 100% → 评分降权
    if awave.get('gain', 0) > 100:
        score['total'] -= 10
        score['a_score'] -= 5
        print(f'  优化过滤：A浪涨幅{awave.get("gain", 0):.1f}% > 100%，评分-10分')
    
    return score


def optimized_detect_bwave_divergence(df: pd.DataFrame, awave: dict, bwave: dict) -> dict | None:
    """优化版底背离检测 — 添加DIF抬高幅度检查"""
    result = detect_bwave_divergence(df, awave, bwave)
    
    if result is None:
        return None
    
    # 优化2：底背离信号要求 DIF抬高 > 10%
    p1_dif = result.get('p1_dif', 0)
    p2_dif = result.get('p2_dif', 0)
    dif_up_pct = (p2_dif - p1_dif) / abs(p1_dif) * 100 if p1_dif != 0 else 0
    
    if dif_up_pct <= 10:
        print(f'  优化过滤：DIF抬高{dif_up_pct:.1f}% ≤ 10%，底背离信号无效')
        return None
    
    # 优化3：RSI确认更强（第二个低点RSI > 第一个低点 + 5）
    p1_rsi = result.get('p1_rsi', 0)
    p2_rsi = result.get('p2_rsi', 0)
    if p2_rsi <= p1_rsi + 5:
        print(f'  优化过滤：RSI抬高{p2_rsi - p1_rsi:.1f} ≤ 5，底背离信号无效')
        return None
    
    return result


def optimized_check_launch_signal(df: pd.DataFrame, awave: dict, bwave: dict) -> dict | None:
    """优化版启动信号检测 — 添加距A高检查"""
    result = check_launch_signal(df, awave, bwave)
    
    if result is None:
        return None
    
    # 优化3：启动信号要求距A高 > 0%（未突破A浪高点）
    dist_to_a_high = result.get('dist_to_a_high', 0)
    if dist_to_a_high <= 0:
        print(f'  优化过滤：距A高{dist_to_a_high:.1f}% ≤ 0%，已突破A高，非B浪末端')
        return None
    
    return result


def main():
    """主函数 — 复用原策略参数，但使用优化版函数"""
    parser = argparse.ArgumentParser(description='B浪低点识别策略（优化版）')
    parser.add_argument('codes', nargs='*')
    parser.add_argument('--pool', choices=['default', 'qualified', 'all'], default='qualified')
    parser.add_argument('--min-score', type=int, default=65)
    parser.add_argument('--debug', type=str, default='')
    parser.add_argument('--backtest', action='store_true')
    args = parser.parse_args()

    if args.debug:
        ts_code = normalize_ts_code(args.debug)
        df = get_data(ts_code)
        if df is None or len(df) < 130:
            log(f"无法获取 {ts_code} 数据")
            return
        
        # 使用优化版函数
        awave = detect_awave(df)
        if awave:
            bwave = detect_bwave(df, awave)
            bwave_r = detect_bwave_relaxed(df, awave) if not bwave else None
            if bwave:
                launch = optimized_check_launch_signal(df, awave, bwave)
                if launch:
                    score = optimized_calc_bwave_score(awave, bwave, launch)
                    log(f"启动信号: {launch['launch_date']} 评分={score['total']} ")
                div = optimized_detect_bwave_divergence(df, awave, bwave)
                if div:
                    s = calc_divergence_score(awave, bwave, div)
                    log(f"底背离信号: 评分={s['total']} ")
        return
    
    # 正常扫描模式
    stocks = load_pool(args.pool)
    log(f"优化版B浪策略 — 盘后扫描")
    log(f"  股票池: {args.pool} ({len(stocks)}只)")
    log(f"  最低评分: {args.min_score}")
    log()
    
    results = []
    total = len(stocks)
    
    for i, (ts_code, name) in enumerate(stocks):
        if (i + 1) % 100 == 0:
            log(f"  [{i+1}/{total}] 扫描中...已发现{len(results)}个B浪信号")
        
        df = get_data(ts_code)
        if df is None or len(df) < 130:
            continue
        
        awave = detect_awave(df)
        if not awave:
            continue
        
        bwave = detect_bwave(df, awave)
        bwave_r = detect_bwave_relaxed(df, awave) if not bwave else None
        bwave_used = bwave if bwave else bwave_r
        
        if not bwave_used:
            continue
        
        # 使用优化版函数
        launch = optimized_check_launch_signal(df, awave, bwave_used)
        div = optimized_detect_bwave_divergence(df, awave, bwave_used)
        
        if launch:
            score = optimized_calc_bwave_score(awave, bwave_used, launch)
            if score['total'] >= args.min_score:
                results.append((ts_code, name, awave, bwave_used, launch, score, 'launch'))
        
        if div:
            s = calc_divergence_score(awave, bwave_used, div)
            if s['total'] >= args.min_score:
                results.append((ts_code, name, awave, bwave_used, div, s, 'divergence'))
    
    # 输出结果（复用原策略的输出逻辑）
    log(f"\n扫描完成！BWaveScore≥{args.min_score}: {len(results)} 个")
    # ...（后续输出逻辑与原策略相同）


if __name__ == '__main__':
    main()
