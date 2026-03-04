import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
import os
import sys
from dotenv import load_dotenv
from core.pipeline import PipelineOrchestrator
from core.services.db_service.feishu_bitable import FeishuBitableManager
import urllib3
urllib3.disable_warnings()

async def main():
    load_dotenv()
    file_path = r"d:\桌面\12345678910-2.0.0\测试小说章节\第0020章_《我不是戏神》第21章 平安符.txt"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    files = [{"name": "第0020章_《我不是戏神》第21章 平安符.txt", "content": content}]
    
    # We will use the same novel ID as before for consistency
    novel_id = "test_short_drama_002" 
    
    pipe = PipelineOrchestrator()
    
    # We will clear out the script memory for this novel_id to prevent breakpoint resumption from skipping it
    # No need, we'll just use a fresh novel_id to see it from start.
    
    print(f"🚀 [Test] Starting pipeline for {novel_id}")
    
    await pipe.process_files_batch(
        files=files,
        novel_id=novel_id,
        style_key="anime",
        gateway="seedance-1.0-pro-fast", # Test the Doubao fast model
        sandbox_mode=False, # Write to actual Feishu and run the whole thing
    )
    
    print("✅ [Test] Parsing and generation queued successfully. Now check Feishu Bitable.")

if __name__ == "__main__":
    asyncio.run(main())
