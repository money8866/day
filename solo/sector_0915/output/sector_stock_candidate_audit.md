# Step 5 — Theme-to-Stock Candidate Layer 审计报告

1. **数据日期**：20260918（主题面板 20260512 → 20260918）
2. **股票 universe**：成员覆盖股票 5000 只；通过 universe 过滤 180188 行；上市状态 L 5565 只
3. **Theme 数量**：真源定义 50 个；面板出现 50 个
4. **Opportunity Theme 数量**：40（风险观察 22）
5. **Candidate 股票数量**：6082 行（去重 1048 只）
6. **WATCH 数量**：34117
7. **EXCLUDE 数量**：141686
8. **CORE / PRIMARY / SECONDARY 数量**：CORE=13895 / PRIMARY=30783 / SECONDARY=6905 / THEMATIC=130302
9. **THEME_DIFFUSION 数量**：2861
10. **THEME_PULLBACK 数量**：1104
11. **高拥挤股票数量**：1943（CANDIDATE/WATCH 中 161）
12. **高扩张股票数量**：价格 13851 / 成交量 6865（极端价 3489 / 极端量 1279）
13. **Pollution 数量**：29784；分布 {'DATA_INVALID': 14242, 'INDUSTRY_CONFLICT': 8343, 'OVER_EXTENSION': 3483, 'THEME_EXITING': 1998, 'VOLUME_SPIKE': 1034, 'CONCEPT_POLLUTION': 684}
14. **跨主题股票数量**：37199（同一交易日同一股票进入多个主题评估）
15. **缺失数据数量**：ret_5 缺失 490；基本面缺失（coverage<100%）26278；data_quality<阈值 14918
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
    - trend_broken 但入选：17809
    - industry_conflict 入选：133
    - 角色 UNKNOWN 入选：0
20. **Top Candidate 样本**：

| 主题 | 股票 | 角色 | 成员 | 类型 | 分数 | 拥挤 | 扩张 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T01 半导体 | 路维光电(688401.SH) | CORE_SUPPLIER | CORE | THEME_DIFFUSION | 89.64 | LOW | LOW | CANDIDATE |
| T03 通信 | 天孚通信(300394.SZ) | CORE_EQUIPMENT | PRIMARY | THEME_CORE | 88.75 | LOW | LOW | CANDIDATE |
| T01 半导体 | 源杰科技(688498.SH) | CORE_APPLICATION | PRIMARY | THEME_CORE | 88.35 | LOW | LOW | CANDIDATE |
| T03 通信 | 新易盛(300502.SZ) | CORE_EQUIPMENT | PRIMARY | THEME_CORE | 87.87 | LOW | LOW | CANDIDATE |
| T03 通信 | 仕佳光子(688313.SH) | CORE_EQUIPMENT | PRIMARY | THEME_CORE | 87.23 | LOW | MEDIUM | CANDIDATE |
| T39 医疗服务 | 百奥赛图(688796.SH) | CORE_APPLICATION | PRIMARY | THEME_DIFFUSION | 86.61 | LOW | LOW | CANDIDATE |
| T15 汽车及零部件 | 浙江荣泰(603119.SH) | CORE_COMPONENT | PRIMARY | THEME_CORE | 86.23 | LOW | LOW | CANDIDATE |
| T42 生物制品 | 康华生物(300841.SZ) | CORE_SUPPLIER | CORE | THEME_DIFFUSION | 86.22 | LOW | LOW | CANDIDATE |


---

Step 5 completed.
No BUY / NO TRADE decision generated.
No HVT / IGE / F120 / Execution logic introduced.
No legacy theme configuration used.