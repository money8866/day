"""
B浪策略信号推送脚本
==================
每天盘后运行策略扫描，推送新信号到微信。

用法:
  python bwave_notify.py              # 运行扫描并推送新信号
  python bwave_notify.py --force    # 强制推送所有信号（不比较昨天）
"""

import os, sys, argparse, subprocess
from datetime import datetime, timedelta
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
NOTIFY_SCRIPT = r'D:\mystock\solo\bwave_strategy.py'  # 原策略脚本


def run_scan(min_score: int = 65) -> str | None:
    """运行策略扫描，返回生成的CSV路径"""
    cmd = [
        'python', NOTIFY_SCRIPT,
        '--pool', 'qualified',
        '--min-score', str(min_score),
    ]
    
    print(f'运行策略扫描...')
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    
    # 从输出中找到CSV路径
    for line in result.stdout.split('\n'):
        if 'CSV:' in line:
            csv_path = line.split('CSV:')[1].strip()
            if os.path.exists(csv_path):
                print(f'扫描完成: {csv_path}')
                return csv_path
    
    print('扫描失败：未找到CSV文件')
    return None


def find_yesterday_csv(today_csv: str) -> str | None:
    """找昨天的CSV文件"""
    today = datetime.now().strftime('%Y%m%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
    
    # 尝试找昨天的CSV
    for fname in os.listdir(OUTPUT_DIR):
        if fname.startswith('bwave_') and fname.endswith('.csv'):
            if yesterday in fname:
                return os.path.join(OUTPUT_DIR, fname)
    
    return None


def find_new_signals(today_csv: str, yesterday_csv: str | None) -> pd.DataFrame:
    """找出新信号"""
    df_today = pd.read_csv(today_csv, encoding='utf-8-sig')
    
    if yesterday_csv is None or not os.path.exists(yesterday_csv):
        print('无昨天的CSV，返回所有信号')
        return df_today
    
    df_yesterday = pd.read_csv(yesterday_csv, encoding='utf-8-sig')
    
    # 找出今天有但昨天没有的信号
    merged = df_today.merge(
        df_yesterday,
        on=['ts_code', 'signal_type'],
        how='left',
        indicator=True
    )
    new_signals = merged[merged['_merge'] == 'left_only'].drop(columns=['_merge'])
    
    print(f'今天信号: {len(df_today)}个，昨天信号: {len(df_yesterday)}个，新信号: {len(new_signals)}个')
    return new_signals


def push_to_wechat(df: pd.DataFrame):
    """推送信号到微信"""
    if len(df) == 0:
        print('无新信号，不推送')
        return
    
    # 构造推送消息
    msg_lines = ['📊 B浪策略新信号', f'共 {len(df)} 个', '']
    
    for _, row in df.iterrows():
        ts_code = row['ts_code']
        signal_type = row['signal_type']
        bwave_score = row.get('bwave_score', row.get('total', 0))
        
        msg_lines.append(f'{ts_code} ({signal_type})')
        msg_lines.append(f'  评分: {bwave_score}分')
        msg_lines.append(f'  A涨: {row.get("a_gain", 0):.1f}%  B跌: {row.get("b_drop", 0):.1f}%')
        msg_lines.append('')
    
    msg = '\n'.join(msg_lines)
    
    # 推送到微信（使用message工具）
    try:
        import subprocess
        # 调用openclaw message工具
        cmd = ['openclaw', 'message', 'send', '--channel', 'openclaw-weixin', '--message', msg]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode == 0:
            print(f'推送成功: {len(df)}个信号')
        else:
            print(f'推送失败: {result.stderr}')
    except Exception as e:
        print(f'推送异常: {e}')


def main():
    parser = argparse.ArgumentParser(description='B浪策略信号推送')
    parser.add_argument('--force', action='store_true', help='强制推送所有信号')
    parser.add_argument('--min-score', type=int, default=65)
    args = parser.parse_args()
    
    # 运行扫描
    today_csv = run_scan(args.min_score)
    if today_csv is None:
        return
    
    # 找出新信号
    if args.force:
        new_signals = pd.read_csv(today_csv, encoding='utf-8-sig')
    else:
        yesterday_csv = find_yesterday_csv(today_csv)
        new_signals = find_new_signals(today_csv, yesterday_csv)
    
    # 推送
    push_to_wechat(new_signals)


if __name__ == '__main__':
    main()
