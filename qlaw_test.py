import requests
import json

def send_wechat_message(message, target=None, chat_id=None):
    # QClaw Gateway 地址（根据实际情况调整）
    GATEWAY_URL = "http://127.0.0.1:19000/proxy" # 或你的 Gateway 地址
    GATEWAY_TOKEN = "31fd9904c07f8c142760e7a03c11fe9e5820da8cfac24d62" # 从 OpenClaw 配置中获取

    headers = {
    "Authorization": f"Bearer {GATEWAY_TOKEN}",
    "Content-Type": "application/json"
    }
    url = f"{GATEWAY_URL}/api/v1/message/send"
    
    payload = {
    "action": "send",
    "channel": "wechat-access", # 或 "wechat-access"
    "message": message
    }
    
    # 如果指定接收人
    if target:
        payload["target"] = target
    
    # 如果发群消息
    if chat_id:
        payload["chatId"] = chat_id
    
    response = requests.get(f"{GATEWAY_URL}/api/v1/status", headers=headers)
    print(response.json())

    response = requests.post(url, headers=headers, json=payload)
    return response.json()

    # 使用示例

send_wechat_message("这是一个测试消息","kongxp") # 发送给特定用户

