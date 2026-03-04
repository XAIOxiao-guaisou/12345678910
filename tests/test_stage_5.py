import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests

BASE_URL = "http://127.0.0.1:8000"

def test_5_6_mock_and_billing():
    print("=== 测试项 5 & 6: 异步轮询与超时处理 & 账单预估准确性 ===")
    
    # Send a small task with Sandbox enabled. MockVideoService 
    # built exactly two rounds of "processing" checks. Then it simulates success.
    # The worker daemon will print out the billing token estimation during local download step.
    payload = {
        "account": "test_account",
        "content": "模拟长时间生成的一段文本，用来验证2次第3方轮询的超时控制和本地虚拟账单计算，共5秒的时长预估测试。",
        "style": "anime",
        "gateway": "seedance-1.5-pro",
        "sandbox_mode": True
    }
    
    resp = requests.post(f"{BASE_URL}/api/upload_novel", json=payload)
    print("响应:", resp.json())

if __name__ == "__main__":
    test_5_6_mock_and_billing()
