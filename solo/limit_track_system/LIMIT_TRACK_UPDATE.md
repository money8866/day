# 更新说明 - 涨停跟踪系统 v2.0

## 📢 更新内容摘要

### 1. 配置路径更新
- **变更前**：`d:\mystock\solo\.env`
- **变更后**：`d:\mystock\config\.env`
- **变更原因**：与项目其他模块保持一致的配置管理

### 2. 新增数据缓存功能（重要）

为了避免重复请求Tushare接口，节省API调用次数，系统新增智能缓存机制：

#### 涨停数据缓存
- **存储位置**：`d:\mystock\solo\cache_limit_track\limit_data\`
- **文件格式**：`.pkl`（Python序列化格式）
- **命名规则**：`limit_YYYYMMDD.pkl`
- **生效范围**：每次获取相同日期的涨停数据时，优先使用缓存

#### 日线数据缓存
- **存储位置**：`d:\mystock\solo\cache_limit_track\daily_data\`
- **文件格式**：`.pkl`
- **命名规则**：`daily_{ts_code}_{start_month}_{end_month}.pkl`
- **按月份缓存**：同一月的数据合并缓存

### 3. 新增命令行参数

#### 强制刷新缓存
```bash
python limit_track_review.py 20260529 --force
# 或者使用短参数
python limit_track_review.py 20260529 -f
```

#### 清理缓存
```bash
python limit_track_review.py --clear-cache
```

### 4. 缓存机制说明

**正常使用流程**：
1. 首次运行 → 从Tushare接口获取数据 → 保存到本地缓存
2. 再次运行相同日期 → 直接从缓存读取 → 跳过API请求
3. 如需更新 → 使用`--force`参数强制刷新

**优势**：
- ✅ 大幅减少API调用次数
- ✅ 提高程序运行速度
- ✅ 避免Tushare接口限流
- ✅ 离线环境下仍可分析历史数据

**注意**：
- ⚠️ 缓存文件占用磁盘空间，定期清理可节省空间
- ⚠️ `--clear-cache`会清空所有缓存，慎用
- ⚠️ `--force`强制刷新当前日期数据

### 5. 智能交易日判断（重要新增）

系统新增智能交易日判断功能，完美处理各种复杂场景：

#### 核心功能

**自动识别非交易日**
- 自动识别周六、周日
- 自动识别法定节假日（通过Tushare接口查询）
- 非交易日自动切换到上一个交易日

**时间智能判断**
- 交易日 16:00 前：自动使用上一个交易日（因为当日数据未更新）
- 交易日 16:00 后：使用当日数据
- 非交易日：自动切换到上一个交易日

#### 使用示例

**场景1：周末运行**
```bash
# 今天 2026-05-30（周六）
python limit_track_review.py
# 输出：⚠️ 20260530 为非交易日，已自动切换到上一个交易日: 20260529
```

**场景2：交易日上午运行**
```bash
# 2026-05-29（周五）上午10点运行
python limit_track_review.py
# 输出：⚠️ 当前时间 10:00 < 16:00，当日数据未更新，已自动切换到上一个交易日: 20260528
```

**场景3：交易日下午运行**
```bash
# 2026-05-29（周五）下午16:30运行
python limit_track_review.py
# 输出：使用当日数据 20260529
```

**场景4：指定非交易日**
```bash
# 指定 20260531（周日）
python limit_track_review.py 20260531
# 输出：⚠️ 20260531 为非交易日，已自动切换到上一个交易日: 20260529
```

**场景5：指定交易日（强制使用）**
```bash
# 强制使用今天的数据（即使16:00前）
python limit_track_review.py 20260529 --force
# 输出：使用指定日期数据 20260529
```

#### 技术实现

新增函数：
- `is_trading_day()` - 判断指定日期是否为交易日
- `get_previous_trading_day()` - 获取上一个交易日
- `get_smart_trade_date()` - 智能获取实际应使用的交易日

智能判断流程：
1. 检查是否为周末（周六周日自动跳过）
2. 查询Tushare节假日数据（确认是否为交易日）
3. 如果是当天且时间 < 16:00，使用上一个交易日
4. 如果是非交易日，使用上一个交易日
5. 否则使用原定日期

#### 优势

- ✅ 全自动智能处理，无需人工判断
- ✅ 避免获取到空数据或错误数据
- ✅ 符合实际交易情况
- ✅ 节省调试时间

#### 注意事项

- ⚠️ 系统默认依赖Tushare的节假日数据
- ⚠️ 如果Tushare接口查询失败，默认工作日视为交易日
- ⚠️ 16:00 的判断基于服务器本地时间
- ⚠️ 使用 `--force` 参数可以强制使用指定日期，绕过智能判断

## 📋 文件清单

### 核心程序
1. `limit_track_review.py` - 主程序（已更新）
2. `test_limit_track.py` - 测试脚本
3. `quickstart_limit_track.py` - 快速入门（已更新）
4. `run_limit_track.bat` - Windows批处理文件

### 文档文件
1. `LIMIT_TRACK_README.md` - 完整使用说明（已更新）
2. `LIMIT_TRACK_FILES.md` - 文件清单
3. `LIMIT_TRACK_QUICKREF.txt` - 快速参考
4. `UPDATE.md` - 本文档

## 🚀 快速开始

### 首次使用（按此操作）

1. **确认配置文件**：
   - 检查 `d:\mystock\config\.env` 是否存在
   - 确认Tushare Token已正确配置

2. **测试系统**：
   ```bash
   cd d:\mystock\solo
   python quickstart_limit_track.py
   ```

3. **运行分析**：
   ```bash
   # 分析指定日期
   python limit_track_review.py 20260529
   ```

### 日常使用

```bash
# 快速分析（使用缓存）
python limit_track_review.py 20260529

# 强制刷新数据
python limit_track_review.py 20260529 --force

# 清理缓存
python limit_track_review.py --clear-cache
```

## 📊 缓存目录结构

```
d:\mystock\solo\cache_limit_track\
├── limit_history.json          # 涨停历史记录
├── limit_data\                 # 涨停数据缓存
│   ├── limit_20260527.pkl
│   ├── limit_20260528.pkl
│   └── limit_20260529.pkl
├── daily_data\                 # 日线数据缓存
│   ├── daily_000001.SZ_202604_202605.pkl
│   └── ...
└── reviews\                    # 复盘报告
    ├── review_20260527.txt
    └── review_20260529.txt
```

## 🔧 故障排查

### 问题1：缓存文件损坏
```
解决：清理缓存重新运行
python limit_track_review.py --clear-cache
```

### 问题2：需要最新数据
```
解决：使用强制刷新参数
python limit_track_review.py 20260529 --force
```

### 问题3：配置路径找不到
```
确认：检查 d:\mystock\config\.env 是否存在
```

## 💡 最佳实践建议

1. **日常使用**：不使用`--force`，让系统自动判断是否需要刷新
2. **数据更新**：仅在数据确实有变更时使用`--force`
3. **缓存维护**：每月或每周清理一次缓存（可选）
4. **备份策略**：重要历史数据可手动备份`cache_limit_track`目录

## 📞 技术支持

如有问题，请检查：
1. 配置文件路径是否正确
2. API Token是否有效
3. 缓存目录是否可读写
4. 网络连接是否正常

---

**版本**：2.1
**更新日期**：2026-05-30
**主要更新**：v2.0 新增智能缓存功能、配置路径统一；v2.1 新增交易日智能判断功能
