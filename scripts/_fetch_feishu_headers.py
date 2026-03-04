import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
load_dotenv()

from core.services.db_service.feishu_bitable import FeishuBitableManager

async def read_headers():
    bm = FeishuBitableManager()
    
    # 1. Factory
    factory_app_token = os.environ.get("FEISHU_APP_TOKEN_FACTORY", "")
    factory_table_id = os.environ.get("FEISHU_TABLE_FACTORY", "")
    print(f"Factory Table ({factory_table_id}):")
    try:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{factory_app_token}/tables/{factory_table_id}/fields"
        token = bm._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        import requests
        resp = requests.get(url, headers=headers)
        data = resp.json()
        if data.get("code") == 0:
            for field in data["data"]["items"]:
                print(f"  - {field['field_name']} ({field['type']})")
        else:
            print("  Error:", data)
    except Exception as e:
        print("Exception:", e)

    # 2. Memory
    mem_app_token = os.environ.get("FEISHU_APP_TOKEN_MEMORY", "")
    mem_table_id = os.environ.get("FEISHU_TABLE_MEMORY", "")
    print(f"\nMemory Table ({mem_table_id}):")
    try:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{mem_app_token}/tables/{mem_table_id}/fields"
        token = bm._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        import requests
        resp = requests.get(url, headers=headers)
        data = resp.json()
        if data.get("code") == 0:
            for field in data["data"]["items"]:
                print(f"  - {field['field_name']} ({field['type']})")
        else:
            print("  Error:", data)
    except Exception as e:
        print("Exception:", e)

if __name__ == "__main__":
    asyncio.run(read_headers())
