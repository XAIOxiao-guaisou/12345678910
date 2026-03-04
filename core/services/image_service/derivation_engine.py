"""
core/services/image_service/derivation_engine.py
v3.0.0-PRO: Image Derivation Engine (I2I Asset Evolution)
"""
import logging
from core.services.image_service.aliyun_image_service import AliyunImageService

logger = logging.getLogger(__name__)

class DerivationEngine:
    """
    负责将实体基准素材（Base Asset）衍生为分镜具体画面（Variant Frame）。
    通过融合视觉锚点提示词与动态动作描述，并固定种子或使用 I2I 垫图来实现一致性。
    """
    def __init__(self, backend="aliyun"):
        self.backend_type = backend
        if backend == "aliyun":
            self.service = AliyunImageService(model="wan2.6-t2i")
        else:
            # 可以根据需要兜底到 Pollinations/Flux
            self.service = AliyunImageService(model="wan2.6-t2i")

    async def derive_scene_frame(
        self,
        entity_id: str,
        base_image_url: str,
        visual_anchor_prompt: str,
        dynamic_description: str,
        base_seed: int,
        image_strength: float = 0.6,
        aspect_ratio: str = "16:9"
    ) -> dict:
        """
        根据基准图和锚点提示词，衍生出特定分镜的帧画面。
        
        Args:
            entity_id: 角色/场景实体ID
            base_image_url: 基准图URL（作为ref_image传入目标API如果支持）
            visual_anchor_prompt: 基准视觉锚点（如发型、服装、材质等恒定特征）
            dynamic_description: 当前分镜的动态动作/环境变化
            base_seed: 基准图的随机种子，用于控制特征的一致性
            image_strength: 垫图参考强度（0.0~1.0），值越小越接近原图（降噪程度）
            
        Returns:
            {"url": str, "seed": int, "status": "success", ...}
        """
        # 1. Prompt 融合机制: “基准图提供结构，锚点词提供细节，衍生图执行动作”
        # 针对极短动态动作的情况加个防御
        visual_anchor = visual_anchor_prompt.strip() if visual_anchor_prompt else "Detailed masterpiece, high resolution"
        dynamic = dynamic_description.strip() if dynamic_description else "in a scene"
        
        composite_prompt = f"{visual_anchor}, {dynamic}"
        
        logger.info(f"🧬 [Derivation Engine] 触发实体 {entity_id} I2I 衍生")
        logger.info(f"   > 锚点前缀: {visual_anchor}")
        logger.info(f"   > 动态动作: {dynamic}")
        logger.info(f"   > 参考垫图: {base_image_url} (Strength: {image_strength}, Seed: {base_seed})")

        # 2. 调用底层大模型生图
        # 注意: wan2.6 目前主要是 T2I 接口支持。我们使用 evolve_image 保持 Seed 一致，
        # 借助 composite_prompt（锚点词前缀）的强度进行伪 I2I。
        if hasattr(self.service, "evolve_image"):
            result = await self.service.evolve_image(
                original_seed=base_seed,
                evolution_prompt=composite_prompt,
                aspect_ratio=aspect_ratio
            )
        else:
            result = await self.service.generate_image(
                prompt=composite_prompt,
                seed=base_seed,
                aspect_ratio=aspect_ratio
            )
            
        return result
