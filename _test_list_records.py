import sys, os, json
sys.path.insert(0, r"d:\桌面\12345678910-2.0.0")

from core.services.db_service.feishu_bitable import FeishuBitableManager
import requests

def test():
    bm = FeishuBitableManager()
    
    APP_TOKEN_FACTORY = os.environ.get("FEISHU_APP_TOKEN_FACTORY", "Cu75bLeuJarqg1s7ysscaNolnPg")
    TABLE_FACTORY = os.environ.get("FEISHU_TABLE_FACTORY", "tbloUrdwqG47ZmgI")
    
    # 1. search endpoint
    search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_FACTORY}/tables/{TABLE_FACTORY}/records/search"
    resp1 = requests.post(search_url, headers=bm._get_headers(), json={"page_size": 2})
    print("Search:")
    print(resp1.json())
    
    # 2. list endpoint
    list_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_FACTORY}/tables/{TABLE_FACTORY}/records?page_size=2"
    resp2 = requests.get(list_url, headers=bm._get_headers())
    print("List:")
    print(resp2.json())

if __name__ == "__main__":
    test()
