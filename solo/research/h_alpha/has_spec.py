# -*- coding: utf-8 -*-
"""H-ALPHA-SOURCE-01 -- step 2: pre-registration document.

Generated from PREREG_A so the spec and the code cannot drift apart.
Contains no result.  Written before step 3 was executed.
"""
import os

import has_common as C

P = C.PREREG_A
OUT = C.OUT


def main():
    lg = C.Log('has_spec')
    L = []
    A = L.append
    A('# H-ALPHA-SOURCE-01 -- SPEC (pre-registration)')
    A('')
    A('**Research ID** %s  ' % P['hypothesis_id'])
    A('**Title** %s  ' % P['title'])
    A('**Version** %s  ' % P['version'])
    A('**Created** %s  ' % P['created'])
    A('**Trading authorisation** %s' % P['trading_authorization'])
    A('')
    A('This document was written after `H_ALPHA_SOURCE_01_FREEZE.md` and '
      '**before** any attribution number existed. Every constant below is '
      'read from `PREREG_A` in `has_common.py`, so the specification and the '
      'executable cannot diverge.')
    A('')
    A('## 1. Question')
    A('')
    A('Where do the recorded HVT-BULL / W7 returns actually come from? Four '
      'candidate sources, each tested with its own matched counterfactual:')
    A('')
    A('1. **Candidate** -- is the stock itself better than a matched random '
      'stock?')
    A('2. **Event** -- does the HVT/W7 event date beat a random date on the '
      'same stock?')
    A('3. **Delay** -- does the system\'s waiting time between the event and '
      'the fill create return?')
    A('4. **Entry** -- does the specific fill day beat a fixed delay and a '
      'random fill day?')
    A('')
    A('## 2. Hypotheses')
    A('')
    A('**Null**  The recorded returns are explained by stock-pool '
      'characteristics, market environment, time effects and random entry; '
      'no component of the event / fill machinery carries a stable, '
      'independent, out-of-sample increment.')
    A('')
    A('**Alternative**  At least one explicitly named component still carries '
      'a stable, repeatable, out-of-sample increment once the other factors '
      'are matched away.')
    A('')
    A('No component is assumed to be the answer in advance.')
    A('')
    A('## 3. Objects under study (frozen)')
    A('')
    A('| system | ledger | own-trade set |')
    A('|---|---|---|')
    A('| HVT | `%s` | `%s` |' % (P['hvt_ledger'], ' or '.join(P['hvt_actions']))
      )
    A('| W7 | `%s` | `%s` |' % (P['w7_ledger'], ' or '.join(P['w7_actions'])))
    A('')
    A('Neither system is modified, re-parameterised or re-run. See the FREEZE '
      'document for the full definition, hashes and the bug scan.')
    A('')
    A('## 4. Research units (four levels)')
    A('')
    A('| level | unit | observed leg | matched counterfactual |')
    A('|---|---|---|---|')
    A('| 1 Candidate | one candidate event | the stock the system bought | '
      'a random eligible stock, **same session**, same board, same market-cap '
      'tercile, same liquidity tercile, same SW-L1 industry preferred |')
    A('| 2 Event | one event anchor | event leg `open[ev+1]` | a random '
      'eligible session of the **same stock** in the **same calendar month**, '
      'excluding +-5 sessions around the event and the fill |')
    A('| 3 Delay | one event anchor | the frozen delay ladder `D0/D1/D3/D5/'
      'D10` | the system\'s own fill (Original) |')
    A('| 4 Entry | one event anchor | the system\'s own fill | a fixed '
      'delay fill and a random fill inside the event lifecycle |')
    A('')
    A('## 5. Frozen measurement convention')
    A('')
    A('* Entry `%s`' % P['entry_rule'])
    A('* Exit `%s`' % P['exit_rule'])
    A('* Horizons `%s`, primary `T+%d`'
      % (list(P['horizons']), P['primary_horizon']))
    A('* Delay ladder `%s` -- fixed here, **never** chosen from results'
      % list(P['delays']))
    A('* Overlap control: one observation per event lifecycle; a further '
      'guard requires `%d` sessions between retained observations of the '
      'same stock.' % P['overlap_guard_sessions'])
    A('* %s' % P['note_exit'])
    A('')
    A('## 6. Universe (identical for every arm)')
    A('')
    A('| constraint | value |')
    A('|---|---|')
    A('| listed sessions | >= %d |' % P['min_list_days'])
    A('| ST | excluded (`%s`) |' % P['exclude_st'])
    A('| delisting | excluded (`%s`) |' % P['exclude_delist'])
    A('| BSE | excluded (`%s`) |' % P['exclude_bse'])
    A('| suspension hole | a run of >= %d missing prints ends eligibility |'
      % P['long_suspension_days'])
    A('')
    A('The observed leg is **not** re-filtered against this universe: it is '
      'whatever the frozen ledger recorded. The universe is what the '
      'counterfactual arms are drawn from.')
    A('')
    A('## 7. Matching')
    A('')
    A('Level 1 uses coarsened exact matching on `%s`, with the same SW-L1 '
      'industry preferred inside the cell and the coarsened cell used only '
      'when the industry subset is empty (the fallback rate is reported). '
      'The control may not be the query\'s own stock and may not be another '
      'treated observation on the same session.' % list(P['match_level1_keys'])
      )
    A('')
    A('Matching variables are exactly market cap, liquidity, board, industry '
      'and trade date. **No variable is added after seeing a result.**')
    A('')
    A('## 8. Costs')
    A('')
    A('Ladder `%s`bp, primary **%dbp**.' % (list(P['cost_bp']),
                                            P['primary_cost_bp']))
    A('')
    A('`%s`' % P['cost_charge'])
    A('')
    A('Every level therefore reports both `delta_gross` and `delta_net30`, '
      'where `delta_net30 = delta_gross - 30bp`.')
    A('')
    A('## 9. Statistics')
    A('')
    A('| item | value |')
    A('|---|---|')
    A('| cluster bootstrap (by calendar month) | B = %d |' % P['boot_B'])
    A('| randomised null replicates | B = %d |' % P['n_perm'])
    A('| seed | %d |' % P['seed'])
    A('| tail levels | %s |' % list(P['tail_levels']))
    A('| minimum n for a reportable cell | %d |' % P['min_n'])
    A('| regime index | `%s` |' % P['regime_index'])
    A('')
    A('## 10. Phase split')
    A('')
    A('Driven by what the frozen ledgers actually cover: IS `%s`, VALID `%s`, '
      'OOS `%s`. Walk-forward folds are consecutive single-year pairs; they '
      'test whether the attribution direction is stable, **not** to select '
      'anything.' % (P['phase_IS'], P['phase_VALID'], P['phase_OOS']))
    A('')
    A('## 11. ROBUST criterion')
    A('')
    A('A source is called `ROBUST` only if **all** of the following hold, '
      'otherwise it is `UNPROVEN`:')
    A('')
    A('1. `Original > Counterfactual`')
    A('2. still true after 30bp net')
    A('3. holds out of sample')
    A('4. holds in more than one year')
    A('5. not confined to a single market regime')
    A('6. still visible after dropping the top 5% of observations')
    A('7. the cluster-bootstrap CI does not straddle the meaningful zero, or '
      'at minimum the direction is consistent')
    A('8. no bias-audit failure')
    A('')
    A('## 12. Final reporting')
    A('')
    A('Only an `ALPHA SOURCE` label is emitted: `Candidate-dominant`, '
      '`Event-dominant`, `Timing-dominant`, `Mixed`, `No robust Alpha`. '
      'These are attribution findings, not strategy ratings.')
    A('')
    A('## 13. Explicitly forbidden')
    A('')
    A('Parameter tuning; adding indicators; adding themes; adding market '
      'filters; machine learning; modifying HVT; modifying W7; searching for '
      'the best entry; searching for the best holding period; redefining the '
      'candidate pool, the event or the delay after seeing results; using '
      'future data; using future profitability to select the sample; using '
      'future breakouts to define a past signal; claiming a causal narrative '
      'in place of an observed matched difference.')
    A('')
    with open(os.path.join(OUT, 'H_ALPHA_SOURCE_01_SPEC.md'), 'w',
              encoding='utf-8') as f:
        f.write('\n'.join(L))
    lg('H_ALPHA_SOURCE_01_SPEC.md written (%d lines)' % len(L))
    lg('done')


if __name__ == '__main__':
    main()
