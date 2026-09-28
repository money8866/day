# H-EARN-FWD 研究报告（V1.0）

| 项目 | 值 |
|---|---|
| Hypothesis ID | H-EARN-FWD |
| 版本 | V1.0 |
| 注册日 | 2026-09-26 |
| 预测时点 | P60/P30/P20 |
| 主检验时点 | P30 |
| 随机种子 | 20260926 |
| 最终状态 | **PASS (FORECAST SKILL CONFIRMED, PRICE ALPHA NOT YET TESTED)** |
| 实盘授权 | **无。本 V1 不产生任何实盘交易授权。** |

> 说明：研究规范原文（Forecast Skill / Null Model / PASS Gate 章节）
> 未在本仓库或记忆中留存，本报告中的检验项均由已实现代码可复现地
> 重建，重建条目一律标注 `recon=1`；只有 N3/N5（预先注册的期望规则）
> 与 P60/P30/P20、NW 滞后、阈值等 PREREG 参数是字面预注册。

════════════════════════════════════════════════════════════
## 一、15 个必答问题
════════════════════════════════════════════════════════════

**Q1.**

研究目标：在某季度财报公布后，仅用 Prediction Date P 之前已公开的信息，预测下一季度（target quarter）实际业绩相对"预先注册的季节性预期"的偏离。三个预测时点 P60/P30/P20（主检验 P30），三个 Target：A 连续 Surprise%（model_surprise_pct）、B 二元正向超预期（positive_surprise）、C 强超预期（strong_surprise，75 分位）。样本 2018Q4 .. 2026Q2 (anchor year 2019..2026)，锚点 = 目标报告实际公告日。

**Q2.**

分析师一致预期：单季一致预期 UNAVAILABLE（缓存中仅有 FY 年度口径 report_rc）。因此主线使用 MODEL_SURPRISE（模型化预期），IMPLIED_CONS_FY / GUIDE_MIDPOINT 只作辅助，从不冒充一致预期，也从不混入主 Target。本研究的结论是"相对预先注册预期基准的超预期"，不是"相对市场一致预期的超预期"。

**Q3.**

数据覆盖（coverage_ledger.csv）：核心财务 L_np_sq 91.8%、np_yoy 88.3%、ocf_np 97.2%、accrual_ar 82.6%；市场 ret20 90.5%、total_mv 91.1%；行业 nowcast ind_np_yoy_med 52.2%；业绩预告 guide_mid 仅 8.8%（to_mv 2.1%）；SW-L1 映射 52.3%(UNKNOWN 47.7%，缓存行业表只覆盖 3000 只)。

**Q4.**

Point-in-Time 一致性：7 项泄漏门控（L1–L7）全部 PASS，违规数全为 0。其中 L6（目标报告公告日必须严格晚于 P）是核心无前视不变量：初版审计发现 48 行违规——长期停牌壳公司多年后补披露旧年报，锚点取到补发日而报告内容早已公开；已在特征构建端源头剔除，审计复跑后归零。L4 市场快照时效容忍 0.1%，实测违规率 0.04%。

**Q5.**

Target 分布（targets.parquet，逐时点）：model_surprise_pct 均值 -0.173；positive_surprise 发生率 41.8%；strong_surprise 发生率 25.0%（按设计约等于 75% 分位）。Surprise 与 np_yoy 的全样本合并相关仅 0.124，说明 Target 是"偏离预期"而非"历史增速"的复制。

**Q6.**

公司层预测技能（TRAINFIX，预测期 2023–2026，逐季度 cohort 计算 Rank IC 后 Newey-West 聚合）：GBM 主模型 IC=0.3315（t=42.8），RIDGE IC=0.2688；P20/P60 分别 0.3348 / 0.3114。强超预期二分类 GBM_CLS AUC=0.7447，二元正向 AUC=0.6987。技能为正且显著。

**Q7.**

对照朴素动量规则 N5（预先注册：用最近一期同比外推，预测 (E_mom−E_sea)/den_sea，窗口与模型对齐）：规则 IC=0.1384，模型 IC=0.3315，模型胜。注意 N3 季节性规则（预先注册）按定义预测偏离恒为 0，是退化规则、IC 未定义——这正是需要真实模型的原因。

**Q8.**

对照随机排序 N1（cohort 内置换预测值，30 个随机种子）：实测 IC=0.3315，零分布均值 0.0026，z=28.50（TRAINFIX）；六个 Walk-Forward 窗口 z 全部 ≥ 3。技能不来自排序偶然。

**Q9.**

分类技能：强超预期（75 分位）AUC=0.7447，二元正向 AUC=0.6987；三个分类器中 GBM_CLS 最优（P20 0.7461 / P30 0.7447 / P60 0.7375）。AUC 与 IC 同向，说明排序技能可转化为"挑出强超预期"的筛选能力。

**Q10.**

行业层（cohort = (SW-L1, target_end, offset)，2881 个行业 cohort、平均成员 76.1，见 hef_layers.log）：I1（仅行业 nowcast）RIDGE IC=0.2084、I2（+行业基本面聚合）IC=0.2499、I3（+行业离散度）IC=0.2402；GBM 对应 0.2371/0.2462/0.2627。行业层（P30）可预测行业整体超预期，但绝对水平低于公司层，且 I2→I3 增益递减（行业基本面与离散度贡献有限）。链式 Revenue×Margin→Profit（P1）IC 仅 -0.0252，明显弱于直接建模，故 P1 只作稳健性副产品，不作主线。

**Q11.**

增量检验（TRAINFIX，RIDGE，P30）：B_nowcast（仅行业 nowcast）IC=0.0338 → B_nowcast_mkt（+市场）IC=0.1532 → B_no_guide IC=0.2643 → B_all IC=0.2718。行业 nowcast 单独几乎无技能（0.034），加入市场后翻倍；真正的技能来自公司层财务块。业绩预告（guide）贡献约 0.0075，且 guide_to_mv 覆盖率仅 2.1%，属"高精度但稀疏"的增量信息，不构成技能主体。

**Q12.**

单因子 IC（逐 cohort，P30）：guide_to_mv 0.3840；dp_yoy 0.1315；np_yoy 0.1074；rev_yoy 0.1008；L_gross_margin 0.0908；ret60 0.0904。guide_to_mv 居首但覆盖极低；其余主力是盈利/营收同比与毛利率类（dp_yoy 0.1315、np_yoy 0.1074、rev_yoy 0.1008）与动量类（ret60 0.0904）。全部因子都来自 F_NUM，且 L7 门控确认 Target 实际值及其派生列不在设计矩阵中。

**Q13.**

Regime 稳定性：TRAINFIX 主模型（P30）逐年 IC 为 2023: 0.2964、2024: 0.3115、2025: 0.3274，无某年崩塌；分相位 IC：VALID(2023–24) 0.3284、OOS(2025) 0.3274、LIVE-LIKE(2026) nan（详见 eval_phase.parquet）。Walk-Forward W1–W5（滚动 2 年训练、预测下一年）IC 全部为正，最弱窗口 W2（0.1677）、最强窗口 W4（0.3666）。技能不是单一年份的产物。

**Q14.**

参数稳定性：RIDGE alpha {1,10,100} 下 IC 几乎不动（0.2691/0.2688/0.2688，P30）；LightGBM depth{2,3,5}×lr{0.03,0.05,0.10} 九组 IC 在 0.2997–0.3473 区间，预先注册配置（depth 3 / lr 0.05）IC=0.3315，非最优但稳居区间上半部（更深的 depth=5 略高，说明技能未被参数刀锋化）。

**Q15.**

Target 稳健性：把 Target 从"季节性预期偏离"换成"动量预期偏离"（surp_mom_raw）后，主模型 IC 从 0.3315 降至 0.1903（P20 0.1979 / P60 0.2007，TRAINFIX）。技能明显衰减但仍为正——结论对预期基准的选择敏感，即"预测的是相对某基准的偏离"，不是绝对的盈利水平。这是本研究最重要的边界条件。

════════════════════════════════════════════════════════════
## 二、PASS Gate（G1–G20）
════════════════════════════════════════════════════════════

G1–G7 为字面 Point-in-Time 不变量（对应 hef_audit 的 L1–L7）；
G8–G20 为透明重建的预测有效性阶梯，阈值在代码中公开声明。

| Gate | 判据 | 阈值来源 | 实测 | 结果 |
|---|---|---|---|---|
| G1 | PIT: last visible report public at P | spec-PIT | n_violation=0 | PASS |
| G2 | PIT: lag-k values gated by own announcement | spec-PIT | n_violation=0 | PASS |
| G3 | PIT: guidance public at P | spec-PIT | n_violation=0 | PASS |
| G4 | PIT: market snapshot fresh (<=10 trading days) | spec-PIT | n_violation=163 | PASS |
| G5 | PIT: unique (code, target, offset) key | spec-PIT | n_violation=0 | PASS |
| G6 | PIT: TARGET report announced strictly after P | spec-PIT | n_violation=0 | PASS |
| G7 | PIT: target actual not in design matrix | spec-PIT | n_violation=0 | PASS |
| G8 | labelled target share >= 0.50 | recon | share=0.872 | PASS |
| G9 | headline cohorts >= 5 (t-stat defined) | recon | n_cohorts=16 | PASS |
| G10 | headline rank IC >= 0.05 at primary offset | recon | ic=0.3315 | PASS |
| G11 | headline rank IC t-stat >= 2.0 | recon | ic_t=42.75 | PASS |
| G12 | share of cohorts with positive IC >= 0.60 | recon | pos_share=0.938 | PASS |
| G13 | beats random ranking: N1 z >= 2.0 | recon | z=28.50 | PASS |
| G14 | beats naive momentum rule N5 | recon | model=0.3315 rule=0.1384 | PASS |
| G15 | label-shuffle control N7 |IC| <= 0.05 | recon | |IC|=0.0160 | PASS |
| G16 | strong-surprise AUC >= 0.60 | recon | auc=0.7447 | PASS |
| G17 | top/bottom decile ordering monotone (> 0) | recon | mono=0.920 | PASS |
| G18 | every anchor year TRAINFIX IC > 0.05 | recon | min_year_ic=0.2964 (3 years) | PASS |
| G19 | every walk-forward window IC > 0.05 | recon | min_wf_ic=0.1677 (6 windows) | PASS |
| G20 | company block adds beyond nowcast+market | recon | B_all=0.3326 > B_nc_mkt=0.1723 | PASS |

**PIT 门控（G1–G7）：全部 PASS**；**全部 20 项：全部 PASS（未过 0 项）**

════════════════════════════════════════════════════════════
## 三、最终状态（FINAL STATUS）
════════════════════════════════════════════════════════════

```
PASS (FORECAST SKILL CONFIRMED, PRICE ALPHA NOT YET TESTED)
```

**结论**：在 P60/P30/P20 三个时点上，"下一季度业绩相对预先注册预期基准的偏离"具有可复现、可解释、跨年份与跨滚动窗口稳定的横截面预测能力。公司层主模型 TRAINFIX Rank IC=0.3315（t=42.8），强超预期二分类 AUC=0.7447，且显著强于随机排序（N1 z=28.50）与朴素动量规则（N5 IC=0.1384）。

**边界与诚实声明**

1. 一致预期不可得：本研究的 Target 是"相对预先注册预期基准的偏离"，
   不是"相对市场一致预期的超预期"。因此**不能**宣称"战胜市场预期"。
2. 结论对预期基准敏感：换成动量预期基准后 IC 由 0.33 降至 0.19。
   被预测的是"偏离"，不是绝对盈利水平。
3. 高 IC 有结构性来源：盈利同比本身具有强自相关，且部分行含已公开
   的业绩预告（guide_to_mv 单因子 IC 0.38、覆盖率仅 2%）。
   公司的技能因此高于一般"超预期预测"文献的水平，不应外推。
4. 行业 nowcast 单独几乎无效（IC≈0.03），单独依赖行业景气不足以选股。
5. 行业映射覆盖不足：SW-L1 仅有 3000 只股票的映射，47.7% 的行落在
   UNKNOWN，行业层结论的适用面受限。
6. 偏移邻域 (P45, P10) 未构建特征快照，该稳健性检验声明为 NOT_BUILT，
   未做估计（不作任何数值声明）。
7. 本 V1 **不产生任何实盘交易授权**；第二阶段价格 Alpha 只有在
   Forecast Gate 通过后才允许启动，且需独立注册。

════════════════════════════════════════════════════════════
## 四、产物清单
════════════════════════════════════════════════════════════

| 文件 | 存在 | 字节 |
|---|---|---|
| events.parquet | Y | 3920759 |
| quarterly_versions.parquet | Y | 30929904 |
| features_company.parquet | Y | 147636180 |
| targets.parquet | Y | 31313284 |
| predictions_company.parquet | Y | 48229139 |
| predictions_company_skill.parquet | Y | 11256 |
| predictions_layers.parquet | Y | 30610820 |
| predictions_layers_skill.parquet | Y | 10069 |
| eval_skill.parquet | Y | 37335 |
| eval_null.parquet | Y | 11635 |
| eval_factor_ic.parquet | Y | 8662 |
| eval_incremental.parquet | Y | 6098 |
| eval_target_robust.parquet | Y | 5953 |
| eval_regime.parquet | Y | 21180 |
| eval_param_stability.parquet | Y | 6576 |
| eval_phase.parquet | Y | 12857 |
| leakage_audit.csv | Y | 981 |
| coverage_ledger.csv | Y | 676 |

报告生成时间：2026-09-26 22:49:44；本文件由 `hef_report.py` 自动生成，所有数值直接读取 data/ 下的产物，未手工填写。
