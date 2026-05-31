# 每日涨停跟踪与复盘系统 - 配置与使用说明

## 📋 功能概述

这是一个完整的每日涨停跟踪与复盘系统，主要功能包括：

1. **涨停数据采集** - 使用 Tushare 的 `limit_list_ths()` 接口获取涨停池数据
2. **智能筛选** - 筛选10点半前涨停、封死、第一板、温和放量的股票
3. **历史记录** - 自动记录每日涨停股票历史
4. **数据缓存** - 智能缓存接口数据，避免重复请求
5. **复盘分析** - 对前20天涨停过的股票进行全面复盘
6. **DeepSeek分析** - 匹配基本面和近期市场风格
7. **二波概率计算** - 基于游资操盘量化策略计算二波概率
8. **微信推送** - 自动生成报告并通过 Server酱 推送到微信

## ⚙️ 配置步骤

### 1. 配置 API Token

请编辑 `d:\mystock\config\.env` 文件，填入真实的 API Key：

```env
# DeepSeek API Key (用于AI分析)
DEEPSEEK_API_KEY=sk-your-actual-deepseek-api-key

# Server酱 SCKEY (用于微信推送)
WECHAT_SCKEY=sctk-your-actual-serverchan-key

# Tushare Token (必须)
TUSHARE_TOKEN=your-actual-tushare-token
```

### 2. 获取 Tushare Token

1. 访问 [Tushare官网](https://tushare.pro/)
2. 注册并登录账号
3. 进入个人中心 → API Token
4. 复制您的 Token 并填入 .env 文件

### 3. 获取 DeepSeek API Key

1. 访问 [DeepSeek官网](https://platform.deepseek.com/)
2. 注册并登录
3. 创建 API Key
4. 填入 .env 文件

### 4. 获取 Server酱 Key

1. 访问 [Server酱官网](https://sct.ftqq.com/)
2. 登录并绑定微信
3. 复制您的 SCKEY 并填入 .env 文件

## 🚀 使用方法

### 运行每日涨停跟踪

```bash
# 分析指定日期
python limit_track_review.py 20260529

# 分析今天的数据（自动获取当前日期）
python limit_track_review.py

# 强制刷新缓存（不使用缓存数据）
python limit_track_review.py 20260529 --force
```

### 缓存管理

系统会自动缓存接口数据以提高效率：

```bash
# 清理所有缓存文件
python limit_track_review.py --clear-cache
```

缓存文件位置：
- 涨停数据缓存：`d:\mystock\solo\cache_limit_track\limit_data\`
- 日线数据缓存：`d:\mystock\solo\cache_limit_track\daily_data\`

### 查看历史记录

涨停历史记录保存在：
```
d:\mystock\solo\cache_limit_track\limit_history.json
```

### 查看复盘报告

每日复盘报告保存在：
```
d:\mystock\solo\cache_limit_track\reviews\review_YYYYMMDD.txt
```

### 使用批处理文件（Windows）

```batch
# 分析指定日期
run_limit_track.bat 20260529

# 分析今天的数据
run_limit_track.bat
```

## 📊 系统架构

### 核心模块

1. **数据采集模块**
   - `get_limit_list_data()` - 获取涨停池数据（带缓存）
   - `filter_first_board_stocks()` - 筛选第一板股票
   - `analyze_moderate_volume()` - 分析温和放量

2. **缓存模块**
   - `cache_data()` - 通用数据缓存函数
   - 支持 pickle 和 csv 格式
   - 智能判断是否需要刷新

3. **复盘分析模块**
   - `calculate_wave2_probability()` - 计算二波概率
   - `analyze_with_deepseek()` - DeepSeek AI分析
   - `generate_review_report()` - 生成复盘报告

4. **推送模块**
   - `send_to_wechat()` - 微信推送

### 二波概率计算策略

系统采用游资操盘量化策略，从6个维度评估二波概率：

| 指标 | 满分 | 评估标准 |
|------|------|----------|
| 回调幅度 | 25分 | 最佳回调幅度 15%-30% |
| 均线多头 | 20分 | MA5 > MA10 > MA20 |
| 量能稳定 | 15分 | 量能变异系数 < 0.5 |
| 突破前期高点 | 20分 | 突破近30日最高价 |
| 近期涨停次数 | 15分 | 累计涨停次数越多越好 |
| 市场情绪 | 5分 | 市场整体氛围好时加分 |

**二波概率 = 各项得分之和（满分100）**

## 🎯 投资策略建议

### 策略一：强者恒强（追涨）
- **筛选条件**：二波概率 ≥ 60%
- **操作建议**：在回调时买入，止损设置在-5%
- **风险等级**：高

### 策略二：低吸潜伏（抄底）
- **筛选条件**：二波概率 40%-60%
- **操作建议**：分批建仓，等待二波启动信号
- **风险等级**：中

### 策略三：观望等待
- **筛选条件**：二波概率 < 40%
- **操作建议**：等待技术形态确认后再入场
- **风险等级**：低

## ⚠️ 风险提示

1. 以上分析仅供参考，不构成投资建议
2. 游资炒作风险较大，请严格控制仓位
3. 建议止损设置在买入价的-5%以内
4. 注意市场整体情绪变化
5. 单只股票仓位建议不超过总资金的10%

## 📝 注意事项

1. **数据延迟**：涨停数据可能存在一定延迟，建议在收盘后运行
2. **API限制**：Tushare免费接口有访问频率限制，避免短时间内大量请求（系统已缓存，无需担心）
3. **数据完整性**：系统依赖历史数据的完整性，首次运行可能数据不足
4. **网络要求**：需要稳定的网络连接以访问 Tushare 和 DeepSeek API
5. **缓存清理**：定期清理缓存可以节省磁盘空间，但会增加API请求

## 🔧 故障排查

### 问题1：Tushare Token 错误

```
错误：您的token不对，请确认。
解决：检查 d:\mystock\config\.env 文件中的 TUSHARE_TOKEN 是否正确
```

### 问题2：获取涨停数据失败

```
可能原因：
1. Tushare Token 无效或过期
2. 网络连接不稳定
3. Tushare 服务端维护

解决：检查网络连接，重新获取 Token，或使用缓存数据
```

### 问题3：微信推送失败

```
可能原因：
1. Server酱 KEY 配置错误
2. 微信未绑定 Server酱

解决：检查 d:\mystock\config\.env 文件中的 WECHAT_SCKEY
```

### 问题4：缓存异常

```
解决：清理缓存重新运行
python limit_track_review.py --clear-cache
```

## 📈 升级建议

1. **增加数据源**：接入更多数据接口
2. **优化策略**：调整二波概率计算权重
3. **扩展功能**：添加技术指标、形态识别
4. **增强AI**：使用更强大的分析模型
5. **风控系统**：添加实时监控和预警

---

**版本**：2.0（新增缓存功能）
**更新日期**：2026-05-30
**配置文件路径**：d:\mystock\config\.env
