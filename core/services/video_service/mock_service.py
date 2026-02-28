import asyncio
import uuid
import logging
import os
from .base import BaseVideoGeneratorAPI

logger = logging.getLogger(__name__)

class MockVideoService(BaseVideoGeneratorAPI):
    def __init__(self, api_key: str = "MOCK_KEY", gateway_name: str = "unknown"):
        super().__init__(api_key)
        self.gateway_name = gateway_name
        self.mock_jobs = {}
        
    async def submit_task(self, prompt: str, **kwargs) -> str:
        task_id = f"mock-{uuid.uuid4().hex[:8]}"
        logger.warning(f"🧪 [MockService - {self.gateway_name}] MOCK_MODE 已拦截真实 API，启动虚拟生成请求: {task_id}")
        
        # Remove massive log spam for every field, just keep essential overview
        logger.info(f"🧪 Payload 预览:\n> Prompt: {prompt[:50]}...\n> Kwargs (Keys): {list(kwargs.keys())}")
        
        self.mock_jobs[task_id] = {
            "status": "processing",
            "prompt": prompt,
            "config": kwargs,
            "gateway": self.gateway_name,
            "polls": 0
        }
        return task_id

    async def check_status(self, task_id: str) -> dict:
        if task_id not in self.mock_jobs:
             return {"status": "failed", "error": "Mock task not found"}
             
        job = self.mock_jobs[task_id]
        job["polls"] += 1
        
        # Simulate realistic polling delay (e.g. 1-2 intervals of 10s wait in worker)
        if job["polls"] < 2:
            logger.info(f"🧪 [MockService] 虚拟任务 {task_id} 正在生成中... (模拟延迟)")
            return {"status": "processing"}
            
        logger.info(f"🧪 [MockService] 虚拟任务 {task_id} 模拟生成成功！")
        # Return success with a mock URL and inject custom job data for billing decoupling
        return {
            "status": "success", 
            "video_url": f"mock://video_url_for_{task_id}",
            "mock_data": job
        }

    async def download_video(self, url: str, filepath: str) -> str:
        logger.info(f"🧪 [MockService] 拦截下载请求，创建本地空文件: {filepath}")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        # Touch file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("Sandbox Mock Video File")
            
        return filepath
