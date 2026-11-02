# 冗余代码清理报告

**清理范围**：`solo/market_analysis.py`、`solo/position_engine_v95.py`、`solo/position_engine_v9.py`、`solo/quant_trading_system.py`
**备份**：`solo/_bak_pre_cleanup_20261031/`（4 个文件，可一键回滚）
**方法**：AST 扫描（`_bt_deadcode_scan.py`）+ 跨文件引用 grep + 端到端回归 + A/B 回放对比

---

## 一、清理成果

| 文件 | 清理前 | 清理后 | 减少 |
|---|---|---|---|
| market_analysis.py | 2660 | 2609 | **-51** |
| position_engine_v95.py | 1275 | 1250 | **-25** |
| position_engine_v9.py | 2819 | 2782 | **-37** |
| quant_trading_system.py | 2220 | 2218 | -2（仅同步调用） |
| **合计** | | | **-115 行** |

删除后复扫结果：**【A】无死函数 ·【B】无未使用 import ·【C】无未使用局部变量**

---

## 二、删除明细

### market_analysis.py
| 类型 | 内容 | 依据 |
|---|---|---|
| 死函数 | `suggest_position()` | 40 行游资仓位逻辑，全项目零调用（审计时已确认） |
| 死函数 | `_writeback_v99_position()` | P0 改造后调用点已改为 `_writeback_v99_result`，此兼容包装失去作用 |
| 未用 import | `import numpy as np` | 全文件无 `np.` |
| 未用 import | `EngineV9Result` / `RegimeResult` / `REGIME_DEFINITIONS` / `_RR` | `run_v9_position_engine_v9` 里 import 了但没用 |
| 冗余参数 | `_apply_position_filters(..., index_results)` | 函数体内从未使用 |
| 冗余参数 | `calculate_market_trend_score(..., trade_date)` | 函数体内从未使用 |
| 冗余参数 | `run_v9_position_engine_v9(..., index_results)` | 函数体内从未使用 |
| 未用局部 | `_apply_position_filters` 的 `original_position` | 赋值后从未读取 |
| 未用循环变量 | `get_market_status_and_position` × 2、`_position_to_tier_idx` × 1 | 解包出但未用的 `status` / `pos_range` / `th` / `st` / `pr` 等，改为 `_` 前缀 |

### position_engine_v95.py
| 类型 | 内容 | 依据 |
|---|---|---|
| 死函数 | `_apply_hysteresis()` | 全项目零调用（V9.9 用的是 `analyze()` 内联的 prev 锚定逻辑） |
| 死函数 | `run_v99()` / `run_v99_report()` | 全项目零引用 |
| 死别名 | `PositionEngineV98/V97/V96` + `PositionV98/97/96Result` | 6 个兼容别名，跨文件零引用 |
| 未用 import | `Tuple` / `MarketDataBundle` / `LayerScore` / `build_test_bundle` / `_clip` | 见下方"踩坑"一节，`MarketDataBundle` 属误删后已修 |
| 冗余参数 | `_one_sentence(trend, money)` | 两个参数从未在函数体使用 |
| 冗余参数 | `_final_grade(target)` | 该参数从未在函数体使用 |

### position_engine_v9.py
| 类型 | 内容 | 依据 |
|---|---|---|
| 死函数 | `run_position_engine_v9()` / `generate_v9_report()` | 全项目零引用 |
| 死函数 | `_safe_div()` / `_percentile_rank()` | 其它文件里的同名函数是各自独立实现，非引用此处 |
| 未用 import | `os` / `sys` / `Any` / `datetime` / `timedelta` | 均只在 import 行出现 |
| 冗余 import | `build_test_bundle` 内的 `from dataclasses import dataclass as _dc` | 模块级已导入 `dataclass` |
| 未用局部 | `calc_l3_momentum` 的 `dt_count` | 赋值后从未读取 |

---

## 三、保留项（扫描器误报 / 有实际用途）

| 项 | 保留原因 |
|---|---|
| `safe_expanduser()` | 赋给 `os.path.expanduser` 的 **monkey patch**（防止 tushare 往用户根目录写缓存）。扫描器看不到这种赋值 |
| `self.generate_report()` / `self.analyze()` / `self._build_*()` | 通过 `self.`（Attribute）调用，扫描器只认 Name 调用 |
| `build_test_bundle()` | 公共测试工具，保留调试入口（零调用但有明确用途） |
| `calc_sentiment_score(total_amount)` | `quant_trading_system.py` 用 4 个位置参数调用 |
| `get_market_status_and_position(sentiment_score, breadth_up_ratio)` | `quant_trading_system.py` 用关键字 `sentiment_score=` 调用 |
| `classify_market_regime(breadth_up_ratio)` | 公开 API，`quant_trading_system.py` 关键字调用 |

---

## 四、踩坑记录（重要）

### 坑 1：跨文件 re-export 被误判成死代码
删除 v95 的 `MarketDataBundle` import 后，`market_analysis.py` 里这行直接 ImportError：

```python
from position_engine_v95 import PositionEngineV99, MarketDataBundle as V99Bundle
```

而它所在的 `try/except Exception` 把异常吞了，只打印一句「[V9.9] 运行异常」，结果：
**脚本不报错、但 V9.9 整段结果不生成**。端到端跑完才发现。

**修复**：
1. `market_analysis.py` 改为直接从源模块导入 —— `from position_engine_v9 import MarketDataBundle as V99Bundle`（它本来就是 v9 的类，不该由 v95 中转）
2. V9.9 段的 `except` 补上 `traceback.print_exc()`，以后这类问题会直接暴露而不是静默

**教训**：删 import 前必须 grep「谁 `from 这个模块 import 了它`」，而不只是看本文件用不用。

### 坑 2：改公开函数签名漏了外部调用方
`quant_trading_system.py` 也调用了 `calculate_market_trend_score` 与 `_apply_position_filters`，改签名时漏了它 → TypeError。

**修复**：已同步 `quant_trading_system.py` 的 2 处调用，并重建了它的清理前备份。

**教训**：改公开函数签名必须全项目 grep 调用方，不能只改同文件内的。

### 坑 3：正则删参数误伤类型标注
用正则 `,\s*NAME(?=\s*[,):])` 删参数时，把 `index_results: list,` 的类型标注留下了，产生 `overview: dict: list,` 这样的非法语法，且因写文件在校验之前，**把文件写坏了**。

**修复**：立即从备份恢复，改用 AST 重建签名（先按原始顺序算好每个参数的默认值与标注，再过滤），并把 `ast.parse()` 校验挪到写文件之前。

---

## 五、验证

| 项目 | 结果 |
|---|---|
| 语法 | 4 个文件全部通过 |
| 死代码复扫 | 【A】无死函数 /【B】无未使用 import /【C】无未使用局部变量 |
| 端到端 | 综合结构分 41.7、当日热度 21.0、市场状态「普跌日（广度否决阈值 30%）」、择时维度 位置 0% / 波动 67%、V9.9 仓位 10%、一致性检查 7 项全 PASS、DB 回写 `engine_version=V9.9` |
| **A/B 回放对比** | 与清理前**完全一致**：新引擎累计 +1.48%、Sharpe 2.30、回撤 -0.58%、胜率 25.5%，仓位变化 33/111 天 → **清理零行为变更** |
| 外部调用方实测 | `quant_trading_system.py` 全链路可跑；`etf_quant_theme.py` / `quant_day.py` 的 `get_market_status_and_position(55)` 单参调用正常；`_entry_tracker.py` / `_eval_entry_timing.py` 的 `_detect_key_low_breakdown` 导入正常 |

---

## 六、回滚

```bash
cd D:/mystock/solo
cp _bak_pre_cleanup_20261031/market_analysis.py .
cp _bak_pre_cleanup_20261031/position_engine_v95.py .
cp _bak_pre_cleanup_20261031/position_engine_v9.py .
cp _bak_pre_cleanup_20261031/quant_trading_system.py .
```

## 七、新增工具

`solo/_bt_deadcode_scan.py` —— AST 版死代码扫描器，可复跑：

```bash
python _bt_deadcode_scan.py market_analysis.py position_engine_v95.py position_engine_v9.py
```

输出三段：【A】本文件内无调用的函数/类、【B】未使用 import、【C】函数内未使用局部变量。

注意：它对 `self.` 方法调用、`os.path.expanduser` 这类 monkey patch、跨文件 re-export 会误报，**删之前必须做跨文件引用确认**。
