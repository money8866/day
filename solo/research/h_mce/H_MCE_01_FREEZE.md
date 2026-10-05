# H-MCE-01 — 冻结记录（FREEZE RECORD）

    Research ID : H-MCE-01
    Title       : A股多K线价格—成交量组合事件的增量信息检验
    Parent      : H-TAIL-MIDBAR-01 (FAIL -> ARCHIVE, unchanged)
    Frozen at   : 2026-10-02 21:22:05 (local)
    Status      : FROZEN BEFORE ANY RESULT WAS OBSERVED

## 1. 被冻结文件

| 文件 | 角色 | 是否可改 |
|---|---|---|
| `H_MCE_01_SPEC.md` | 唯一口径来源（22 节预注册规范） | 冻结，运行后不得修改 |
| `H_MCE_01_FREEZE.md` | 本记录 | 冻结 |
| `mce_common.py` | 公共层（由 SPEC 逐字转写） | 由 SPEC 派生 |
| `mce_run.py` | 主运行 | 由 SPEC 派生 |
| `has_report.py` | 报告 + 交付校验 | 由 SPEC 派生 |

## 2. SPEC 指纹

```text
file      : H_MCE_01_SPEC.md
sha256    : 49E64CE9D7CCABCAADC81C0F03BF000F36E52834805B130120DC11499C7D4A2E
size      : 25106 bytes
mtime     : 2026-10-02 21:22:05
```

`mce_common.SPEC_SHA256` 固化为上值。`mce_common.verify_spec()` 在每次运行开始时
重算并比对；不一致即判定预注册被事后编辑，实验作废（`mce_run.py` 返回非 0）。

## 3. 冻结的核心口径（摘要；完整定义见 SPEC）

```text
IS          = 2019-01-11 .. 2023-12-31   （2018 零样本，见 SPEC §2.2）
OOS         = 2024-01-02 .. 20260924
宇宙 U1     = hve_common.eligibility(g) & (board != 'BSE')
宇宙 U2     = traded & ~st & (board != 'BSE')      （幸存者偏差对照）
价格口径    = qfq OHLC；prev_close := close[t-1]
量比口径    = volume_ratio_20 = vol[t] / mean(vol[t-20 .. t-1])（分母不含当日）
horizons    = {1,3,5,10,20}
成本        = 0 / 15 / 30 / 50 bp；主口径 30bp
基准        = A（全A eligible）/ B（同前置、无K线组合）/ C（同股票 |Δt|<=20）
Null        = Null1 随机股票×日期 / Null2 匹配后打乱标签 / Null3 同股票随机重排
              B=2000, rounds=8, resolution >= 0.99 才采纳
Bootstrap   = B=5000, 按日历月聚类
多重检验    = 10 事件 × 5 horizon = 50 主检验, Benjamini-Hochberg, q<0.10
判决        = ROBUST / PROMISING / FRAGILE / NO EDGE（禁止单一综合评分）
```

## 4. 运行前置不变量

```text
1. 目录外只读：不修改 research/hve、research/h_rbp、research/h_tail_midbar、
   cache_daily、report_daily 及任何生产脚本。
2. future_column_scan 任一失败 -> FATAL -> EXIT 3（SPEC §8 / §21.6）。
3. 严禁用 OOS 结果回改规则（SPEC §9）。
4. trading_authorization = NO（SPEC §18）。
```

## 5. 运行与判读纪律

```text
本实验为否定性优先检验。
若 ROBUST 与 PROMISING 事件数均为 0 -> 逐字输出「未发现稳定优势」并 STOP，
且不再继续优化这 10 个形态（SPEC §18 / §26）。
```

## 6. 变更日志

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0 | 2026-10-02 | 初版冻结（与 SPEC v1.0 同批）。 |
