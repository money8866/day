# Step 5 — Theme-to-Stock Candidate 回测（信息增量验证）

- 数据日期：20260916
- 配置版本：1.5（回测结果不用于反向调参，需求 §三十二）
- 前向期：T+3, T+5, T+10, T+20 交易日
- 复权口径：adj_factor_back
- 对照：Theme Candidate Pool vs Theme All Members vs Random Members
- 重点不是追求最高收益，而是检验「主题机会 → 主题内候选股票」是否带来信息增量。

## 一、分层统计

| 分组 | 对照 | 前向期 | 样本 | 均值 | 中位数 | 胜率 | Top10% | Bottom10% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| THEME_CORE | candidate_pool | T+3 | 1651 | -0.0037 | -0.0071 | 0.412 | 0.1051 | -0.0913 |
| THEME_CORE | candidate_pool | T+5 | 1646 | -0.0031 | -0.0089 | 0.419 | 0.1383 | -0.1121 |
| THEME_CORE | candidate_pool | T+10 | 1585 | -0.0012 | -0.0032 | 0.484 | 0.1966 | -0.1777 |
| THEME_CORE | candidate_pool | T+20 | 1355 | 0.0027 | 0.0071 | 0.529 | 0.2314 | -0.2473 |
| THEME_DIFFUSION | candidate_pool | T+3 | 432 | 0.0047 | 0.0018 | 0.514 | 0.1075 | -0.0857 |
| THEME_DIFFUSION | candidate_pool | T+5 | 432 | 0.0084 | 0.0047 | 0.523 | 0.1379 | -0.0964 |
| THEME_DIFFUSION | candidate_pool | T+10 | 400 | 0.0236 | 0.0233 | 0.642 | 0.1852 | -0.1282 |
| THEME_DIFFUSION | candidate_pool | T+20 | 323 | 0.0322 | 0.0431 | 0.653 | 0.2488 | -0.2197 |
| THEME_SECOND_LINE | candidate_pool | T+3 | 150 | -0.0162 | -0.0170 | 0.380 | 0.0980 | -0.1125 |
| THEME_SECOND_LINE | candidate_pool | T+5 | 150 | -0.0102 | -0.0167 | 0.353 | 0.1416 | -0.1287 |
| THEME_SECOND_LINE | candidate_pool | T+10 | 148 | -0.0124 | -0.0220 | 0.392 | 0.1863 | -0.1884 |
| THEME_SECOND_LINE | candidate_pool | T+20 | 116 | -0.0005 | -0.0115 | 0.457 | 0.2614 | -0.2404 |
| THEME_PULLBACK | candidate_pool | T+3 | 342 | -0.0011 | -0.0019 | 0.465 | 0.1418 | -0.1298 |
| THEME_PULLBACK | candidate_pool | T+5 | 342 | 0.0040 | -0.0015 | 0.482 | 0.2274 | -0.1707 |
| THEME_PULLBACK | candidate_pool | T+10 | 342 | -0.0261 | -0.0098 | 0.471 | 0.3278 | -0.3404 |
| THEME_PULLBACK | candidate_pool | T+20 | 322 | -0.0434 | -0.0007 | 0.497 | 0.2487 | -0.3954 |
| ALL_THEME_MEMBERS | theme_all_members | T+3 | 164742 | -0.0048 | -0.0061 | 0.437 | 0.1151 | -0.1098 |
| ALL_THEME_MEMBERS | theme_all_members | T+5 | 164518 | -0.0080 | -0.0091 | 0.425 | 0.1489 | -0.1481 |
| ALL_THEME_MEMBERS | theme_all_members | T+10 | 157804 | -0.0249 | -0.0211 | 0.398 | 0.1935 | -0.2477 |
| ALL_THEME_MEMBERS | theme_all_members | T+20 | 138020 | -0.0103 | 0.0042 | 0.512 | 0.2691 | -0.3137 |
| RANDOM_MEMBERS | random_members | T+3 | 500 | -0.0050 | -0.0097 | 0.410 | 0.1269 | -0.1049 |
| RANDOM_MEMBERS | random_members | T+5 | 499 | -0.0015 | -0.0051 | 0.439 | 0.1523 | -0.1245 |
| RANDOM_MEMBERS | random_members | T+10 | 480 | -0.0199 | -0.0121 | 0.427 | 0.1651 | -0.2285 |
| RANDOM_MEMBERS | random_members | T+20 | 429 | -0.0085 | 0.0051 | 0.515 | 0.2710 | -0.3188 |

## 二、候选池相对主题等权的超额（信息增量）

| 类型 | 前向期 | 候选池均值 | 主题全部成员均值 | 超额 |
| --- | --- | --- | --- | --- |
| THEME_CORE | T+3 | -0.0037 | -0.0048 | +0.0010 |
| THEME_DIFFUSION | T+3 | 0.0047 | -0.0048 | +0.0095 |
| THEME_SECOND_LINE | T+3 | -0.0162 | -0.0048 | -0.0114 |
| THEME_PULLBACK | T+3 | -0.0011 | -0.0048 | +0.0036 |
| THEME_CORE | T+5 | -0.0031 | -0.0080 | +0.0048 |
| THEME_DIFFUSION | T+5 | 0.0084 | -0.0080 | +0.0163 |
| THEME_SECOND_LINE | T+5 | -0.0102 | -0.0080 | -0.0023 |
| THEME_PULLBACK | T+5 | 0.0040 | -0.0080 | +0.0120 |
| THEME_CORE | T+10 | -0.0012 | -0.0249 | +0.0237 |
| THEME_DIFFUSION | T+10 | 0.0236 | -0.0249 | +0.0484 |
| THEME_SECOND_LINE | T+10 | -0.0124 | -0.0249 | +0.0125 |
| THEME_PULLBACK | T+10 | -0.0261 | -0.0249 | -0.0012 |
| THEME_CORE | T+20 | 0.0027 | -0.0103 | +0.0130 |
| THEME_DIFFUSION | T+20 | 0.0322 | -0.0103 | +0.0425 |
| THEME_SECOND_LINE | T+20 | -0.0005 | -0.0103 | +0.0097 |
| THEME_PULLBACK | T+20 | -0.0434 | -0.0103 | -0.0331 |

- 正向超额组数：12 / 16
- 结论口径：仅当候选池在多数前向期优于主题等权时，才说明主题→个股映射带来了信息增量；否则应视为映射无效并复核配置（但不得为提高 T+5 胜率反复调参）。
