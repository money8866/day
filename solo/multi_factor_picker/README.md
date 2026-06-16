# 多因子量化选股系统

基于 Tushare Pro 的 A 股全市场量化选股工具，围绕四个核心因子筛选优质标的。

## 核心因子

| 因子 | 说明 |
|------|------|
| 技术壁垒 | ROE > 15%，连续 3 年 ROE 为正，毛利率 > 30%，研发费用率 > 5% |
| 供需缺口 | 行业增速 > 30%，产能利用率提升，产品涨价信号 |
| 业绩兑现 | 季度净利润环比 > 50% 或同比 > 100% 或业绩预告扭亏/预盈 |
| 机构认可 | 北向资金持股连续 5 日上升或单日净买入 > 1 亿元 |

## 安装

```bash
pip install -r requirements.txt
```

## 配置

1. 设置环境变量 `TUSHARE_TOKEN`：
   ```bash
   export TUSHARE_TOKEN="your_token_here"  # Linux/Mac
   set TUSHARE_TOKEN=your_token_here        # Windows
   ```

2. 编辑 `config.yaml` 调整因子阈值和权重

## 运行

```bash
python main.py
```

## 输出

- 控制台表格显示筛选结果
- CSV 文件保存至 `output/selected_stocks_YYYYMMDD_HHMMSS.csv`

## 项目结构

```
multi_factor_picker/
├── config.yaml         # 配置文件
├── requirements.txt    # 依赖
├── main.py            # 主程序
├── data_fetcher.py     # 数据获取模块
├── factor_checker.py   # 因子检查模块
├── scorer.py          # 评分模块
└── output/            # 结果输出目录
```
