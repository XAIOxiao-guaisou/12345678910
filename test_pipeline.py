import os
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

from core.pipeline import PipelineOrchestrator

def run_test():
    with open("第0001章_《我不是戏神》第1章 戏鬼回家.txt", "r", encoding="utf-8") as f:
        novel_text = f.read()

    pipeline = PipelineOrchestrator()
    print("Testing Pipeline with Architecture Awareness...")
    result = pipeline.process_novel_to_feishu(
        novel_text=novel_text,
        style_key="realistic",
        llm_temperature=0.3,
        chunk_size=1200
    )
    
    print("\n--- Pipeline Result ---")
    print(result)

if __name__ == "__main__":
    run_test()
