"""
core/services/post_production/audio_orchestrator.py
v3.0.0: 音频制作编排器（Audio Production Orchestrator）

职责：
  作为 Stage5 PostMuxStage 与底层 TTS/SFX 服务之间的中间层。
  提供两个高层接口：
    1. generate_narration() — 用 DeepSeek + J2 模板生成旁白文本 → TTSService 合成
    2. generate_sfx_prompt()— 用 DeepSeek + J2 模板生成音效描述 → SFXService 生成

  调用流程：
    Scene dict
      ├── generate_narration(scene) → DeepSeek[audio_tts_narration.j2] → TTS → .mp3
      └── generate_sfx_prompt(scene) → DeepSeek[audio_sfx_prompt.j2] → SFX → .wav

  与 Stage5 解耦：Stage5 只需 await orchestrator.produce(scene, i)
"""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class AudioOrchestrator:
    """
    音频制作编排器。
    单实例在 Stage5 初始化时创建，对所有集数复用。
    """

    def __init__(self, tts_service, sfx_service, output_dir: str = "/tmp"):
        self.tts = tts_service
        self.sfx = sfx_service
        self.output_dir = output_dir
        self._prompts_dir = Path(__file__).parent.parent.parent.parent / "prompts"

    async def produce(
        self,
        scene: dict,
        ep_index: int,
        ep_name: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        为单集场景生成 TTS 人声和 SFX 音效。

        Returns:
            (tts_path, sfx_path) — 文件路径，失败时对应值为 None
        """
        tts_path, sfx_path = await asyncio.gather(
            self._produce_tts(scene, ep_index, ep_name),
            self._produce_sfx(scene, ep_index, ep_name),
        )
        return tts_path, sfx_path

    async def _produce_tts(
        self, scene: dict, ep_index: int, ep_name: str
    ) -> Optional[str]:
        """
        生成旁白：DeepSeek 精炼文本 → TTS 合成 → .mp3
        若飞书表已有「旁白与台词」字段，直接使用（跳过 DeepSeek）。
        """
        # 优先使用飞书预填文本
        narration = scene.get("旁白与台词", "").strip()

        # 降级：从 audio_prompt 字段提取（Stage2 直接写入的原始旁白）
        if not narration:
            narration = scene.get("audio_prompt", "").strip()

        # 再降级：用 DeepSeek + J2 模板生成
        if not narration:
            narration = await asyncio.to_thread(
                self._deepseek_generate, "audio_tts_narration.j2", scene
            )

        if not narration:
            logger.info(f"[AudioOrch][ep{ep_index+1}] 无旁白内容，跳过 TTS")
            return None

        out_path = os.path.join(self.output_dir, f"{ep_name}_tts.mp3")
        try:
            result = await self.tts.synthesize(text=narration, output_path=out_path)
            return result if result and os.path.exists(result) else None
        except Exception as e:
            logger.error(f"[AudioOrch][ep{ep_index+1}] TTS 合成失败: {e}")
            return None

    async def _produce_sfx(
        self, scene: dict, ep_index: int, ep_name: str
    ) -> Optional[str]:
        """
        生成音效：飞书预填音效关键词 / DeepSeek + J2 生成 → SFX API → .wav
        SFX 失败为非致命错误，返回 None 即可（mux 时跳过该轨）。
        """
        if not self.sfx:
            return None

        # 优先使用飞书预填文本
        sfx_desc = scene.get("音效提示词", "").strip()

        # 降级：DeepSeek 生成
        if not sfx_desc:
            sfx_desc = await asyncio.to_thread(
                self._deepseek_generate, "audio_sfx_prompt.j2", scene
            )

        if not sfx_desc:
            return None

        out_path = os.path.join(self.output_dir, f"{ep_name}_sfx.wav")
        try:
            result = await self.sfx.generate_sfx(
                description=sfx_desc, output_path=out_path
            )
            return result if result and os.path.exists(result) else None
        except Exception as e:
            logger.warning(f"[AudioOrch][ep{ep_index+1}] SFX 生成失败（非致命）: {e}")
            return None

    def _deepseek_generate(self, template_file: str, scene: dict) -> str:
        """
        同步：用 Jinja2 渲染模板并调用 DeepSeek 生成文本。
        在 asyncio.to_thread 中执行。
        """
        try:
            from jinja2 import Environment, FileSystemLoader
            from core.services.llm_service.deepseek_service import DeepSeekService
            env = Environment(
                loader=FileSystemLoader(str(self._prompts_dir)),
                trim_blocks=True, lstrip_blocks=True,
            )
            tpl = env.get_template(template_file)
            prompt_text = tpl.render(scene=scene)
            result = DeepSeekService.call_deepseek(prompt_text)
            return result.strip() if result else ""
        except Exception as e:
            logger.warning(f"[AudioOrch] {template_file} DeepSeek 生成失败: {e}")
            return ""
