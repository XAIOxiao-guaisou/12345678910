"""
aliyun_image_service.py — v2.7.1 阿里云百炼文生图服务

使用 qwen-image-plus 文生图模型，复用 .env 中已有的 ALIYUN_API_KEY。

API 文档: https://help.aliyun.com/zh/model-studio/developer-reference/wanx-text-to-image
接口: POST https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis
"""
import os
import asyncio
import logging
import aiohttp
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


class AliyunImageService:
    """
    阿里云百炼 Wanx2.1 文生图异步客户端。
    与 PollinationsService 接口完全兼容（同样返回 {"url", "seed", "status"}）。
    
    模型: wanx2.1-t2i-turbo（速度最快，质量优）
    """

    SUBMIT_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "text2image/image-synthesis"
    )
    TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    MODEL = "qwen-image-plus"
    MAX_POLL = 30       # 最多轮询 30 次（每次 3 秒 = 90 秒超时）
    POLL_INTERVAL = 3   # 秒

    def __init__(self):
        self.api_key = (
            os.getenv("ALIYUN_API_KEY", "")
            or os.getenv("DASHSCOPE_API_KEY", "")
        )
        if not self.api_key:
            logger.warning("⚠️ [AliyunImage] ALIYUN_API_KEY 未配置，文生图将失败")
        else:
            logger.info(f"✅ [AliyunImage] API Key 已加载，模型={self.MODEL}")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",   # 异步模式
        }

    async def generate_image(
        self,
        prompt: str,
        seed: int = None,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: str = "low quality, blurry, distorted, watermark, text, logo",
    ) -> dict:
        """
        提交文生图任务并轮询结果。

        Returns:
            成功: {"url": str, "seed": int, "status": "success"}
            失败: {"status": "error", "error": str}
        """
        if not self.api_key:
            return {"status": "error", "error": "ALIYUN_API_KEY 未配置"}

        import random
        resolved_seed = seed if seed is not None else random.randint(1, 2**31 - 1)

        payload = {
            "model": self.MODEL,
            "input": {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
            },
            "parameters": {
                "size": f"{width}*{height}",
                "seed": resolved_seed,
                "n": 1,
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                # ① 提交任务
                async with session.post(
                    self.SUBMIT_URL,
                    headers=self._headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json()

                if data.get("code") and data["code"] != "OK" and data.get("code") != 0:
                    err = data.get("message", str(data))
                    logger.error(f"[AliyunImage] 提交失败: {err}")
                    return {"status": "error", "error": err}

                task_id = data.get("output", {}).get("task_id")
                if not task_id:
                    return {"status": "error", "error": f"未获取到 task_id: {data}"}

                logger.info(f"🎨 [AliyunImage] 任务已提交 task_id={task_id}")

                # ② 轮询结果
                check_headers = {
                    "Authorization": f"Bearer {self.api_key}",
                }
                strike_count = 0
                max_strikes = 5

                for attempt in range(self.MAX_POLL):
                    await asyncio.sleep(self.POLL_INTERVAL)
                    try:
                        async with session.get(
                            self.TASK_URL.format(task_id=task_id),
                            headers=check_headers,
                            timeout=aiohttp.ClientTimeout(total=20),
                        ) as poll_resp:
                            if poll_resp.status in (502, 503, 504) or poll_resp.status >= 500:
                                raise aiohttp.ClientResponseError(
                                    poll_resp.request_info, 
                                    poll_resp.history, 
                                    status=poll_resp.status, 
                                    message="Gateway/Server Error"
                                )
                            poll_data = await poll_resp.json()
                            strike_count = 0 # reset strikes on success
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        strike_count += 1
                        wait_sec = min(5 * (2 ** strike_count), 60)
                        logger.warning(
                            f"📡 [AliyunImage] 轮询网络异常 ({e.__class__.__name__}): {e}, "
                            f"strike={strike_count}/{max_strikes}, 退避 {wait_sec} 秒"
                        )
                        if strike_count >= max_strikes:
                            return {"status": "error", "error": f"连续轮询失败超限: {e}"}
                        await asyncio.sleep(wait_sec)
                        continue

                    output = poll_data.get("output", {})
                    task_status = output.get("task_status", "UNKNOWN")

                    if task_status == "SUCCEEDED":
                        results = output.get("results", [])
                        if results:
                            img_url = results[0].get("url", "")
                            logger.info(
                                f"✅ [AliyunImage] 生成成功 seed={resolved_seed} "
                                f"url={img_url[:60]}..."
                            )
                            return {
                                "url": img_url,
                                "seed": resolved_seed,
                                "status": "success",
                                "model": self.MODEL,
                            }
                        return {"status": "error", "error": "SUCCEEDED 但无 results"}

                    elif task_status in ("FAILED", "CANCELED"):
                        err = output.get("message", "未知错误")
                        logger.error(f"❌ [AliyunImage] 任务失败: {err}")
                        return {"status": "error", "error": err}

                    elif task_status in ("PENDING", "RUNNING"):
                        logger.debug(
                            f"⏳ [AliyunImage] 轮询 {attempt+1}/{self.MAX_POLL} "
                            f"状态={task_status}"
                        )
                    else:
                        logger.warning(f"[AliyunImage] 未知状态: {task_status}")

                return {"status": "error", "error": f"轮询超时 ({self.MAX_POLL}次)"}

        except Exception as e:
            logger.error(f"❌ [AliyunImage] 异常: {e}")
            return {"status": "error", "error": str(e)}

    async def evolve_image(
        self,
        original_seed: int,
        evolution_prompt: str,
        width: int = 1024,
        height: int = 1024,
    ) -> dict:
        """固定原 seed 进行视觉演进（保留美学风格，更新内容）。"""
        return await self.generate_image(
            prompt=evolution_prompt,
            seed=original_seed,
            width=width,
            height=height,
        )
