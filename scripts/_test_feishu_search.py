import sys, os, json
sys.path.insert(0, r"d:\桌面\12345678910-2.0.0")
from dotenv import load_dotenv
load_dotenv(r"d:\桌面\12345678910-2.0.0\.env")

from core.services.db_service.feishu_bitable import FeishuBitableManager
import requests

def test():
    APP_TOKEN = os.environ.get("FEISHU_APP_TOKEN", "J7OPbwEHqaJMefs1NLecTvA1n2e")
    TABLE_SCRIPT = os.environ.get("FEISHU_TABLE_SCRIPT", "tbluFmGLkmqPTd9S")

    bm = FeishuBitableManager()
    search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_SCRIPT}/records/search"

    payload = {
        "page_size": 15,
        "filter": {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": "所属小说ID",
                    "operator": "contains",
                    "value": ["e2e_test_戏鬼回家_1772352485"]
                }
            ]
        }
    }

    resp = requests.post(search_url, headers=bm._get_headers(), json=payload)
    with open("d:/桌面/12345678910-2.0.0/_test_output.json", "w", encoding="utf-8") as f:
        json.dump(resp.json(), f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    test()
