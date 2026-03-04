import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
load_dotenv()

from core.services.db_service.feishu_bitable import FeishuBitableManager

async def fetch_recent():
    bm = FeishuBitableManager()
    records = await bm.get_all_records(bm.table_script)
    # Sort by '集数' or '创建时间'
    # Assuming the recent ones are at the end, or we just print the last 10
    print(f"Total records in script table: {len(records)}")
    for r in records[-10:]:
        print("-" * 50)
        print(f"集数: {r.get('集数')}")
        print(f"画面描述 (Visual Prompt): {r.get('画面描述 (Visual Prompt)')}")
        print(f"运镜指令: {r.get('运镜（Camera）')}")

if __name__ == "__main__":
    asyncio.run(fetch_recent())
