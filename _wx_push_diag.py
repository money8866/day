# -*- coding: utf-8 -*-
"""微信 ClawBot 推送失败诊断：复现 prepare failed 并试报文变体。"""
import base64, json, os, urllib.request, urllib.error

BASE = "https://ilinkai.weixin.qq.com"
CV = "1.0.3"
HOME = os.path.expanduser("~")
CRED = os.path.join(HOME, ".workbuddy/wechat-clawbot-push/credentials.json")
CACHE = os.path.join(HOME, ".workbuddy/wechat-clawbot-push/push_cache.json")

secret = json.load(open(CRED, encoding="utf-8"))["botToken"]
cache = json.load(open(CACHE, encoding="utf-8"))
uid, ctx = cache["user_id"], cache["context_token"]


def uin():
    return base64.b64encode(str(int.from_bytes(os.urandom(4), "big")).encode()).decode()


def post(path, body):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("AuthorizationType", "ilink_bot_token")
    req.add_header("Authorization", "Bearer " + secret)
    req.add_header("X-WECHAT-UIN", uin())
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)


def show(tag, st, body):
    print("\n=== %s ===" % tag)
    print("HTTP", st)
    try:
        j = json.loads(body)
        s = json.dumps(j, ensure_ascii=False)
        print(s[:1200])
    except Exception:
        print(body[:1200])
    return body


# 0) 只读鉴权校验：空 buf，不推进服务端游标
st, b = post("/ilink/bot/getupdates", {"get_updates_buf": "", "base_info": {"channel_version": CV}})
show("A. getupdates 空buf（只读鉴权校验）", st, b)

# 1) 复现当前 MCP 使用的报文
base_msg = {
    "from_user_id": "",
    "to_user_id": uid,
    "client_id": "push-" + os.urandom(8).hex(),
    "message_type": 2,
    "message_state": 2,
    "context_token": ctx,
    "item_list": [{"type": 1, "text_item": {"text": "测试消息 A"}}],
}
st, b = post("/ilink/bot/sendmessage", {"msg": base_msg, "base_info": {"channel_version": CV}})
show("B. 当前报文（原样复现）", st, b)


def variant(tag, **over):
    m = dict(base_msg)
    m.update(over)
    st, b = post("/ilink/bot/sendmessage", {"msg": m, "base_info": {"channel_version": CV}})
    show(tag, st, b)


# 2) 变体：from_user_id 用 bot id
variant("C. from_user_id=bot_id", from_user_id="61f53d8088c1@im.bot")

# 3) 变体：message_state=1
variant("D. message_state=1", message_state=1)

# 4) 变体：message_type=1
variant("E. message_type=1", message_type=1)

# 5) 变体：channel_version 换成常见的 1.0.0 / 1.0.1
for cv in ("1.0.0", "1.0.1", "1.0.2"):
    m = dict(base_msg)
    st, b = post("/ilink/bot/sendmessage", {"msg": m, "base_info": {"channel_version": cv}})
    show("F. channel_version=%s" % cv, st, b)
