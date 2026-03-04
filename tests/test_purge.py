import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import os
import sys
import logging
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.services.db_service.feishu_bitable import FeishuBitableManager, APP_TOKEN_SCRIPT, TABLE_SCRIPT, APP_TOKEN_MEMORY, TABLE_MEMORY

logging.basicConfig(level=logging.INFO)
load_dotenv()

manager = FeishuBitableManager()

print("\n--- SCRIPT TABLE FIELDS ---")
script_fields = manager._list_table_fields_with_ids(APP_TOKEN_SCRIPT, TABLE_SCRIPT)
for f in script_fields.keys():
    print(f"'{f}'")

print("\n--- MEMORY TABLE FIELDS ---")
memory_fields = manager._list_table_fields_with_ids(APP_TOKEN_MEMORY, TABLE_MEMORY)
for f in memory_fields.keys():
    print(f"'{f}'")

