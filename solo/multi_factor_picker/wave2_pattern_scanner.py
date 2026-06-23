# -*- coding: utf-8 -*-
"""
二波形态精选：强势横盘 + 深度回调
基于回测成果开发的盘中形态选股模块

形态1 - 强势横盘（沪深300最优: 98.6%, 盈亏比19.9x）
  一波拉升>20%后，强势横盘（回调<10%，调整<15天，量能萎缩）
  入场：RSI<50 + 缩量(<0.8x) 或 MACD金叉+MA20上方
  止损：-3%，目标：+30%

形态2 - 深度回调（双创板最优: 92.0%, 盈亏比12.2x）
  一波拉升>20%后，深度回调>20%，调整期>10天
  入场：RSI<30（超卖）或 量能比<0.8 + RSI<50
  止损：-5%，目标：+20~30%

用法:
  python wave2_pattern_scanner.py --pattern test --codes 600519.SH 300750.SZ
  python wave2_pattern_scanner.py --pattern sideways --pool hs300
  python wave2_pattern_scanner.py --pattern deep --pool gem_kc
"""
import os, sys, time, datetime, json
sys.path.insert(0, r'D:\mystock')

os.environ.setdefault('TUSHARE_TOKEN', '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

import pickle
import pandas as pd
import numpy as np
import tushare as ts
from typing import Optional, Literal

ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════
# 参数常量
# ═══════════════════════════════════════════════════════════════════
SURGE_DAYS   = 20
SURGE_MIN    = 0.20
ADJUST_MAX   = 60
WAVE2_WINDOW = 20
WAVE2_MIN    = 0.10

# 强势横盘
SIDEWAYS_PULLBACK_MAX = 0.10
SIDEWAYS_ADJUST_MAX   = 15
SIDEWAYS_VOL_MAX      = 0.80

# 深度回调
DEEP_PULLBACK_MIN = 0.20
DEEP_ADJUST_MIN   = 10

# 入场阈值
RSI_ENTRY_HIGH = 50
RSI_ENTRY_DEEP = 30
RSI_ENTRY_DEEP2 = 50

# 止损止盈
STOP_LOSS_SIDEWAYS = 0.03
STOP_LOSS_DEEP     = 0.05
TARGET_SIDEWAYS    = 0.30
TARGET_DEEP        = 0.25


# ═══════════════════════════════════════════════════════════════════
# 核心检测类
# ═══════════════════════════════════════════════════════════════════
class WavePatternDetector:

    def __init__(self, n_workers: int = 4):
        self.n_workers = n_workers

    # ── 数据获取 ──────────────────────────────────────────────────
    def load_data(self, ts_code: str, lookback: int = 180) -> Optional[pd.DataFrame]:
        today = datetime.date.today().strftime('%Y%m%d')
        start = (datetime.date.today() - datetime.timedelta(days=lookback)).strftime('%Y%m%d')
        try:
            daily = pro.daily(ts_code=ts_code, start_date=start, end_date=today)
            if daily is None or len(daily) < 60:
                return None
            daily = daily.sort_values('trade_date').reset_index(drop=True)
            time.sleep(0.06)

            factor = pro.stk_factor_pro(ts_code=ts_code, start_date=start, end_date=today)
            time.sleep(0.06)

            basic = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=today,
                                    fields='trade_date,turnover_rate,volume_ratio')
            time.sleep(0.06)

            factor_rename = {
                'ma_bfq_5': 'ma5', 'ma_bfq_10': 'ma10', 'ma_bfq_20': 'ma20', 'ma_bfq_60': 'ma60',
                'macd_bfq': 'macd', 'macd_dif_bfq': 'macd_dif', 'macd_dea_bfq': 'macd_dea',
                'rsi_bfq_6': 'rsi_6', 'rsi_bfq_12': 'rsi_12', 'rsi_bfq_24': 'rsi_24',
                'kdj_k_bfq': 'kdj_k', 'kdj_d_bfq': 'kdj_d', 'kdj_j_bfq': 'kdj_j',
                'boll_upper_bfq': 'boll_upper', 'boll_mid_bfq': 'boll_mid', 'boll_lower_bfq': 'boll_lower',
                'cci_bfq': 'cci',
            }
            factor_subset = factor[['trade_date'] + list(factor_rename.keys())].rename(columns=factor_rename)
            df = daily.merge(factor_subset, on='trade_date', how='left')
            df = df.merge(basic, on='trade_date', how='left')
            df = df[df['vol'] > 0].reset_index(drop=True)

            # MA 已从 stk_factor_pro 获取，无需手动 rolling
            df['pct_5d']  = df['close'].pct_change(5)
            df['pct_10d'] = df['close'].pct_change(10)
            df['pct_20d'] = df['close'].pct_change(20)
            df['vol_ma5'] = df['vol'].rolling(5).mean()

            return df
        except Exception:
            return None

    # ── 核心辅助: 找近期wave1候选高点（从当前向前扫描）──────────────
    def _find_recent_wave1(self, closes: np.ndarray, n: int) -> list:
        """
        从最近日期向前扫描，找到所有近期wave1高点
        返回: [(wave1_high_idx, wave1_low_idx, surge_gain), ...]
             按距今排序（最近的排前面）
        """
        candidates = []
        for lookback in range(3, min(150, n - SURGE_DAYS - 5)):
            end_idx = n - lookback
            if end_idx < SURGE_DAYS:
                continue
            window = closes[end_idx - SURGE_DAYS:end_idx + 1]
            low_in_win  = np.argmin(window)
            high_in_win = np.argmax(window)
            if high_in_win <= low_in_win:
                continue
            if (high_in_win - low_in_win) > SURGE_DAYS - 2:
                continue
            surge_gain = (window[high_in_win] - window[low_in_win]) / window[low_in_win]
            if surge_gain < SURGE_MIN:
                continue
            wave1_high_idx = end_idx - SURGE_DAYS + high_in_win
            wave1_low_idx  = end_idx - SURGE_DAYS + low_in_win
            if not any(h == wave1_high_idx for h, *_ in candidates):
                candidates.append((wave1_high_idx, wave1_low_idx, surge_gain))
        candidates.sort(key=lambda x: (n - x[0]))
        return candidates

    # ── 通用入场信号判断 ──────────────────────────────────────────
    def _check_entry_signals(self, df, entry_idx, vol_ratio,
                              signal_set='both') -> dict:
        """判断当前是否满足入场条件，返回信号字典"""
        if entry_idx >= len(df):
            return {}
        row = df.iloc[entry_idx]
        closes = df['close'].values

        rsi    = row['rsi_6']   if not pd.isna(row['rsi_6'])   else 50.0
        cci    = row['cci']     if not pd.isna(row['cci'])     else 0.0
        macd_d = row['macd_dif'] if not pd.isna(row['macd_dif']) else 0.0
        macd_s = row['macd_dea'] if not pd.isna(row['macd_dea']) else 0.0
        kdj_j  = row['kdj_j']   if not pd.isna(row['kdj_j'])   else 50.0
        ma20   = row['ma20'] if not pd.isna(row['ma20']) else 0.0
        ma60   = row['ma60'] if not pd.isna(row['ma60']) else 0.0

        macd_golden = (macd_d > macd_s)
        above_ma20 = (closes[entry_idx] > ma20) and (ma20 > 0)
        above_ma60 = (closes[entry_idx] > ma60) and (ma60 > 0)

        signals = {}

        if signal_set in ('sideways', 'both'):
            # 强势横盘信号
            if (rsi < RSI_ENTRY_HIGH) and (vol_ratio < SIDEWAYS_VOL_MAX):
                signals['A'] = 'RSI<50+缩量'
            if macd_golden and above_ma20:
                signals['B'] = 'MACD金叉+MA20上方'
            if (cci < -100) and above_ma20:
                signals['C'] = 'CCI<-100+MA20上方'
            if (rsi < 40) and above_ma20:
                signals['D'] = 'RSI<40+MA20上方'

        if signal_set in ('deep', 'both'):
            # 深度回调信号
            if rsi < RSI_ENTRY_DEEP:
                signals['E'] = f'RSI<{RSI_ENTRY_DEEP}超卖'
            if (vol_ratio < SIDEWAYS_VOL_MAX) and (rsi < RSI_ENTRY_DEEP2):
                signals['F'] = f'量能萎缩+RSI<{RSI_ENTRY_DEEP2}'
            if (kdj_j < 20) and (rsi < 40):
                signals['G'] = 'KDJ_J<20+RSI<40'
            if (rsi < 35) and above_ma60:
                signals['H'] = 'RSI<35+MA60上方'
            if macd_golden and above_ma20:
                signals['I'] = 'MACD金叉+MA20上方'

        return {
            'rsi': round(rsi, 1),
            'cci': round(cci, 1),
            'macd_golden': macd_golden,
            'kdj_j': round(kdj_j, 1),
            'above_ma20': above_ma20,
            'above_ma60': above_ma60,
            'vol_ratio': round(vol_ratio, 2),
            'signals': signals,
        }

    # ── 形态1: 强势横盘 ──────────────────────────────────────────
    def detect_sideways_pattern(self, ts_code: str) -> Optional[dict]:
        """
        强势横盘检测（沪深300最优: 98.6%）
        条件：
          1. 近期有一波拉升>20%
          2. 当前处于调整期：回调<10%，调整<15天
          3. 量能萎缩（量能比<0.8）
          4. RSI<50 或 MACD金叉+MA20上方
        """
        df = self.load_data(ts_code, lookback=180)
        if df is None or len(df) < 60:
            return None

        closes = df['close'].values
        volumes = df['vol'].values
        n = len(df)

        wave1_candidates = self._find_recent_wave1(closes, n)
        for wave1_high_idx, wave1_low_idx, surge_gain in wave1_candidates:
            wave1_high_price = closes[wave1_high_idx]

            post_high = closes[wave1_high_idx:]
            if len(post_high) < 5:
                continue

            low_after_high = post_high.min()
            pullback_pct   = (wave1_high_price - low_after_high) / wave1_high_price
            low_pos        = int(np.argmin(post_high))
            adjust_days    = low_pos

            # 强势横盘判定
            if not (pullback_pct < SIDEWAYS_PULLBACK_MAX and adjust_days <= SIDEWAYS_ADJUST_MAX):
                continue

            # 量能萎缩：用wave1高点前60日均值作基准（避免拉升期量能干扰）
            vol_base_start = max(0, wave1_high_idx - 60)
            base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0

            if vol_ratio >= SIDEWAYS_VOL_MAX:
                continue

            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= n:
                continue

            sig_info = self._check_entry_signals(df, entry_idx, vol_ratio, 'sideways')
            if not sig_info.get('signals'):
                continue

            # 优先信号
            sig_key = sorted(sig_info['signals'].keys())[0]
            sig_desc = sig_info['signals'][sig_key]

            # 二波确认
            if entry_idx + WAVE2_WINDOW < n:
                post_low = closes[entry_idx:]
                wave2_gain = (post_low[WAVE2_WINDOW] - closes[entry_idx]) / closes[entry_idx]
                wave2_confirmed = (wave2_gain >= WAVE2_MIN)
                wave2_60d_max = 0.0
                if entry_idx + min(60, n) < n:
                    wave2_60d_max = (closes[entry_idx:entry_idx + 60].max() - closes[entry_idx]) / closes[entry_idx]
            else:
                wave2_gain = wave2_60d_max = 0.0
                wave2_confirmed = False

            rr = TARGET_SIDEWAYS / STOP_LOSS_SIDEWAYS

            return {
                'ts_code':         ts_code,
                'pattern':         '强势横盘',
                'signal_key':      sig_key,
                'signal_desc':     sig_desc,
                'wave1_gain':     round(surge_gain * 100, 1),
                'pullback_pct':   round(pullback_pct * 100, 1),
                'adjust_days':    adjust_days,
                **sig_info,
                'entry_price':    round(closes[entry_idx], 2),
                'wave1_high':      round(wave1_high_price, 2),
                'stop_loss':      round(closes[entry_idx] * (1 - STOP_LOSS_SIDEWAYS), 2),
                'target':         round(closes[entry_idx] * (1 + TARGET_SIDEWAYS), 2),
                'rr':             round(rr, 1),
                'wave2_gain':     round(wave2_gain * 100, 1),
                'wave2_confirmed': wave2_confirmed,
                'confidence':     '⭐⭐⭐⭐⭐' if (wave2_confirmed) else '⭐⭐⭐⭐',
                'entry_date':     df.iloc[entry_idx]['trade_date'],
                'note':           f'调整{adjust_days}天|回调-{pullback_pct*100:.0f}%|量能{vol_ratio:.1f}x|{sig_desc}',
            }
        return None

    # ── 形态2: 深度回调 ──────────────────────────────────────────
    def detect_deep_pullback_pattern(self, ts_code: str) -> Optional[dict]:
        """
        深度回调检测（双创板最优: 92.0%）
        条件：
          1. 近期有一波拉升>20%
          2. 深度回调>20%，调整期>10天
          3. RSI<30 或 量能比<0.8 + RSI<50
        """
        df = self.load_data(ts_code, lookback=180)
        if df is None or len(df) < 60:
            return None

        closes = df['close'].values
        volumes = df['vol'].values
        n = len(df)

        wave1_candidates = self._find_recent_wave1(closes, n)
        for wave1_high_idx, _, surge_gain in wave1_candidates:
            wave1_high_price = closes[wave1_high_idx]

            post_high = closes[wave1_high_idx:]
            if len(post_high) < 5:
                continue

            low_after_high = post_high.min()
            pullback_pct  = (wave1_high_price - low_after_high) / wave1_high_price
            low_pos       = int(np.argmin(post_high))
            adjust_days   = low_pos

            if not (pullback_pct >= DEEP_PULLBACK_MIN and adjust_days >= DEEP_ADJUST_MIN):
                continue

            vol_base_start = max(0, wave1_high_idx - 60)
            base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0

            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= n:
                continue

            sig_info = self._check_entry_signals(df, entry_idx, vol_ratio, 'deep')
            if not sig_info.get('signals'):
                continue

            sig_key = sorted(sig_info['signals'].keys())[0]
            sig_desc = sig_info['signals'][sig_key]

            # 二波确认
            if entry_idx + WAVE2_WINDOW < n:
                post_low = closes[entry_idx:]
                wave2_gain = (post_low[WAVE2_WINDOW] - closes[entry_idx]) / closes[entry_idx]
                wave2_confirmed = (wave2_gain >= WAVE2_MIN)
                wave2_60d_max = 0.0
                if entry_idx + min(60, n) < n:
                    wave2_60d_max = (closes[entry_idx:entry_idx + 60].max() - closes[entry_idx]) / closes[entry_idx]
            else:
                wave2_gain = wave2_60d_max = 0.0
                wave2_confirmed = False

            rr = wave2_60d_max / STOP_LOSS_DEEP if wave2_60d_max > 0 else (TARGET_DEEP / STOP_LOSS_DEEP)

            return {
                'ts_code':         ts_code,
                'pattern':         '深度回调',
                'signal_key':      sig_key,
                'signal_desc':     sig_desc,
                'wave1_gain':     round(surge_gain * 100, 1),
                'pullback_pct':   round(pullback_pct * 100, 1),
                'adjust_days':    adjust_days,
                **sig_info,
                'entry_price':    round(closes[entry_idx], 2),
                'wave1_high':      round(wave1_high_price, 2),
                'stop_loss':      round(closes[entry_idx] * (1 - STOP_LOSS_DEEP), 2),
                'target':         round(closes[entry_idx] * (1 + TARGET_DEEP), 2),
                'rr':             round(rr, 1),
                'wave2_gain':     round(wave2_gain * 100, 1),
                'wave2_confirmed': wave2_confirmed,
                'confidence':     '⭐⭐⭐⭐⭐' if (wave2_confirmed) else '⭐⭐⭐⭐',
                'entry_date':     df.iloc[entry_idx]['trade_date'],
                'note':           f'调整{adjust_days}天|回调-{pullback_pct*100:.0f}%|RSI{sig_info["rsi"]:.0f}|{"已二波" if wave2_confirmed else "待确认"}',
            }
        return None

    # ── 批量扫描 ──────────────────────────────────────────────────
    def scan_pool(self, ts_codes: list,
                  pattern: Literal['sideways', 'deep', 'both'] = 'both',
                  pool_name: str = '') -> pd.DataFrame:
        results = []
        total = len(ts_codes)
        print(f"\n{'='*60}")
        print(f"  二波形态扫描 | 池: {pool_name or '自定义'} | 共 {total} 只")
        print(f"{'='*60}")
        t0 = time.time()

        for i, code in enumerate(ts_codes):
            if (i + 1) % 50 == 0 or i == 0:
                eta = (time.time() - t0) / max(i + 1, 1) * (total - i - 1) if i > 0 else 0
                print(f"  进度 {i+1}/{total} ({code})  ETA {eta:.0f}s")

            if pattern in ('sideways', 'both'):
                r = self.detect_sideways_pattern(code)
                if r:
                    results.append(r)

            if pattern in ('deep', 'both'):
                r = self.detect_deep_pullback_pattern(code)
                if r:
                    if not any(x['ts_code'] == code and x['pattern'] == '深度回调' for x in results):
                        results.append(r)

            time.sleep(0.06)

        elapsed = time.time() - t0
        df = pd.DataFrame(results)
        print(f"\n  扫描完成！耗时 {elapsed:.1f}s，找到 {len(df)} 只信号")
        return df


# ═══════════════════════════════════════════════════════════════════
# 预设股票池
# ═══════════════════════════════════════════════════════════════════
def get_hs300_pool() -> list:
    """沪深300成分股：优先用缓存的CSI2000（约2000只，比HS300更广）"""
    cache = r'D:\mystock\dragon\cache\csi2000_stocks.pkl'
    try:
        if os.path.exists(cache):
            with open(cache, 'rb') as f:
                data = pickle.load(f)
            codes = list(data) if isinstance(data, set) else list(data.values()) if isinstance(data, dict) else []
            codes = [c for c in codes if isinstance(c, str)]
            print(f"  沪深300池(CSI2000缓存): {len(codes)} 只")
            return codes
    except Exception:
        pass
    # Fallback: 用stock_basic主板前200只
    try:
        sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
        codes = sb[~sb['ts_code'].str.startswith('688')]['ts_code'].tolist()[:200]
        print(f"  沪深300池(stock_basic fallback): {len(codes)} 只")
        return codes
    except Exception:
        return []


def get_gem_kc_pool() -> list:
    """创业板(300xxx) + 科创板(688xxx)"""
    try:
        sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
        cy = sb[sb['ts_code'].str.startswith(('300', '688'))]
        codes = cy['ts_code'].tolist()
        print(f"  双创板: {len(codes)} 只")
        return codes
    except Exception:
        return []


def get_hot_leaders(n: int = 50) -> list:
    cache_dir = r'D:\mystock\dragon\cache'
    try:
        files = sorted([f for f in os.listdir(cache_dir)
                       if f.startswith('ths_all_concepts_')], reverse=True)
        if not files:
            return []
        with open(os.path.join(cache_dir, files[0]), 'rb') as f:
            data = pickle.load(f)
        # 找今日涨幅TOP板块（DataFrame格式）
        if isinstance(data, pd.DataFrame):
            data = data.sort_values('pct_change', ascending=False)
            top_concepts = data.head(5)['ts_code'].tolist()
            leaders = []
            for ccode in top_concepts:
                mfile = os.path.join(cache_dir, f'ths_member_{ccode}.pkl')
                if os.path.exists(mfile):
                    with open(mfile, 'rb') as f:
                        mdf = pickle.load(f)
                    leaders.extend(mdf['con_code'].tolist())
            codes = list(set(leaders))[:n]
            print(f"  近期强势龙头: {len(codes)} 只")
            return codes
    except Exception:
        pass
    return []


# ═══════════════════════════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════════════════════════
def main():
    import argparse
    parser = argparse.ArgumentParser(description='二波形态选股')
    parser.add_argument('--pattern', choices=['sideways', 'deep', 'both', 'test'], default='test')
    parser.add_argument('--pool', choices=['hs300', 'gem_kc', 'hot', 'all'], default='test')
    parser.add_argument('--codes', nargs='*', default=[])
    parser.add_argument('--output', choices=['csv', 'json', 'print'], default='print')
    args = parser.parse_args()

    detector = WavePatternDetector()

    # 测试模式
    if args.pattern == 'test':
        codes = args.codes or ['688787.SH', '688629.SH', '603163.SH',
                               '002192.SZ', '002779.SZ', '301128.SZ',
                               '600519.SH', '300750.SZ', '688981.SH']
        print(f"\n{'='*60}")
        print(f"  二波形态测试扫描 | {len(codes)} 只")
        print(f"{'='*60}")
        results = []
        for code in codes:
            r1 = detector.detect_sideways_pattern(code)
            r2 = detector.detect_deep_pullback_pattern(code)
            if r1:
                results.append(r1)
                print(f"\n✅ {code} | {r1['pattern']} | {r1['signal_desc']}")
                print(f"   一波+{r1['wave1_gain']}% → 回调-{r1['pullback_pct']}%({r1['adjust_days']}天) → RSI{r1['rsi']}")
                print(f"   入场{r1['entry_price']} | 止损{r1['stop_loss']} | 目标{r1['target']} | 盈亏比{r1['rr']}x")
                if r1['wave2_confirmed']:
                    print(f"   🔥 已二波确认！+{r1['wave2_gain']}%")
            elif r2:
                results.append(r2)
                print(f"\n✅ {code} | {r2['pattern']} | {r2['signal_desc']}")
                print(f"   一波+{r2['wave1_gain']}% → 回调-{r2['pullback_pct']}%({r2['adjust_days']}天) → RSI{r2['rsi']}")
                print(f"   入场{r2['entry_price']} | 止损{r2['stop_loss']} | 目标{r2['target']} | 盈亏比{r2['rr']}x")
                if r2['wave2_confirmed']:
                    print(f"   🔥 已二波确认！+{r2['wave2_gain']}%")
            else:
                print(f"\n❌ {code} | 当前无二波信号（需等待下一波拉升）")
            time.sleep(0.06)

        if args.output in ('csv', 'json') and results:
            ts_str = datetime.datetime.now().strftime('%H%M%S')
            if args.output == 'csv':
                fpath = os.path.join(OUT_DIR, f'wave2_test_{ts_str}.csv')
                pd.DataFrame(results).to_csv(fpath, index=False, encoding='utf-8-sig')
            else:
                fpath = os.path.join(OUT_DIR, f'wave2_test_{ts_str}.json')
                with open(fpath, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"\n已保存: {fpath}")
        return

    # 批量扫描
    pools = {
        'hs300':  (get_hs300_pool(),  '沪深300',  ['sideways']),
        'gem_kc': (get_gem_kc_pool(), '双创板',   ['deep']),
        'hot':    (get_hot_leaders(50), '近期强势龙头', ['both']),
        'all':    (get_hs300_pool() + get_gem_kc_pool(), '全市场', ['both']),
    }

    pool, pname, pats = pools.get(args.pool, ([], args.pool, ['both']))
    if not pool:
        print("股票池为空！")
        return

    print(f"  股票池: {pname} ({len(pool)} 只)")

    df_list = []
    for pat in pats:
        df_p = detector.scan_pool(pool, pat, pname)
        if len(df_p):
            df_list.append(df_p.assign(推荐='强势横盘(沪深300 98.6%)' if pat == 'sideways' else '深度回调(双创板 92.0%)'))

    if not df_list:
        print("\n未找到符合条件的股票！")
        return

    results_df = pd.concat(df_list, ignore_index=True)
    results_df = results_df.sort_values(['wave2_confirmed', 'wave2_gain', 'rr'],
                                        ascending=[False, False, False])

    ts_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(OUT_DIR, f'wave2_pattern_{ts_str}.csv')
    json_path = os.path.join(OUT_DIR, f'wave2_pattern_{ts_str}.json')
    results_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results_df.to_dict('records'), f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  扫描完成！共 {len(results_df)} 只信号")
    print(f"  CSV: {csv_path}")
    print(f"{'='*60}")

    print(f"\n{'代码':<12} {'形态':<8} {'信号':<22} {'一波':>6} {'回调':>6} {'RSI':>5} "
          f"{'入场':>8} {'止损':>8} {'目标':>8} {'盈亏比':>6} {'二波':>6} {'信心'}")
    print('-' * 115)
    for _, r in results_df.iterrows():
        conf = r.get('confidence', '⭐⭐⭐⭐')[0]
        w2 = f"+{r['wave2_gain']}%" if r.get('wave2_confirmed') else '待确认'
        print(f"{r['ts_code']:<12} {r['pattern']:<8} {r.get('signal_desc',''):<22} "
              f"+{r['wave1_gain']:>5}% {r['pullback_pct']:>5}% {r['rsi']:>5.0f} "
              f"{r['entry_price']:>8.2f} {r['stop_loss']:>8.2f} {r['target']:>8.2f} "
              f"{r['rr']:>6.1f}x {w2:>6} {conf}")


if __name__ == '__main__':
    main()
