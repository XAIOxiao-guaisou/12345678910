import sys
import os
sys.path.append(os.getcwd())
import requests
import json
from core.services.db_service.feishu_bitable import FeishuBitableManager
from core.config import settings

def main():
    manager = FeishuBitableManager()
    token = manager._get_token()
    headers = {"Authorization": f"Bearer {token}"}
    
    # 获取剧本表的字段列表
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{settings.FEISHU_APP_TOKEN_SCRIPT}/tables/{settings.FEISHU_TABLE_SCRIPT}/fields"
    resp = requests.get(url, headers=headers)
    
    if resp.status_code != 200:
        print("Error getting fields:", resp.text)
        return
        
    data = resp.json()
    for item in data.get("data", {}).get("items", []):
        if item.get("field_name") == "状态":
            print(json.dumps(item, indent=2, ensure_ascii=False))
            
if __name__ == "__main__":
    main()
