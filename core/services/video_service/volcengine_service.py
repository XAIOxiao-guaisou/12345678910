from core.config import settings
import os
import asyncio
import logging
from typing import Dict
from volcenginesdkarkruntime import Ark
from .base import BaseVideoGeneratorAPI

logger = logging.getLogger(__name__)

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

    async def submit_task(self, prompt: str, image_url: str = "", **kwargs) -> str:
        """
        提交视频生成任务。
        v2.8.0: image_url 从 **kwargs 升级为显式参数，支持 Seedance I2V 模式。
        Args:
            prompt    : 视频文字描述
            image_url : 非空时，构建 I2V 混合内容（image + text），走图生视频端点
        """
        if not self.client:
            raise ValueError("Volcengine API Key not configured.")

        # 构建内容列表（I2V 时，image 在 text 之前）
        if image_url:
            content = [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": prompt},
            ]
            logger.info(
                f"[Seedance I2V] Submitting task (Model: {self.model_id}). "
                f"image_url={image_url[:60]}... prompt={prompt[:40]}..."
            )
        else:
            content = [{"type": "text", "text": prompt}]
            logger.info(
                f"[Seedance T2V] Submitting task (Model: {self.model_id}). Prompt: {prompt[:50]}..."
            )

        try:
            response = await asyncio.to_thread(
                self.client.content_generation.tasks.create,
                model=self.model_id,
                content=content,
            )
            task_id = response.id
            logger.info(f"Task submitted successfully. Task ID: {task_id}")
            return task_id
        except Exception as e:
            logger.error(f"Failed to submit task to Volcengine: {e}")
            raise

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
