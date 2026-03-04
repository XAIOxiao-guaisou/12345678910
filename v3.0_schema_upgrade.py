import os
import sys
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

from core.services.db_service.feishu_bitable import FeishuBitableManager
from core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("schema_upgrade")

def ensure_field(manager, app_token, table_id, field_name, field_type, property_dict=None):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    # check existing
    resp = requests.get(url, headers=manager._get_headers())
    resp.raise_for_status()
    existing_fields = {f["field_name"]: f["field_id"] for f in resp.json().get("data", {}).get("items", [])}
    
    if field_name in existing_fields:
        logger.info(f"Field '{field_name}' already exists. Skipping.")
        return True

    payload = {"field_name": field_name, "type": field_type}
    if property_dict:
        payload["property"] = property_dict
        
    try:
        resp = requests.post(url, headers=manager._get_headers(), json=payload)
        resp.raise_for_status()
        logger.info(f"✅ Field '{field_name}' created successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to create field '{field_name}': {e}")
        if hasattr(e, "response") and e.response:
            logger.error(f"Response: {e.response.text}")
        return False

def main():
    manager = FeishuBitableManager()
    
    factory_token = settings.FEISHU_APP_TOKEN_FACTORY
    factory_table = settings.FEISHU_TABLE_FACTORY
    
    script_token = settings.FEISHU_APP_TOKEN_SCRIPT
    script_table = settings.FEISHU_TABLE_SCRIPT
    
    # 1. 素材生成表 (Factory - Asset Center) Upgrade
    # Asset_Type: 3 (Single Select: base / variant)
    # Version_Tag: 1 (Text)
    # Base_Reference: 1 (Text)
    # Visual_Seed: 2 (Number)
    # Visual_Anchor_Prompt: 1 (Text)
    logger.info("Upgrading Factory Table...")
    ensure_field(manager, factory_token, factory_table, "Asset_Type", 3, {"options": [{"name": "base"}, {"name": "variant"}]})
    ensure_field(manager, factory_token, factory_table, "Version_Tag", 1)
    ensure_field(manager, factory_token, factory_table, "Base_Reference", 1)
    ensure_field(manager, factory_token, factory_table, "Visual_Seed", 2)
    ensure_field(manager, factory_token, factory_table, "Visual_Anchor_Prompt", 1)
    
    # 2. 剧本拆解表 (Script) Upgrade
    # Row_Lock: 7 (Checkbox boolean)
    # Storage_Zone: 3 (Single Select: 草稿 / 冻结 / 归档)
    logger.info("Upgrading Script Table...")
    ensure_field(manager, script_token, script_table, "Row_Lock", 7)
    ensure_field(manager, script_token, script_table, "Storage_Zone", 3, {"options": [{"name": "草稿"}, {"name": "冻结"}, {"name": "归档"}]})

    logger.info("Upgrade Complete.")

if __name__ == "__main__":
    main()
