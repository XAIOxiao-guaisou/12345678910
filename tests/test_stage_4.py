import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def test_4_reroute_and_promotion():
    print("=== 测试项 4: 一键投产与热更 (One-Click Reroute & Promotion) ===")
    
    # 建立一条 Sandbox 的沙盒记录，模拟用户在 WebUI 测试出来的好效果
    payload = {
        "account": "test_account",
        "content": "陈伶在雨夜中醒来，为了测试沙盒的热更新投产，她准备使用一键重投。这会产生一个沙盒的记录。",
        "style": "anime",
        "gateway": "seedance-1.5-pro",
        "sandbox_mode": True
    }
    
    print("1. 提交初始沙盒任务...")
    resp = requests.post(f"{BASE_URL}/api/upload_novel", json=payload)
    print("响应:", resp.json())
    
    print("2. 等候15秒生成飞书记录...")
    time.sleep(15)

    # Note: In a real test we'd parse the log to get the exact record ID.
    # To automate the verification, let's call Feishu directly but for now we rely
    # on manual verification or checking the most recent records from Bitable.
    # For now, we just test the /api/update_presets endpoint.
    
    print("3. 测试配置固化接口 (/api/update_presets)...")
    preset_payload = {
        "gateway": "seedance-1.5-pro",
        "video_params": {
            "resolution": "1080p",
            "fps": "60",
            "sampling_steps": 25
        }
    }
    resp = requests.post(f"{BASE_URL}/api/update_presets", json=preset_payload)
    print("配置固化响应:", resp.json())

    print("4. 测试一键转正 (/api/reroute_sandbox)...")
    # Taking ID recvcuAdIdimZv from the worker.log output which was Sandbox=True.
    reroute_resp = requests.post(f"{BASE_URL}/api/reroute_sandbox", json={"record_id": "recvcuAdIdimZv"})
    print("重投转正响应:", reroute_resp.json())

if __name__ == "__main__":
    test_4_reroute_and_promotion()
