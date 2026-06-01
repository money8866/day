"""
东方财富自选板块写入模块

将股票代码写入东财"热点跟踪"自选板块。
格式说明：
  - 深市(000/001/002/003/300/301/159) → 0.XXXXXX
  - 沪市(600/601/603/605/688/510/562/588) → 1.XXXXXX
"""
import sqlite3
import os
import subprocess
import time
from datetime import datetime

# 东方财富用户数据路径
EM_USER_DIR = r"C:\eastmoney\swc8\config\User\9971113309768870"
EM_DB = os.path.join(EM_USER_DIR, "self_stock.db")

BLOCK_KEY = "0_热点跟踪"


def ts_code_to_em_code(ts_code):
    """
    转换Tushare代码为东财自选股格式

    输入: '000791.SZ' 或 '600021.SH'
    输出: '0.000791' 或 '1.600021'
    """
    code = ts_code.strip()
    # 去掉 .SZ/.SH 后缀
    if "." in code:
        code = code.split(".")[0]

    num = int(code)
    # 沪市: 600xxx, 601xxx, 603xxx, 605xxx, 688xxx, 510xxx, 562xxx, 588xxx
    if (600000 <= num <= 609999) or (688000 <= num <= 689999) or (510000 <= num <= 569999):
        return f"1.{code}"
    # 深市: 000xxx, 001xxx, 002xxx, 003xxx, 300xxx, 301xxx, 159xxx
    else:
        return f"0.{code}"


def update_hot_track_block(ts_codes):
    """
    将股票列表写入"热点跟踪"自选板块。
    先清空已有股票，再写入新列表。

    参数:
        ts_codes: list of str 格式 ['000791.SZ', '600021.SH', ...]
    """
    if not ts_codes:
        print("  ⚠ 无股票列表，跳过写入")
        return False

    if not os.path.exists(EM_DB):
        print(f"  ⚠ 未找到东方财富自选股数据库: {EM_DB}")
        return False

    # 转换为东财格式
    em_codes = [ts_code_to_em_code(c) for c in ts_codes]
    # 去重
    seen = set()
    unique_em = []
    for c in em_codes:
        if c not in seen:
            seen.add(c)
            unique_em.append(c)

    stock_code_arr = ",".join(unique_em)

    # 生成价格占位
    now_str = datetime.now().strftime("%Y%m%d%H%M%S")
    price_parts = [f"{c}:{now_str}:0.000000" for c in unique_em]
    stock_price_arr = ",".join(price_parts)

    try:
        conn = sqlite3.connect(EM_DB)
        cursor = conn.cursor()

        # 读取当前group_version
        row = cursor.execute(
            "SELECT group_version FROM selfstock WHERE group_key=?",
            (BLOCK_KEY,)
        ).fetchone()

        if not row:
            print(f"  ⚠ 未找到'{BLOCK_KEY}'板块，请在东方财富中先创建'热点跟踪'自选板块")
            conn.close()
            return False

        old_ver = row[0]
        new_ver = old_ver + 1

        cursor.execute(
            """UPDATE selfstock SET
               stock_code_arr=?,
               stock_price_arr=?,
               group_version=?
               WHERE group_key=?""",
            (stock_code_arr, stock_price_arr, new_ver, BLOCK_KEY)
        )
        conn.commit()
        conn.close()

        print(f"  ✓ 已写入{len(unique_em)}只股票到'{BLOCK_KEY}'板块 (ver {old_ver}→{new_ver})")
        restart_emweb()
        return True

    except Exception as e:
        print(f"  ✗ 写入东方财富自选板块失败: {e}")
        return False


def restart_emweb():
    """重启东方财富客户端，触发云同步。"""
    em_exe = r"C:\eastmoney\swc8\EMWeb.exe"
    if not os.path.exists(em_exe):
        print("  ⚠ 未找到EMWeb.exe，跳过重启")
        return

    print("   重启东方财富客户端同步到手机...")
    # 先杀掉所有东财进程
    subprocess.run("taskkill /f /im EMWeb.exe 2>nul", shell=True, capture_output=True)
    subprocess.run("taskkill /f /im ETWeb.exe 2>nul", shell=True, capture_output=True)
    time.sleep(2)
    # 重新启动
    subprocess.Popen([em_exe], shell=True)
    print("   ✓ 东方财富已重启，请稍后查看手机端同步")
