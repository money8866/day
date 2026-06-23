# -*- coding: utf-8 -*-
"""Patch wave2_pattern_scanner.py: 修复扫描方向"""
import re

path = r'D:\mystock\solo\multi_factor_picker\wave2_pattern_scanner.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

helper = '''
    # ── 辅助: 找历史上最近的wave1高点（向前扫描） ───────────────────
    def _find_recent_wave1(self, closes, volumes, n):
        """从最近日期向前扫描，找到所有近期wave1高点"""
        candidates = []
        for lookback in range(5, min(150, n - SURGE_DAYS - 5)):
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
                candidates.append((wave1_high_idx, wave1_low_idx, surge_gain,
                                  closes[wave1_low_idx], closes[wave1_high_idx]))
        candidates.sort(key=lambda x: (n - x[0]))
        return candidates

'''

# 插入 _find_recent_wave1 helper
insert_marker = '    # ── 形态1: 强势横盘 ──────────────────────────────────────────\n    def detect_sideways_pattern'
content = content.replace(insert_marker, helper + insert_marker)

# ── 替换强势横盘循环 ──
old_s = '''        for i in range(SURGE_DAYS + ADJUST_MAX, n):
            # ── Step 1: 找一波拉升高点 ──
            window_closes = closes[i - SURGE_DAYS:i + 1]
            low_idx_win  = np.argmin(window_closes)
            high_idx_win = np.argmax(window_closes)
            if high_idx_win <= low_idx_win:
                continue
            if (high_idx_win - low_idx_win) > SURGE_DAYS - 2:
                continue
            surge_gain = (window_closes[high_idx_win] - window_closes[low_idx_win]) / window_closes[low_idx_win]
            if surge_gain < SURGE_MIN:
                continue

            wave1_high_idx  = i - SURGE_DAYS + high_idx_win
            wave1_low_idx   = i - SURGE_DAYS + low_idx_win
            wave1_high_price = closes[wave1_high_idx]

            # ── Step 2: 调整期分析 ──
            post_high = closes[wave1_high_idx:]
            if len(post_high) < 5:
                continue

            low_after_high  = post_high.min()
            pullback_pct    = (wave1_high_price - low_after_high) / wave1_high_price
            low_pos         = int(np.argmin(post_high))
            adjust_days     = int(low_pos)

            # 强势横盘判定
            if not (pullback_pct < SIDEWAYS_PULLBACK_MAX and adjust_days <= SIDEWAYS_ADJUST_MAX):
                continue

            # ── Step 3: 量能萎缩 ──
            if wave1_high_idx >= 20:
                base_vol = volumes[wave1_high_idx - 20:wave1_high_idx].mean()
            else:
                base_vol = volumes[:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0
            if vol_ratio >= SIDEWAYS_VOL_MAX:
                continue

            # ── Step 4: 入场信号 ──
            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= len(df):
                continue'''

new_s = '''        # 找所有近期wave1候选高点（从当前向前扫描）
        wave1_candidates = self._find_recent_wave1(closes, volumes, n)

        for wave1_high_idx, wave1_low_idx, surge_gain, _, wave1_high_price in wave1_candidates:
            # ── Step 2: 调整期分析 ──
            post_high = closes[wave1_high_idx:]
            if len(post_high) < 5:
                continue

            low_after_high = post_high.min()
            pullback_pct  = (wave1_high_price - low_after_high) / wave1_high_price
            low_pos       = int(np.argmin(post_high))
            adjust_days   = low_pos

            # 强势横盘判定
            if not (pullback_pct < SIDEWAYS_PULLBACK_MAX and adjust_days <= SIDEWAYS_ADJUST_MAX):
                continue

            # ── Step 3: 量能萎缩 ──
            if wave1_high_idx >= 20:
                base_vol = volumes[wave1_high_idx - 20:wave1_high_idx].mean()
            else:
                base_vol = volumes[:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0
            if vol_ratio >= SIDEWAYS_VOL_MAX:
                continue

            # ── Step 4: 入场信号（entry = 调整最低点）─
            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= n:
                continue'''

content = content.replace(old_s, new_s)

# ── 替换深度回调循环 ──
old_d = '''        for i in range(SURGE_DAYS + ADJUST_MAX, n):
            # ── Step 1: 找一波拉升高点 ──
            window_closes = closes[i - SURGE_DAYS:i + 1]
            low_idx_win  = np.argmin(window_closes)
            high_idx_win = np.argmax(window_closes)
            if high_idx_win <= low_idx_win:
                continue
            if (high_idx_win - low_idx_win) > SURGE_DAYS - 2:
                continue
            surge_gain = (window_closes[high_idx_win] - window_closes[low_idx_win]) / window_closes[low_idx_win]
            if surge_gain < SURGE_MIN:
                continue

            wave1_high_idx  = i - SURGE_DAYS + high_idx_win
            wave1_high_price = closes[wave1_high_idx]

            # ── Step 2: 深度回调判定 ──
            post_high = closes[wave1_high_idx:]
            if len(post_high) < 5:
                continue

            low_after_high = post_high.min()
            pullback_pct   = (wave1_high_price - low_after_high) / wave1_high_price
            low_pos        = np.argmin(post_high)
            adjust_days    = int(low_pos)

            if not (pullback_pct >= DEEP_PULLBACK_MIN and adjust_days >= DEEP_ADJUST_MIN):
                continue

            # ── Step 3: 量能萎缩 ──
            if wave1_high_idx >= 20:
                base_vol = volumes[wave1_high_idx - 20:wave1_high_idx].mean()
            else:
                base_vol = volumes[:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0

            # ── Step 4: 入场信号 ──
            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= len(df):
                continue'''

new_d = '''        # 找所有近期wave1候选高点
        wave1_candidates = self._find_recent_wave1(closes, volumes, n)

        for wave1_high_idx, _, surge_gain, _, wave1_high_price in wave1_candidates:
            # ── Step 2: 深度回调判定 ──
            post_high = closes[wave1_high_idx:]
            if len(post_high) < 5:
                continue

            low_after_high = post_high.min()
            pullback_pct  = (wave1_high_price - low_after_high) / wave1_high_price
            low_pos       = int(np.argmin(post_high))
            adjust_days   = low_pos

            if not (pullback_pct >= DEEP_PULLBACK_MIN and adjust_days >= DEEP_ADJUST_MIN):
                continue

            # ── Step 3: 量能萎缩 ──
            if wave1_high_idx >= 20:
                base_vol = volumes[wave1_high_idx - 20:wave1_high_idx].mean()
            else:
                base_vol = volumes[:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0

            # ── Step 4: 入场信号 ──
            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= n:
                continue'''

content = content.replace(old_d, new_d)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f'修复完成！修改了 {len(content)} 字节')
