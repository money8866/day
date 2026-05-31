# 每日涨停跟踪与复盘系统 - 文件清单

## 📦 已创建的文件

### 1. 核心程序文件

#### `limit_track_review.py` - 主程序
**功能**：完整的每日涨停跟踪与复盘系统

**主要模块**：
- 数据采集模块：`get_limit_list_data()`, `get_stock_daily_data()`
- 筛选模块：`filter_first_board_stocks()`, `analyze_moderate_volume()`
- 分析模块：`calculate_wave2_probability()`, `analyze_with_deepseek()`
- 报告模块：`generate_review_report()`, `send_to_wechat()`

**使用方法**：
```bash
python limit_track_review.py 20260529  # 分析指定日期
python limit_track_review.py            # 分析今天的数据
```

#### `test_limit_track.py` - 测试脚本
**功能**：验证系统配置和数据获取是否正常

**使用方法**：
```bash
python test_limit_track.py
```

#### `quickstart_limit_track.py` - 快速入门
**功能**：展示系统功能和配置状态

**使用方法**：
```bash
python quickstart_limit_track.py
```

#### `run_limit_track.bat` - 批处理脚本
**功能**：Windows 环境下快速运行涨停跟踪

**使用方法**：
```bash
run_limit_track.bat 20260529
run_limit_track.bat  # 使用今天的日期
```

### 2. 配置文件

#### `LIMIT_TRACK_README.md` - 详细文档
**内容**：
- 功能概述
- 配置步骤
- 使用方法
- 系统架构
- 二波概率计算策略
- 投资策略建议
- 风险提示
- 故障排查

#### `LIMIT_TRACK_FILES.md` - 文件清单（本文档）
**内容**：所有创建文件的说明和使用指南

### 3. 数据存储

**目录结构**：
```
d:\mystock\solo\
├── cache_limit_track\           # 缓存目录
│   ├── limit_history.json      # 涨停历史记录
│   └── reviews\               # 复盘报告目录
│       └── review_YYYYMMDD.txt # 每日复盘报告
```

## 🚀 快速开始

### 第一步：配置 API

编辑 `d:\mystock\solo\.env` 文件：

```env
# DeepSeek API Key
DEEPSEEK_API_KEY=sk-your-deepseek-key

# Server酱 SCKEY
WECHAT_SCKEY=sctk-your-serverchan-key

# Tushare Token（必须）
TUSHARE_TOKEN=your-tushare-token
```

### 第二步：测试配置

```bash
python test_limit_track.py
```

### 第三步：运行每日跟踪

```bash
# 分析今天的数据
python limit_track_review.py

# 或者使用批处理文件
run_limit_track.bat
```

## 📊 核心功能详解

### 1. 涨停数据采集

**接口**：`pro.limit_list_ths(trade_date='YYYYMMDD', limit_type='涨停池')`

**筛选条件**：
- ✓ 10点半前涨停
- ✓ 涨停并封死
- ✓ 第一板（排除连板股）
- ✓ 日线温和放量（量比 1.5-5.0 倍）

### 2. 二波概率计算

**评分维度**（满分100分）：

| 指标 | 满分 | 计算方法 |
|------|------|----------|
| 回调幅度 | 25分 | 最佳回调 15%-30% 得满分 |
| 均线多头 | 20分 | MA5 > MA10 > MA20 得满分 |
| 量能稳定 | 15分 | 变异系数 < 0.5 得满分 |
| 突破前期高点 | 20分 | 突破近30日最高价得满分 |
| 近期涨停次数 | 15分 | 涨停次数越多分数越高 |
| 市场情绪 | 5分 | 涨停股数量 > 50 得满分 |

### 3. DeepSeek AI 分析

**分析维度**：
1. 基本面匹配度
2. 近期市场风格
3. 游资操盘特征
4. 二波启动信号
5. 风险提示

### 4. 投资策略

**策略一：强者恒强**（激进型）
- 筛选条件：二波概率 ≥ 60%
- 操作建议：回调时买入，止损 -5%

**策略二：低吸潜伏**（稳健型）
- 筛选条件：二波概率 40%-60%
- 操作建议：分批建仓，等待启动信号

**策略三：观望等待**（保守型）
- 筛选条件：二波概率 < 40%
- 操作建议：等待技术形态确认

## 📝 文件路径汇总

| 文件名 | 路径 | 说明 |
|--------|------|------|
| 主程序 | `d:\mystock\solo\limit_track_review.py` | 核心程序 |
| 测试脚本 | `d:\mystock\solo\test_limit_track.py` | 配置测试 |
| 快速入门 | `d:\mystock\solo\quickstart_limit_track.py` | 功能演示 |
| 批处理 | `d:\mystock\solo\run_limit_track.bat` | Windows快速运行 |
| 详细文档 | `d:\mystock\solo\LIMIT_TRACK_README.md` | 完整使用说明 |
| 文件清单 | `d:\mystock\solo\LIMIT_TRACK_FILES.md` | 本文档 |
| 环境配置 | `d:\mystock\solo\.env` | API密钥配置 |
| 历史记录 | `d:\mystock\solo\cache_limit_track\limit_history.json` | 涨停历史数据 |
| 复盘报告 | `d:\mystock\solo\cache_limit_track\reviews\` | 每日复盘报告 |

## ⚠️ 重要提醒

1. **必须配置 Tushare Token**：
   - 访问 https://tushare.pro/ 注册账号
   - 获取 API Token
   - 填入 .env 文件

2. **建议配置 DeepSeek Key**：
   - 用于 AI 分析功能
   - 可提升分析质量

3. **建议配置 Server酱**：
   - 用于微信推送
   - 方便实时接收复盘报告

4. **风险控制**：
   - 单只股票仓位不超过 10%
   - 止损设置在 -5%
   - 以上分析仅供参考

## 🎯 系统优势

1. **自动化**：全自动采集、筛选、分析、推送
2. **智能化**：6维度量化评分 + AI分析
3. **实时性**：每日收盘后自动生成复盘报告
4. **便捷性**：支持微信推送，随时随地查看
5. **可扩展**：模块化设计，易于二次开发

## 📞 故障排查

### 问题：Tushare Token 错误
```bash
# 检查配置
python test_limit_track.py

# 查看错误信息
错误：您的token不对，请确认。

# 解决：重新获取 Token 并更新 .env 文件
```

### 问题：微信推送失败
```bash
# 检查 Server酱 配置
# 确保微信已绑定 Server酱
# 查看 .env 文件中的 WECHAT_SCKEY
```

### 问题：获取数据失败
```bash
# 检查网络连接
# 确认 Tushare 服务正常
# 避免频繁调用（限流）
```

## 📈 升级建议

1. **增加数据源**：接入更多数据接口
2. **优化策略**：调整二波概率计算权重
3. **扩展功能**：添加技术指标、形态识别
4. **增强AI**：使用更强大的分析模型
5. **风控系统**：添加实时监控和预警

---

**版本**: 1.0  
**创建日期**: 2026-05-30  
**状态**: ✅ 已完成  
**使用前请先配置 API Token**
