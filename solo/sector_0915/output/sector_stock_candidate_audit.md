# Step 5 — Theme-to-Stock Candidate Layer 审计报告

1. **数据日期**：20260916（主题面板 20260512 → 20260916）
2. **股票 universe**：成员覆盖股票 4995 只；通过 universe 过滤 165056 行；上市状态 L 5556 只
3. **Theme 数量**：真源定义 50 个；面板出现 50 个
4. **Opportunity Theme 数量**：40（风险观察 22）
5. **Candidate 股票数量**：5499 行（去重 920 只）
6. **WATCH 数量**：31543
7. **EXCLUDE 数量**：129324
8. **CORE / PRIMARY / SECONDARY 数量**：CORE=13301 / PRIMARY=28190 / SECONDARY=6711 / THEMATIC=118164
9. **THEME_DIFFUSION 数量**：2263
10. **THEME_PULLBACK 数量**：988
11. **高拥挤股票数量**：1864（CANDIDATE/WATCH 中 157）
12. **高扩张股票数量**：价格 13031 / 成交量 6096（极端价 3305 / 极端量 1109）
13. **Pollution 数量**：27634；分布 {'DATA_INVALID': 13798, 'INDUSTRY_CONFLICT': 7120, 'OVER_EXTENSION': 3299, 'THEME_EXITING': 1994, 'VOLUME_SPIKE': 880, 'CONCEPT_POLLUTION': 543}
14. **跨主题股票数量**：32880（同一交易日同一股票进入多个主题评估）
15. **缺失数据数量**：ret_5 缺失 283；基本面缺失（coverage<100%）25777；data_quality<阈值 14438
16. **历史回测结果**：见 output/sector_stock_candidate_backtest.md
17. **Future Leakage Check**：
    - 价格：全部结构指标只用 trade_date ≤ D 的行情；前向收益仅内部回测使用，不写入对外输出
    - 财报：merge_asof 按 ann_date ≤ trade_date 向后匹配，绝无未来财报
    - 主题状态：直接消费 Step 3/4 已截断的历史面板，无前视
    - 成员：静态映射视为恒定义；非静态行按 effective_date ≤ trade_date 过滤（违规 0 行）
18. **Legacy Contamination Check**：
    - legacy_config_used = False
    - 读取到的 legacy 文件：无
    - 忽略清单：['theme_config.json', 'subtheme_map.json', 'theme.json', 'theme_master.json', 'theme_mapping.json']
19. **异常样本**：
    - trend_broken 但入选：17081
    - industry_conflict 入选：115
    - 角色 UNKNOWN 入选：0
20. **Top Candidate 样本**：

| 主题 | 股票 | 角色 | 成员 | 类型 | 分数 | 拥挤 | 扩张 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T01 半导体 | 中微公司(688012.SH) | CORE_SUPPLIER | CORE | THEME_CORE | 86.52 | LOW | LOW | WATCH |
| T01 半导体 | 拓荆科技(688072.SH) | CORE_SUPPLIER | CORE | THEME_CORE | 86.31 | LOW | LOW | CANDIDATE |
| T01 半导体 | 耐科装备(688419.SH) | CORE_SUPPLIER | CORE | THEME_CORE | 86.05 | LOW | LOW | CANDIDATE |
| T01 半导体 | 金海通(603061.SH) | CORE_SUPPLIER | CORE | THEME_CORE | 86.02 | LOW | LOW | CANDIDATE |
| T01 半导体 | 源杰科技(688498.SH) | CORE_APPLICATION | PRIMARY | THEME_CORE | 85.65 | LOW | LOW | CANDIDATE |
| T01 半导体 | 中船特气(688146.SH) | CORE_SUPPLIER | CORE | THEME_WATCH | 80.66 | LOW | LOW | WATCH |
| T01 半导体 | 康强电子(002119.SZ) | CORE_SUPPLIER | CORE | THEME_WATCH | 80.47 | LOW | LOW | WATCH |
| T01 半导体 | 中科飞测(688361.SH) | CORE_SUPPLIER | CORE | THEME_WATCH | 80.31 | LOW | LOW | WATCH |

**验证结果：27/27 通过**

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| CHECK_CONFIG_LOADED | PASS | version=1.6 |
| CHECK_THEME_IN_MASTER | PASS | 未知主题=[] |
| CHECK_MEMBERSHIP_SOURCE | PASS | 所有候选均来自 data/sector_membership.csv |
| CHECK_LEGACY_ISOLATION | PASS | legacy 读取=[] |
| CHECK_NO_FUTURE_PRICE | PASS | 前向列=[] |
| CHECK_NO_FUTURE_FUNDAMENTAL | PASS | ann_date>trade_date 行数=0 |
| CHECK_MEMBERSHIP_PIT | PASS | effective_date>trade_date 行数=0 |
| CHECK_THEME_RETURN_PIT | PASS | leakage: 未来成员进入 D=False 到期后可见=True 原is_static=True；独立重算不一致=0/300 |
| CHECK_STATUS_ENUM | PASS | 非法状态=[] |
| CHECK_CANDIDATE_TYPE_ENUM | PASS | 非法类型=[] |
| CHECK_ROLE_ENUM | PASS | 非法角色=[] |
| CHECK_DIFFUSION_TYPE_ENUM | PASS | 非法扩散类型=[] |
| CHECK_DIFFUSION_PATTERN_ENUM | PASS | 非法形态=[] 非法阶段=[] |
| CHECK_POLLUTION_ENFORCED | PASS | 非法分级=[] 污染行泄漏进 CANDIDATE=0 |
| CHECK_SCORE_RANGE | PASS | min=12.737 max=91.650 |
| CHECK_UNIVERSE_FILTER | PASS | ST 入选=0 |
| CHECK_NO_DUPLICATE | PASS | 重复行=0 |
| CHECK_SELECTION_LIMITS | PASS | 单主题最多=12 单类型最多=5 |
| CHECK_POLLUTION_LOGGED | PASS | 污染类型=['CONCEPT_POLLUTION', 'DATA_INVALID', 'INDUSTRY_CONFLICT', 'OVER_EXTENSION', 'THEME_EXITING', 'VOLUME_SPIKE'] |
| CHECK_EXTENSION_NOT_TOP1 | PASS | 违规主题日=0 |
| CHECK_DIFFUSION_IDENTIFIABLE | PASS | 强主题扩散类型=['CORE_EXPANSION', 'FAILED_DIFFUSION', 'FULL_BREADTH_EXPANSION', 'NO_DIFFUSION', 'PRIMARY_EXPANSION', 'SECOND_LINE_EXPANSION'] |
| CHECK_INPUT_COVERAGE | PASS | ret_5 覆盖=0.998 基本面覆盖=1.000 |
| CHECK_NO_TRADING_TERMS | PASS | 候选内容层命中禁用词=[]；not_a_buy_list=True |
| CHECK_NO_DORMANT_CANDIDATE | PASS | DORMANT/INVALID 入选=0 |
| CHECK_CROSS_THEME_BONUS_CAP | PASS | 越界行=0 cap=5.0 |
| CHECK_ROLE_NOT_BY_RETURN | PASS | CORE_LEADER 非 CORE_COMPANY 来源=0 |
| CHECK_STEP5_STOP_NOTICE | PASS | 审计报告缺失声明=[] |

---

Step 5 completed.
No BUY / NO TRADE decision generated.
No HVT / IGE / F120 / Execution logic introduced.
No legacy theme configuration used.