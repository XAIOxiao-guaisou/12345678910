import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests
import time
import json

BASE_URL = "http://127.0.0.1:8000"

def test_1_sandbox_isolation():
    print("=== 测试项 1: _sandbox_mode 隔离性 ===")
    
    # 任务 A: 开启沙盒
    print("[提交流程] 提交任务 A (Sandbox=True)")
    payload_a = {
        "account": "test_account",
        "content": "测试小说内容，非常精彩的开篇，让人欲罢不能。",
        "style": "anime",
        "gateway": "seedance-1.5-pro",
        "llm_temperature": 0.7,
        "top_p": 1.0,
        "chunk_size": 1200,
        "video_params": {"resolution": "720p", "fps": 24},
        "sandbox_mode": True
    }
    resp_a = requests.post(f"{BASE_URL}/api/upload_novel", json=payload_a)
    print("响应 A:", resp_a.json())
    
    time.sleep(2)
    
    # 任务 B: 关闭沙盒
    print("[提交流程] 提交任务 B (Sandbox=False)")
    payload_b = {
        "account": "test_account",
        "content": "另一篇小说的内容，测试生产模式。",
        "style": "anime",
        "gateway": "seedance-1.5-pro",
        "llm_temperature": 0.7,
        "top_p": 1.0,
        "chunk_size": 1200,
        "video_params": {"resolution": "1080p", "fps": 30},
        "sandbox_mode": False
    }
    resp_b = requests.post(f"{BASE_URL}/api/upload_novel", json=payload_b)
    print("响应 B:", resp_b.json())

def test_2_gateway_tag():
    print("=== 测试项 2: 网关专属标签解析 ===")
    
    # For this one, we'll manually inject a record via Feishu API, or just use the worker parser directly 
    # to mock the feishu insertion since we just want to see worker behavior.
    # We will simulate Feishu insertion using the backend Bitable logic.
    from core.services.db_service.feishu_bitable import FeishuBitableManager
    feishu = FeishuBitableManager()
    
    # [WAN_2_6_CONFIG]
    fake_prompt = "一个赛博朋克风格的城市街道。\n\n[WAN_2_6_CONFIG]\n{\"resolution\": \"1080p\", \"fps\": 30, \"_sandbox_mode\": true}\n[/WAN_2_6_CONFIG]"
    
    print("[提交流程] 手动向飞书插入错乱的网关请求")
    rid_list = feishu.insert_new_parsed_scenes([{
        "scene_num": 999,
        "novel_text": "测试网关识别模块",
        "summary": "测试段落",
        "visual_prompt": fake_prompt,
        "audio_prompt": "无"
    }])
    
    print(f"插入记录成功，ID: {rid_list}")
    # Update the record to specifically have wrong gateway but the worker should align it
    if rid_list:
        feishu.update_record(rid_list[0], {
            "所属模型/网关": "seedance-1.5-pro",
            "状态": ["待生成"]
        })
        print(f"强制将记录 {rid_list[0]} 的模型设为 seedance-1.5-pro，等待 Worker 纠偏。")

if __name__ == "__main__":
    test_1_sandbox_isolation()
    test_2_gateway_tag()
