# -*- coding: utf-8 -*-
"""
二波行情每日扫描 → 微信推送脚本
运行：盘后 16:30-17:00

用法：
  py D:\mystock\solo\multi_factor_picker\wave2_daily_notify.py

定时任务建议：
  Windows任务计划: 工作日 16:30 执行 run_wave2_notify.bat
"""
import os, sys, time, json, datetime, io, sqlite3
sys.path.insert(0, r'D:\mystock')

# 编码处理
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

# ═══════════════════════════════════════════════
# 微信推送
# ═══════════════════════════════════════════════
def send_wechat(title, content, media_path=None):
    """通过 OpenClaw message 工具推送微信"""
    try:
        # 构建消息
        msg = f"【{title}】\n\n{content}"
        # 实际通过 subprocess 调用 openclaw CLI
        import subprocess
        # 使用 PowerShell 调用
        script = f'''
        $msg = @"
{msg}
"@
        $json = @{{
            channel = "openclaw-weixin"
            action = "send"
            message = $msg
        }} | ConvertTo-Json -Compress
        Write-Output "JSON: $json"
        '''
        # 直接写文件让外部处理
        payload_file = os.path.join(OUT_DIR, 'wechat_payload.json')
        with open(payload_file, 'w', encoding='utf-8') as f:
            json.dump({'msg': msg, 'title': title, 'media': media_path}, f, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"WeChat push failed: {e}")
        return False

# ═══════════════════════════════════════════════
# 股票名称查询
# ═══════════════════════════════════════════════
def get_stock_names(codes):
    """从本地缓存获取股票名称"""
    name_map = {}
    cache_dir = r'D:\mystock\cache_daily'
    if os.path.exists(cache_dir):
        for fname in os.listdir(cache_dir):
            if 'daily' in fname.lower() or 'block' in fname.lower():
                try:
                    fpath = os.path.join(cache_dir, fname)
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        for item in data.get('stocks', data.get('concepts', [])):
                            if 'code' in item:
                                name_map[item['code']] = item.get('name', item['code'])
                    break
                except:
                    pass
    # fallback: Tushare
    if not name_map and codes:
        try:
            import tushare as ts
            ts.set_token(os.environ['TUSHARE_TOKEN'])
            pro = ts.pro_api()
            codes_str = ','.join(codes[:50])
            df = pro.stock_basic(ts_code=codes_str, fields='ts_code,name')
            for _, row in df.iterrows():
                name_map[row['ts_code']] = row['name']
        except:
            pass
    return name_map

# ═══════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════
import pandas as pd
import numpy as np
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
import wave2_daily as wd

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'

def main():
    today = datetime.date.today()
    trade_date = wd.get_latest_trade_date()
    trade_date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

    print(f"=" * 60)
    print(f"二波行情每日扫描 {trade_date_str} 周{['一','二','三','四','五','六','日'][today.weekday()]}")
    print(f"=" * 60)

    # 获取股票池（近期强势股优先）
    stocks = wd.get_stock_pool()
    if not stocks:
        print("股票池为空，退出")
        return

    print(f"扫描股票池: {len(stocks)} 只\n")

    # 扫描
    results = []
    total = len(stocks)
    for i, code in enumerate(stocks):
        if (i+1) % 25 == 0 or i == 0:
            print(f"  进度: {i+1}/{total} ({code[:8]})...")
        r = wd.scan_stock(code, lookback=90)
        if r:
            results.append(r)
        time.sleep(0.12)

    print(f"\n{'='*60}")
    print(f"扫描完成: {total} 只中发现 {len(results)} 个二波信号")

    if not results:
        msg = f"📊 二波行情扫描 {trade_date_str}\n\n今日扫描 {total} 只，无二波信号\n\n可能原因：\n• 市场整体偏弱\n• 无股票满足"一波拉升+回调>5%"条件\n• RSI普遍偏高或偏低"
        print(msg)
        save_empty_scan(trade_date_str, total)
        return results

    # 按 base_score 排序
    results.sort(key=lambda x: (x['base_score'], x['rr_ratio']), reverse=True)

    # 获取名称
    codes = [r['ts_code'] for r in results]
    names = get_stock_names(codes)

    # 保存
    csv_path = os.path.join(OUT_DIR, f'wave2_daily_{trade_date}.csv')
    df = pd.DataFrame(results)
    df['name'] = df['ts_code'].map(lambda x: names.get(x, x))
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')

    # ── 格式化微信消息 ──
    today_weekday = ['一','二','三','四','五','六','日'][today.weekday()]
    header = f"📈 二波行情扫描 {trade_date_str}（周{today_weekday}）\n扫描范围: {total} 只 | 信号: {len(results)} 个\n"

    # 按形态分组统计
    pattern_count = {}
    for r in results:
        pattern_count[r['pattern']] = pattern_count.get(r['pattern'], 0) + 1

    pattern_emoji = {
        '强势横盘': '📍', '放量回调': '📊', '深度回调': '📉',
        'V型急跌': '⚡', '缩量回调': '🔽', '三角收敛': '🔻', '其他': '❓'
    }

    body_lines = []
    for r in results[:8]:  # 只发TOP8
        name = names.get(r['ts_code'], r['ts_code'])
        code = r['ts_code'].replace('.SH', '').replace('.SZ', '')
        emoji = pattern_emoji.get(r['pattern'], '•')
        body_lines.append(
            f"{emoji}【{name}({code})】\n"
            f"   形态: {r['pattern']} | {r['combo']}\n"
            f"   一波+{r['wave1_gain']}% → 回调{r['pullback']}% | RSI={r['rsi_now']}\n"
            f"   入:{r['entry_price']} 止:{r['stop_price']} 目:{r['target_price']}\n"
            f"   盈亏比: {r['rr_ratio']}x | 信号分: {r['base_score']}\n"
        )

    # 底部统计
    stats = []
    for p, cnt in sorted(pattern_count.items(), key=lambda x: -x[1]):
        emoji = pattern_emoji.get(p, '•')
        stats.append(f"{emoji}{p}×{cnt}")

    footer = ("\n⚠️ 仅供参考，不构成投资建议\n"
              "🚫 投资有风险，决策需谨慎")

    msg = header + '\n' + '\n'.join(body_lines) + '\n' + '｜'.join(stats) + footer

    print(msg)
    print(f"\n结果已保存: {csv_path}")

    # ── 推送微信（通过外部脚本）──
    push_file = os.path.join(OUT_DIR, f'wave2_push_{trade_date}.txt')
    with open(push_file, 'w', encoding='utf-8') as f:
        f.write(msg)
    print(f"推送内容已保存: {push_file}")

    return results

def save_empty_scan(trade_date_str, total):
    """记录空结果"""
    log = os.path.join(OUT_DIR, 'wave2_empty_log.txt')
    with open(log, 'a', encoding='utf-8') as f:
        f.write(f"{trade_date_str}: 扫描{total}只，无信号\n")

if __name__ == '__main__':
    main()
