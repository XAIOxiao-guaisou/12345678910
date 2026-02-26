import sys
import os
sys.path.append(os.getcwd())
import requests
from core.services.db_service.feishu_bitable import FeishuBitableManager
from core.config import settings

def test_update():
    manager = FeishuBitableManager()
    # Find a record id that is currently in "待生成"
    url_search = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{settings.FEISHU_APP_TOKEN_SCRIPT}/tables/{settings.FEISHU_TABLE_SCRIPT}/records"
    resp_search = requests.get(url_search, headers=manager._get_headers())
    items = resp_search.json().get("data", {}).get("items", [])
    if not items:
        print("No tasks found at all.")
        return
    rid = items[0]["record_id"]
    print(f"Testing update on record: {rid}")
    
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{settings.FEISHU_APP_TOKEN_SCRIPT}/tables/{settings.FEISHU_TABLE_SCRIPT}/records/{rid}"
    payload = {"fields": {"状态": ["生成中"]}}
    resp = requests.put(url, headers=manager._get_headers(), json=payload)
    print("Response status:", resp.status_code)
    print("Response JSON:", resp.json())

if __name__ == "__main__":
    test_update()
