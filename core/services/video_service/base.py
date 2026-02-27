from abc import ABC, abstractmethod
import aiohttp
import logging

logger = logging.getLogger(__name__)

class BaseVideoGeneratorAPI(ABC):
    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    async def submit_task(self, prompt: str, **kwargs) -> str:
        """
        提交生成任务到平台 API
        返回: Task ID (字符串)
        """
        return ""

    @abstractmethod
    async def check_status(self, task_id: str) -> dict:
        """
        根据 Task ID 查询生成进度
        返回: {"status": "processing"|"success"|"failed", "video_url": "...", "error": "..."}
        """
        return {}

    async def download_video(self, url: str, filepath: str) -> str:
        """
        通用视频下载逻辑
        """
        logger.info(f"正在下载视频到: {filepath}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        with open(filepath, 'wb') as f:
                            f.write(await resp.read())
                        logger.info(f"✅ 视频已成功下载至本地")
                        return filepath
                    else:
                        logger.error(f"视频下载失败，HTTP状态码: {resp.status}")
                        return url
        except Exception as e:
            logger.error(f"下载视频时发生异常: {e}")
            return url
