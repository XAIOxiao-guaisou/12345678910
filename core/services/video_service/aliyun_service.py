import os
import time
import json
import logging
import asyncio
import aiohttp
from core.services.video_service.base import BaseVideoGeneratorAPI
from core.config import settings
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception

logger = logging.getLogger(__name__)

def _is_dashscope_retryable(e):
    err_str = str(e).lower()
    if isinstance(e, aiohttp.ClientResponseError):
        if e.status in (400, 401, 403, 404):
            return False
    if isinstance(e, (aiohttp.ClientError, asyncio.TimeoutError)):
        return True
    if "throttling" in err_str or "timeout" in err_str or "limit" in err_str or "50" in err_str:
        return True
    return False

class Wan2_6VideoAPI(BaseVideoGeneratorAPI):
    """
    Alien DashScope 视频生成 API (支持 wan2.6-t2v)
    使用 aiohttp 异步请求，符合框架标准。
    """
    def __init__(self, api_key: str | None = None, model: str = "wan2.6-t2v"):
        self.model = model
        self.api_key = api_key or settings.ALIYUN_API_KEY or os.environ.get(self.model) or os.environ.get(self.model.replace('.', '_').replace('-', '_').upper())
        if not self.api_key:
            # Fallback direct key mapping if env var fails
            pass
            
        self.submit_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
        self.headers = {
            "X-DashScope-Async": "enable",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    @retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(5), retry=retry_if_exception(_is_dashscope_retryable))
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
                resp.raise_for_status()
                data = await resp.json()
                if "code" in data and data["code"]:
                    raise Exception(f"DashScope Submit Error: {data}")
                return data.get("output", {}).get("task_id", "")
        return ""

    @retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(5), retry=retry_if_exception(_is_dashscope_retryable))
    async def check_status(self, task_id: str) -> dict:
        """异步查询任务状态"""
        url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        check_headers = {"Authorization": f"Bearer {self.api_key}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=check_headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
                output = data.get("output", {})
                
                status_code = output.get("task_status", "UNKNOWN")
                
                if status_code == "SUCCEEDED":
                    video_url = output.get("video_url")
                    return {"status": "success", "video_url": video_url}
                elif status_code in ["FAILED", "CANCELED"]:
                    return {"status": "failed", "error": data.get("message", "Task failed")}
                elif status_code in ["PENDING", "RUNNING"]:
                    return {"status": "running"}
                else:
                    return {"status": "unknown"}
        return {"status": "unknown"}
