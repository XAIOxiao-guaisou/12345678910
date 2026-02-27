import logging
from core.services.db_service.feishu_bitable import FeishuBitableManager
from core.config import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def debug_fields():
    mgr = FeishuBitableManager()
    
    print("=== Memory Table Fields ===")
    mem_fields = mgr.get_table_field_names(settings.FEISHU_APP_TOKEN_MEMORY, settings.FEISHU_TABLE_MEMORY)
    print(mem_fields)
    
    print("=== Script Table Fields ===")
    script_fields = mgr.get_table_field_names(settings.FEISHU_APP_TOKEN_SCRIPT, settings.FEISHU_TABLE_SCRIPT)
    print(script_fields)

if __name__ == "__main__":
    debug_fields()
