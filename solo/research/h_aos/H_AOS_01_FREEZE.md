# H-AOS-01 FREEZE — SPEC 与执行代码冻结记录

    hypothesis_id : H-AOS-01
    title         : A股动态 Alpha OS：Regime × Alpha Health × Selective Trading
    freeze_date   : 2026-10-02
    working_dir   : d:\mystock\solo\research\h_aos\

---

## 1. SPEC 冻结件

| 文件 | SHA256 | 字节 | 行数 | mtime |
|---|---|---|---|---|
| `H_AOS_01_SPEC.md` | `A039E8565247836E130517A021E72A85818413F993CF5529F01BEDC98D0D4A38` | 29,690 | 623 | 2026-10-02 12:12:07 |

SPEC 版本 `1.01`。相对初版 `1.0` 的唯一变更：§19 增补第 6 条（WEAK 判定操作化切点）
与第 7 条（CORE 共同窗口裁剪函数），并新增一行变更日志。该变更**不修改任何冻结的
Alpha / Regime / 成本 / 分期定义**，且在 P1/P2 结果被观测之前完成。

    hash 说明：`aos_p1_results.json` / `aos_p2_results.json` 内嵌的 `spec_sha256`
    为 `8072DCD2...`，是 §19 补登记**之前**的 SPEC 版本（v1.00）。
    补登记为纯文档性变更（不触及任何代码路径或阈值），故上述两份结果无需重算；
    顶层 `H_AOS_01_RESULTS.json` 记录的是最终版 `A039E856...`（v1.01）。

---

## 2. 执行代码冻结件

| 文件 | SHA256 | 字节 |
|---|---|---|
| `aos_common.py` | `CDC302CB9ACA60E34A14D99EB903FAAF191C50BC49C51A39B0D4D6239D928163` | 46,852 |
| `aos_p1.py` | `B62FCFFD1F35A43C143BE2BC6BBB93A6882C290ADA825741F61A64A58FD41E24` | 5,133 |
| `aos_p2.py` | `D596D6F5FEF446054393E1A03D31285F59C73D9BF31A3187817E434D1226C4F2` | 5,779 |

---

## 3. 只读依赖（本 SPEC 未修改，仅引用）

    research/hve/hve_common.py            build_grid / build_indicators / eligibility /
                                          cluster_events_ranked / fwd_ret / perf_stats
    research/hve/hve_hvt.py               L6_REEXPANSION 条件集
    research/hve/hve_events.py            gate_C / PRIMARY
    research/h_tail_midbar/tmb_common.py  PREREG_T / ma_masks / first_mask / _roll / _slope
    research/h_rbp/hrbp_common.py         PREREG_H / vr20_excl_panel / gather_win / first_from
    research/h_rbp/hrbp_signals.py        entry_of / S3 / RS2_5D
    research/_brk_build.py                压力位 R_N / 池条件 / LIQ_MIN

---

## 4. 冻结后的修改规则

SPEC §21 原文：

> 本文件冻结后，任何修改都必须新增一行变更日志，并说明 Hypothesis 与修改原因。

禁止事项（SPEC §2 / 任务书 §2）：

    - 不得在观测到 OOS 结果后回头调整任何冻结参数
    - 不得用全样本分位数替换 §4.2 的 IS 阈值
    - 不得修改六个 Alpha 的定义（§3）
    - 不得把 IS 结果描述为 OOS

---

## 5. 环境

    python        3.x
    panel         S=5,816 × N=2,120（2018-01-02 ~ 2026-09-24），CACHE_VERSION=4
    universe      B（eligible & board!=BSE），cell share = 0.6445
    cost          主基准 30bp round-trip
    phases        IS 2022-01-01~2023-12-31 / VALID 2024 / OOS 2025-01-01~2026-09-24
    seed          20261101

---

## 6. 强制声明

> 本研究的结论仅适用于当前样本（Universe B，2022-01-01 ~ 2026-09-24）、
> 当前成本假设（主基准 30bp round-trip）与当前 OOS 设计。
> 动态 Alpha Router 相对于预设基准表现出/未表现出统计与经济意义上的增量优势；
> 该结论仍需继续进行前瞻验证。
