# Step 5 — Theme-to-Stock Candidate 回测（信息增量验证）

- 数据日期：20260918
- 配置版本：1.6（回测结果不用于反向调参，需求 §三十二）
- 前向期：T+3, T+5, T+10, T+20 交易日
- 复权口径：adj_factor_back
- 对照：Theme Candidate Pool vs Theme All Members vs Random Members
- 重点不是追求最高收益，而是检验「主题机会 → 主题内候选股票」是否带来信息增量。

## 一、分层统计

| 分组 | 对照 | 前向期 | 样本 | 均值 | 中位数 | 胜率 | Top10% | Bottom10% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| THEME_CORE | candidate_pool | T+3 | 1639 | -0.0044 | -0.0076 | 0.408 | 0.1023 | -0.0908 |
| THEME_CORE | candidate_pool | T+5 | 1639 | -0.0030 | -0.0087 | 0.420 | 0.1370 | -0.1098 |
| THEME_CORE | candidate_pool | T+10 | 1594 | -0.0001 | -0.0032 | 0.482 | 0.1943 | -0.1689 |
| THEME_CORE | candidate_pool | T+20 | 1362 | 0.0021 | 0.0073 | 0.532 | 0.2219 | -0.2440 |
| THEME_DIFFUSION | candidate_pool | T+3 | 403 | 0.0055 | 0.0040 | 0.538 | 0.1005 | -0.0844 |
| THEME_DIFFUSION | candidate_pool | T+5 | 403 | 0.0094 | 0.0068 | 0.541 | 0.1317 | -0.0963 |
| THEME_DIFFUSION | candidate_pool | T+10 | 379 | 0.0245 | 0.0244 | 0.649 | 0.1824 | -0.1247 |
| THEME_DIFFUSION | candidate_pool | T+20 | 320 | 0.0303 | 0.0410 | 0.644 | 0.2410 | -0.2094 |
| THEME_SECOND_LINE | candidate_pool | T+3 | 177 | -0.0105 | -0.0141 | 0.401 | 0.1182 | -0.1106 |
| THEME_SECOND_LINE | candidate_pool | T+5 | 177 | -0.0054 | -0.0152 | 0.395 | 0.1442 | -0.1221 |
| THEME_SECOND_LINE | candidate_pool | T+10 | 177 | -0.0116 | -0.0191 | 0.390 | 0.1937 | -0.1970 |
| THEME_SECOND_LINE | candidate_pool | T+20 | 137 | -0.0040 | -0.0117 | 0.445 | 0.3016 | -0.2758 |
| THEME_PULLBACK | candidate_pool | T+3 | 665 | 0.0007 | -0.0014 | 0.475 | 0.1351 | -0.1173 |
| THEME_PULLBACK | candidate_pool | T+5 | 665 | -0.0025 | -0.0053 | 0.460 | 0.1843 | -0.1634 |
| THEME_PULLBACK | candidate_pool | T+10 | 639 | -0.0304 | -0.0274 | 0.412 | 0.2587 | -0.3130 |
| THEME_PULLBACK | candidate_pool | T+20 | 572 | -0.0399 | -0.0245 | 0.446 | 0.2794 | -0.3835 |
| ALL_THEME_MEMBERS | theme_all_members | T+3 | 164993 | -0.0048 | -0.0061 | 0.437 | 0.1151 | -0.1098 |
| ALL_THEME_MEMBERS | theme_all_members | T+5 | 164769 | -0.0079 | -0.0091 | 0.425 | 0.1489 | -0.1481 |
| ALL_THEME_MEMBERS | theme_all_members | T+10 | 161196 | -0.0247 | -0.0214 | 0.395 | 0.1925 | -0.2461 |
| ALL_THEME_MEMBERS | theme_all_members | T+20 | 139920 | -0.0104 | 0.0035 | 0.510 | 0.2680 | -0.3124 |
| RANDOM_MEMBERS | random_members | T+3 | 480 | 0.0013 | -0.0017 | 0.473 | 0.1153 | -0.0910 |
| RANDOM_MEMBERS | random_members | T+5 | 479 | -0.0038 | -0.0099 | 0.418 | 0.1443 | -0.1227 |
| RANDOM_MEMBERS | random_members | T+10 | 479 | 0.0002 | -0.0031 | 0.489 | 0.2295 | -0.1965 |
| RANDOM_MEMBERS | random_members | T+20 | 389 | -0.0045 | 0.0083 | 0.530 | 0.2343 | -0.2887 |

## 二、候选池相对主题等权的超额（信息增量）

| 类型 | 前向期 | 候选池均值 | 主题全部成员均值 | 超额 |
| --- | --- | --- | --- | --- |
| THEME_CORE | T+3 | -0.0044 | -0.0048 | +0.0003 |
| THEME_DIFFUSION | T+3 | 0.0055 | -0.0048 | +0.0103 |
| THEME_SECOND_LINE | T+3 | -0.0105 | -0.0048 | -0.0058 |
| THEME_PULLBACK | T+3 | 0.0007 | -0.0048 | +0.0055 |
| THEME_CORE | T+5 | -0.0030 | -0.0079 | +0.0049 |
| THEME_DIFFUSION | T+5 | 0.0094 | -0.0079 | +0.0173 |
| THEME_SECOND_LINE | T+5 | -0.0054 | -0.0079 | +0.0025 |
| THEME_PULLBACK | T+5 | -0.0025 | -0.0079 | +0.0055 |
| THEME_CORE | T+10 | -0.0001 | -0.0247 | +0.0246 |
| THEME_DIFFUSION | T+10 | 0.0245 | -0.0247 | +0.0492 |
| THEME_SECOND_LINE | T+10 | -0.0116 | -0.0247 | +0.0131 |
| THEME_PULLBACK | T+10 | -0.0304 | -0.0247 | -0.0057 |
| THEME_CORE | T+20 | 0.0021 | -0.0104 | +0.0125 |
| THEME_DIFFUSION | T+20 | 0.0303 | -0.0104 | +0.0408 |
| THEME_SECOND_LINE | T+20 | -0.0040 | -0.0104 | +0.0064 |
| THEME_PULLBACK | T+20 | -0.0399 | -0.0104 | -0.0295 |

- 正向超额组数：13 / 16
- 结论口径：仅当候选池在多数前向期优于主题等权时，才说明主题→个股映射带来了信息增量；否则应视为映射无效并复核配置（但不得为提高 T+5 胜率反复调参）。
