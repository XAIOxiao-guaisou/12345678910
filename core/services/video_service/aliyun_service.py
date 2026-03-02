"""
aliyun_service.py — v2.7.2 阿里云 DashScope 视频生成 API

同时支持:
  - wan2.6-i2v  图生视频（image-to-video）[默认/当前使用]
  - wan2.6-t2v  文生视频（text-to-video）[备用]

i2v 接口规范 (DashScope):
  POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
  Header: X-DashScope-Async: enable
  input:
    image_url  : str   必填，公开可访问 URL 或 base64（格式 data:;base64,...）
    prompt     : str   可选，文字引导描述
  parameters:
    resolution : str   可选，"1280*720"（默认按图像宽高比自动适配）
    duration   : int   可选，视频秒数（默认 5）
  状态查询: GET https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}
  SUCCEEDED 后: output.video_url 获取视频下载链接

t2v 接口（备用，同 endpoint，去掉 image_url）不变。
"""
import os
import logging
import asyncio
import aiohttp
from core.services.video_service.base import BaseVideoGeneratorAPI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


class Wan2_6VideoAPI(BaseVideoGeneratorAPI):
    """
    阿里云 DashScope Wan2.6 视频生成 API（i2v/t2v 双模式）。
    默认使用 wan2.6-i2v（图生视频），也可通过 model 参数切换到 t2v。
    """

    SUBMIT_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "video-generation/video-synthesis"
    )
    TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    def __init__(self, api_key: str = None, model: str = "wan2.6-i2v"):
        self.model = model
        self.api_key = (
            api_key
            or os.environ.get("ALIYUN_API_KEY", "")
            or os.environ.get("DASHSCOPE_API_KEY", "")
        )
        if not self.api_key:
            logger.warning(
                f"⚠️ [{self.model}] ALIYUN_API_KEY 未配置，视频生成将失败"
            )
        else:
            logger.info(f"✅ [{self.model}] API Key 已加载")

        # i2v 模式标记
        self.is_i2v = "i2v" in model.lower()

        self._headers = {
            "X-DashScope-Async": "enable",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def submit_task(self, prompt: str, image_url: str = "", **kwargs) -> str:
        """
        提交视频生成任务。

        Args:
            prompt    : 文字描述（i2v 时作为引导，t2v 时为核心输入）
            image_url : i2v 模式必须提供，图像公开 URL 或 base64

        Returns:
            task_id (str) 供后续状态轮询
        """
        if self.is_i2v:
            if not image_url:
                raise ValueError(
                    f"[{self.model}] i2v 模式必须提供 image_url 参数"
                )
            input_payload = {
                "img_url": image_url,
                "prompt": prompt,          # 可选但推荐：增强画面动态一致性
            }
        else:
            # t2v 模式：仅 prompt
            input_payload = {"prompt": prompt}

        params = {}
        if "duration" in kwargs: params["duration"] = kwargs["duration"]
        if "resolution" in kwargs: params["resolution"] = kwargs["resolution"]
        
        payload = {
            "model": self.model,
            "input": input_payload,
            "parameters": params,
        }

        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                self.SUBMIT_URL, headers=self._headers, json=payload
            ) as resp:
                data = await resp.json()

        # DashScope 错误码检查
        code = data.get("code", "")
        if code and code not in ("OK", 0, ""):
            raise Exception(f"DashScope Submit Error: {data}")

        task_id = data.get("output", {}).get("task_id", "")
        if not task_id:
            raise Exception(f"DashScope 未返回 task_id: {data}")

        logger.info(f"[{self.model}] 任务已提交 task_id={task_id}")
        return task_id

    async def check_status(self, task_id: str) -> dict:
        """
        查询任务状态。

        Returns:
            {"status": "succeeded"|"failed"|"running"|"queued", "video_url": str}
        """
        url = self.TASK_URL.format(task_id=task_id)
        check_headers = {"Authorization": f"Bearer {self.api_key}"}

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=check_headers) as resp:
                data = await resp.json()

        output = data.get("output", {})
        status_code = output.get("task_status", "UNKNOWN").upper()

        # SUCCEEDED: i2v/t2v 的视频 URL 字段名均为 video_url（DashScope 规范统一）
        if status_code == "SUCCEEDED":
            video_url = (
                output.get("video_url")
                or output.get("results", [{}])[0].get("video_url", "")
            )
            return {"status": "succeeded", "video_url": video_url}
        elif status_code == "FAILED":
            err_msg = output.get("message", "Unknown DashScope error")
            return {"status": "failed", "error": err_msg}
        elif status_code in ("PENDING", "RUNNING", "QUEUED"):
            return {"status": "running"}
        else:
            return {"status": "unknown", "raw": status_code}
