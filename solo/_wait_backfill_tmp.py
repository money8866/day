# -*- coding: utf-8 -*-
"""等待外部 backfill_daily_basic.py 完成（只读监控，不做任何写操作）"""
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

DB = r'D:\mystock\cache_daily\stock_data.db'
MAX_MINUTES = 480          # 最长等待 8 小时
PROBE_SEC = 60
DONE_FLAG_FILE = None      # 无副作用


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


def procs_running():
    try:
        out = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=20, creationflags=0x08000000
        ).stdout
        # 无法从 tasklist 看命令行，改用 wmic
        out = subprocess.run(
            ['wmic', 'process', 'where', "name='python.exe'", 'get', 'ProcessId,CommandLine',
             '/FORMAT:LIST'],
            capture_output=True, text=True, timeout=25, creationflags=0x08000000
        ).stdout
        return 'backfill_daily_basic' in out
    except Exception:
        return True  # 查询失败时保守认为仍在运行


def db_state():
    c = sqlite3.connect(DB, timeout=30)
    try:
        db = c.execute('SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_basic_cache').fetchone()
        af = c.execute('SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM adj_factor_cache').fetchone()
        meta = dict(c.execute("SELECT key,value FROM cache_meta WHERE key IN ('daily_basic_batch_date','adj_factor_batch_date')").fetchall())
    finally:
        c.close()
    return db, af, meta


t0 = time.time()
last_line = ''
while time.time() - t0 < MAX_MINUTES * 60:
    time.sleep(PROBE_SEC)
    running = procs_running()
    db, af, meta = db_state()
    line = (f"running={running} daily_basic=({db[0]:,},{db[1]}~{db[2]}) "
            f"adj_factor=({af[0]:,},{af[1]}~{af[2]}) meta={meta}")
    if line != last_line:
        log(line)
        last_line = line
    if not running:
        log('backfill_daily_basic.py 进程已退出，监控结束')
        break
else:
    log('超过最长等待时间，监控结束（进程仍在运行）')
log(f'daily_basic_cache: 共 {db[0]:,} 行，{db[1]}~{db[2]}')
log(f'adj_factor_cache : 共 {af[0]:,} 行，{af[1]}~{af[2]}')
log('DONE_WAIT')
