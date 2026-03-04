import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import os
import json
import urllib.request
import time

def test_bailian():
    # Read API Key
    api_key = "sk-9614cb4a2f74425eacf3bd4f4cda0246"
    
    print("API Key available:", bool(api_key))
    if not api_key:
        print("Could not find environment variable 'wan2.6-i2v'")
        return

    # Endpoint for DashScope video generation
    url = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis'
    headers = {
        'X-DashScope-Async': 'enable',
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    
    # Payload for generating video
    # Testing if wan2.6-i2v accepts just a text prompt, or requires an image
    data = {
        'model': 'wan2.6-i2v',
        'input': {
            'prompt': 'A digital art illustration of a glowing blue orb, 8k resolution'
        },
        'parameters': {}
    }
    
    print("Submitting task...")
    req = urllib.request.Request(url, json.dumps(data).encode('utf-8'), headers)
    
    try:
        with urllib.request.urlopen(req) as f:
            resp = json.loads(f.read().decode('utf-8'))
            print("Response:", json.dumps(resp, indent=2))
            
            # If task submitted, try checking status once
            task_id = resp.get('output', {}).get('task_id')
            if task_id:
                print(f"Task ID: {task_id}, waiting 5s to check status...")
                time.sleep(5)
                check_url = f'https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}'
                check_req = urllib.request.Request(check_url, headers={'Authorization': f'Bearer {api_key}'})
                with urllib.request.urlopen(check_req) as cf:
                    cresp = json.loads(cf.read().decode('utf-8'))
                    print("Status Check:", json.dumps(cresp, indent=2))
                    
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.reason}")
        print("Details:", e.read().decode('utf-8'))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    test_bailian()
