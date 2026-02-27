import requests
import json
import time

url = "http://127.0.0.1:8000/api/upload_novel"
payload = {
    "account": "e2e_debug_user",
    "content": "清晨，温暖的阳光挥洒在书桌上。一只橘色的猫咪轻巧地跃上窗台。它转头看向窗外的飞鸟，眼神中充满好奇。",
    "style": "anime",
    "gateway": "seedance-1.5-pro"
}

print("🚀 提交 15 秒测试微短剧剧本...")
response = requests.post(url, json=payload)
print(f"🔥 API 响应: {response.status_code}")
print(json.dumps(response.json(), indent=2, ensure_ascii=False))
