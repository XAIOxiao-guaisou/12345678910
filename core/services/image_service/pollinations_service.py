from core.config import settings
"""
pollinations_service.py — v2.7.0 图像生成服务

职责:
  - 调用 Pollinations.ai Z-Image-Turbo 接口生成静态概念图
  - seed 参数锁定视觉一致性（角色/场景跨章连续）
  - 支持匿名（无 key）与授权（POLLINATIONS_API_KEY）两种模式
  - 内置 2 次自动重试
"""
import os
import asyncio
import logging
import random
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()  # 自动读取项目根目录 .env 文件
except ImportError:
    pass  # python-dotenv 未安装时降级，静默依赖系统环境变量

logger = logging.getLogger(__name__)

try:
    import httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False
    logger.warning("⚠️ httpx 未安装，PollinationsService 不可用。请运行: pip install httpx")


class PollinationsService:
    """
    Pollinations.ai Z-Image-Turbo 异步图像生成客户端。

    使用方式:
        svc = PollinationsService()
        result = await svc.generate_image(prompt="...", seed=42)
        # result: {"url": str, "seed": int, "status": "success"} 或 {"status": "error", "error": str}
    """

    BASE_URL = "https://image.pollinations.ai"
    MODEL = "flux"          # Pollinations 公开 turbo 模型标识（z-image-turbo 的别名）
    DEFAULT_WIDTH = 1024
    DEFAULT_HEIGHT = 1024
    MAX_RETRY = 2
    RETRY_DELAY = 3.0       # 秒

    def __init__(self):
        # 兼容多种命名规范：
        #   POLLINATIONS_API_KEY  — 标准命名
        #   pollinations.ai       — 用户原始方案命名
        #   POLLINATIONS_KEY      — 简写兼容
        self.api_key: str = (
            settings.POLLINATIONS_API_KEY
            or ""
            or settings.POLLINATIONS_KEY
        )
        if self.api_key:
            logger.info("✅ [Pollinations] API Key 已加载（授权模式）")
        else:
            logger.info("🔓 [Pollinations] 未配置 Key，走安全匹名通道")

    # ------------------------------------------------------------------
    # 核心生成接口
    # ------------------------------------------------------------------
    async def generate_image(
        self,
        prompt: str,
        seed: Optional[int] = None,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        negative_prompt: str = "low quality, blurry, distorted, watermark, text, logo",
    ) -> dict:
        """
        生成图像。

        Args:
            prompt:           英文生图提示词
            seed:             可选固定种子（None = 随机分配，保存后写回飞书供下次复用）
            width / height:   输出尺寸，默认 1024×1024
            negative_prompt:  负向提示词

        Returns:
            成功: {"url": str, "seed": int, "status": "success", "model": str}
            失败: {"status": "error", "error": str}
        """
        if not _HTTPX_OK:
            return {"status": "error", "error": "httpx 未安装"}

        resolved_seed = seed if seed is not None else random.randint(1, 2**31 - 1)

        # Pollinations GET 接口：GET /prompt/{encoded_prompt}?model=...&seed=...
        # 文档: https://pollinations.ai/docs
        import urllib.parse
        encoded_prompt = urllib.parse.quote(prompt, safe="")
        url = f"{self.BASE_URL}/prompt/{encoded_prompt}"

        params = {
            "model": self.MODEL,
            "seed": resolved_seed,
            "width": width,
            "height": height,
            "nologo": "true",
            "enhance": "false",   # 关闭自动增强，保证 seed 稳定复现
        }
        if negative_prompt:
            params["negative"] = negative_prompt

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for attempt in range(1, self.MAX_RETRY + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=90.0,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url, params=params, headers=headers)

                if resp.status_code == 200:
                    # Pollinations 直接返回图片二进制或重定向到 CDN URL
                    # 最终 URL（含 seed）是视觉状态的持久标识
                    final_url = str(resp.url)
                    logger.info(
                        f"🖼️ [Pollinations] 生成成功 seed={resolved_seed} "
                        f"url={final_url[:80]}..."
                    )
                    return {
                        "url": final_url,
                        "seed": resolved_seed,
                        "status": "success",
                        "model": self.MODEL,
                    }
                else:
                    err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    logger.warning(f"⚠️ [Pollinations] 第{attempt}次失败: {err}")
            except Exception as e:
                logger.warning(f"⚠️ [Pollinations] 第{attempt}次异常: {e}")

            if attempt < self.MAX_RETRY:
                await asyncio.sleep(self.RETRY_DELAY)

        return {"status": "error", "error": f"Pollinations 请求失败（已重试 {self.MAX_RETRY} 次）"}

    # ------------------------------------------------------------------
    # 便捷方法：种子迭代（保留原风格 + 注入新 prompt）
    # ------------------------------------------------------------------
    async def evolve_image(
        self,
        original_seed: int,
        evolution_prompt: str,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
    ) -> dict:
        """
        使用原 seed 对已有图像进行视觉演进（保留风格，更新内容）。
        evolution_prompt 应包含对变化的明确描述（如：受伤后右臂绑绷带）。
        """
        return await self.generate_image(
            prompt=evolution_prompt,
            seed=original_seed,   # 固定原 seed 保证美学延续
            width=width,
            height=height,
        )
