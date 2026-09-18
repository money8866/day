# Step 5 — Theme-to-Stock Candidate Layer 审计报告

1. **数据日期**：20260917（主题面板 20260512 → 20260917）
2. **股票 universe**：成员覆盖股票 4995 只；通过 universe 过滤 171956 行；上市状态 L 5565 只
3. **Theme 数量**：真源定义 50 个；面板出现 50 个
4. **Opportunity Theme 数量**：40（风险观察 22）
5. **Candidate 股票数量**：5729 行（去重 1011 只）
6. **WATCH 数量**：32723
7. **EXCLUDE 数量**：134895
8. **CORE / PRIMARY / SECONDARY 数量**：CORE=13506 / PRIMARY=29358 / SECONDARY=6795 / THEMATIC=123688
9. **THEME_DIFFUSION 数量**：2492
10. **THEME_PULLBACK 数量**：1053
11. **高拥挤股票数量**：1894（CANDIDATE/WATCH 中 159）
12. **高扩张股票数量**：价格 13289 / 成交量 6401（极端价 3368 / 极端量 1172）
13. **Pollution 数量**：28508；分布 {'DATA_INVALID': 13915, 'INDUSTRY_CONFLICT': 7684, 'OVER_EXTENSION': 3362, 'THEME_EXITING': 1994, 'VOLUME_SPIKE': 940, 'CONCEPT_POLLUTION': 613}
14. **跨主题股票数量**：34815（同一交易日同一股票进入多个主题评估）
15. **缺失数据数量**：ret_5 缺失 283；基本面缺失（coverage<100%）25902；data_quality<阈值 14563
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
    - trend_broken 但入选：17467
    - industry_conflict 入选：123
    - 角色 UNKNOWN 入选：0
20. **Top Candidate 样本**：

| 主题 | 股票 | 角色 | 成员 | 类型 | 分数 | 拥挤 | 扩张 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T37 医疗器械 | 汉邦科技(688755.SH) | CORE_SUPPLIER | CORE | THEME_DIFFUSION | 86.60 | LOW | LOW | CANDIDATE |
| T01 半导体 | 源杰科技(688498.SH) | CORE_APPLICATION | PRIMARY | THEME_CORE | 84.95 | LOW | LOW | CANDIDATE |
| T03 通信 | 天孚通信(300394.SZ) | CORE_EQUIPMENT | PRIMARY | THEME_CORE | 83.43 | LOW | LOW | CANDIDATE |
| T05 消费电子 | 福立旺(688678.SH) | CORE_APPLICATION | PRIMARY | THEME_CORE | 82.73 | LOW | LOW | WATCH |
| T37 医疗器械 | 华大智造(688114.SH) | CORE_SUPPLIER | CORE | THEME_DIFFUSION | 81.45 | LOW | HIGH | CANDIDATE |
| T37 医疗器械 | 可孚医疗(301087.SZ) | CORE_SUPPLIER | CORE | THEME_CORE | 81.27 | LOW | LOW | CANDIDATE |
| T37 医疗器械 | 理邦仪器(300206.SZ) | CORE_SUPPLIER | CORE | THEME_DIFFUSION | 81.23 | LOW | LOW | CANDIDATE |
| T01 半导体 | 路维光电(688401.SH) | CORE_SUPPLIER | CORE | THEME_CORE | 80.90 | LOW | LOW | CANDIDATE |


---

Step 5 completed.
No BUY / NO TRADE decision generated.
No HVT / IGE / F120 / Execution logic introduced.
No legacy theme configuration used.