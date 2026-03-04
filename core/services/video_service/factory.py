from .aliyun_service import Wan2_6VideoAPI
from .volcengine_service import VolcengineVideoAPI
from pydantic import BaseModel

class DefaultParams(BaseModel):
    gateway: str

GATEWAY_REGISTRY = {
    "wan_2_6": {
        "class": Wan2_6VideoAPI,
        "schema": DefaultParams, 
    },
    "seedance-1.5-pro": {
        "class": VolcengineVideoAPI,
        "schema": DefaultParams,
    },
    "seedance-1.0-pro-fast": {
        "class": VolcengineVideoAPI,
        "schema": DefaultParams,
    }
}

import logging
from .base import VideoGenerationException
logger = logging.getLogger(__name__)

class VideoAPIWrapper:
    def __init__(self, api_instance):
        self.api_instance = api_instance
        
    async def submit_task(self, prompt: str, **kwargs) -> str:
        try:
            return await self.api_instance.submit_task(prompt, **kwargs)
        except Exception as e:
            err_str = str(e)
            if "400" in err_str or "Bad Request" in err_str or "Error" in err_str:
                logger.error(f"Gateway Error [{self.api_instance.__class__.__name__}]: {e}")
                raise VideoGenerationException(f"Gateway Submission Error: {e}") from e
            raise VideoGenerationException(err_str) from e

    async def check_status(self, task_id: str) -> dict:
        try:
            return await self.api_instance.check_status(task_id)
        except Exception as e:
            logger.error(f"Gateway Status Check Error [{self.api_instance.__class__.__name__}]: {e}")
            raise VideoGenerationException(f"Gateway Status Check Error: {e}") from e

def get_video_api(gateway: str, api_model: str) -> VideoAPIWrapper:
    from core.config import settings
    if "seedance" in gateway.lower() or "volcengine" in gateway.lower() or gateway == "API_MODE":
        key = settings.VOLCENGINE_API_KEY
        if not key or "YOUR" in key:
            raise VideoGenerationException("未配置火山引擎 API Key，请在 .env 中设置 VOLCENGINE_API_KEY")
        return VideoAPIWrapper(VolcengineVideoAPI(api_key=key, model_id=api_model))
    elif "wan2.6" in gateway.lower() or "wan_2_" in gateway.lower() or "aliyun" in gateway.lower():
        key = settings.ALIYUN_API_KEY or settings.DASHSCOPE_API_KEY
        if not key or "YOUR" in key:
            raise VideoGenerationException("未配置阿里云 API Key，请在 .env 中设置 ALIYUN_API_KEY")
        return VideoAPIWrapper(Wan2_6VideoAPI(api_key=key, model=api_model))
    else:
        raise VideoGenerationException(f"Unsupported gateway: {gateway}")
