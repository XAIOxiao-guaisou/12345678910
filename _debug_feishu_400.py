import os
import requests
from dotenv import load_dotenv

def debug_feishu():
    load_dotenv()
    APP_ID = os.environ.get("FEISHU_APP_ID")
    APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
    
    APP_TOKEN_SCRIPT = os.environ.get("FEISHU_APP_TOKEN_SCRIPT", "J7OPbwEHqaJMefs1NLecTvA1n2e")
    TABLE_SCRIPT = os.environ.get("FEISHU_TABLE_SCRIPT", "tbluFmGLkmqPTd9S")

    # Get token
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    token = resp.json().get("tenant_access_token")

    # Call search
    search_url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_SCRIPT}"
        f"/tables/{TABLE_SCRIPT}/records/search"
    )
    payload = {
        "page_size": 500,
        "filter": {
            "conjunction": "and",
            "conditions": [
                {"field_name": "视觉提示词", "operator": "isEmpty", "value": []},
                {"field_name": "小说原文（内容）", "operator": "isEmpty", "value": []}
            ]
        }
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    print(f"URL: {search_url}")
    print(f"Payload: {payload}")
    
    resp = requests.post(search_url, headers=headers, json=payload)
    print(f"Status Code: {resp.status_code}")
    import json
    with open("debug_out.json", "w", encoding="utf-8") as f:
        f.write(resp.text)
    print("Response written to debug_out.json")

if __name__ == "__main__":
    debug_feishu()
