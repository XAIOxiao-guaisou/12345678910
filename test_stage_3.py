import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def test_3_prompt_fallback():
    print("=== 测试项 3: 风格化提示词退避 (realistic) ===")
    
    # We submit a task with style='realistic' which doesn't have a folder yet.
    # The DeepSeek pipeline should throw a warning but successfully fall back to `default/`.
    payload = {
        "account": "test_account",
        "content": "陈伶在雨夜中醒来，发现自己身处陌生环境。",
        "style": "realistic",  # The non-existent template
        "gateway": "seedance-1.5-pro",
        "sandbox_mode": True
    }
    
    resp = requests.post(f"{BASE_URL}/api/upload_novel", json=payload)
    print("响应:", resp.json())

if __name__ == "__main__":
    test_3_prompt_fallback()
