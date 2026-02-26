import os
import time
import json
import logging
import asyncio
import aiohttp
from core.services.video_service.base import BaseVideoGeneratorAPI

logger = logging.getLogger(__name__)

class Wan2_6VideoAPI(BaseVideoGeneratorAPI):
    """
    Alien DashScope 视频生成 API (支持 wan2.6-t2v)
    使用 aiohttp 异步请求，符合框架标准。
    """
    def __init__(self, api_key: str = None, model: str = "wan2.6-t2v"):
        self.model = model
        self.api_key = api_key or os.environ.get(self.model) or os.environ.get(self.model.replace('.', '_').replace('-', '_').upper())
        if not self.api_key:
            # Fallback direct key mapping if env var fails
            pass
            
        self.submit_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
        self.headers = {
            "X-DashScope-Async": "enable",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def submit_task(self, prompt: str, **kwargs) -> str:
        """异步提交生成任务"""
        payload = {
            "model": self.model,
            "input": {
                "prompt": prompt
            },
            "parameters": {}
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(self.submit_url, headers=self.headers, json=payload) as resp:
                data = await resp.json()
                if "code" in data and data["code"]:
                    raise Exception(f"DashScope Submit Error: {data}")
                return data.get("output", {}).get("task_id")

    async def check_status(self, task_id: str) -> dict:
        """异步查询任务状态"""
        url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        check_headers = {"Authorization": f"Bearer {self.api_key}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=check_headers) as resp:
                data = await resp.json()
                output = data.get("output", {})
                
                status_code = output.get("task_status", "UNKNOWN")
                
                if status_code == "SUCCEEDED":
                    return {"status": "succeeded", "video_url": output.get("video_url")}
                elif status_code == "FAILED":
                    return {"status": "failed", "error": output.get("message", "Unknown DashScope error")}
                elif status_code in ["PENDING", "RUNNING"]:
                    return {"status": "running"}
                else:
                    return {"status": "unknown"}
