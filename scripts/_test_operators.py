import sys, os, json
sys.path.insert(0, r"d:\桌面\12345678910-2.0.0")

from core.services.db_service.feishu_bitable import FeishuBitableManager
import requests

def test():
    bm = FeishuBitableManager()
    
    APP_TOKEN_FACTORY = os.environ.get("FEISHU_APP_TOKEN_FACTORY", "Cu75bLeuJarqg1s7ysscaNolnPg")
    TABLE_FACTORY = os.environ.get("FEISHU_TABLE_FACTORY", "tbloUrdwqG47ZmgI")
    
    search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_FACTORY}/tables/{TABLE_FACTORY}/records/search"

    payload_is = {
        "filter": {
            "conjunction": "and",
            "conditions": [{"field_name": "entity_id", "operator": "is", "value": ["test_entity_123"]}]
        }
    }
    
    payload_contains = {
        "filter": {
            "conjunction": "and",
            "conditions": [{"field_name": "entity_id", "operator": "contains", "value": ["test_entity_123"]}]
        }
    }

    print("--- Testing IS operator ---")
    resp_is = requests.post(search_url, headers=bm._get_headers(), json=payload_is)
    print("Found total:", resp_is.json().get("data", {}).get("total"))

    print("--- Testing CONTAINS operator ---")
    resp_contains = requests.post(search_url, headers=bm._get_headers(), json=payload_contains)
    print("Found total:", resp_contains.json().get("data", {}).get("total"))

if __name__ == "__main__":
    test()
