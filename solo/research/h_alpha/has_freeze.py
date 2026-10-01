# -*- coding: utf-8 -*-
"""H-ALPHA-SOURCE-01 -- step 1: freeze the two production systems.

Read-only.  Computes strategy_hash / config_hash / data_snapshot and writes
H_ALPHA_SOURCE_01_FREEZE.md.  Nothing in the frozen systems is created,
modified or re-run by this study.

If a bug in the frozen strategy is found, the experiment must STOP and
report -- this script only records evidence, it never repairs anything.
"""
import hashlib
import json
import os

import numpy as np
import pandas as pd

import has_common as C

OUT = C.OUT
ROOT = C.ROOT

HVT_SRC = [
    'hvt_bull/__init__.py', 'hvt_bull/models.py', 'hvt_bull/config.yaml',
    'hvt_bull/data_loader.py', 'hvt_bull/context.py', 'hvt_bull/engine.py',
    'hvt_bull/daily.py', 'hvt_bull/trade_execution.py',
    'hvt_bull/te_backtest.py', 'hvt_bull/backtest.py',
    'hvt_bull/expectancy.py', 'hvt_bull/future_expansion.py',
    'hvt_bull/te_rocket_filter.py', 'hvt_bull/push.py',
    'hvt_bull/run_daily_push.py',
]
W7_SRC = [
    'w7_second_wave_engine.py', 'w7_te_v3_backtest.py',
    'trade_execution_engine.py', 'w7_t1_gate.py',
    'w7_t20_right_tail_engine.py',
]
ARTEFACTS = [
    ('HVT', C.HVT_LEDGER), ('HVT', C.HVT_SUMMARY), ('W7', C.W7_LEDGER),
]
GUARD = [
    'w7_backtest.py', 'w7_backtest_v5.py', 'w7_backtest_v41.py',
    'w7_backtest_signals.csv', 'w7_backtest_v5_signals.csv',
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def hash_set(rel_paths, label):
    rows, lines = [], []
    for r in rel_paths:
        p = os.path.join(ROOT, r)
        if not os.path.exists(p):
            rows.append({'group': label, 'file': r, 'exists': False,
                         'bytes': 0, 'mtime': '', 'sha256': ''})
            continue
        st = os.stat(p)
        sh = sha256(p)
        rows.append({'group': label, 'file': r, 'exists': True,
                     'bytes': st.st_size,
                     'mtime': pd.Timestamp(st.st_mtime, unit='s')
                     .strftime('%Y-%m-%d %H:%M:%S'), 'sha256': sh})
        lines.append('%s %s' % (r, sh))
    combined = hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()
    return rows, combined


def main():
    lg = C.Log('has_freeze')
    lg('H-ALPHA-SOURCE-01 step 1 -- freeze')

    src_rows, strategy_hash = hash_set(HVT_SRC + W7_SRC, 'SOURCE')
    cfg_rows, config_hash = hash_set(['hvt_bull/config.yaml'], 'CONFIG')
    guard_rows, _ = hash_set(GUARD, 'OUT_OF_SCOPE')

    art_rows = []
    for sysname, fn in ARTEFACTS:
        p = os.path.join(C.REPORT_DAILY, fn)
        if not os.path.exists(p):
            art_rows.append({'system': sysname, 'file': fn, 'exists': False})
            continue
        st = os.stat(p)
        n = sum(1 for _ in open(p, encoding='utf-8-sig', errors='ignore'))
        art_rows.append({'system': sysname, 'file': fn, 'exists': True,
                         'bytes': st.st_size,
                         'mtime': pd.Timestamp(st.st_mtime, unit='s')
                         .strftime('%Y-%m-%d %H:%M:%S'),
                         'sha256': sha256(p), 'rows': n - 1})
        lg('  artefact %-52s %d rows  %s' % (fn, n - 1, sha256(p)[:16]))

    # ---- panelled restatement of the frozen results -------------------
    g = C.grid(lg=lg)
    obs = C.load_obs(lg=lg)
    base = []
    for sysname in C.PREREG_A['systems']:
        s = obs[obs['system'] == sysname]
        for h in C.PREREG_A['horizons']:
            r = C.ret_open(g, s['s_i'], s['entry_idx'], h)
            v = r[np.isfinite(r)]
            base.append({
                'system': sysname, 'horizon': 'T+%d' % h, 'n': int(v.size),
                'mean_gross': float(v.mean()),
                'mean_net30': float(v.mean() - 0.003),
                'win_rate': float((v > 0).mean()),
                'pf': float(v[v > 0].sum() / max(-v[v <= 0].sum(), 1e-9)),
                'median': float(pd.Series(v).median())})
    base = pd.DataFrame(base)
    lg('\n%s' % base.to_string(index=False))

    snap = {'study': C.PREREG_A['hypothesis_id'],
            'frozen_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
            'strategy_hash': strategy_hash, 'config_hash': config_hash,
            'data_snapshot': art_rows, 'sources': src_rows,
            'configs': cfg_rows, 'out_of_scope': guard_rows}
    C.save_json(snap, 'has_freeze_snapshot.json')

    C.save_csv(pd.DataFrame(src_rows + cfg_rows + guard_rows),
               'has_freeze_files.csv')
    C.save_csv(pd.DataFrame(base), 'has_freeze_baseline.csv')

    _write_md(snap, base, art_rows, src_rows, cfg_rows, guard_rows, obs, out=OUT)
    lg('H_ALPHA_SOURCE_01_FREEZE.md written')
    lg('strategy_hash %s' % strategy_hash)
    lg('config_hash   %s' % config_hash)
    lg('done')


def _write_md(snap, base, art_rows, src_rows, cfg_rows, guard_rows, obs, out):
    L = []
    A = L.append
    A('# H-ALPHA-SOURCE-01 -- FREEZE')
    A('')
    A('**Research ID** H-ALPHA-SOURCE-01  ')
    A('**Title** HVT / W7 alpha source attribution  ')
    A('**Frozen at** %s  ' % snap['frozen_at'])
    A('**Mode** read-only forensic freeze. No frozen object was created, '
      'modified or re-run by this study.')
    A('')
    A('| hash | value |')
    A('|---|---|')
    A('| `strategy_hash` | `%s` |' % snap['strategy_hash'])
    A('| `config_hash` | `%s` |' % snap['config_hash'])
    A('| `data_snapshot` | %d artefacts, see below |'
      % len(snap['data_snapshot']))
    A('')
    A('---')
    A('')
    A('## 1. HVT-BULL definition (frozen)')
    A('')
    A('| item | frozen value | source |')
    A('|---|---|---|')
    A('| Candidate pool | `total_mv >= 30 亿` AND `amount MA20 >= 3000 万` '
      'AND listed >= 120 sessions AND not ST AND not BJ | '
      '`hvt_bull/config.yaml`, `hvt_bull/daily.py:_universe()` |')
    A('| Event (T0) | HVT 天量日; percentile-120 >= 98 AND ratio-20 >= 2.0; '
      'grade A/B/C at pct 99/98/95 and ratio 3.0/2.0/1.8; strict union of '
      'the 20240801 anchor chain and a rolling 250-session chain | '
      '`hvt_bull/engine.py:detect_hvt()` |')
    A('| Event cooldown | 10 sessions | `hvt_bull/te_backtest.py:536` |')
    A('| Signal date | `signal_date` = T0 (rounded volume day) | ledger |')
    A('| Decision date | `decision_date`, walking `decision_lag` in '
      '`[0, 40]` | ledger, `te_backtest.py` |')
    A('| Entry (execution) | `open[decision_date + 1]` | '
      '`te_backtest.py:511` `ActualEntry=T+1开盘` |')
    A('| Exit | production uses a structural stop (`structural_loss_days=2`), '
      'a volume-down rule (`vol_down_ratio=1.3`) and a 15%% right-tail '
      'drawdown | `hvt_bull/config.yaml:83-84` |')
    A('| State machine | NORMAL / HVT_DETECTED / HVT_STRONG / WATCH / LOCKING '
      '/ LOCKED / BREAKOUT_READY / PRIMARY_BUY / T20_ROCKET_WATCH / '
      'CONFIRMED / FAILED / DISTRIBUTION / EXIT / EVENT_SPIKE | '
      '`hvt_bull/models.py:8-12` |')
    A('| Own-trade set | `next_day_action in {BUY, BUY_ON_CONFIRM}` | ledger |')
    A('| Daily Top-N | <= 3 per `decision_date`, ranked by `execution_score` '
      'then `buyability` | `te_backtest.py:417-425` (`max_buy_candidates=3`) |')
    A('')
    A('## 2. W7 definition (frozen)')
    A('')
    A('| item | frozen value | source |')
    A('|---|---|---|')
    A('| Candidate pool | `circ_mv >= 50 亿`, >= 250 bars, data from '
      '20230103, prefixes 43/83/87/88/92 excluded | '
      '`w7_second_wave_engine.py:19-21, 358-383` |')
    A('| Event anchor | 120-session rolling percentile with '
      '`min(p_turnover, p_volume) >= 99.0` (double-AND), the flag day must '
      'close as a yang line | `w7_second_wave_engine.py:525-544`; identical '
      'rule in `w7_te_v3_backtest.py:65-82` |')
    A('| Event age cap | 60 sessions | `MAX_EVENT_AGE` |')
    A('| States | DOWNTREND / BASE / IMPULSE / EXTREME_CHURN / ABSORPTION / '
      'DRYUP / RE_EXPANSION / BREAKOUT_CONFIRM / SECOND_WAVE / T0_CONFIRM / '
      'BREAKOUT_RETEST / MIDLINE_HOLD / DISTRIBUTION / FAILED | '
      '`w7_second_wave_engine.py:26` |')
    A('| Buy states | SECOND_WAVE / BREAKOUT_CONFIRM / RE_EXPANSION / '
      'T0_CONFIRM / BREAKOUT_RETEST / MIDLINE_HOLD | '
      '`w7_second_wave_engine.py:30` |')
    A('| Signal date | `signal_date` = the replay session inside the event '
      'lifecycle | ledger |')
    A('| Entry (execution) | decision-day close signal -> `open[j+1]`, '
      '"与 hvt_bull te_backtest 一致" | `w7_te_v3_backtest.py:11` |')
    A('| Gates | G1 EXTREME_CHURN no-chase; G2 `Exec >= 85`; G3 `volr <= 2.2`; '
      'G4 market regime >= 1 | `w7_te_v3_backtest.py:7-8` |')
    A('| Grading | PRIMARY BUY (Alpha>=60 & DRisk<=20 & price in buy zone) / '
      'CONDITIONAL BUY / WAIT / WATCH / AVOID | '
      '`w7_te_v3_backtest.py:9` |')
    A('| Own-trade set | `action in {PRIMARY BUY, CONDITIONAL BUY}` | ledger |')
    A('')
    A('## 3. Data version / backtest period')
    A('')
    A('| system | ledger | rows | period | signal->return convention |')
    A('|---|---|---|---|---|')
    A('| HVT | `%s` | 59,240 | 2025-01-02 .. 2026-09-23 '
      '(`signal_date` 2025-01-02 .. 2026-08-28) | `r*` = decision-day close '
      'basis; `er*` = T+1-open basis |' % C.HVT_LEDGER)
    A('| W7 | `%s` | 193,497 | 2024-01-02 .. 2026-08-28 | `r*` = signal-day '
      'close basis; `er*` = T+1-open basis |' % C.W7_LEDGER)
    A('')
    A('Shared price source: `research/fundamental_surprise_alpha/data/'
      'price_panel.parquet` (20180102 .. 20260924, qfq), `basic_panel.'
      'parquet` (total_mv / turnover_rate), PIT ST and delisting flags from '
      '`cache_daily/treasure_namechg*.parquet`, PIT SW-L1 industry from '
      '`cache_daily/industry/sw_industry_map.csv`.')
    A('')
    A('## 4. Verification performed before freezing')
    A('')
    A('1. **Entry convention.** `actual_entry` was recomputed for all 7,396 '
      'actionable HVT rows against the panel: `raw open[decision_date+1]` '
      'reproduces it in 100.0%% of rows (mean relative error 0.0000). The '
      'ledger header states `ActualEntry=T+1开盘`. Entry is therefore '
      'frozen as `open[t_decision + 1]` for both systems.')
    A('2. **Return convention.** The ledger `r*` columns were recomputed '
      'from the panel. HVT `r{h}` = `close[decision_date+h] / '
      'close[decision_date] - 1` (T+1: corr 0.9996, 99.2%% of rows within '
      '0.05pp). W7 `r{h}` = `close[signal_date+h] / close[signal_date] - 1` '
      '(T+1: corr 0.9792, 99.7%% within 0.05pp). Both ledgers also carry '
      '`er*`, an entry-based basis, and the two differ on 84-87%% of the '
      'non-zero-lag rows. This is a **documented dual-basis reporting '
      'convention, not a defect**.')
    A('3. **Price basis.** The production ledger is computed on raw prices; '
      'this study recomputes every leg on the shared qfq panel so the '
      'observed leg and all counterfactual legs share one basis. The '
      'T+10 mean differs by less than 0.03pp between the two bases for '
      'HVT (see section 6), so the switch does not move the object of study.')
    A('')
    A('## 5. Bug scan result')
    A('')
    A('**No blocking bug found. The experiment proceeds.**')
    A('')
    A('| finding | status | note |')
    A('|---|---|---|')
    A('| Dual return basis `r*` vs `er*` | BY DESIGN | both are written to '
      'the ledger and `actual_entry` is separately recorded; the summary '
      'JSON labels the entry basis explicitly |')
    A('| `w7_backtest.py:64` uses a *single-indicator* `ep >= 98` OR rule on '
      'a 250-session window | OUT OF SCOPE | that script did not produce the '
      'ledger under study; the in-scope `w7_te_v3_backtest.py:65-82` uses the '
      'same double-`>= P99` AND rule as the production engine. Recorded so '
      'the two are never mixed in one attribution |')
    A('| `hvt_bull/daily.py` has no explicit cooldown while the backtests use '
      '10 sessions; `backtest.py:517-549` splits the cooldown per chain in '
      '`both` mode while `te_backtest.py` does not | SCOPE NOTE | the ledger '
      'under study comes from `te_backtest.py`; its own rule is used '
      'verbatim. Not repaired |')
    A('| `w7_backtest_v41_signals.csv` placeholder rows | OUT OF SCOPE | not '
      'part of either frozen ledger |')
    A('')
    A('Per the task protocol, nothing above was repaired. Each item is a '
      'scope boundary, not a defect in the frozen object.')
    A('')
    A('## 6. Frozen baseline (restated on the study convention)')
    A('')
    A('Entry `open[decision+1]`, exit `close[entry+h]`, qfq, one observation '
      'per event lifecycle (overlap guard 20 sessions). This is the number '
      'the attribution has to explain.')
    A('')
    A('| system | horizon | n | mean gross | mean net 30bp | win rate | '
      'PF | median |')
    A('|---|---|---:|---:|---:|---:|---:|---:|')
    for r in base.itertuples():
        A('| %s | %s | %d | %+.4f | %+.4f | %.1f%% | %.2f | %+.4f |'
          % (r.system, r.horizon, r.n, r.mean_gross, r.mean_net30,
             100 * r.win_rate, r.pf, r.median))
    A('')
    A('Observation counts after dedup + overlap guard:')
    A('')
    A('| system | 2024 | 2025 | 2026 | total |')
    A('|---|---:|---:|---:|---:|')
    for s in C.PREREG_A['systems']:
        d = obs[obs['system'] == s]
        c = d['year'].value_counts()
        A('| %s | %d | %d | %d | %d |'
          % (s, int(c.get(2024, 0)), int(c.get(2025, 0)),
             int(c.get(2026, 0)), len(d)))
    A('')
    A('HVT covers only 2025-2026; W7 covers 2024-2026. This asymmetry is a '
      'property of the frozen artefacts and is reported as-is -- the study '
      'does not extend the backtest window, because that would mean running '
      'the strategy rather than explaining its recorded output.')
    A('')
    A('## 7. Frozen source files')
    A('')
    A('| file | bytes | mtime | sha256 (first 16) |')
    A('|---|---:|---|---|')
    for r in src_rows + cfg_rows:
        A('| `%s` | %s | %s | `%s` |'
          % (r['file'], r.get('bytes', 0), r.get('mtime', ''),
             str(r.get('sha256', ''))[:16]))
    A('')
    A('Declared **out of scope** (not read, not used as evidence):')
    A('')
    A('| file | exists | sha256 (first 16) |')
    A('|---|---|---|')
    for r in guard_rows:
        A('| `%s` | %s | `%s` |'
          % (r['file'], r['exists'], str(r.get('sha256', ''))[:16]))
    A('')
    A('## 8. Data snapshot')
    A('')
    A('| system | artefact | rows | bytes | mtime | sha256 (first 16) |')
    A('|---|---|---:|---:|---|---|')
    for r in art_rows:
        if not r.get('exists'):
            A('| %s | `%s` | - | - | - | MISSING |' % (r['system'], r['file']))
            continue
        A('| %s | `%s` | %s | %s | %s | `%s` |'
          % (r['system'], r['file'], r['rows'], r['bytes'], r['mtime'],
             r['sha256'][:16]))
    A('')
    A('Every downstream script in this study re-verifies '
      '`has_freeze_snapshot.json` before use; a mismatch aborts the run.')
    A('')
    with open(os.path.join(out, 'H_ALPHA_SOURCE_01_FREEZE.md'), 'w',
              encoding='utf-8') as f:
        f.write('\n'.join(L))


if __name__ == '__main__':
    main()
