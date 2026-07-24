# -*- coding: utf-8 -*-
"""直接 HTTP 调用同花顺 MCP 端点（JSON-RPC），绕过 mcporter CLI 的引号解析问题。"""
import urllib.request, json, sys, time

BASE = {
    "ashare": "https://fuyao.aicubes.cn/mcp/a-share",
    "index":  "https://fuyao.aicubes.cn/mcp/a-share-index",
}
TOKEN = "eyJidHkiOiJvaWRjIiwia2lkIjoiNm0yd2o5eGs3Y3F6eHlrMSIsInR5cCI6IkpXVCIsImFsZyI6IkVTMjU2In0.eyJzY3AiOiJvcGVuaWQgYmFzZV91c2VyaW5mbyBtY3A6ZGF0YS5yZWFkIG9mZmxpbmVfYWNjZXNzIiwiaXNzIjoiaHR0cDovLzEwLjIxNy4xNDEuMjEvb2lkYyIsInN1YiI6IlAtNmZYbXRHNHRfME5VRUs5T3d6YlZnOVdNTHZxb0ZSVkdUVTF6OE5hQUc1VDd1aVd2STVLSnJuUlpSYmd1dm9uWFNNM2F0UUJMemRVUHciLCJhdWQiOiJ1cG9jX2tuOXhlemRjdGtfcWNsYXciLCJqdGkiOiI3NDM4NWY3NC1iYWU4LTQzMGQtOGY1YS05ZjcwOWJjOWU3YTUiLCJpYXQiOjE3ODQ4ODM4NDQsImV4cCI6MTc4NDg4NzQ0NH0.IMKRnV4Dt1Si0sqYi2u3m2Kdepwk4e4QmNw2iG-11WuhXIEDw4mT7T_ecS1FmTZLsib4H5E-YoJ9Yr7O-3kqVA"
HEADERS = {
    "X-Authorization": TOKEN,
    "X-Consumer-Id": "qclaw",
    "X-Client-Secret": "1",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

def call(service, tool, arguments, rid=1):
    url = BASE[service]
    body = json.dumps({
        "jsonrpc": "2.0", "id": rid,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments}
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        # 若是 SSE 流，读取内容
    # 解析：可能是 JSON，也可能是 SSE (data: ...)
    text = raw.strip()
    # 找最后一个 data: 行后的 JSON
    if "data:" in text:
        lines = [l for l in text.splitlines() if l.startswith("data:")]
        if lines:
            text = lines[-1][len("data:"):].strip()
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text[:2000]}

if __name__ == "__main__":
    # 测试：指数
    r = call("ashare", "get_a_share_prices_snapshot",
             {"thscodes": "000001.SZ,399001.SZ,399006.SZ,000300.SH,000905.SH,000852.SH"})
    print(json.dumps(r, ensure_ascii=False, indent=2)[:1500])
