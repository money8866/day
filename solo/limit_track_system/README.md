# 涨停跟踪系统 - 完整使用指南 v2.3

## 📅 版本信息
**版本**：v2.3  
**整理日期**：2026-05-30  
**系统位置**：`d:\mystock\solo\limit_track_system\`

---

## 📁 目录结构

```
d:\mystock\solo\limit_track_system\
│
├── 📄 limit_track_review.py        # 主程序（核心）
├── 📄 full_backtrack.py           # 完整历史回溯脚本
├── 📄 verify_db.py                # 数据库验证脚本
├── 📄 demo_backtrack.py           # 功能演示脚本
│
├── 📂 cache\                      # 缓存目录
│   ├── 📂 limit_data\            # 涨停数据缓存
│   ├── 📂 daily_data\            # 日线数据缓存
│   ├── 📂 reviews\               # 复盘报告
│   ├── 📄 limit_history.json     # 涨停历史记录
│   └── 📄 limit_history.db       # SQLite数据库
│
├── 📂 文档\
│   ├── 📄 LIMIT_TRACK_README.md              # 主使用说明
│   ├── 📄 LIMIT_TRACK_UPDATE.md              # 更新说明
│   ├── 📄 LIMIT_TRACK_FILES.md               # 文件清单
│   ├── 📄 LIMIT_TRACK_QUICKREF.txt          # 快速参考
│   ├── 📄 LIMIT_TRACK_TRADING_DAY_GUIDE.md  # 交易日判断指南
│   ├── 📄 LIMIT_TRACK_HISTORY_BACKTRACK_GUIDE.md    # 历史回溯指南
│   ├── 📄 LIMIT_TRACK_HISTORY_QUICKREF.txt  # 历史回溯快速参考
│   ├── 📄 UPDATE_LOG_V2.2.md                # v2.2更新日志
│   ├── 📄 更新说明_v2.3.md                  # v2.3更新说明
│   └── 📄 目录整理说明.md                   # 目录整理说明
│
└── 📄 README.md                    # 本文档
```

---

## 🎯 功能概述

### 核心功能
1. **涨停数据采集** - 使用 Tushare 接口获取涨停池数据
2. **智能筛选** - 筛选10点半前涨停、封死、第一板、温和放量
3. **历史复盘** - 对前20天涨停过的股票进行全面复盘
4. **特征识别** - 识别"高位震荡缩量"洗盘特征
5. **二波概率计算** - 基于游资量化策略计算二波概率
6. **AI分析** - DeepSeek基本面和风格匹配
7. **微信推送** - 自动生成报告并推送到微信

### 优化特性（v2.3）
- ✅ 不分析当天涨停的股票，专注历史涨停股
- ✅ 新增"高位震荡缩量"核心特征筛选
- ✅ 强化AI分析，增加板块热点轮动判断
- ✅ SQLite数据库集成，支持快速查询
- ✅ 相对路径配置，支持自由移动

---

## 🚀 快速开始

### 1. 进入系统目录
```bash
cd d:\mystock\solo\limit_track_system
```

### 2. 运行每日分析
```bash
# 分析今天（自动判断交易日）
python limit_track_review.py

# 分析指定日期
python limit_track_review.py 20260529

# 强制刷新
python limit_track_review.py 20260529 --force
```

### 3. 回溯历史数据
```bash
# 回溯过去20天（首次使用推荐）
python full_backtrack.py

# 回溯指定天数
python full_backtrack.py  # 默认20天
```

### 4. 查询历史数据
```bash
# 查询所有
python limit_track_review.py --query

# 按概率筛选
python limit_track_review.py --query --min-prob 60

# 导出CSV
python limit_track_review.py --query --export data.csv
```

---

## 📊 常用命令

### 缓存管理
```bash
# 清理缓存
python limit_track_review.py --clear-cache

# 查看帮助
python limit_track_review.py --help
```

### 数据验证
```bash
# 验证数据库
python verify_db.py

# 运行演示
python demo_backtrack.py
```

### 参数说明
| 参数 | 说明 | 示例 |
|------|------|------|
| `trade_date` | 交易日期（YYYYMMDD） | `20260529` |
| `--force` | 强制刷新缓存 | `--force` |
| `--clear-cache` | 清理所有缓存 | `--clear-cache` |
| `--backtrack` | 回溯历史数据 | `--backtrack --days 30` |
| `--query` | 查询历史数据 | `--query --min-prob 60` |
| `--export` | 导出CSV文件 | `--export data.csv` |

---

## 📈 投资策略

### 策略一：高位震荡缩量潜伏（推荐）
- **选股标准**：价格相对位置 0.85~1.15，量比 < 0.8
- **操作建议**：耐心观察，等待放量突破信号
- **仓位控制**：建议小仓位分批建仓
- **止损设置**：-5% 左右

### 策略二：高概率精选
- **选股标准**：二波概率 ≥ 60%
- **操作建议**：可以适当关注
- **注意事项**：仍需结合其他指标

### 策略三：中期观察
- **选股标准**：二波概率 40%~60%
- **操作建议**：等待更好的买入时机

---

## ⚠️ 重要提示

1. **高位震荡缩量可能是洗盘，也可能是出货**
   - 洗盘特征：缩量但价格在关键支撑位
   - 出货特征：缩量但价格跌破重要均线

2. **板块热点轮动很重要**
   - 即使个股符合特征，但板块不在热点也难有表现
   - 关注市场主线，避免参与冷门板块

3. **严格控制仓位**
   - 游资炒作风险较大
   - 建议单只股票仓位 < 10%

4. **历史数据仅作参考**
   - 市场在变化，不能完全依赖历史模式
   - 结合实时盘面和消息面判断

---

## 📂 文件说明

### 核心程序
| 文件 | 功能 | 说明 |
|------|------|------|
| `limit_track_review.py` | 主程序 | 包含所有核心功能 |
| `full_backtrack.py` | 回溯脚本 | 回溯历史涨停数据 |
| `verify_db.py` | 验证脚本 | 验证SQLite数据库 |
| `demo_backtrack.py` | 演示脚本 | 演示系统功能 |

### 缓存文件
| 文件/目录 | 功能 | 格式 |
|-----------|------|------|
| `cache/limit_data/` | 涨停数据缓存 | .pkl |
| `cache/daily_data/` | 日线数据缓存 | .pkl |
| `cache/reviews/` | 复盘报告 | .txt |
| `cache/limit_history.json` | 历史记录 | JSON |
| `cache/limit_history.db` | 数据库 | SQLite |

### 文档文件
| 文件 | 内容 |
|------|------|
| `README.md` | 本文档 |
| `LIMIT_TRACK_README.md` | 详细使用说明 |
| `LIMIT_TRACK_UPDATE.md` | 功能更新说明 |
| `LIMIT_TRACK_QUICKREF.txt` | 快速参考 |
| `LIMIT_TRACK_TRADING_DAY_GUIDE.md` | 交易日判断指南 |
| `LIMIT_TRACK_HISTORY_BACKTRACK_GUIDE.md` | 历史回溯指南 |
| `更新说明_v2.3.md` | v2.3详细更新说明 |
| `目录整理说明.md` | 目录整理说明 |

---

## 🔧 配置要求

### 环境要求
- Python 3.7+
- pandas, numpy, tushare, requests
- SQLite3（Python内置）

### API配置
配置文件位置：`d:\mystock\config\.env`

必需配置：
```env
TUSHARE_TOKEN=your-tushare-token
```

可选配置：
```env
DEEPSEEK_API_KEY=your-deepseek-key
WECHAT_SCKEY=your-serverchan-key
```

---

## 📞 技术支持

### 常见问题
1. **Q: 缓存文件在哪？**  
   A: `cache/` 目录下

2. **Q: 如何清理缓存？**  
   A: `python limit_track_review.py --clear-cache`

3. **Q: 数据库在哪？**  
   A: `cache/limit_history.db`

4. **Q: 如何查看报告？**  
   A: `cache/reviews/` 目录下

### 诊断步骤
1. 检查配置文件：`d:\mystock\config\.env`
2. 验证数据库：`python verify_db.py`
3. 查看日志：检查终端输出
4. 清理缓存：`python limit_track_review.py --clear-cache`

---

## 🎉 系统状态

- ✅ 代码优化完成（v2.3）
- ✅ 目录整理完成
- ✅ 测试通过
- ✅ 数据库初始化成功
- ✅ 所有功能正常运行

---

**版本**：v2.3  
**维护日期**：2026-05-30  
**状态**：✅ 正常运行
