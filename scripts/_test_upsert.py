import sys, os, time
sys.path.insert(0, r"d:\桌面\12345678910-2.0.0")

from core.services.db_service.feishu_bitable import FeishuBitableManager
import logging

logging.basicConfig(level=logging.INFO)

def test():
    bm = FeishuBitableManager()
    
    # Check if table works
    print("Trying upsert...")
    rid = bm.upsert_asset_record(
        novel_id="test_novel_xyz",
        entity_id="test_entity_123",
        entity_name="Test Entity",
        asset_type="character",
        visual_prompt="A test prompt",
        image_url="http://example.com/test.jpg",
        seed=12345,
        chapter_tag="TestChapter"
    )
    print("Record ID:", rid)
    time.sleep(2)
    
    # Check if we can search it
    found = bm.get_asset_by_entity(entity_id="test_entity_123", novel_id="test_novel_xyz")
    print("Found:", found)

    print("All records:", len(bm._get_blank_record_ids(os.environ.get("FEISHU_APP_TOKEN_FACTORY", "Cu75bLeuJarqg1s7ysscaNolnPg"), os.environ.get("FEISHU_TABLE_FACTORY", "tbloUrdwqG47ZmgI"))))

if __name__ == "__main__":
    test()
