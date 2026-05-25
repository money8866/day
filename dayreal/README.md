# 股票盘中预警系统

使用 pytdx 库实现的股票实时预警系统，支持监控概念板块和个股，当价格接近5日均线或板块异动时发出预警。

## 功能特性

- 使用 pytdx 免费获取实时行情，稳定无限制
- 支持概念板块整体监控
- 自动识别板块龙头
- 检测板块日内异动
- 监控价格接近5日均线的机会
- 个股和板块可分别配置监控频率
- 可配置预警阈值
- 支持日志记录预警信息

## 板块异动检测条件

当满足以下任意 2 个条件时，系统会发出板块异动预警：
- 板块平均涨幅 ≥ 3%
- 板块内涨停家数 ≥ 3 家
- 板块内上涨股票比例 ≥ 70%
- 板块内涨幅 ≥ 5% 的股票 ≥ 5 家

## 安装依赖

```bash
pip install -r requirements.txt
```

## 配置说明

### 1. 概念板块配置

编辑 [concepts.csv](file:///workspace/concepts.csv) 文件，添加需要监控的概念板块：

```csv
concept
AI服务器
人工智能
芯片
华为概念
```

### 2. 个股配置

编辑 [stocks.csv](file:///workspace/stocks.csv) 文件，添加需要监控的个股：

```csv
code,name
600519,贵州茅台
000001,平安银行
000858,五粮液
```

### 3. 系统配置

编辑 [config.yaml](file:///workspace/config.yaml) 文件：

```yaml
files:
  concepts_csv: concepts.csv  # 概念板块文件
  stocks_csv: stocks.csv      # 个股文件

warning:
  ma5_threshold: 0.02       # 5日均线偏离度阈值（2%）
  stock_check_interval: 10   # 个股检查间隔（秒）
  concept_check_interval: 60 # 板块检查间隔（秒）

notification:
  enabled: true   # 是否启用通知
  method: log     # 通知方式：print 或 log
```

## 使用方法

运行主程序：

```bash
python main.py
```

程序会自动：
1. 连接通达信服务器
2. 获取概念板块股票列表
3. 计算5日均线
4. 实时监控价格
5. 当价格接近5日均线时发出个股预警
6. 当板块满足异动条件时发出板块预警，包含龙头和前5股票信息

## 预警示例

### 个股预警
```
【个股预警】
股票: 贵州茅台 (600519)
当前价格: 1800.50
5日均线: 1780.00
偏离度: 1.15%
时间: 2026-05-24 10:30:00
```

### 板块异动预警
```
【板块异动】
概念: AI服务器
信号: 板块平均涨幅 4.50%, 涨停家数 5, 5%+ 家数 8
板块平均: 4.50%
涨停: 5家 | 上涨: 85%
龙头: 某某股份 (000001) 10.00%
前5:
  1. 某某股份 (000001) 10.00%
  2. 某某科技 (000002) 9.50%
  3. 某某信息 (000003) 8.80%
  4. 某某数据 (000004) 7.60%
  5. 某某智能 (000005) 6.90%
时间: 2026-05-24 10:30:00
```

## 项目结构

- [main.py](file:///workspace/main.py) - 主程序入口
- [config.py](file:///workspace/config.py) - 配置管理
- [tdx_data.py](file:///workspace/tdx_data.py) - 通达信数据获取
- [technical_analysis.py](file:///workspace/technical_analysis.py) - 技术指标和板块分析
- [warning_system.py](file:///workspace/warning_system.py) - 预警系统
- [config.yaml](file:///workspace/config.yaml) - 配置文件
- [requirements.txt](file:///workspace/requirements.txt) - 依赖列表
