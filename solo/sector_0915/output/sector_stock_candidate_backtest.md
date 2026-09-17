# Step 5 — Theme-to-Stock Candidate 回测（信息增量验证）

- 数据日期：20260916
- 配置版本：1.6（回测结果不用于反向调参，需求 §三十二）
- 前向期：T+3, T+5, T+10, T+20 交易日
- 复权口径：adj_factor_back
- 对照：Theme Candidate Pool vs Theme All Members vs Random Members
- 重点不是追求最高收益，而是检验「主题机会 → 主题内候选股票」是否带来信息增量。

## 一、分层统计

| 分组 | 对照 | 前向期 | 样本 | 均值 | 中位数 | 胜率 | Top10% | Bottom10% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| THEME_CORE | candidate_pool | T+3 | 1639 | -0.0044 | -0.0076 | 0.408 | 0.1023 | -0.0908 |
| THEME_CORE | candidate_pool | T+5 | 1639 | -0.0031 | -0.0089 | 0.420 | 0.1370 | -0.1098 |
| THEME_CORE | candidate_pool | T+10 | 1584 | -0.0002 | -0.0031 | 0.482 | 0.1941 | -0.1694 |
| THEME_CORE | candidate_pool | T+20 | 1352 | 0.0024 | 0.0076 | 0.534 | 0.2225 | -0.2447 |
| THEME_DIFFUSION | candidate_pool | T+3 | 403 | 0.0052 | 0.0037 | 0.536 | 0.1005 | -0.0844 |
| THEME_DIFFUSION | candidate_pool | T+5 | 403 | 0.0091 | 0.0066 | 0.538 | 0.1317 | -0.0964 |
| THEME_DIFFUSION | candidate_pool | T+10 | 375 | 0.0244 | 0.0244 | 0.651 | 0.1824 | -0.1270 |
| THEME_DIFFUSION | candidate_pool | T+20 | 312 | 0.0321 | 0.0421 | 0.663 | 0.2439 | -0.2107 |
| THEME_SECOND_LINE | candidate_pool | T+3 | 177 | -0.0105 | -0.0141 | 0.401 | 0.1182 | -0.1106 |
| THEME_SECOND_LINE | candidate_pool | T+5 | 177 | -0.0054 | -0.0152 | 0.395 | 0.1442 | -0.1221 |
| THEME_SECOND_LINE | candidate_pool | T+10 | 177 | -0.0116 | -0.0191 | 0.390 | 0.1937 | -0.1970 |
| THEME_SECOND_LINE | candidate_pool | T+20 | 137 | -0.0040 | -0.0117 | 0.445 | 0.3016 | -0.2758 |
| THEME_PULLBACK | candidate_pool | T+3 | 665 | 0.0007 | -0.0014 | 0.475 | 0.1351 | -0.1173 |
| THEME_PULLBACK | candidate_pool | T+5 | 665 | -0.0025 | -0.0053 | 0.457 | 0.1843 | -0.1634 |
| THEME_PULLBACK | candidate_pool | T+10 | 637 | -0.0305 | -0.0285 | 0.411 | 0.2587 | -0.3130 |
| THEME_PULLBACK | candidate_pool | T+20 | 568 | -0.0398 | -0.0238 | 0.449 | 0.2794 | -0.3835 |
| ALL_THEME_MEMBERS | theme_all_members | T+3 | 164854 | -0.0048 | -0.0062 | 0.436 | 0.1150 | -0.1098 |
| ALL_THEME_MEMBERS | theme_all_members | T+5 | 164630 | -0.0080 | -0.0091 | 0.425 | 0.1488 | -0.1481 |
| ALL_THEME_MEMBERS | theme_all_members | T+10 | 159683 | -0.0248 | -0.0214 | 0.396 | 0.1928 | -0.2468 |
| ALL_THEME_MEMBERS | theme_all_members | T+20 | 138807 | -0.0104 | 0.0039 | 0.511 | 0.2687 | -0.3131 |
| RANDOM_MEMBERS | random_members | T+3 | 500 | -0.0015 | -0.0055 | 0.436 | 0.1327 | -0.1033 |
| RANDOM_MEMBERS | random_members | T+5 | 499 | 0.0025 | 0.0000 | 0.481 | 0.1599 | -0.1257 |
| RANDOM_MEMBERS | random_members | T+10 | 480 | -0.0175 | -0.0094 | 0.452 | 0.1750 | -0.2277 |
| RANDOM_MEMBERS | random_members | T+20 | 449 | -0.0062 | 0.0054 | 0.526 | 0.2698 | -0.3136 |

## 二、候选池相对主题等权的超额（信息增量）

| 类型 | 前向期 | 候选池均值 | 主题全部成员均值 | 超额 |
| --- | --- | --- | --- | --- |
| THEME_CORE | T+3 | -0.0044 | -0.0048 | +0.0003 |
| THEME_DIFFUSION | T+3 | 0.0052 | -0.0048 | +0.0100 |
| THEME_SECOND_LINE | T+3 | -0.0105 | -0.0048 | -0.0057 |
| THEME_PULLBACK | T+3 | 0.0007 | -0.0048 | +0.0055 |
| THEME_CORE | T+5 | -0.0031 | -0.0080 | +0.0049 |
| THEME_DIFFUSION | T+5 | 0.0091 | -0.0080 | +0.0171 |
| THEME_SECOND_LINE | T+5 | -0.0054 | -0.0080 | +0.0025 |
| THEME_PULLBACK | T+5 | -0.0025 | -0.0080 | +0.0055 |
| THEME_CORE | T+10 | -0.0002 | -0.0248 | +0.0246 |
| THEME_DIFFUSION | T+10 | 0.0244 | -0.0248 | +0.0493 |
| THEME_SECOND_LINE | T+10 | -0.0116 | -0.0248 | +0.0132 |
| THEME_PULLBACK | T+10 | -0.0305 | -0.0248 | -0.0057 |
| THEME_CORE | T+20 | 0.0024 | -0.0104 | +0.0128 |
| THEME_DIFFUSION | T+20 | 0.0321 | -0.0104 | +0.0425 |
| THEME_SECOND_LINE | T+20 | -0.0040 | -0.0104 | +0.0064 |
| THEME_PULLBACK | T+20 | -0.0398 | -0.0104 | -0.0294 |

- 正向超额组数：13 / 16
- 结论口径：仅当候选池在多数前向期优于主题等权时，才说明主题→个股映射带来了信息增量；否则应视为映射无效并复核配置（但不得为提高 T+5 胜率反复调参）。
