import requests
import json
from pathlib import Path
GATEWAY_URL = "http://127.0.0.1:19000/proxy" # 或你的 Gateway 地址
GATEWAY_TOKEN = "31fd9904c07f8c142760e7a03c11fe9e5820da8cfac24d62" # 从 OpenClaw 配置中获取

class QClawFix:
    def __init__(self):
        self.gateway_url = GATEWAY_URL
        self.token = self._get_token()
 
    def _get_token(self):
        config_file = Path.home() / ".qclaw" / "openclaw.json"
        if not config_file.exists():
            raise FileNotFoundError("配置文件不存在")
    
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
            token = config.get('gateway', {}).get('token', '')
            if not token:
                raise ValueError("Token 未配置")
                return token
 
    def test_connection(self):
        headers = {"Authorization": f"Bearer {self.token}"}
 
        print(f"🔍 测试 Gateway 连接...")
        print(f" URL: {self.gateway_url}")
        print(f" Token: {self.token[:20]}...")
 
 # 测试1: 检查状态
        response = requests.get(f"{self.gateway_url}/api/v1/status", headers=headers)
        print(f"\n📊 状态检查: {response.status_code}")
        print(f" 响应: {response.text}")
 
        if response.status_code == 200:
            print("✅ Gateway 连接成功")
            return True
        elif response.status_code == 401:
            print("❌ Token 认证失败")
            print(" 解决方法: 运行 `openclaw gateway token generate` 重新生成 token")
            return False
        else:
            print(f"❌ 连接失败: {response.status_code}")
            return False
 
    def send_message(self, message, target, channel="wechat"):
        headers = {
        "Authorization": f"Bearer {self.token}",
        "Content-Type": "application/json"
        }
 
        payload = {
        "action": "send",
        "channel": channel,
        "message": str(message),
        "target": str(target)
        }
        
        print(f"\n📤 发送消息...")
        print(f" Payload: {json.dumps(payload, ensure_ascii=False)}")
        
        response = requests.post(
        f"{self.gateway_url}/api/v1/message/send",
        headers=headers,
        json=payload,
        timeout=10
        )
        
        print(f"\n📥 响应: {response.status_code}")
        print(f" 内容: {response.text}")
        
        return response.json()

# 使用
if __name__ == "__main__":
    qclaw = QClawFix()
 
 # 先测试连接
    if qclaw.test_connection():
 # 连接成功，尝试发送
        result = qclaw.send_message(
        message="测试消息",
        target="用户名", # 替换成实际用户名
        channel="wechat"
        )
        print(f"\n结果: {result}")
    else:
        print("\n⚠️ 请先修复认证问题")