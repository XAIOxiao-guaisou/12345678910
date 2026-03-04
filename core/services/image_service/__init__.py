"""
core/services/image_service/__init__.py
v3.0.0: 图像生成服务包 — 工厂路由层

用法:
    from core.services.image_service import get_image_service
    svc = get_image_service("wan2.6-t2i")
    result = await svc.generate_image(prompt="...")

模型→服务 路由规则:
  flux-*                → FluxImageService（专属 image2image endpoint）
  wan*/wanx*/stable-*   → AliyunImageService（同步/异步自动选择）
  (未知模型)             → AliyunImageService（用该 model 名称）
"""
from .aliyun_image_service import AliyunImageService, FREE_QUOTA_MODELS
from .flux_image_service import FluxImageService
from .pollinations_service import PollinationsService


def get_image_service(model: str = "wan2.6-t2i"):
    """
    按模型名称返回正确的图像生成服务实例。

    Args:
        model: 模型 ID，参见 core/services/aliyun_model_registry.py

    Returns:
        AliyunImageService | FluxImageService | PollinationsService
    """
    m = model.strip().lower()

    if m.startswith("flux"):
        return FluxImageService(model=model)

    if m.startswith("pollinations") or m == "":
        return PollinationsService()

    # 万相系列（wan2.x / wanx2.x）+  SD 系列 → AliyunImageService
    return AliyunImageService(model=model)


__all__ = [
    "AliyunImageService",
    "FluxImageService",
    "PollinationsService",
    "get_image_service",
    "FREE_QUOTA_MODELS",
]
