"""
core/services/video_service/__init__.py
v3.0.0: 视频生成服务包 — 工厂路由层

用法:
    from core.services.video_service import get_video_service
    svc = get_video_service("wan2.6-i2v-flash")
    task_id = await svc.submit_task(prompt="...", image_url="...")

路由规则:
  wan* / wanx*  → Wan2_6VideoAPI
  seedance*     → VolcengineVideoAPI (火山引擎)
  (未知)         → Wan2_6VideoAPI (默认)
"""
from .aliyun_service import Wan2_6VideoAPI

try:
    from .volcengine_service import VolcengineVideoAPI
    _HAS_VOLCENGINE = True
except ImportError:
    _HAS_VOLCENGINE = False


def get_video_service(model: str = "wan2.6-i2v-flash", **kwargs):
    """
    按模型名称返回正确的视频生成服务实例。

    Args:
        model  : 模型 ID，参见 core/services/aliyun_model_registry.ALIYUN_VIDEO_MODELS
        **kwargs: 透传给服务构造（如 audio=True, resolution="1080P"）

    Returns:
        Wan2_6VideoAPI | VolcengineVideoAPI
    """
    m = model.strip().lower()

    if m.startswith("seedance") or m.startswith("volcengine"):
        if _HAS_VOLCENGINE:
            return VolcengineVideoAPI(model=model, **kwargs)
        raise ImportError("VolcengineVideoAPI 未安装或 volcengine_service.py 不存在")

    # 默认：阿里云万相系列
    return Wan2_6VideoAPI(model=model, **kwargs)


__all__ = ["Wan2_6VideoAPI", "get_video_service"]
if _HAS_VOLCENGINE:
    __all__.append("VolcengineVideoAPI")
