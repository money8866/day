"""生成实盘应用建议报告，调用 AI 解读"""
import json, os, sys, time
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import deepseek, send_pushplus
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

eval_path = os.path.join(BASE_DIR, "output", "tdx_backtest_eval_20240101_20260714.json")
with open(eval_path, "r", encoding="utf-8") as f:
    eval_data = json.load(f)

prompt = f"""你是A股顶级机构量化投资经理，擅长将机器学习回测结果转化为可执行的实盘策略。

你刚刚完成了一个ETF Winner Prediction Engine的LightGBM Walk-Forward滚动回测，现在需要你基于回测数据，撰写一份面向实盘操盘手的应用指南。

## 回测环境

- 数据源: 通达信本地日线数据 (.day文件)
- 回测方法: Walk-Forward滚动训练，每20天重新训练
- 回测周期: 2024-04-03 ~ 2026-06-30
- 测试日期: 28个(每20天一个观测点)
- 总样本: 954条
- 模型: LightGBM Regressor (特征工程36个特征)
- 训练数据: 每期用前120天历史数据训练

## 回测核心结果

### 20天预测 (短期)
- 结论: **无效**。IC=0.003，分组无单调性，Q5实际收益3.3% vs Q1的0.3%，无区分度
- 解释: 20天太短，价格噪声主导，机器学习无法提取有效信号

### 40天预测 (中期) — 主力持仓周期
- IC: **0.5504** (强预测力)
- 分组单调性完美: Q1=-5.4% < Q2=-1.7% < Q3=1.0% < Q4=4.5% < Q5=21.5%
- 多空收益差: 26.9%
- Top3策略: 总收益415%, 胜率84.6%, 年化Sharpe 1.48, 最大回撤26.93%
- Q5胜率89.3%, Q1胜率仅24.3%

### 60天预测 (长期) — 最优持仓周期
- IC: **0.8154** (极强预测力)
- 分组单调性完美: Q1=-12.0% < Q2=-3.0% < Q3=1.6% < Q4=7.9% < Q5=37.6%
- 多空收益差: 49.6%
- Top3策略: 总收益986.48%, 胜率100%, 年化Sharpe 1.77, 最大回撤0.0%
- Q5胜率99.4%, Q1胜率仅9.4%

## 当前最新预测 (2026-07-14)

LightGBM模型对35只ETF的最新预测排名:

| 排名 | ETF | 主题 | 60D预期 | 40D预期 | 综合预期 | Top3概率 |
|------|-----|------|---------|---------|---------|---------|
| 1 | 518880.SH | 黄金 | 20.3% | 15.6% | 15.2% | 95% |
| 2 | 159869.SZ | 游戏 | 36.9% | 4.5% | 12.4% | 91% |
| 3 | 515210.SH | 钢铁 | 22.1% | 9.8% | 10.7% | 85% |
| 4 | 515790.SH | 光伏 | 26.7% | 3.8% | 10.3% | 84% |
| 5 | 512480.SH | 半导体 | 22.4% | -1.8% | 8.3% | 77% |
| 6 | 515180.SH | 红利 | 15.1% | 4.3% | 5.7% | 69% |
| 7 | 516160.SH | 新能源 | 14.0% | 2.2% | 3.1% | 60% |
| 8 | 159755.SZ | 电池 | 9.6% | 0.3% | 2.4% | 58% |
| 9 | 512660.SH | 军工 | 10.2% | -1.1% | 1.8% | 56% |
| 10 | 159611.SZ | 电力 | 9.5% | 0.6% | 1.4% | 54% |

## 分析要求

请基于以上数据，撰写一份**面向实盘操盘手**的应用指南，包含以下内容：

### 一、策略核心逻辑
- 为什么这个模型能预测？核心逻辑是什么？
- 为什么20天无效而60天极强？背后的市场行为解释
- 模型在"发现"什么？是趋势追随还是反转捕捉？

### 二、当前信号解读
- 当前(2026-07-14)模型给出的最强信号是什么？
- 黄金ETF(518880)排第一意味着什么？这是避险信号还是均值回归信号？
- 游戏(159869)60D预期36.9%但40D仅4.5%，这种"远期爆发"形态历史上准确吗？
- 钢铁、光伏排名靠前，是否意味着周期股在回暖？

### 三、实操策略建议
- 建议持仓周期: 40天还是60天？为什么？
- 仓位管理: 基于回测最大回撤26.93%，建议仓位比例？
- 买点: 是信号出现当天买入，还是等回调？信号出现后多久建仓？
- 卖点: 固定持有到期？还是达到目标收益止盈？还是跌破止损线？
- 止损: 基于Q1平均亏损12%，建议止损线多少？
- 信号失效判断: 什么情况下模型信号应该被忽略？

### 四、风险控制
- 模型在什么市场环境下可能失效？（暴涨暴跌/震荡/趋势/极端行情）
- 如何识别模型失效的前兆？
- 最大回撤26.93%意味着什么？实盘能承受吗？
- 建议的仓位和资金管理方案

### 五、执行清单
- 每日操作检查清单
- 信号加减仓规则
- 复盘频率和标准

**重要要求:**
1. 语言简洁专业，适合手机阅读，无段落缩进
2. 数据引用准确，每个结论要有回测数据支撑
3. 观点明确，给出可执行的具体数字（仓位、止损、止盈百分比）
4. 不要过度乐观，要指出模型的局限性和风险
5. 报告总长度控制在2000字以内
6. 不使用表格（手机阅读体验差），改用分节和列表
"""

print(f"Prompt length: {len(prompt)} chars")
print("Calling DeepSeek Pro...")

start = time.time()
report = deepseek(prompt, use_flash=False)
elapsed = time.time() - start
print(f"Response: {elapsed:.1f}s, {len(report)} chars")

report_path = os.path.join(BASE_DIR, "report", "AI_Trading_Guide_20260714.md")
with open(report_path, "w", encoding="utf-8") as f:
    f.write(report)

print(f"\nSaved: {report_path}")
print("\n" + "=" * 70)
print(report)
print("=" * 70)

pushplus_token = os.getenv("PUSHPLUS")
if pushplus_token:
    print("\nSending to WeChat...")
    msg = report.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    try:
        resp = requests.post("https://www.pushplus.plus/send", json={
            "token": pushplus_token,
            "title": "ETF Winner 实盘操作指南 - 2026-07-14",
            "content": msg,
            "template": "markdown"
        }, timeout=15)
        r = resp.json()
        if r.get("code") == 200:
            print("PushPlus sent successfully.")
        else:
            print(f"PushPlus failed: {r.get('msg')}")
    except Exception as e:
        print(f"PushPlus error: {e}")