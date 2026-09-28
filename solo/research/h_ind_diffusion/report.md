# H-IND-DIFFUSION-01 行业强度扩散 → 个股短线延续 Alpha 研究报告

自动科研智能体 V1.0 · 最终结论

生成时间：2026-09-27 21:14:08　｜　预注册指纹：`e8c92f5eb9a56c88`　｜　派生定义指纹：`9837bbf708cda6ff`

══════════════════════════════════════════════

最终判定：**FAIL — NO ROBUST INCREMENTAL INDUSTRY DIFFUSION ALPHA FOUND**

TRADING_AUTHORIZATION = **NO**　｜　§56 处置 = **ARCHIVE**

致命 Gate 未通过：G8、G9、G13

══════════════════════════════════════════════

## 一、研究问题（§0 / §60）

在已经知道行业 Momentum 与股票 Momentum 的情况下，行业内「强度从少数股票向多数股票扩散」是否包含新的、可事前识别、可跨期重复、成本后存在经济意义的信息？

研究单位不是单只股票的技术形态，而是 Industry State → Within-industry Diffusion → Stock Return（§1）。文献先验（§2）仅作 Prior，不外推至 A 股短线（§1/§2）。

## 二、数据与口径

| 项 | 值 |
|---|---|
| 面板 | 5816 股 × 2120 交易日 |
| 区间 | 20180102 ~ 20260924 |
| 行业 | 申万 L1 31 个（主口径）+ L2 131 个（稳健性抽检） |
| 行业来源 | sw_member_all.csv |
| 有效样本 | 8762946 股票日（PASS） |
| 剔除（如实披露，不静默） | ST=188066 SUSPEND=48522 NEW=1988598 BJ=746240 NO_DATA=385889 ENDED=209659 |
| L1 覆盖 | 0.993600（重算 0.993600）；UNKNOWN=56084 |
| 分期 | TRAIN=1215 VALID=484 OOS=243 LIVE-LIKE=178 |
| 前向收益 | exe_H = close[k+H]/open[k+1]-1（k 日出信号，k+1 开盘进场） |
| 行业 N 门槛 | N >= 20（§7/§40）；LOW_SAMPLE 标记 |
| 随机种子 / 置换次数 | 20260927 / 1000 |

## 三、核心结果

### 3.1 行业层信号（h=5，全样本）

| 信号 | Rank IC | t_NW | Q5−Q1（行业） | mono |
|---|---|---|---|---|
| 行业动量 EW20（§9） | -0.01556 | -2.50 | +0.00044 | +0.70 |
| Breadth20 水平（§10） | -0.01151 | -1.96 | +0.00078 | +0.80 |
| BreadthChange5（§11 主方向变量） | +0.00534 | +1.12 | +0.00142 | +0.90 |
| BreadthAcceleration（§12） | -0.00023 | -0.05 | -0.00042 | -0.40 |
| LeaderBreadth Top20%（§14） | +0.00138 | +0.32 | +0.00072 | +0.70 |
| RS_BreadthChange（§18） | -0.00418 | -1.44 | -0.00069 | -0.70 |
| DiffusionGap（§16） | +0.00347 | +1.08 | +0.00033 | +0.10 |
| diffusion_score（4 分量等权） | +0.00056 | +0.12 | +0.00029 | +0.50 |

### 3.2 股票层暴露与组合

| 对象 | 口径 | 日均/IC | t_NW | 备注 |
|---|---|---|---|---|
| exposure（0.5·行业扩散分位 + 0.5·行业内 RS20 分位） | ALL h5 | -0.03738 | -7.35 | 原始方向；IC 显著为负 |
| exposure | OOS h5 | -0.05762 | -5.96 | 方向与假设相反且显著 |
| expo_ind（仅行业扩散分量） | ALL h5 | +0.00056 | +0.12 | ≈0 |
| DIFF（行业Top3 × 行业内Top20%） | ALL h5 | +0.00008 | +0.05 | 与 P4 口径一致 |
| B4（行业动量 + 股票动量） | ALL h5 | -0.00204 | -1.02 | §44 基准 |
| **DIFF − B4（增量 α）** | ALL h5 | +0.00212 | +1.65 | §44 唯一有效比较 |
| **DIFF − B4** | OOS h5 | +0.00300 | +0.89 | 未达显著 |

关键观察：股票层 `exposure` 的负 IC 主要由「行业内 RS20 分位」分量驱动（expo_rs h5 ALL IC=-0.04315，t=-8.59），而纯行业扩散分量 expo_ind 趋近于 0（IC=+0.00056，t=+0.12）。即在 2018–2026 全样本上，20 日相对强势呈反转特征，行业扩散分量自身在股票横截面上不产生方向性收益。

## 四、G1–G20 判定（§50）

| Gate | 名称 | 致命 | 判定 | 证据 |
|---|---|---|---|---|
| G1 | PIT / Data Integrity | LETHAL | PASS | 阶段 hash 一致=True；valid 重算=8762946 vs meta=8762946；coverage 重算=0.993600 vs meta=0.993600 |
| G2 | Industry Mapping |  | PASS | SW L1 PIT 覆盖率=0.993600（门槛 0.90）；UNKNOWN=56084（未删除样本，如实披露） |
| G3 | Tradability |  | PASS | DIFF 入选样本 T+1 可成交率=0.9944（门槛 0.95） |
| G4 | Momentum Control | LETHAL | PASS | M2 ind_breadth：ALL beta=+0.0006 t=+1.0619；OOS beta=+0.0033 t=+2.5278（ALL 需 beta>0 且 t>0，OOS 需同号） |
| G5 | Industry Momentum Control | LETHAL | PASS | M3 diffusion：ALL beta=+0.0010 t=+3.1312；OOS beta=+0.0002 t=+0.3213 |
| G6 | Market Control |  | PASS | FM diffusion（含 §24 市场层残差化）：ALL beta=+0.0008 t=+3.1224（n=2046）；OOS beta=+0.0005 t=+0.8878 |
| G7 | Random Industry-Date Null |  | **FAIL** | N1 随机行业-日期：obs=+0.00033 null=+0.00050 p=0.6843 z=-0.46 |
| G8 | Momentum-Matched Null | LETHAL | **FAIL** | N3 动量匹配：obs=-0.00004 null=+0.00041 p=0.9890 | N4 行业动量匹配：obs=+0.00033 null=-0.00044 p=0.0060 |
| G9 | OOS | LETHAL | **FAIL** | OOS exposure ALL：IC=-0.05762 t=-5.96 Q5-Q1=-0.00275（n=243） |
| G13 | OOS Discrimination | LETHAL | **FAIL** | OOS |IC|=+0.05762（≥0.02）AUC=+0.4762（≥0.52）Prec@10=+0.4504（≥0.10）mono_rho=-1.00（>0）方向无关上限 AUC'=+0.5238 |
| G10 | Walk Forward |  | **FAIL** | OOS 增量与 IC 双正窗口 0/6 = +0.00（阈值 > 0.50） |
| G11 | Cross-Year |  | PASS | 正年份 4/6 = +0.67（阈值 0.60）；最大单年贡献占比 +0.36（上限 0.50） |
| G12 | Regime |  | PASS | BEAR/NORMAL/BULL 增量符号翻转 1 次（上限 1）；明细 [['BEAR', 0.0031076179311612115, 1.5024470160605097], ['NORMAL', 0.004823324119334097, 2.070261523123942], ['BULL', -0.00034359952159501635, -0.16891356548729955]] |
| G14 | Parameter Stability |  | PASS | 主参数面 12 格中增量为正 8 格 = +0.67（阈值 0.60） |
| G15 | Tail Stability |  | **FAIL** | Leave-Top5% 后 DIFF-B4 日均增量 = -0.00122（t=-0.96） |
| G16 | Cost | LETHAL | PASS | 30bp 往返后 DIFF-B4：gross=+0.00067 换手/日=0.2006 净日均=+0.00007 净年化=+1.60% t=+0.21 |
| G17 | Permutation |  | **FAIL** | §45 置换（n_perm=1000）IC_exposure h5 ALL：obs=-0.03761 null=+0.00000 p=1.0000 z=-107.95 |
| G18 | Multiple Testing |  | **FAIL** | 关键检验 K1-K7 经 BH-FDR 后族内/合并池同时显著：3/7 项满足；明细 {'model|M3.diffusion|ALL|h5|ALL': ('p=0.000871', True, True), 'fama_macbeth|diffusion|ALL|h5|ALL': ('p=0.000897', True, True), 'baseline|DIFF-B4|h5|ALL': ('p=0.1', False, False), 'oos|signal_ALL|h5|OOS': ('p=2.49e-09', True, True), 'permutation|IC_exposure|h5|ALL': ('p=1', False, False), 'null|N3_MomentumMatched|DIFF_return|h5|ALL': ('p=0.989', False, False), 'null|N4_IndustryMomentumMatched|IS_DIFF_return|h5|ALL': ('p=0.00599', False, True)} |
| G19 | Economic Significance |  | **FAIL** | 30bp 后净日均增量 +0.00007 vs 预注册门槛 0.0020 |
| G20 | Incremental Alpha | LETHAL | PASS | §44 DIFF−B4：ALL 日均=+0.00212 t=+1.65；OOS 日均=+0.00300 t=+0.89（ALL 需 >0 且 t>0，OOS 需同号） |

PASS 数：11 / 20。致命 Gate（§51：G1 G4 G5 G8 G9 G13 G16 G20）未通过项：G8、G9、G13。

## 五、§55 十五问逐条回答

1. **Q1 行业 Breadth 是否预测未来行业收益？**

　　否（弱/反向）。Breadth20 水平（ind_up_20）h5 IC=-0.01151（t=-1.96）；行业动量 ind_mom_20 IC=-0.01556（t=-2.50）。

2. **Q2 Breadth Change 是否比 Breadth Level 更有信息？**

　　方向上「是」，但幅度不显著。BreadthChange5（ind_dchg_5）IC=+0.00534（t=+1.12）> Breadth20 水平 IC=-0.01151（t=-1.96）；15 个 IND 信号中 |t| 最大者仍 < 3。

3. **Q3 行业 Momentum 是否已经解释 Breadth？**

　　是（大部分）。模型阶梯：M2 加入 ind_breadth 后 beta=+0.000566（t=+1.06，h5 ALL），相对 M1（ind_mom t=+1.98）增量 t 不足 2；M3 加入 diffusion 后 ind_breadth 被吸收。

4. **Q4 Leader Breadth 是否提供增量信息？**

　　无显著增量。ind_lb_020（LeaderBreadth20%）h5 IC=+0.00138（t=+0.32）；DiffusionGap（LB20−Breadth5）IC=+0.00347（t=+1.08）。

5. **Q5 Narrow Leadership 与 Broad Diffusion 是否存在差异？**

　　存在方向差异但不显著。§27 四象限：A（动量↑扩散↑）均值=-0.00003，B（动量↑未扩散）均值=+0.00941，A−B=+0.00167（t=+0.20）。

6. **Q6 Diffusion 是否能预测行业内部股票？**

　　否（原始方向显著为负）。股票层 exposure：ALL IC=-0.03738（t=-7.35），行业内部口径 IC 见 oos_results。

7. **Q7 控制 Stock Momentum 后是否仍然有效？**

　　G4 判定 PASS。M2 的 ind_breadth 相对 M1 增量 beta=+0.000566（t=+1.06，h5 ALL）。

8. **Q8 控制 Industry Momentum 后是否仍然有效？**

　　行业层 G5=PASS（M3 diffusion beta=+0.001009，t=+3.13，h5 ALL）；但股票层暴露 OOS 不显著（t=-5.96），组合层 DIFF−B4 OOS t=+1.65。

9. **Q9 控制 Market Momentum 后是否仍然有效？**

　　G6=PASS。FM diffusion（§24 市场层残差化后）ALL beta=+0.000818，t=+3.12。

10. **Q10 Random Industry-Date 是否能够复制？**

　　G7=FAIL。N1：obs=+0.00033 null=+0.00050 p=0.6843。

11. **Q11 Momentum-Matched Null 是否能够复制？**

　　G8=FAIL。N3 p=0.9890；N4 p=0.0060（单侧，方向为「观测 > null」）。

12. **Q12 OOS 是否有效？**

　　G9=FAIL / G13=FAIL。OOS exposure ALL：IC=-0.05762（t=-5.96），Q5−Q1=-0.00275，AUC=+0.4762，Prec@10=+0.4504。

13. **Q13 是否跨年份、跨 Regime 稳定？**

　　G11=PASS / G12=PASS。正年份比例 +0.67；Regime 符号翻转 1 次。

14. **Q14 30bp 成本后是否仍有经济意义？**

　　G16=PASS / G19=FAIL。30bp 净日均增量=+0.00007（净年化 +1.60%），门槛 0.0020。

15. **Q15 Industry Diffusion 是否最终形成独立于既有 20D Momentum 的 Incremental Alpha？**

　　否。G20=PASS（ALL 日均=+0.00212 t=+1.65；OOS 日均=+0.00300 t=+0.89）；且股票层暴露方向与假设相反，组合层增量未显著。

## 六、Null / 反事实 / 置换（§26 §27 §45）

| Null 模型 | obs | null 均值 | 单侧 p | Z |
|---|---|---|---|---|
| N1 Random Industry-Date | +0.00033 | +0.00050 | 0.6843 | -0.46 |
| N3 Momentum-Matched | -0.00004 | +0.00041 | 0.9890 | -2.25 |
| N4 Industry-Momentum Matched | +0.00033 | -0.00044 | 0.0060 | +2.49 |
| N5 Breadth Shuffled | +0.00033 | +0.00065 | 0.7413 | -0.63 |

| 四象限 | 所选行业 h5 平均收益 |
|---|---|
| A（动量↑扩散↑） | -0.00003 |
| B（动量↑未扩散） | +0.00941 |
| C（动量↓扩散↑） | +0.00295 |
| D（动量↓扩散↓） | +0.00143 |
| **A − B（§27 核心反事实）** | +0.00167（t=+0.20） |

## 七、多重检验（§46）

共 601 项检验；族内 BH 显著 72 项，合并池 BH 显著 76 项（alpha=0.05）。

| 族 | n | raw p<0.05 | 族内显著 | 合并池显著 |
|---|---|---|---|---|
| baseline | 120 | 16 | 5 | 7 |
| cost | 15 | 7 | 4 | 3 |
| discrimination | 24 | 12 | 12 | 12 |
| fama_macbeth | 84 | 12 | 5 | 6 |
| feature | 19 | 6 | 6 | 5 |
| horizon | 60 | 22 | 21 | 18 |
| model | 24 | 10 | 9 | 8 |
| null | 104 | 12 | 0 | 6 |
| oos | 16 | 7 | 5 | 5 |
| permutation | 84 | 4 | 0 | 2 |
| quantile | 12 | 6 | 5 | 4 |
| regime | 3 | 1 | 0 | 0 |
| tail | 4 | 2 | 0 | 0 |
| walkforward | 6 | 0 | 0 | 0 |
| window | 20 | 5 | 0 | 0 |
| year | 6 | 0 | 0 | 0 |

G18 关键检验集（K1–K7）：

| 关键检验 | raw p | 族内显著 | 合并池显著 |
|---|---|---|---|
| model|M3.diffusion|ALL|h5|ALL | 0.0008706 | True | True |
| fama_macbeth|diffusion|ALL|h5|ALL | 0.0008971 | True | True |
| baseline|DIFF-B4|h5|ALL | 0.09995 | False | False |
| oos|signal_ALL|h5|OOS | 2.487e-09 | True | True |
| permutation|IC_exposure|h5|ALL | 1 | False | False |
| null|N3_MomentumMatched|DIFF_return|h5|ALL | 0.989 | False | False |
| null|N4_IndustryMomentumMatched|IS_DIFF_return|h5|ALL | 0.005994 | False | True |

## 八、参数稳定性与尾部（§37 §38 §39）

参数面四轴（breadth_win / mom_win / ind_n / leader_pct）全部结果见 `parameter_surface.csv`；判定：G14=PASS，G15=FAIL。

## 九、失败归因（§49）

| 代码 | 失败类型 | 失败数 | 占失败比 | 桶内失败率 |
|---|---|---|---|---|
| F4 | Market Regime Reversal | 4502 | 5.6% | 0.673 |
| F1 | Industry Momentum Reversal | 12740 | 15.8% | 0.744 |
| F2 | Breadth Fake Expansion | 26272 | 32.6% | 0.635 |
| F6 | Narrow Leadership | 12809 | 15.9% | 0.515 |
| F3 | Leader Collapse | 8396 | 10.4% | 0.676 |
| F7 | Stock-specific Shock | 258 | 0.3% | 0.717 |
| F8 | Liquidity Failure | 1178 | 1.5% | 0.337 |
| F5 | Industry Rotation | 534 | 0.7% | 0.386 |
| F0 | Other/Unclassified | 14016 | 17.4% | 0.350 |
| TOTAL | ALL selected samples | 80705 | 100.0% | 0.546 |

注：§49 失败归因为**描述性**统计（失败 = DIFF 选股样本 exe_5 <= 0，F0–F8 按固定优先级首个命中归类），不用于事后修补策略，也不构成 §53 所要求的「预注册失败风险预测检验」。

## 十、最终判定（§52 / §53 / §54 / §61）

§52 要求的全部条件中未满足：G9、G10、G13、G15、G17、G18。

§53 CONDITIONAL：不触发。触发需要预注册的「Industry Diffusion 预测 Failure Risk」检验证据，本研究未预注册该检验（[R5]），不得以描述性失败归因替代。

结论：**FAIL — NO ROBUST INCREMENTAL INDUSTRY DIFFUSION ALPHA FOUND**

结构性问题（§54）：

1. 股票层扩散暴露的收益方向与假设相反（`exposure` h5 ALL IC=-0.03738，t=-7.35；OOS IC=-0.05762，t=-5.96），反向不参与 Gate（§59）。
2. 行业层 diffusion 在控住行业动量后 beta 显著（M3 beta=+0.001009，t=+3.13），但该显著性未在 OOS 期复现（OOS t=+0.32），组合层增量亦不显著（DIFF−B4 OOS t=+0.89），即 §22 的 M3−M2 增量检验未通过。
3. 成本与尾部：30bp 后净日均增量 +0.00007；G16=PASS。

## 十一、§56 接口处置

本研究未通过，**ARCHIVE**。不得作为 Theme/Industry Gate 接入既有 20D Momentum 选股链路，不得污染现有生产策略。

`TRADING_AUTHORIZATION = NO`（§61 默认 NO）。本研究不产生任何具体股票买入授权；若未来需交易层研究，必须另立独立课题 `H-IND-DIFFUSION-TRADE-01` 并重新通过全部 G1–G20。

## 十二、产出文件（§57）

| 文件 | 状态 | 大小 |
|---|---|---|
| industry_daily_state.parquet | OK | 19956.4 KB |
| industry_diffusion_features.parquet | OK | 1090.8 KB |
| stock_diffusion_exposure.parquet | OK | 287957.5 KB |
| ic_results.csv | OK | 47.8 KB |
| auc_results.csv | OK | 50.6 KB |
| quantile_results.csv | OK | 144.9 KB |
| fama_macbeth.csv | OK | 19.0 KB |
| model_ladder.csv | OK | 38.8 KB |
| baseline_results.csv | OK | 13.0 KB |
| null_models.csv | OK | 23.0 KB |
| permutation.csv | OK | 15.8 KB |
| fdr_results.csv | OK | 76.1 KB |
| oos_results.csv | OK | 17.8 KB |
| walkforward.csv | OK | 7.3 KB |
| year_results.csv | OK | 1.7 KB |
| regime_results.csv | OK | 0.6 KB |
| parameter_surface.csv | OK | 4.1 KB |
| cost_analysis.csv | OK | 3.6 KB |
| tail_analysis.csv | OK | 1.6 KB |
| failure_analysis.csv | OK | 1.2 KB |
| gate_results.csv | OK | 5.1 KB |
| h_ind_diffusion.json | OK | 20.2 KB |
| report.md | OK（本报告） | — |

## 十三、实现修正与溯源（§50 G1）

- **`hid_null.py`** · Acc 零分布累积的 NaN 污染（P5 首跑之后、P6 之前）

　　原实现以 sum += null_vals 累积置换零分布，使某日整条为 NaN 的置换向量（如面板末尾各 horizon 前向收益整体缺失的日子）永久污染该子样本全部 1000 个置换位，导致 N1/N4 在 ALL/TRAIN/LIVE-LIKE 上 n_perm=0、null 与 p 均为 NaN；据此判定 G7/G8 会以 NaN 伪装 FAIL。已改为「逐位置有限值计数」累积（sum 仅累加有限值、fin 记有限次数），并在同一冻结口径下重跑 P5。

　　影响范围：仅影响 null_models.csv 的 N1/N4 行；N2/N3/N5 与 permutation.csv 的全部置换值在原实现下 n_perm 已为 1000（无 NaN 位置），重跑后已逐值核对完全一致。特征定义、样本、方向、阈值、Gate 判据均未改动（§54：非事后救援）。

──────────────────────────────────────────────

本报告全部数值取自 `research/h_ind_diffusion/` 下已冻结产物；预注册指纹与派生定义指纹已在 P1–P7 全链路校验一致（G1）。
