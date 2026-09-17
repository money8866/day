# SEOS 审计报告（第四步）

- 生成时间：2026-09-16 09:39:16
- 数据截止：20260915
- 配置：config/seos_config.json v1.1
- 主题定义唯一来源：sector_master.json v2.0（= 需求文档的 theme_master.json）

## 1. 继承关系与边界

```
sector_master.json  →  theme_mapping  →  theme_membership
        →  Theme Daily Statistics  →  Theme Health
        →  SEOS  →  Signal Flags / Theme Phase / Rotation Signal
        →  Opportunity Pool  →  下一阶段（个股层，本阶段不进入）
```

- SEOS 只消费 Step 3 产出，不重新定义行业分类、概念分类、股票主题归属、core / primary company、keyword mapping。
- 输出对象只有主题（THEME），不含任何个股信号。
- 明确不进入：HVT / IGE / F120 / 个股评分 / 个股 BUY / NO TRADE / 交易执行 / 仓位建议 / 个股止损 / 个股买点。

## 2. 术语映射（需求 ↔ 本项目）

| 需求文档 | 本项目 |
| --- | --- |
| theme_master.json | sector_master.json（项目根目录，非 config/） |
| theme_id / theme_name | sector_id / sector_name |
| theme_seos.py | sector_seos_build.py |
| data/theme_seos_daily.csv | data/sector_seos_daily.csv |
| data/theme_signal_flags.csv | data/sector_signal_flags.csv |
| data/theme_divergence.csv | data/sector_divergence.csv |
| data/theme_rotation.csv | data/sector_rotation.csv |
| data/group_seos_daily.csv | data/sector_group_seos_daily.csv |
| data/theme_phase_history.csv | data/sector_phase_history.csv |
| output/theme_seos_today.json | output/sector_seos_today.json |
| output/theme_opportunity_pool.json | output/sector_opportunity_pool.json |
| config/seos_config.json | config/seos_config.json（同名保留） |
| output/seos_backtest.md / seos_audit.md | 同名保留 |

字段语义一一对应，未改变任何定义。

## 3. 输入契约与覆盖率

- 输入：sector_daily_stats.csv / sector_breadth.csv / sector_strength.csv / sector_volume.csv / sector_state_history.csv / sector_quality_daily.csv / sector_state_today.json（Step 3 产出）
- 面板：4500 行 = 50 主题 × 90 交易日（20260512 ~ 20260915）
- data_quality_score 覆盖率 100.00%；DATA_INVALID 行 0
- refresh 覆盖：增量重算 50 行（20260915）
- membership_as_of：20260915；membership_conf_min：0.7
- 基准：{'primary': '000300.SH', 'primary_name': '沪深300', 'secondary': '000852.SH', 'secondary_name': '中证1000'}

## 4. SEOS 特征与权重

| 支柱 | 权重 | 子项 | 子权重 |
| --- | --- | --- | --- |
| breadth_expansion | 0.25 | breadth_expansion_5 0.4、breadth_expansion_10 0.35、breadth_acceleration 0.25 | — |
| core_breadth | 0.20 | core_breadth_delta_5 0.5、core_breadth_delta_3 0.3、core_lead_breadth 0.2 | — |
| relative_strength | 0.15 | rs_turn_5 0.4、rs_turn_10 0.25、relative_strength_5 0.25、rs_fresh_cross 0.1 | — |
| volume_participation | 0.15 | volume_ratio_5_excess 0.6、volume_ratio_delta_5 0.4 | — |
| amount_share | 0.10 | amount_share_delta_5 0.5、amount_share_delta_10 0.3、amount_share_delta_3 0.2 | — |
| health_momentum | 0.10 | theme_health_delta_5 0.45、theme_health_delta_3 0.3、theme_health_delta_10 0.25 | — |
| consistency | 0.05 | improving_dim_ratio 0.6、positive_contribution_ratio 0.25、concentration_inverse 0.15 | — |

- 一致性维度（improving_dim_ratio）由 6 个同步改善维度构成：breadth_delta_5、core_breadth_delta_5、rs_turn_5、volume_ratio_delta_5、amount_share_delta_5、theme_health_delta_5
- extension_penalty 权重：ew_ret_5 0.4、ew_ret_10 0.25、ew_ret_20 0.15、top5_concentration 0.2；最大扣减 0.45
- ramp 区间按各特征在全样本上的无条件分布做单调尺度归一（仅缩放，不做收益择优、不搜索阈值）。

## 5. Phase 分布与状态转换

| 阶段 | 末日数量 | 窗口内出现次数 |
| --- | --- | --- |
| DORMANT | 49 | 4002 |
| EARLY | 0 | 195 |
| EMERGING | 0 | 156 |
| CONFIRMING | 0 | 18 |
| STRONG | 0 | 4 |
| COOLING | 0 | 20 |
| DETERIORATING | 1 | 99 |
| EXITING | 0 | 6 |
| DATA_INVALID | 0 | 0 |

- 窗口内状态转换 248 次

| 转换 | 次数 |
| --- | --- |
| EARLY->DORMANT | 50 |
| EMERGING->EARLY | 44 |
| DORMANT->EMERGING | 39 |
| DORMANT->EARLY | 36 |
| DETERIORATING->DORMANT | 18 |
| EARLY->DETERIORATING | 16 |
| EARLY->EMERGING | 9 |
| CONFIRMING->COOLING | 5 |
| EMERGING->CONFIRMING | 4 |
| COOLING->DETERIORATING | 4 |
| EARLY->EXITING | 3 |
| EXITING->DORMANT | 3 |

Hysteresis：进入用 phase_thresholds（EARLY 55 / EMERGING 65 / CONFIRMING 72 / STRONG 75），退出（降级）用 phase_exit_thresholds（EARLY 48 / EMERGING 58 / CONFIRMING 65 / STRONG 70）；梯级升级需目标级连续 2 日高于当前级、降级需 SEOS 连续 2 日跌破退出阈值；覆盖态（COOLING / DETERIORATING / EXITING）需连续 2 日条件成立才进入，需连续同日数解除才退出；DETERIORATING / EXITING 仅允许从已启动状态（EARLY/EMERGING/CONFIRMING/STRONG/COOLING）进入。

## 6. Signal Flags 统计

| Flag | 窗口内次数 | 末日数量 |
| --- | --- | --- |
| BREADTH_EXPANDING | 1337 | 0 |
| CORE_EXPANDING | 1235 | 3 |
| RS_TURNING_UP | 1977 | 2 |
| VOLUME_PARTICIPATION | 1307 | 1 |
| AMOUNT_SHARE_EXPANDING | 2042 | 20 |
| HEALTH_IMPROVING | 2017 | 1 |
| EXTENSION_HIGH | 6 | 0 |
| CONCENTRATION_HIGH | 18 | 0 |
| CORE_WEAK | 1392 | 38 |
| BREADTH_DIVERGENCE | 439 | 0 |
| RS_DIVERGENCE | 640 | 1 |
| VOLUME_DIVERGENCE | 131 | 1 |
| FAILED_EXPANSION | 1049 | 1 |

## 7. startup_quality 结构类型

| 结构类型 | 窗口内 | 末日 |
| --- | --- | --- |
| FAILED_EXPANSION | 908 | 1 |
| HEALTHY_EXPANSION | 578 | 0 |
| INSUFFICIENT_DATA | 845 | 7 |
| NARROW_LEADERSHIP | 7 | 0 |
| NEUTRAL | 2135 | 42 |
| VOLUME_SPIKE | 27 | 0 |

| 核心扩散形态（core_pattern） | 窗口内 | 末日 |
| --- | --- | --- |
| A_HEALTHY_DIFFUSION | 303 | 0 |
| B_EDGE_DRIVEN | 176 | 1 |
| C_EARLY_CORE_ONLY | 231 | 1 |
| D_MIXED | 650 | 14 |
| INSUFFICIENT_DATA | 3140 | 34 |

## 8. Divergence 与 Rotation

| 背离类型 | 窗口内 | 末日 |
| --- | --- | --- |
| PRICE_BREADTH_DIVERGENCE | 439 | 0 |
| PRICE_CORE_DIVERGENCE | 684 | 1 |
| RS_BREADTH_DIVERGENCE | 640 | 1 |
| VOLUME_BREADTH_DIVERGENCE | 131 | 1 |

| rotation_signal | 窗口内 | 末日 |
| --- | --- | --- |
| ROTATION_IN | 185 | 0 |
| ROTATION_OUT | 67 | 3 |
| ROTATION_NEUTRAL | 4248 | 47 |

> ROTATION_IN / ROTATION_OUT 只描述【已观测到】的结构变化，不预测未来轮动。

## 9. Rotation Group（辅助信息，不覆盖单主题状态）

| Group | 名称 | 主题数 | Breadth | Health | SEOS | SEOS 5D变化 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R01 | 科技成长 | 6 | -0.4111 | 35.21 | 41.1771 | -8.3705 | WEAKENING |
| R02 | 先进制造 | 9 | -0.5002 | 32.9322 | 40.6302 | -9.8668 | WEAKENING |
| R03 | 资源周期 | 7 | -0.5452 | 26.95 | 27.5592 | -26.7688 | WEAKENING |
| R04 | 金融地产 | 6 | -0.808 | 20.4483 | 22.4978 | -14.9887 | WEAKENING |
| R05 | 大消费 | 7 | -0.7341 | 16.84 | 18.6264 | -33.1461 | WEAKENING |
| R06 | 医药医疗 | 7 | -0.7934 | 18.8229 | 23.8383 | -37.4127 | WEAKENING |
| R07 | 公用防御 | 3 | -0.6842 | 25.19 | 25.6878 | -28.4612 | WEAKENING |
| R08 | 政策基建 | 5 | -0.7172 | 24.598 | 28.1927 | -23.776 | WEAKENING |

## 10. 验证结果

| 检查项 | 结果 | 说明 |
| --- | --- | --- |
| CHECK_MASTER_SOURCE | PASS | sector_master.json v2.0 / 50 主题；SEOS 输出 50 个主题，全部合法 |
| CHECK_LEGACY_ISOLATION | PASS | 本次运行读取 8 个文件，未包含任何 legacy 配置；磁盘上仍存在 legacy 文件（仅作参考，未被读取）：['D:\\mystock\\solo\\theme.json', 'D:\\mystock\\solo\\theme_config.json', 'D:\\mystock\\solo\\bak0615\\theme.json', 'D:\\mystock\\solo\\multi_factor_picker\\cache\\theme.json', 'D:\\mystock\\solo\\theme_kg_v3\\theme_kg_v3\\config\\subtheme_map.json', 'D:\\mystock\\solo\\theme_kg_v3\\theme_kg_v3\\config\\theme_config.json'] |
| CHECK_THEME_ID_VALID | PASS | 主题 id 覆盖 50/50，空值 0 个；缺失：无 |
| CHECK_MEMBERSHIP_NO_LEAK | PASS | membership effective_date 最大 2026-09-15，超出面板末日 0 行；membership_as_of=20260915（<= 面板末日：True）；SEOS 不重建归属，直接沿用 Step 3 快照 |
| CHECK_NO_LOOKAHEAD_SEOS | PASS | 比对 8 个截断日 × 30 列 / 11704 个数值，最大偏差 0.000e+00 |
| CHECK_NO_LOOKAHEAD_PHASE | PASS | 比对 8 个截断日 × 7 列 / 2800 个数值，最大偏差 0.000e+00 |
| CHECK_NO_LOOKAHEAD_ROTATION | PASS | 比对 8 个截断日 × 3 列 / 1200 个数值，最大偏差 0.000e+00 |
| CHECK_INDICATOR_TRACEABLE | PASS | 评分独立复算最大偏差 0.000e+00；输出列缺失 0 个[]；有效行分项缺失 0 行 |
| CHECK_NO_PHASE_JUMP | PASS | 状态转换 248 次，非法跳变 0 次（全部落在白名单转移集合内） |
| CHECK_HYSTERESIS | PASS | 降级必须低于退出阈值：违约 0 次；3 日内回转（抖动）26/248 = 10.5%（上限 25%） |
| CHECK_MISSING_DATA_HANDLED | PASS | 滑动窗口未就绪 845 行（breadth_delta_5,core_breadth_delta_5,rs_turn_5 缺值），全部落于 DORMANT/DATA_INVALID；越界 0 行；未就绪行的分项按 neutral_fill_score 中性填充，不产生启动信号 |
| CHECK_DATA_INVALID_PROPAGATION | PASS | 真实数据 DATA_INVALID 行 0；注入 dq<0.6 后末日 50 个主题全部为 DATA_INVALID 且 seos_score 为空：True；flags 全部置 False：True |

- 合计 12/12 项通过。

## 11. 未来函数与 Legacy 污染检查

- 未来函数：以【物理截断重算】为准 —— 只用 <=D 的数据重跑整条链路，逐列比对 D 日数值（见 CHECK_NO_LOOKAHEAD_SEOS / PHASE / ROTATION）。
- Legacy 污染：本程序读取的文件清单在运行期被记录，theme_config.json / subtheme_map.json / theme.json 未出现在清单中。

## 12. 停止边界

第四步到此停止。未实现、且本阶段不实现：Step 5 个股 Theme→Stock Candidate、Step 6 HVT / IGE / F120、Step 7 Execution、Step 8 BUY / NO TRADE。
