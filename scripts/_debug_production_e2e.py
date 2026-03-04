#!/usr/bin/env python3
"""
_debug_production_e2e.py
Full End-to-End Production Test for Aiduanju Engine v2.7.2
"""
import sys, asyncio, os, time
sys.path.insert(0, r"d:\桌面\12345678910-2.0.0")

from dotenv import load_dotenv
load_dotenv(r"d:\桌面\12345678910-2.0.0\.env")

from core.pipeline import PipelineOrchestrator
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ProductionE2E")

async def main():
    novel_path = r"d:\桌面\12345678910-2.0.0\测试小说章节\第0001章_《我不是戏神》第1章 戏鬼回家.txt"
    if not os.path.exists(novel_path):
        logger.error(f"File not found: {novel_path}")
        return

    with open(novel_path, "r", encoding="utf-8") as f:
        content = f.read()

    files = [{"name": "第0001章_《我不是戏神》第1章 戏鬼回家", "content": content}]
    # Use a unique novel_id to avoid cache hits
    novel_id = "e2e_test_戏鬼回家_" + str(int(time.time()))

    logger.info(f"Starting E2E Pipeline for novel_id: {novel_id}")
    pipeline = PipelineOrchestrator()

    async def progress_cb(stage, current, total, name, status):
        logger.info(f"[Progress] {stage}: {current}/{total} | {name} -> {status}")

    # Pass duration=10 to explicitly ask for a 10-second video
    # wan2.6 expects '720P' or '1080P' for resolution
    video_params = {"duration": 10, "resolution": "720P"}

    # Run the pipeline with sandbox_mode=False
    # This automatically triggers run_video_generation asynchronously
    result = await pipeline.process_files_batch(
        files=files,
        novel_id=novel_id,
        style_key="realistic",
        gateway="wan_2_6",
        sandbox_mode=False,
        on_progress=progress_cb,
        video_params=video_params
    )

    logger.info(f"Pipeline process_files_batch returned: {result}")

    # Process files batch will dispatch video gen as a background task.
    # We await all background tasks to finish polling and downloading.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        logger.info(f"Waiting for {len(pending)} background tasks to complete (Video Gen & Downloads)...")
        await asyncio.gather(*pending, return_exceptions=True)
        logger.info("All background tasks finished.")
    else:
        logger.info("No background tasks found, maybe no videos were dispatched.")

if __name__ == "__main__":
    asyncio.run(main())
