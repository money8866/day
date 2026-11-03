# -*- coding: utf-8 -*-
"""按官方 WorkBuddy 实现校准 channel_version / client_id，定位 prepare failed 根因。"""
import base64, json, os, urllib.request, urllib.error

BASE = "https://ilinkai.weixin.qq.com"
HOME = os.path.expanduser("~")
secret = json.load(open(os.path.join(HOME, ".workbuddy/wechat-clawbot-push/credentials.json"), encoding="utf-8"))["botToken"]
cache = json.load(open(os.path.join(HOME, ".workbuddy/wechat-clawbot-push/push_cache.json"), encoding="utf-8"))
uid, ctx = cache["user_id"], cache["context_token"]


def uin():
    return base64.b64encode(str(int.from_bytes(os.urandom(4), "big")).encode()).decode()


def post(path, body):
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=raw, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("AuthorizationType", "ilink_bot_token")
    req.add_header("Authorization", "Bearer " + secret)
    req.add_header("X-WECHAT-UIN", uin())
    req.add_header("Content-Length", str(len(raw)))
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)


def send(tag, cv, client_id, text="通道校准测试"):
    msg = {
        "from_user_id": "",
        "to_user_id": uid,
        "client_id": client_id,
        "message_type": 2,
        "message_state": 2,
        "context_token": ctx,
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }
    st, b = post("/ilink/bot/sendmessage", {"msg": msg, "base_info": {"channel_version": cv}})
    print("\n=== %s ===\ncv=%s  client_id=%s\nHTTP %s\n%s" % (tag, cv, client_id, st, b[:500]))
    return b


import time, random

wid = "workbuddy-%d-%s" % (int(time.time() * 1000), random.random().__str__()[2:8])

# 1) 官方 channel_version + 桥的 client_id
send("G1 官方cv + push-前缀client_id", "workbuddy-desktop-1.0.0", "push-" + os.urandom(8).hex())
# 2) 官方 channel_version + 官方 client_id 格式
send("G2 官方cv + 官方client_id格式", "workbuddy-desktop-1.0.0", wid)
# 3) 其他候选 channel_version
for cv in ("workbuddy-desktop-1.0.1", "workbuddy-desktop-1.0.2", "workbuddy-desktop-1.0.3",
           "workbuddy-1.0.0", "1.0.3"):
    send("G3 候选cv=%s" % cv, cv, wid)
