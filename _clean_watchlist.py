# -*- coding: utf-8 -*-
"""把 mx-self-select 导出的东财自选 CSV 清洗成适合录入腾讯自选股的格式。

输入: d:/mystock/miaoxiang/self_select/self_select_06989293.csv
      (代码列形如 ="600802", 末尾有空行和备注行)
输出:
  - eastmoney_to_tencent.csv  序号,名称,代码,市场 (干净6位代码)
  - codes_only.txt            每行一个6位代码, 便于逐只粘贴搜索
"""
import csv
import os
import re

SRC = "d:/mystock/miaoxiang/self_select/self_select_06989293.csv"
OUT_DIR = "d:/mystock/miaoxiang/self_select"
OUT_CSV = os.path.join(OUT_DIR, "eastmoney_to_tencent.csv")
OUT_TXT = os.path.join(OUT_DIR, "codes_only.txt")


def clean_code(raw: str) -> str:
    # csv 解析后字段内容形如 ="600802", 用正则取 6 位代码
    m = re.search(r"\d{6}", raw or "")
    return m.group(0) if m else ""


def main():
    rows = []
    with open(SRC, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for r in reader:
            if len(r) < 3:
                continue
            name = (r[0] or "").strip()
            code = clean_code(r[1])
            market = (r[2] or "").strip()
            if not code or not code.isdigit():
                continue  # 跳过空行 / 备注行
            rows.append((name, code, market))

    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["序号", "名称", "代码", "市场"])
        for i, (name, code, market) in enumerate(rows, 1):
            w.writerow([i, name, code, market])

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        for _, code, _ in rows:
            f.write(code + "\n")

    print(f"total: {len(rows)}")
    print(f"saved: {OUT_CSV}")
    print(f"saved: {OUT_TXT}")
    # 简单校验: 前5只 + 名称里带 XD/ST 的提示
    for name, code, market in rows[:5]:
        print(f"  {name} {code}.{market}")
    flagged = [f"{n}({c})" for n, c, _ in rows if n.startswith(("XD", "ST", "*ST"))]
    if flagged:
        print("注意以下名称带 XD/ST 标记(除权或风险警示): " + ", ".join(flagged))


if __name__ == "__main__":
    main()
