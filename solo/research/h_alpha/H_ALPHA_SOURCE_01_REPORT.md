# H-ALPHA-SOURCE-01 — HVT / W7 Alpha 来源分解

纯科研归因实验：未寻找新策略、未优化参数、未增加指标、未修改 HVT / W7 定义。

- `strategy_hash` = `85d0029671a3531485a725db72bd84267ded3e7983f31ee132d9e7310cc81111`
- `config_hash` = `9bbde872f1bacc23a1b863d59d799f796d275cbc04591018f7bd24f1ea451af9`
- frozen at 2026-09-30 22:54:26
- observations 5831 (HVT 2371 / W7 3460)
- read: T+10, 30bp net, A-share T+1 execution (entry = next open)

## §1 Executive Summary

```
Candidate:  UNPROVEN
Event:      UNPROVEN
Delay:      UNPROVEN
Entry:      UNPROVEN
ALPHA SOURCE: No robust Alpha
```

四层反事实主结果（system = ALL，T+10，30bp）：
```
comparison               original  countrfct      delta     net30
Candidate:vs matched stock    +0.0104    +0.0047    +0.0057   +0.0027
Event:vs random date      +0.0307    +0.0627    -0.0320   -0.0350
Delay:Original - D0       +0.0105    +0.0316    -0.0211   -0.0241
Delay:Original - D1       +0.0106    +0.0343    -0.0237   -0.0267
Delay:Original - D3       +0.0106    +0.0299    -0.0193   -0.0223
Delay:Original - D5       +0.0105    +0.0291    -0.0186   -0.0216
Delay:Original - D10      +0.0106    +0.0257    -0.0151   -0.0181
Entry:Original - E1       +0.0105    +0.0316    -0.0211   -0.0241
Entry:Original - E3       +0.0106    +0.0317    -0.0211   -0.0241
Entry:Original - ER       +0.0106    +0.0285    -0.0179   -0.0209
```

## §2 原始 HVT / W7（基准，不重优化）

```
sys   h           n      gross      net30      win      pf
ALL   T+3      5763    +0.0063    +0.0033    0.485    1.33
HVT   T+3      2347    +0.0078    +0.0048    0.510    1.41
W7    T+3      3416    +0.0052    +0.0022    0.468    1.27
ALL   T+5      5756    +0.0077    +0.0047    0.483    1.33
HVT   T+5      2343    +0.0118    +0.0088    0.503    1.52
W7    T+5      3413    +0.0048    +0.0018    0.470    1.21
ALL   T+10     5750    +0.0104    +0.0074    0.477    1.32
HVT   T+10     2340    +0.0146    +0.0116    0.494    1.42
W7    T+10     3410    +0.0075    +0.0045    0.466    1.25
ALL   T+20     5689    +0.0188    +0.0158    0.488    1.42
HVT   T+20     2305    +0.0239    +0.0209    0.505    1.50
W7    T+20     3384    +0.0153    +0.0123    0.477    1.36
```

上行与 FREEZE 账本自报一致（HVT T+10 gross +0.0148 / net30 +0.0118，W7 T+10 gross +0.0078 / net30 +0.0048）。这里只做基准，不重新优化。

## §3 Candidate Attribution — Candidate vs Matched Random Stock

```
sys   h           n       cand       ctrl      delta     net30                 95% CI
ALL   T+3      5763    +0.0063    +0.0042    +0.0021   -0.0009 [+0.0003, +0.0043]
HVT   T+3      2347    +0.0078    +0.0045    +0.0033   +0.0003 [+0.0007, +0.0063]
W7    T+3      3416    +0.0052    +0.0039    +0.0013   -0.0017 [-0.0015, +0.0042]
ALL   T+5      5756    +0.0077    +0.0039    +0.0037   +0.0007 [+0.0011, +0.0067]
HVT   T+5      2343    +0.0118    +0.0055    +0.0062   +0.0032 [+0.0025, +0.0106]
W7    T+5      3413    +0.0048    +0.0028    +0.0020   -0.0010 [-0.0024, +0.0064]
ALL   T+10     5750    +0.0104    +0.0047    +0.0057   +0.0027 [+0.0015, +0.0098]
HVT   T+10     2340    +0.0146    +0.0092    +0.0054   +0.0024 [-0.0016, +0.0124]
W7    T+10     3410    +0.0075    +0.0016    +0.0059   +0.0029 [-0.0000, +0.0111]
ALL   T+20     5689    +0.0188    +0.0099    +0.0089   +0.0059 [+0.0042, +0.0137]
HVT   T+20     2305    +0.0239    +0.0140    +0.0099   +0.0069 [+0.0018, +0.0183]
W7    T+20     3384    +0.0153    +0.0071    +0.0082   +0.0052 [+0.0019, +0.0141]
```

## §4 Event Attribution — HVT / W7 Event vs Same-Stock Random Date

```
sys   h           n      event      rdate      delta     net30
ALL   T+3      5645    +0.0118    +0.0110    +0.0008   -0.0022
HVT   T+3      2277    +0.0179    +0.0124    +0.0055   +0.0025
W7    T+3      3368    +0.0076    +0.0101    -0.0025   -0.0055
ALL   T+5      5641    +0.0185    +0.0269    -0.0084   -0.0114
HVT   T+5      2275    +0.0253    +0.0298    -0.0044   -0.0074
W7    T+5      3366    +0.0138    +0.0249    -0.0111   -0.0141
ALL   T+10     5645    +0.0307    +0.0627    -0.0320   -0.0350
HVT   T+10     2273    +0.0382    +0.0768    -0.0386   -0.0416
W7    T+10     3372    +0.0257    +0.0532    -0.0275   -0.0305
ALL   T+20     5630    +0.0524    +0.0993    -0.0469   -0.0499
HVT   T+20     2265    +0.0580    +0.1232    -0.0653   -0.0683
W7    T+20     3365    +0.0486    +0.0831    -0.0345   -0.0375
```

诊断（非预注册结果，仅用于审核该反事实的可读性）：
```
random date BEFORE the event   3062 (0.525)   T+10 +0.0920
random date AFTER  the event   2599 (0.446)   T+10 +0.0284
event leg open[ev+1]           5661           T+10 +0.0307
unconditional panel baseline  414683 cells    T+10 +0.0072
buy open[ev-10] hold 10  +0.1141 win 0.891
buy open[ev+1]  hold 10  +0.0316 win 0.616
buy open[ev+20] hold 10  +0.0183 win 0.531
```

HVT / W7 事件本身位于一轮上涨的尾部；同月随机日有 52.5% 落在事件之前的拉升段。因此 `delta_event < 0` 的方向可信（事件日不是一个好买点），但幅度不宜过度读取。

## §5 Delay Attribution — D0 / D1 / D3 / D5 / D10

```
variant                 T+3        T+5       T+10       T+20
Original - D0       -0.0057    -0.0113    -0.0211    -0.0349
Original - D1       -0.0082    -0.0136    -0.0237    -0.0364
Original - D3       -0.0069    -0.0116    -0.0193    -0.0314
Original - D5       -0.0077    -0.0108    -0.0186    -0.0305
Original - D10      -0.0036    -0.0065    -0.0151    -0.0220
```

每行 = 系统自己的成交 − 从事件算起第 k 日成交（open[ev+k+1]）。全部为负且 D10 最不差：等待本身不产生 Alpha，而是在消耗 Alpha。

## §6 Entry Attribution — Original / Fixed Delay / Random Entry

```
variant          sys         n   original  countrfct      delta     net30
Original - E1    ALL      5805    +0.0105    +0.0316    -0.0211   -0.0241
Original - E1    HVT      2353    +0.0146    +0.0386    -0.0241   -0.0271
Original - E1    W7       3452    +0.0078    +0.0268    -0.0190   -0.0220
Original - E3    ALL      5805    +0.0106    +0.0317    -0.0211   -0.0241
Original - E3    HVT      2355    +0.0148    +0.0357    -0.0209   -0.0239
Original - E3    W7       3450    +0.0078    +0.0291    -0.0213   -0.0243
Original - ER    ALL      5763    +0.0106    +0.0285    -0.0179   -0.0209
Original - ER    HVT      2347    +0.0148    +0.0263    -0.0115   -0.0145
Original - ER    W7       3416    +0.0077    +0.0300    -0.0224   -0.0254
```

## §7 Null Model — Randomised HVT / W7 (B = 1000)

```
family          sys   h           n   observed   nullmean     excess       p
NULL_CANDIDATE  ALL   T+3      2572    +0.0048    +0.0048    -0.0000   0.511
NULL_CANDIDATE  ALL   T+5      2572    +0.0069    +0.0057    +0.0012   0.199
NULL_CANDIDATE  ALL   T+10     2572    +0.0081    +0.0065    +0.0016   0.203
NULL_CANDIDATE  ALL   T+20     2572    +0.0205    +0.0123    +0.0082   0.004
NULL_CANDIDATE  HVT   T+3      1154    +0.0063    +0.0051    +0.0013   0.247
NULL_CANDIDATE  HVT   T+5      1154    +0.0104    +0.0066    +0.0038   0.050
NULL_CANDIDATE  HVT   T+10     1154    +0.0123    +0.0089    +0.0034   0.131
NULL_CANDIDATE  HVT   T+20     1154    +0.0256    +0.0142    +0.0114   0.003
NULL_CANDIDATE  W7    T+3      1418    +0.0036    +0.0046    -0.0011   0.764
NULL_CANDIDATE  W7    T+5      1418    +0.0041    +0.0049    -0.0008   0.664
NULL_CANDIDATE  W7    T+10     1418    +0.0047    +0.0046    +0.0001   0.483
NULL_CANDIDATE  W7    T+20     1418    +0.0164    +0.0108    +0.0056   0.078
NULL_EVENT      ALL   T+3      5242    +0.0065    +0.0107    -0.0042   1.000
NULL_EVENT      ALL   T+5      5242    +0.0078    +0.0251    -0.0173   1.000
NULL_EVENT      ALL   T+10     5242    +0.0102    +0.0626    -0.0525   1.000
NULL_EVENT      ALL   T+20     5242    +0.0178    +0.1012    -0.0834   1.000
NULL_EVENT      HVT   T+3      2100    +0.0085    +0.0127    -0.0042   1.000
NULL_EVENT      HVT   T+5      2100    +0.0120    +0.0287    -0.0167   1.000
NULL_EVENT      HVT   T+10     2100    +0.0146    +0.0776    -0.0630   1.000
NULL_EVENT      HVT   T+20     2100    +0.0234    +0.1264    -0.1029   1.000
NULL_EVENT      W7    T+3      3142    +0.0051    +0.0093    -0.0042   1.000
NULL_EVENT      W7    T+5      3142    +0.0049    +0.0227    -0.0178   1.000
NULL_EVENT      W7    T+10     3142    +0.0072    +0.0526    -0.0454   1.000
NULL_EVENT      W7    T+20     3142    +0.0142    +0.0845    -0.0703   1.000
NULL_ENTRY      ALL   T+3      5780    +0.0063    +0.0125    -0.0062   1.000
NULL_ENTRY      ALL   T+5      5780    +0.0077    +0.0178    -0.0100   1.000
NULL_ENTRY      ALL   T+10     5780    +0.0106    +0.0287    -0.0181   1.000
NULL_ENTRY      ALL   T+20     5780    +0.0190    +0.0456    -0.0266   1.000
NULL_ENTRY      HVT   T+3      2358    +0.0079    +0.0125    -0.0047   1.000
NULL_ENTRY      HVT   T+5      2358    +0.0118    +0.0172    -0.0053   1.000
NULL_ENTRY      HVT   T+10     2358    +0.0148    +0.0266    -0.0118   1.000
NULL_ENTRY      HVT   T+20     2358    +0.0243    +0.0404    -0.0161   1.000
NULL_ENTRY      W7    T+3      3422    +0.0052    +0.0125    -0.0073   1.000
NULL_ENTRY      W7    T+5      3422    +0.0049    +0.0182    -0.0133   1.000
NULL_ENTRY      W7    T+10     3422    +0.0077    +0.0301    -0.0225   1.000
NULL_ENTRY      W7    T+20     3422    +0.0153    +0.0491    -0.0337   1.000
```

## §8 OOS / Walk Forward

```
comparison               sys       IS2024     VL2025    OOS2026   stable
Candidate:vs matched stock ALL      +0.0067    +0.0034    +0.0103     True
Candidate:vs matched stock HVT          n/a    +0.0035    +0.0095     True
Candidate:vs matched stock W7       +0.0067    +0.0032    +0.0113     True
Event:vs random date     ALL      -0.0083    -0.0399    -0.0355     True
Event:vs random date     HVT          n/a    -0.0376    -0.0408     True
Event:vs random date     W7       -0.0083    -0.0422    -0.0291     True
Delay:Original - D0      ALL      -0.0346    -0.0093    -0.0363     True
Delay:Original - D0      HVT          n/a    -0.0150    -0.0440     True
Delay:Original - D0      W7       -0.0346    -0.0035    -0.0271     True
Delay:Original - D1      ALL      -0.0375    -0.0133    -0.0356     True
Delay:Original - D1      HVT          n/a    -0.0173    -0.0398     True
Delay:Original - D1      W7       -0.0375    -0.0092    -0.0305     True
Delay:Original - D3      ALL      -0.0309    -0.0128    -0.0242     True
Delay:Original - D3      HVT          n/a    -0.0172    -0.0224     True
Delay:Original - D3      W7       -0.0309    -0.0083    -0.0263     True
Delay:Original - D5      ALL      -0.0300    -0.0130    -0.0212     True
Delay:Original - D5      HVT          n/a    -0.0164    -0.0152     True
Delay:Original - D5      W7       -0.0300    -0.0095    -0.0284     True
Delay:Original - D10     ALL      -0.0252    -0.0132    -0.0105     True
Delay:Original - D10     HVT          n/a    -0.0129    -0.0047     True
Delay:Original - D10     W7       -0.0252    -0.0134    -0.0174     True
Entry:Original - E1      ALL      -0.0346    -0.0093    -0.0363     True
Entry:Original - E1      HVT          n/a    -0.0150    -0.0440     True
Entry:Original - E1      W7       -0.0346    -0.0035    -0.0271     True
Entry:Original - E3      ALL      -0.0352    -0.0128    -0.0276     True
Entry:Original - E3      HVT          n/a    -0.0178    -0.0277     True
Entry:Original - E3      W7       -0.0352    -0.0078    -0.0276     True
Entry:Original - ER      ALL      -0.0270    -0.0148    -0.0169     True
Entry:Original - ER      HVT          n/a    -0.0119    -0.0105     True
Entry:Original - ER      W7       -0.0270    -0.0179    -0.0246     True
```

逐年（T+10，ALL）：
```
Candidate:vs matched stock 2024  n=1234  delta +0.0067  net30 +0.0037
Event:vs random date     2024  n=1227  delta -0.0083  net30 -0.0113
Delay:Original - D0      2024  n=1254  delta -0.0346  net30 -0.0376
Delay:Original - D1      2024  n=1254  delta -0.0375  net30 -0.0405
Delay:Original - D3      2024  n=1254  delta -0.0309  net30 -0.0339
Delay:Original - D5      2024  n=1254  delta -0.0300  net30 -0.0330
Delay:Original - D10     2024  n=1254  delta -0.0252  net30 -0.0282
Entry:Original - E1      2024  n=1254  delta -0.0346  net30 -0.0376
Entry:Original - E3      2024  n=1254  delta -0.0352  net30 -0.0382
Entry:Original - ER      2024  n=1235  delta -0.0270  net30 -0.0300
Candidate:vs matched stock 2025  n=3178  delta +0.0034  net30 +0.0004
Event:vs random date     2025  n=3111  delta -0.0399  net30 -0.0429
Delay:Original - D0      2025  n=3203  delta -0.0093  net30 -0.0123
Delay:Original - D1      2025  n=3202  delta -0.0133  net30 -0.0163
Delay:Original - D3      2025  n=3201  delta -0.0128  net30 -0.0158
Delay:Original - D5      2025  n=3197  delta -0.0130  net30 -0.0160
Delay:Original - D10     2025  n=3203  delta -0.0132  net30 -0.0162
Entry:Original - E1      2025  n=3203  delta -0.0093  net30 -0.0123
Entry:Original - E3      2025  n=3203  delta -0.0128  net30 -0.0158
Entry:Original - ER      2025  n=3187  delta -0.0148  net30 -0.0178
Candidate:vs matched stock 2026  n=1338  delta +0.0103  net30 +0.0073
Event:vs random date     2026  n=1307  delta -0.0355  net30 -0.0385
Delay:Original - D0      2026  n=1348  delta -0.0363  net30 -0.0393
Delay:Original - D1      2026  n=1348  delta -0.0356  net30 -0.0386
Delay:Original - D3      2026  n=1349  delta -0.0242  net30 -0.0272
Delay:Original - D5      2026  n=1349  delta -0.0212  net30 -0.0242
Delay:Original - D10     2026  n=1344  delta -0.0105  net30 -0.0135
Entry:Original - E1      2026  n=1348  delta -0.0363  net30 -0.0393
Entry:Original - E3      2026  n=1348  delta -0.0276  net30 -0.0306
Entry:Original - ER      2026  n=1341  delta -0.0169  net30 -0.0199
```

## §9 Tail / Regime / Year

```
comparison                     full    leave1%    leave5%   leave10%   tail%
Candidate:vs matched stock    +0.0057    +0.0005    -0.0086    -0.0155   0.383
Event:vs random date        -0.0320    -0.0347    -0.0425    -0.0478   0.300
Delay:Original - D0         -0.0211    -0.0253    -0.0338    -0.0401   0.382
Delay:Original - D1         -0.0237    -0.0275    -0.0358    -0.0416   0.382
Delay:Original - D3         -0.0193    -0.0229    -0.0302    -0.0356   0.382
Delay:Original - D5         -0.0186    -0.0219    -0.0282    -0.0334   0.382
Delay:Original - D10        -0.0151    -0.0184    -0.0247    -0.0297   0.382
Entry:Original - E1         -0.0211    -0.0253    -0.0338    -0.0401   0.382
Entry:Original - E3         -0.0211    -0.0248    -0.0329    -0.0384   0.382
Entry:Original - ER         -0.0179    -0.0214    -0.0280    -0.0334   0.382
```

市场状态（持仓交易日的 CSI300 状态，T+10，ALL）：
```
comparison               regime       n    nmon   observed   counterf      win      pf
Candidate:vs matched stock   BULL    2956      18    +0.0071    +0.0016    0.468    1.22
Event:vs random date       BULL    2930      18    +0.0259    +0.0603    0.583    2.59
Delay:Original - D0        BULL    2985      18    +0.0072    +0.0268    0.468    1.23
Delay:Original - D1        BULL    2986      18    +0.0073    +0.0314    0.469    1.23
Delay:Original - D3        BULL    2984      18    +0.0072    +0.0285    0.468    1.23
Delay:Original - D5        BULL    2984      18    +0.0071    +0.0275    0.468    1.23
Delay:Original - D10       BULL    2985      18    +0.0072    +0.0261    0.468    1.23
Entry:Original - E1        BULL    2985      18    +0.0072    +0.0268    0.468    1.23
Entry:Original - E3        BULL    2986      18    +0.0073    +0.0302    0.469    1.23
Entry:Original - ER        BULL    2961      18    +0.0072    +0.0276    0.468    1.23
Candidate:vs matched stock  RANGE    2610      26    +0.0139    +0.0076    0.490    1.42
Event:vs random date      RANGE    2525      26    +0.0370    +0.0672    0.644    3.33
Delay:Original - D0       RANGE    2635      26    +0.0141    +0.0376    0.491    1.42
Delay:Original - D1       RANGE    2633      26    +0.0141    +0.0386    0.491    1.43
Delay:Original - D3       RANGE    2635      26    +0.0142    +0.0320    0.491    1.43
Delay:Original - D5       RANGE    2631      26    +0.0141    +0.0309    0.491    1.43
Delay:Original - D10      RANGE    2632      26    +0.0142    +0.0250    0.492    1.43
Entry:Original - E1       RANGE    2635      26    +0.0141    +0.0376    0.491    1.42
Entry:Original - E3       RANGE    2634      26    +0.0142    +0.0339    0.491    1.43
Entry:Original - ER       RANGE    2618      26    +0.0142    +0.0289    0.490    1.43
Candidate:vs matched stock   BEAR     184       8    +0.0138    +0.0132    0.462    1.51
Event:vs random date       BEAR     190       8    +0.0223    +0.0394    0.611    2.77
Delay:Original - D0        BEAR     185       8    +0.0144    +0.0224    0.465    1.53
Delay:Original - D1        BEAR     185       8    +0.0144    +0.0211    0.465    1.53
Delay:Original - D3        BEAR     185       8    +0.0144    +0.0238    0.465    1.53
Delay:Original - D5        BEAR     185       8    +0.0144    +0.0296    0.465    1.53
Delay:Original - D10       BEAR     184       8    +0.0142    +0.0298    0.462    1.52
Entry:Original - E1        BEAR     185       8    +0.0144    +0.0224    0.465    1.53
Entry:Original - E3        BEAR     185       8    +0.0144    +0.0249    0.465    1.53
Entry:Original - ER        BEAR     184       8    +0.0138    +0.0359    0.462    1.51
```

年度样本量：{2024: 1257, 2025: 3213, 2026: 1361}

## §10 Bias Audit

```
look_ahead             PASS
selection_bias         PASS
survivorship_bias      PASS
execution_bias         PASS
overlapping_sample     PASS
future_pivot           PASS
parameter_leakage      PASS
randomization_leakage  PASS
```

- **look_ahead** — entry = open[t_decision + 1] holds for 5831/5831 obs; the event precedes the decision for 5831/5831; the identity D0 == E1 == event leg is exact (max|diff| 0.0e+00). No leg reads a price dated after entry + h.
- **selection_bias** — The sample is the frozen ledger's own actionable decisions (5831 obs) and was not re-derived from outcomes; the counterfactual stock is drawn from the same cell with the treated names of that session excluded. Level-1 conclusions are therefore conditional on the frozen candidate rule -- disclosed, not corrected.
- **survivorship_bias** — panel 20180102..20260924, 5816 stocks; 247 stocks stop trading >=40 sessions before the panel end (0 of them appear in the sample), so the price matrix retains names that later left the tape.
- **execution_bias** — T+1 execution throughout: entry = next session open; the freeze verified actual_entry == raw open[decision_date + 1] in 7396/7396 rows. Cost ladder 0/10/20/30/50bp, 30bp primary. Known residual: board limit-up / limit-down unfillability is not modelled on either leg.
- **overlapping_sample** — One observation per event lifecycle (dedup on stock+event, then a 20-session guard): 0 duplicate (system, stock, event) rows remain; max observations for one stock-month = 2. Residual serial correlation is handled by month-cluster resampling.
- **future_pivot** — No signal is defined with future data: both ledgers' event fields (HVT signal_date = volume-spike day, W7 event_date = extreme-churn day) are contemporaneous stamps of the frozen production run and the decision lag is strictly positive for every observation. FLAG (interpretability, not leakage): the Level-2 benchmark draws a session from the same calendar month, so 52.5% of draws land before the event and inherit the pre-event run-up, which tilts delta_event negative and caps how strongly its magnitude may be read.
- **parameter_leakage** — Every threshold (horizons, delay ladder, matching buckets, cost ladder, overlap guard, seeds) is a literal of PREREG_A written before any result; the ladder D0/D1/D3/D5/D10 was registered in H_ALPHA_SOURCE_01_SPEC.md before it was measured. No selection on results took place in this study.
- **randomization_leakage** — Null draws are independent of the observed legs: the event randomiser excludes +-5 sessions around both the event and the fill, so it can never return the original date; the candidate randomiser bans the query stock on that session and every treated name. The ER ensemble support is the frozen lifecycle, which by design contains the original fill -- disclosed and conservative. Base size per family: {'NULL_CANDIDATE': 2572, 'NULL_ENTRY': 5780, 'NULL_EVENT': 5242}.

══════════════════════════════════════════════

限制：
- Level-2 benchmark lands in the pre-event run-up (52.5% of draws precede the event), so delta_event < 0 is directionally informative but its magnitude is inflated against the event.
- Matching does not control for the momentum / volatility state of the candidate at its fill, so part of delta_candidate may be a volatility tilt rather than selection skill.
- Only 30-31 month clusters exist over 2024-2026; per-year bootstraps rest on about 12 clusters each.
- The production risk overlay (structural stop, right-tail DD, double stop) is excluded by design: this study attributes the signal, not the money-management layer.

──────────────────────────────────────────────

本研究不输出 BUY / NO TRADE，仅输出 ALPHA SOURCE：**No robust Alpha**

`trading_authorization = NO`
