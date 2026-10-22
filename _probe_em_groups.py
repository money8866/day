# -*- coding: utf-8 -*-
"""探针: 调用 mx-self-select 同款 search-code 接口, 检查原始返回中是否包含自选分组信息。

只读探测, 不改任何数据。
"""
import json
import sys

SCRIPTS_DIR = r"C:\Users\kongx\.workbuddy\skills\mx-self-select\scripts"
sys.path.insert(0, SCRIPTS_DIR)

import manage_watchlist as mw  # noqa: E402


def collect_keys(obj, prefix=""):
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= collect_keys(v, prefix + k + ".")
    elif isinstance(obj, list) and obj:
        keys |= collect_keys(obj[0], prefix + "[].")
    return keys


def main():
    raw = mw._http_post(
        mw.SEARCH_URL,
        {
            "keyWord": "我的自选",
            "pageNo": 1,
            "pageSize": 3,
            "needChoiceOptional": False,
        },
    )
    node = mw._dig_result_node(raw)
    data_list = node.get("dataList") or []
    print("returned rows (pageSize=3):", len(data_list))
    print("total:", node.get("total"))
    print("result-node keys:", sorted(node.keys()))
    if data_list:
        print("\n--- first item raw ---")
        print(json.dumps(data_list[0], ensure_ascii=False, indent=2)[:3000])
        all_keys = collect_keys(data_list[0])
        print("\n--- all keys in item ---")
        print(sorted(all_keys))
        groupish = [k for k in all_keys if any(
            t in k.upper() for t in ("GROUP", "CATEGORY", "BLOCK", "BOARD", "FENZU", "CLASSIFY")
        )]
        print("\ngroup-ish keys:", groupish or "NONE")


if __name__ == "__main__":
    main()
