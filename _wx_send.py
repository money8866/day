# -*- coding: utf-8 -*-
"""
微信 ClawBot 推送封装：从 credentials.json 读取明文 token，调用 send_wx.py 发送。
用法：python _wx_send.py "消息内容"
"""
import json, os, sys, subprocess

HOME = os.path.expanduser("~")
CRED = os.path.join(HOME, ".workbuddy", "wechat-clawbot-push", "credentials.json")
SEND = os.path.join(HOME, ".workbuddy", "skills", "workbuddy-claw-wechat-send", "scripts", "send_wx.py")


def get_token():
    if os.path.exists(CRED):
        try:
            t = json.load(open(CRED, encoding="utf-8")).get("botToken")
            if t:
                return t
        except Exception:
            pass
    # 兜底：从 iLink 游标恢复
    import base64, re, glob
    cands = sorted(glob.glob(os.path.join(HOME, ".workbuddy", "claw-state", "weixin", "*_im.bot.cursor.json")),
                   key=os.path.getmtime, reverse=True)
    for f in cands:
        d = json.load(open(f, encoding="utf-8"))
        raw = base64.b64decode(d.get("get_updates_buf", "")).decode("utf-8", "ignore")
        m = re.search(r'[0-9a-f]{8,32}@im\.bot:[0-9A-Za-z+/=_\-]{16,}', raw)
        if m:
            return m.group(0)
    return None


def get_user_id():
    s = json.load(open(os.path.join(HOME, ".workbuddy", "settings.json"), encoding="utf-8"))
    for v in s.get("claw", {}).get("users", {}).values():
        ch = v.get("channels", {}).get("weixinClawBot")
        if ch and ch.get("userId"):
            return ch["userId"]
    return None


def main():
    if len(sys.argv) < 2:
        print("用法: python _wx_send.py \"消息内容\"")
        sys.exit(1)
    text = sys.argv[1]
    tok, uid = get_token(), get_user_id()
    if not tok:
        print("❌ 未取到 botToken")
        sys.exit(2)
    env = dict(os.environ, WB_CLAW_BOT_TOKEN=tok)
    if uid:
        env["WB_CLAW_USER_ID"] = uid
    r = subprocess.run([sys.executable, SEND, text], env=env, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())


if __name__ == "__main__":
    main()
