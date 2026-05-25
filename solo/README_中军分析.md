# 主线板块 + 中军分析系统

## 概述

本系统结合了主线板块分析和中军识别功能，帮助投资者在市场中找到强势板块及其中的稳健龙头（中军）。

## 文件说明

### 核心程序
1. **block.py** - 主线板块分析模块（已有）
2. **main_backbone_analysis.py** - 中军分析独立模块
3. **main_with_backbone.py** - 整合版：主线板块 + 中军分析

### 环境配置
4. **requirements.txt** - Python依赖包列表
5. **setup_env.bat** - Windows环境配置脚本（批处理）
6. **setup_env.ps1** - Windows环境配置脚本（PowerShell）
7. **verify_env.py** - 环境验证脚本

### 快速启动
8. **run_中军分析.bat** - 一键运行分析脚本

## 快速开始

### 第一步：配置环境

双击运行 `setup_env.bat`，或在命令行中执行：

```bash
cd c:\Users\kongx\mystock\solo
setup_env.bat
```

### 第二步：验证环境

```bash
python verify_env.py
```

### 第三步：运行分析

双击运行 `run_中军分析.bat`，或在命令行中执行：

```bash
python main_with_backbone.py
```

## 环境配置详解

### 方式一：自动配置（推荐）

**Windows用户：**
```cmd
setup_env.bat
```

**PowerShell用户：**
```powershell
.\setup_env.ps1
```

### 方式二：手动配置

1. 确保安装 Python 3.8+
2. 安装依赖包：
```bash
pip install -r requirements.txt
```
3. 确认 `TUSHARE.env` 配置文件存在且包含有效的 Token

## 中军识别策略

### 中军特征

| 特征 | 具体表现 | 评分权重 |
|------|---------|---------|
| 市值区间 | 100-500亿 | 30分 |
| 量比 | 突破日量比>2 | 20分 |
| 换手率 | 5%-10% | 15分 |
| 均线系统 | MA5>MA20>MA60 或 MA20刚上穿MA60 | 20分 |
| 平台突破 | 长期震荡后放量突破 | 15分 |
| 当日涨幅 | 5%-8%为佳 | 10分 |

## 使用方法

### 方式一：运行整合版（推荐）

```bash
cd c:\Users\kongx\mystock\solo
python main_with_backbone.py
```

程序会自动：
1. 调用 block.py 获取主线板块
2. 对前5个主线板块进行中军分析
3. 输出综合结果并保存为CSV文件

### 方式二：分步运行

#### 第一步：运行主线板块分析
```bash
cd c:\Users\kongx\mystock
python block.py
```

#### 第二步：运行中军分析
```bash
cd c:\Users\kongx\mystock\solo
python main_backbone_analysis.py
```

## 输出结果

结果保存在 `c:\Users\kongx\mystock\cache_backbone\` 目录下：
- `main_backbone_analysis_YYYYMMDD.csv` - 整合版完整结果
- `backbones_YYYYMMDD.csv` - 独立版分析结果

## 参数配置

可以在代码中调整以下参数：

```python
MIN_MARKET_CAP = 100  # 最小市值（亿）
MAX_MARKET_CAP = 500  # 最大市值（亿）
MIN_VOLUME_RATIO = 2.0  # 最小量比
MIN_TURNOVER = 5.0  # 最小换手率
MAX_TURNOVER = 10.0  # 最大换手率
BACKBONE_SCORE_THRESHOLD = 50  # 中军评分阈值
TOP_SECTOR_COUNT = 5  # 分析前N个主线板块
```

## 注意事项

1. 首次运行需要生成缓存数据，可能需要较长时间
2. Tushare API 有频率限制，程序已添加延时处理
3. 请确保 TUSHARE.env 配置文件中有有效的 API Token
