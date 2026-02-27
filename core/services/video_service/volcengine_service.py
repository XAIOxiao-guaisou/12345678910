import os
import asyncio
import logging
from typing import Dict
from volcenginesdkarkruntime import Ark
from .base import BaseVideoGeneratorAPI
from core.config import settings
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception

logger = logging.getLogger(__name__)

def _is_volc_retryable(e):
    err_str = str(e).lower()
    try:
        from volcenginesdkarkruntime._exceptions import ArkAPIConnectionError, ArkRateLimitError
        if isinstance(e, (ArkAPIConnectionError, ArkRateLimitError)):
            return True
    except ImportError:
        pass
        return True
    if getattr(e, "status_code", 500) in (400, 401, 403, 404):
        return False
    if "rate" in err_str or "limit" in err_str or "timeout" in err_str or "connection" in err_str or "50" in err_str:
        return True
    return False

class VolcengineVideoAPI(BaseVideoGeneratorAPI):
    def __init__(self, api_key: str = None, model_id: str = "doubao-seedance-1-5-pro-251215"):
        # 如果没有传入 API Key，尝试从环境变量获取
        api_key = api_key or settings.VOLCENGINE_API_KEY
        super().__init__(api_key)
        self.model_id = model_id
        if not self.api_key:
            logger.warning("Volcengine API Key is missing. Please set VOLCENGINE_API_KEY.")
            self.client = None
        else:
            self.client = Ark(api_key=self.api_key)

    @retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(5), retry=retry_if_exception(_is_volc_retryable))
    async def submit_task(self, prompt: str, **kwargs) -> str:
        if not self.client:
            raise ValueError("Volcengine API Key not configured.")
        
        logger.info(f"Submitting video task to Volcengine (Model: {self.model_id}). Prompt: {prompt[:50]}...")
        
        # Volcengine SDK is synchronous for this endpoint by default (unless using AsyncArk),
        # but the task submit API returns quickly with a task ID.
        try:
            # We can use asyncio.to_thread if we want to avoid blocking the event loop
            response = await asyncio.to_thread(
                self.client.content_generation.tasks.create,
                model=self.model_id,
                content=[
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            )
            task_id = response.id
            logger.info(f"Task submitted successfully. Task ID: {task_id}")
            return task_id
        except Exception as e:
            logger.error(f"Failed to submit task to Volcengine: {e}")
            raise

    @retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(5), retry=retry_if_exception(_is_volc_retryable))
    async def check_status(self, task_id: str) -> Dict:
        if not self.client:
            raise ValueError("Volcengine API Key not configured.")
            
        try:
            response = await asyncio.to_thread(
                self.client.content_generation.tasks.get,
                task_id=task_id
            )
            # Volcengine uses 'status' (e.g., "running", "succeeded", "failed", "queued")
            # And 'content' containing the results.
            status = response.status
            
            result = {
                "status": status,
                "video_url": None,
                "error": None
            }
            
            if status == "succeeded":
                # Extract URL from successful response payload
                # Note: This structure differs by platform, usually it's under response.content
                # We need to gracefully fetch the video URL
                if getattr(response, 'content', None) and getattr(response.content, 'video_url', None):
                    result["video_url"] = response.content.video_url
                else:
                    logger.warning(f"Task {task_id} succeeded but could not parse video URL. Raw content: {getattr(response, 'content', 'No content')}")
                    
            elif status == "failed":
                # Extract error message
                result["error"] = getattr(response.error, 'message', "Unknown error")
                
            return result
        except Exception as e:
            logger.error(f"Failed to check status for Volcengine task {task_id}: {e}")
            raise
