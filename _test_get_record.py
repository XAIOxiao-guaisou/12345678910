import sys, os, json
sys.path.insert(0, r"d:\桌面\12345678910-2.0.0")

from core.services.db_service.feishu_bitable import FeishuBitableManager
import requests

def test():
    bm = FeishuBitableManager()
    
    APP_TOKEN_FACTORY = os.environ.get("FEISHU_APP_TOKEN_FACTORY", "Cu75bLeuJarqg1s7ysscaNolnPg")
    TABLE_FACTORY = os.environ.get("FEISHU_TABLE_FACTORY", "tbloUrdwqG47ZmgI")
    
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN_FACTORY}/tables/{TABLE_FACTORY}/records/recvcAYniraMKv"
    resp = requests.get(url, headers=bm._get_headers())
    
    print(json.dumps(resp.json(), indent=2, ensure_ascii=False))

if __name__ == "__main__":
    test()
