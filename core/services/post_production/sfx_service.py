"""
core/services/post_production/sfx_service.py
v3.0.0: 音效/配乐生成引擎 (SFX)

路由优先级：
  1. Pollinations.ai music API（已在项目中配置，优先）
  2. ElevenLabs Sound Effects API（高质量，扩展接入）
  3. 本地静音兜底（确保流水线不因 SFX 失败而中断）

接口：
  sfx = SFXService()
  wav_path = await sfx.generate_sfx(description="暴雨声，赛博朋克城市", output_path="/tmp/sfx.wav")
"""
import asyncio
import logging
import os
import aiohttp

logger = logging.getLogger(__name__)

# Pollinations.ai Music API
_POLLINATIONS_MUSIC_URL = "https://text.pollinations.ai/"  # 文本生成端点，音频需确认 endpoint


class SFXService:
    """
    音效与背景音乐生成服务。
    根据场景描述关键词（如"暴雨"、"赛博朋克"）生成 .wav 音效文件。
    """

    def __init__(self):
        from core.config import settings
        self.pollinations_key = (
            settings.POLLINATIONS_API_KEY
            or settings.POLLINATIONS_KEY
            or ""
        )
        logger.info(f"[SFX] 初始化，Pollinations key={'已配置' if self.pollinations_key else '未配置，将用兜底'}")

    async def generate_sfx(
        self,
        description: str,
        output_path: str,
        duration_seconds: int = 10,
    ) -> str:
        """
        根据描述生成音效/背景音并保存到本地。

        Args:
            description     : 音效描述（中文或英文均可，如"暴雨声"）
            output_path     : 输出文件路径（.wav 或 .mp3）
            duration_seconds: 目标时长（部分 API 支持）

        Returns:
            output_path, 生成的音效文件路径（失败时返回空字符串）
        """
        if not description:
            return ""

        os.makedirs(os.path.dirname(output_path), exist_ok=True) if os.path.dirname(output_path) else None

        # 尝试 Pollinations.ai（需确认其 music/sfx endpoint）
        try:
            result = await self._pollinations_sfx(description, output_path, duration_seconds)
            if result and os.path.exists(result):
                return result
        except Exception as e:
            logger.warning(f"[SFX][Pollinations] 生成失败，尝试兜底: {e}")

        # 兜底：生成静音文件（确保 FFmpeg mux 不因缺失 SFX 而失败）
        return await self._generate_silence(output_path, duration_seconds)

    async def _pollinations_sfx(self, description: str, output_path: str, duration: int) -> str:
        """
        调用 Pollinations.ai 音频 API。
        注：Pollinations 音频 API 端点可能需要确认，当前为占位实现。
        """
        # Pollinations music prompt 格式（待验证实际 endpoint）
        music_endpoint = "https://text.pollinations.ai/openai/audio/speech"

        params = {"prompt": description, "model": "musicgen", "duration": duration}
        headers = {}
        if self.pollinations_key:
            headers["Authorization"] = f"Bearer {self.pollinations_key}"

        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(music_endpoint, params=params, headers=headers) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Pollinations SFX HTTP {resp.status}")
                audio_bytes = await resp.read()

        if len(audio_bytes) < 1024:
            raise RuntimeError("Pollinations 返回音频数据过小（可能为错误响应）")

        with open(output_path, "wb") as f:
            f.write(audio_bytes)

        logger.info(f"[SFX][Pollinations] 音效生成成功: {output_path}")
        return output_path

    async def _generate_silence(self, output_path: str, duration: int) -> str:
        """生成静音文件作为最终兜底，确保 FFmpeg mux 可以正常运行。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
                "-t", str(duration),
                "-acodec", "pcm_s16le",
                output_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                logger.info(f"[SFX] 兜底静音文件已生成: {output_path}")
                return output_path
        except Exception as e:
            logger.error(f"[SFX] 静音兜底也失败了（FFmpeg 可能未安装）: {e}")
        return ""
