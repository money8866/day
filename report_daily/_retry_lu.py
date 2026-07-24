# -*- coding: utf-8 -*-
import urllib.request, json, time, os
BASE={"ashare":"https://fuyao.aicubes.cn/mcp/a-share"}
TOKEN="eyJidHkiOiJvaWRjIiwia2lkIjoiNm0yd2o5eGs3Y3F6eHlrMSIsInR5cCI6IkpXVCIsImFsZyI6IkVTMjU2In0.eyJzY3AiOiJvcGVuaWQgYmFzZV91c2VyaW5mbyBtY3A6ZGF0YS5yZWFkIG9mZmxpbmVfYWNjZXNzIiwiaXNzIjoiaHR0cDovLzEwLjIxNy4xNDEuMjEvb2lkYyIsInN1YiI6IlAtNmZYbXRHNHRfME5VRUs5T3d6YlZnOVdNTHZxb0ZSVkdUVTF6OE5hQUc1VDd1aVd2STVLSnJuUlpSYmd1dm9uWFNNM2F0UUJMemRVUHciLCJhdWQiOiJ1cG9jX2tuOXhlemRjdGtfcWNsYXciLCJqdGkiOiI3NDM4NWY3NC1iYWU4LTQzMGQtOGY1YS05ZjcwOWJjOWU3YTUiLCJpYXQiOjE3ODQ4ODM4NDQsImV4cCI6MTc4ODg4NzQ0NH0.IMKRnV4Dt1Si0sqYi2u3m2Kdepwk4e4QmNw2iG-11WuhXIEDw4mT7T_ecS1FmTZLsib4H5E-YoJ9Yr7O-3kqVA"
HEADERS={"X-Authorization":TOKEN,"X-Consumer-Id":"qclaw","X-Client-Secret":"1","Content-Type":"application/json","Accept":"application/json, text/event-stream"}
def call(tool,args,rid=1):
    body=json.dumps({"jsonrpc":"2.0","id":rid,"method":"tools/call","params":{"name":tool,"arguments":args}}).encode()
    req=urllib.request.Request(BASE["ashare"],data=body,headers=HEADERS,method="POST")
    with urllib.request.urlopen(req,timeout=60) as r:
        raw=r.read().decode("utf-8","replace")
    t=raw.strip()
    if "data:" in t:
        ls=[l for l in t.splitlines() if l.startswith("data:")]
        if ls: t=ls[-1][len("data:"):].strip()
    d=json.loads(t)
    if "result" in d and "content" in d["result"]:
        return json.loads(d["result"]["content"][0]["text"])
    return d
all_lu=[]
for attempt in range(4):
    for p in [1,2,3]:
        lu=call("get_a_share_special_data_limit_up_pool",{"page":p,"size":50})
        if lu and "data" in lu and lu["data"] and "item" in lu["data"]:
            all_lu.extend(lu["data"]["item"])
    if all_lu:
        break
    print("attempt",attempt,"empty, retry"); time.sleep(2)
print("TOTAL limit_up:",len(all_lu))
with open("D:/mystock/report_daily/q_limitup_0724.json","w",encoding="utf-8") as f:
    json.dump({"total":len(all_lu),"items":all_lu},f,ensure_ascii=False,indent=2)
print("saved")
