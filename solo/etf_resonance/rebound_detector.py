
import sys
import pandas as pd
import numpy as np
sys.path.insert(0, r'd:\mystock\solo')
import tushare_quant as tq
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from etf_resonance.wave3_detector import (
    find_pivots, Pivot, PIVOT_WINDOW, W1_MIN_GAIN, W2_RETRACE_MIN, W2_RETRACE_MAX
)

@dataclass
class SimpleWave:
    L0: Pivot
    H1: Pivot
    L2: Pivot
    w1_gain: float
    w2_retrace: float

@dataclass
class ReboundSignal:
    ts_code: str
    name: str
    industry: str
    wave: SimpleWave
    current_price: float
    rebound_score: float
    reasons: List[str] = field(default_factory=list)
    signal_type: str = '回升买点'
    
    ma5: float = None
    ma10: float = None
    ma20: float = None
    vol_ratio: float = None
    days_since_L2: int = None
    rebound_pct: float = None
    dist_to_H1_pct: float = None

def find_simple_wave(pivots: List[Pivot], df: pd.DataFrame) -> Optional[SimpleWave]:
    """
    简化版波浪检测，只找L0→H1→L2的结构用于回升买点
    比detect_waves更宽松，不需要H3/L4/H5
    """
    if len(pivots) < 3:
        return None
    
    best_wave = None
    best_score = -1
    
    # 寻找连续的 L→H→L 结构
    for i in range(len(pivots) - 2):
        # 寻找 L0
        if pivots[i].kind != 'low':
            continue
        L0 = pivots[i]
        
        # 寻找下一个高点 H1
        H1 = None
        for j in range(i+1, len(pivots)):
            if pivots[j].kind == 'high':
                H1 = pivots[j]
                break
        if H1 is None:
            continue
        
        # 寻找下一个低点 L2
        L2 = None
        for j in range(i+2, len(pivots)):
            if pivots[j].kind == 'low':
                L2 = pivots[j]
                break
        if L2 is None:
            continue
        
        # 计算涨幅和回调
        w1_gain = (H1.price - L0.price) / max(L0.price, 0.01)
        w2_retrace = (H1.price - L2.price) / max(H1.price - L0.price, 0.01)
        
        # 条件检查（比wave3_detector稍微宽松）
        if w1_gain < W1_MIN_GAIN:  # 0.40
            continue
        
        if not (W2_RETRACE_MIN <= w2_retrace <= W2_RETRACE_MAX):
            continue
        
        if L2.price <= L0.price:
            continue
        
        # 还要检查L2的位置要在H1之后
        if L2.idx < H1.idx:
            continue
        
        # 评分选最好的
        score = w1_gain * 10
        if score > best_score:
            best_score = score
            best_wave = SimpleWave(
                L0=L0, H1=H1, L2=L2, w1_gain=w1_gain, w2_retrace=w2_retrace
            )
    
    return best_wave

def detect_rebound_signal(wave: SimpleWave, df: pd.DataFrame, name: str = '', industry: str = '') -> Optional[ReboundSignal]:
    """
    检测回升买点信号
    """
    if wave is None:
        return None
    
    # 1. 基础条件
    current_price = df['close'].values[-1]
    
    # L2必须已经是确定的点，不能是未来的
    # 确保最后一个收盘价 > L2（已经确认回升）
    if current_price < wave.L2.price:
        return None
    
    # 计算L2后的天数
    # 找出L2在dataframe中的位置
    # 遍历查找
    l2_idx = -1
    for i, d in enumerate(df['trade_date']):
        if str(d) == wave.L2.date:
            l2_idx = i
            break
    if l2_idx == -1:
        return None
    
    days_since_L2 = len(df) - l2_idx - 1
    
    if days_since_L2 <3:
        return None
    if days_since_L2 >30:
        return None
    
    # 不能已经突破 H1，突破了就应该用wave3_detector
    if current_price > wave.H1.price:
        return None
    
    # 2. 计算均线和量比
    closes = df['close'].values
    vol = df['vol'].values if 'vol' in df.columns else np.zeros(len(df))
    
    ma5 = np.mean(closes[-5:]) if len(closes)>=5 else closes[-1]
    ma10 = np.mean(closes[-10:]) if len(closes)>=10 else closes[-1]
    ma20 = np.mean(closes[-20:]) if len(closes)>=20 else closes[-1]
    
    vol_5 = np.mean(vol[-5:])
    vol_20 = np.mean(vol[-20:]) if len(vol)>=20 else vol[-1]
    vol_ratio = vol_5/vol_20 if vol_20>0 else 0
    
    # 3. 计算回升幅度
    l2_price = wave.L2.price
    rebound_pct = (current_price / l2_price -1)*100
    
    dist_to_H1_pct = (wave.H1.price / current_price -1)*100
    
    # 4. 计算评分
    score =0
    reasons = []
    
    if current_price>ma5:
        score +=15; reasons.append('MA5已突破')
    if current_price>ma10:
        score +=15; reasons.append('MA10已突破')
    if current_price>ma20:
        score +=20; reasons.append('MA20已突破')
    
    if vol_ratio>1.0:
        score +=15; reasons.append(f'量比{vol_ratio:.2f}放大')
    
    if rebound_pct>5:
        score +=15; reasons.append(f'回升{rebound_pct:.1f}%')
    elif rebound_pct>0:
        score +=10; reasons.append(f'回升{rebound_pct:.1f}%')
    
    if dist_to_H1_pct>5:
        score +=10; reasons.append(f'距H1还有{dist_to_H1_pct:.1f}%空间')
    
    if 5<=days_since_L2<=15:
        score +=10; reasons.append(f'回调后{days_since_L2}天黄金时间窗口')
    
    # 5. 过滤条件
    if rebound_pct<=0:
        return None
    
    return ReboundSignal(
        ts_code=df['ts_code'].iloc[-1] if 'ts_code' in df.columns else '',
        name=name, industry=industry,
        wave=wave,
        current_price=current_price,
        rebound_score=score,
        reasons=reasons,
        ma5=ma5, ma10=ma10, ma20=ma20,
        vol_ratio=vol_ratio,
        days_since_L2=days_since_L2,
        rebound_pct=rebound_pct,
        dist_to_H1_pct=dist_to_H1_pct
    )

def print_rebound_signal(sig: ReboundSignal) -> None:
    w = sig.wave
    print(f"\n{'='*70}")
    print(f"  [回升买点信号] {sig.name} ({sig.ts_code})")
    print(f"{'='*70}")
    
    print(f"\n波浪基础:")
    print(f"  L0: {w.L0.date} / {w.L0.price:.2f}")
    print(f"  H1: {w.H1.date} / {w.H1.price:.2f}")
    print(f"  L2: {w.L2.date} / {w.L2.price:.2f}")
    print(f"  W1涨幅: {w.w1_gain*100:.1f}%, W2回调: {w.w2_retrace*100:.1f}%")
    
    print(f"\n当前状态:")
    print(f"  当前价: {sig.current_price:.2f}")
    print(f"  距 L2 时间: {sig.days_since_L2}天")
    print(f"  从 L2 回升: {sig.rebound_pct:.1f}%")
    print(f"  距 H1 空间: {sig.dist_to_H1_pct:.1f}%")
    
    print(f"\n技术面:")
    print(f"  MA5: {sig.ma5:.2f}  MA10: {sig.ma10:.2f}  MA20: {sig.ma20:.2f}")
    print(f"  量比: {sig.vol_ratio:.2f}")
    
    print(f"\n回升买点综合分: {sig.rebound_score:.1f}/100")
    print(f"理由: {'; '.join(sig.reasons)}")


def scan_rebound_signals(scope='etf', min_score=60, top_n=20):
    """扫描回升买点信号

    Args:
        scope: 'etf' = ETF成份股池, 'all' = 全市场
        min_score: 最低评分阈值
        top_n: 显示前N个结果
    """
    import os
    import time

    print('='*70)
    print(f'  回升买点扫描 (scope={scope}, min_score={min_score})')
    print('='*70)

    # 加载股票池
    codes = []
    name_map = {}
    industry_map = {}

    if scope == 'etf':
        # 从wave3_signals.csv读取ETF成份股池（与wave3_detector共用）
        csv_path = os.path.join(os.path.dirname(__file__), 'output', 'wave3_signals.csv')
        if os.path.exists(csv_path):
            df_pool = pd.read_csv(csv_path)
            for _, row in df_pool.iterrows():
                codes.append(row['code'])
                name_map[row['code']] = row.get('name', '')
                industry_map[row['code']] = row.get('industry', '')
        # 补充：从ETF成份股缓存加载完整池
        cons_path = r'D:\mystock\cache_daily\etf_constituents_all.json'
        if os.path.exists(cons_path):
            import json
            with open(cons_path, 'r', encoding='utf-8') as f:
                cons_data = json.load(f)
            # 兼容两种格式: dict{etf_code: [stock_dict]} 或 list[stock_dict]
            if isinstance(cons_data, dict):
                for etf_code, stocks in cons_data.items():
                    for s in stocks:
                        tc = s.get('ts_code', '') if isinstance(s, dict) else str(s)
                        if tc and tc not in codes:
                            codes.append(tc)
                            if isinstance(s, dict):
                                name_map[tc] = s.get('name', '')
            elif isinstance(cons_data, list):
                for s in cons_data:
                    tc = s.get('ts_code', '') if isinstance(s, dict) else str(s)
                    if tc and tc not in codes:
                        codes.append(tc)
                        if isinstance(s, dict):
                            name_map[tc] = s.get('name', '')
        print(f'加载股票池: {len(codes)} 只')
    else:
        # 全市场：从market缓存读取
        market_path = r'd:\mystock\cache_daily\market_20260709.csv'
        if os.path.exists(market_path):
            df_m = pd.read_csv(market_path)
            codes = df_m['ts_code'].tolist()
        print(f'加载全市场: {len(codes)} 只')

    found_signals = []
    total = len(codes)

    for i, code in enumerate(codes):
        if (i + 1) % 100 == 0:
            print(f'  进度: {i+1}/{total}  已找到 {len(found_signals)} 个信号')

        df = tq.get_hist_data(code)
        if df is None or len(df) < 60:
            continue

        try:
            pivots = find_pivots(df)
            wave = find_simple_wave(pivots, df)
            if not wave:
                continue

            name = name_map.get(code, '')
            industry = industry_map.get(code, '')
            sig = detect_rebound_signal(wave, df, name, industry)
            if sig and sig.rebound_score >= min_score:
                sig.ts_code = code
                found_signals.append(sig)
        except Exception:
            continue

    found_signals.sort(key=lambda s: -s.rebound_score)

    print(f'\n[结果] 发现 {len(found_signals)} 个回升买点信号 (评分≥{min_score}):')
    print(f'{"代码":12s} {"名称":10s} {"现价":>8s} {"信号分":>6s} {"距H1":>8s} {"回升":>8s} {"L2后天数":>8s}')
    print('-' * 70)

    for sig in found_signals[:top_n]:
        print(f'{sig.ts_code:12s} {sig.name:10s} {sig.current_price:8.2f} {sig.rebound_score:6.1f} {sig.dist_to_H1_pct:7.1f}% {sig.rebound_pct:7.1f}% {sig.days_since_L2:8d}天')

    # 打印前3个详细结果
    if found_signals:
        print(f'\n{"="*70}')
        print('  详细结果 (前3个)')
        print(f'{"="*70}')
        for i, sig in enumerate(found_signals[:3]):
            print(f'\n--- 第{i+1}名 ---')
            print_rebound_signal(sig)

    # 保存结果到CSV
    if found_signals:
        output_path = os.path.join(os.path.dirname(__file__), 'output', 'rebound_signals.csv')
        rows = []
        for sig in found_signals:
            w = sig.wave
            rows.append({
                'code': sig.ts_code, 'name': sig.name, 'industry': sig.industry,
                'current_price': sig.current_price,
                'rebound_score': sig.rebound_score,
                'w1_start_date': w.L0.date, 'w1_end_date': w.H1.date, 'w2_end_date': w.L2.date,
                'w1_gain_pct': round(w.w1_gain * 100, 1),
                'w2_retrace_pct': round(w.w2_retrace * 100, 1),
                'days_since_L2': sig.days_since_L2,
                'rebound_pct': round(sig.rebound_pct, 1),
                'dist_to_H1_pct': round(sig.dist_to_H1_pct, 1),
                'ma5': round(sig.ma5, 2), 'ma10': round(sig.ma10, 2), 'ma20': round(sig.ma20, 2),
                'vol_ratio': round(sig.vol_ratio, 2),
                'reasons': '; '.join(sig.reasons),
            })
        df_out = pd.DataFrame(rows)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        df_out.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f'\n[已保存] {output_path}')

    return found_signals


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='回升买点扫描器')
    parser.add_argument('--scope', type=str, default='etf', choices=['etf', 'all'],
                        help='扫描范围: etf=ETF成份股池, all=全市场')
    parser.add_argument('--min-score', type=float, default=60,
                        help='最低评分阈值 (默认60)')
    parser.add_argument('--top', type=int, default=20,
                        help='显示前N个结果 (默认20)')
    parser.add_argument('--code', type=str, default='',
                        help='只检测单只股票 (如 002371.SZ)')
    args = parser.parse_args()

    if args.code:
        # 单只股票检测
        print(f'=== 单只股票检测: {args.code} ===')
        df = tq.get_hist_data(args.code)
        if df is None or len(df) < 60:
            print('数据不足')
        else:
            pivots = find_pivots(df)
            print(f'找到 {len(pivots)} 个枢轴点')
            wave = find_simple_wave(pivots, df)
            if wave:
                print(f'L0: {wave.L0.date} / {wave.L0.price:.2f}')
                print(f'H1: {wave.H1.date} / {wave.H1.price:.2f}')
                print(f'L2: {wave.L2.date} / {wave.L2.price:.2f}')
                print(f'W1涨幅: {wave.w1_gain*100:.1f}%, W2回调: {wave.w2_retrace*100:.1f}%')
                sig = detect_rebound_signal(wave, df, args.code, '')
                if sig:
                    sig.ts_code = args.code
                    print_rebound_signal(sig)
                else:
                    print('未检测到回升买点（可能已突破H1或还在下跌中）')
            else:
                print('未找到有效的波浪结构')
    else:
        # 批量扫描
        scan_rebound_signals(scope=args.scope, min_score=args.min_score, top_n=args.top)

