import logging
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from core.pipeline import PipelineOrchestrator

def run_test():
    with open("第0001章_《我不是戏神》第1章 戏鬼回家.txt", "r", encoding="utf-8") as f:
        novel_text = f.read()

    pipeline = PipelineOrchestrator()
    print("=== 开始单独测试 Pipeline 抽取与入库（跳过 Worker 视频生成） ===")
    
    # Run the pipeline function, which ends with DB insertion
    res = pipeline.process_novel_to_feishu(
        novel_text=novel_text,
        style_key="anime",
        llm_temperature=0.7,
        top_p=1.0,
        chunk_size=1200
    )
    
    print("\n=== 测试结果 ===")
    import json
    print(json.dumps(res, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    run_test()
