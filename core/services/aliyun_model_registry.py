"""
core/services/aliyun_model_registry.py
v3.0.0: 阿里云 DashScope 全量免费配额模型注册表

该文件作为模型元数据的单一事实来源（Single Source of Truth）。
服务层根据此表做模型路由决策。配额信息来源：阿里云官方定价页（2025.03 采集）。
"""

# ══════════════════════════════════════════════════════
# 图像生成模型（中国内地部署，有新人免费额度）
# ══════════════════════════════════════════════════════
ALIYUN_IMAGE_MODELS: dict[str, dict] = {

    # ── 万相2.6（新同步接口）──────────────────────────────────────────────
    "wan2.6-t2i": {
        "desc":       "万相2.6 旗舰，同步接口直出，16:9=1696*960",
        "endpoint":   "sync_multimodal",   # 走 multimodal-generation/generation
        "free_quota": 50,
        "price":      "0.20元/张",
        "recommended": True,
        "service":    "AliyunImageService",
    },

    # ── 万相2.5（异步接口）───────────────────────────────────────────────
    "wan2.5-t2i-preview": {
        "desc":       "万相2.5 preview，灵活尺寸，宽高比约束更宽",
        "endpoint":   "async_text2image",
        "free_quota": 50,
        "price":      "0.20元/张",
        "recommended": True,
        "service":    "AliyunImageService",
    },

    # ── 万相2.2（异步接口）───────────────────────────────────────────────
    "wan2.2-t2i-flash": {
        "desc":       "万相2.2极速，速度提升50%",
        "endpoint":   "async_text2image",
        "free_quota": 100,
        "price":      "0.14元/张",
        "recommended": False,
        "service":    "AliyunImageService",
    },
    "wan2.2-t2i-plus": {
        "desc":       "万相2.2专业版，写实质感全面升级",
        "endpoint":   "async_text2image",
        "free_quota": 100,
        "price":      "0.20元/张",
        "recommended": False,
        "service":    "AliyunImageService",
    },

    # ── 万相2.1（异步接口）───────────────────────────────────────────────
    "wanx2.1-t2i-turbo": {
        "desc":       "万相2.1极速，生成速度快，大额度首选",
        "endpoint":   "async_text2image",
        "free_quota": 500,
        "price":      "0.14元/张",
        "recommended": False,
        "service":    "AliyunImageService",
    },
    "wanx2.1-t2i-plus": {
        "desc":       "万相2.1专业版，细节丰富",
        "endpoint":   "async_text2image",
        "free_quota": 500,
        "price":      "0.20元/张",
        "recommended": False,
        "service":    "AliyunImageService",
    },

    # ── Stable Diffusion（异步接口，免费体验）────────────────────────────
    "stable-diffusion-3.5-large": {
        "desc":       "SD3.5大，8亿参数MMDiT，100万像素，目前免费体验",
        "endpoint":   "async_text2image",
        "free_quota": 500,
        "price":      "免费体验",
        "recommended": False,
        "service":    "AliyunImageService",
    },
    "stable-diffusion-3.5-large-turbo": {
        "desc":       "SD3.5大Turbo，ADD加速版，目前免费体验",
        "endpoint":   "async_text2image",
        "free_quota": 500,
        "price":      "免费体验",
        "recommended": False,
        "service":    "AliyunImageService",
    },
    "stable-diffusion-xl": {
        "desc":       "SDXL，业界SOTA，支持高分辨率生成",
        "endpoint":   "async_text2image",
        "free_quota": 500,
        "price":      "免费体验",
        "recommended": False,
        "service":    "AliyunImageService",
    },
    "stable-diffusion-v1.5": {
        "desc":       "SD v1.5，基础版，广泛兼容",
        "endpoint":   "async_text2image",
        "free_quota": 500,
        "price":      "免费体验",
        "recommended": False,
        "service":    "AliyunImageService",
    },

    # ── FLUX（专属接口 image2image/image-synthesis）──────────────────────
    "flux-merged": {
        "desc":       "FLUX融合版，兼顾深度与速度，目前免费体验",
        "endpoint":   "flux_image2image",
        "free_quota": 100,
        "price":      "免费体验",
        "recommended": False,
        "service":    "FluxImageService",
    },
    "flux-dev": {
        "desc":       "FLUX开发版，高质量，非商业，目前免费体验",
        "endpoint":   "flux_image2image",
        "free_quota": 100,
        "price":      "免费体验",
        "recommended": False,
        "service":    "FluxImageService",
    },
    "flux-schnell": {
        "desc":       "FLUX快速版，轻量，目前免费体验",
        "endpoint":   "flux_image2image",
        "free_quota": 100,
        "price":      "免费体验",
        "recommended": False,
        "service":    "FluxImageService",
    },
}


# ══════════════════════════════════════════════════════
# 视频生成模型（中国内地部署，有免费额度）
# ══════════════════════════════════════════════════════
ALIYUN_VIDEO_MODELS: dict[str, dict] = {

    # ── 文生视频（T2V）──────────────────────────────────────────────────
    "wan2.6-t2v": {
        "desc":       "万相2.6文生视频，多镜头叙事，支持自动配音/自定义音频",
        "type":       "t2v",
        "free_quota": "50秒",
        "price":      "720P 0.6元/秒 | 1080P 1元/秒",
        "recommended": True,
        "features":   ["audio_auto", "audio_custom", "multi_shot"],
    },
    "wan2.5-t2v-preview": {
        "desc":       "万相2.5文生视频，支持配音，480P/720P/1080P",
        "type":       "t2v",
        "free_quota": "50秒",
        "price":      "480P 0.3元/秒",
        "recommended": False,
        "features":   ["audio_auto", "audio_custom"],
    },
    "wan2.2-t2v-plus": {
        "desc":       "万相2.2文生视频专业版，稳定流畅",
        "type":       "t2v",
        "free_quota": "50秒",
        "price":      "480P 0.14元/秒",
        "recommended": False,
        "features":   [],
    },
    "wanx2.1-t2v-turbo": {
        "desc":       "万相2.1文生视频极速版，性价比高",
        "type":       "t2v",
        "free_quota": "200秒",
        "price":      "0.24元/秒",
        "recommended": False,
        "features":   [],
    },
    "wanx2.1-t2v-plus": {
        "desc":       "万相2.1文生视频专业版，画面更具质感",
        "type":       "t2v",
        "free_quota": "200秒",
        "price":      "0.70元/秒",
        "recommended": False,
        "features":   [],
    },

    # ── 图生视频（I2V）──────────────────────────────────────────────────
    "wan2.6-i2v-flash": {
        "desc":       "万相2.6图生视频极速版，支持配音，推荐I2V首选",
        "type":       "i2v",
        "free_quota": "50秒",
        "price":      "720P有声 0.3元/秒 | 无声 0.15元/秒",
        "recommended": True,
        "features":   ["audio_auto", "audio_custom", "i2v"],
    },
    "wan2.6-i2v": {
        "desc":       "万相2.6图生视频标准版，多镜头叙事",
        "type":       "i2v",
        "free_quota": "50秒",
        "price":      "720P 0.6元/秒 | 1080P 1元/秒",
        "recommended": True,
        "features":   ["audio_auto", "audio_custom", "i2v"],
    },
    "wan2.5-i2v-preview": {
        "desc":       "万相2.5图生视频，支持配音",
        "type":       "i2v",
        "free_quota": "50秒",
        "price":      "480P 0.3元/秒",
        "recommended": False,
        "features":   ["audio_auto", "audio_custom", "i2v"],
    },
    "wan2.2-i2v-flash": {
        "desc":       "万相2.2图生视频极速版，稳定性高",
        "type":       "i2v",
        "free_quota": "50秒",
        "price":      "480P 0.10元/秒",
        "recommended": False,
        "features":   ["i2v"],
    },
    "wan2.2-i2v-plus": {
        "desc":       "万相2.2图生视频专业版，细节丰富",
        "type":       "i2v",
        "free_quota": "50秒",
        "price":      "480P 0.14元/秒",
        "recommended": False,
        "features":   ["i2v"],
    },
    "wanx2.1-i2v-turbo": {
        "desc":       "万相2.1图生视频极速版，性价比高",
        "type":       "i2v",
        "free_quota": "200秒",
        "price":      "0.24元/秒",
        "recommended": False,
        "features":   ["i2v"],
    },
    "wanx2.1-i2v-plus": {
        "desc":       "万相2.1图生视频专业版，质感更强",
        "type":       "i2v",
        "free_quota": "200秒",
        "price":      "0.70元/秒",
        "recommended": False,
        "features":   ["i2v"],
    },
}


def get_recommended_image_model(prefer_speed: bool = False) -> str:
    """返回推荐的图像生成模型 ID。"""
    if prefer_speed:
        return "wanx2.1-t2i-turbo"   # 500张额度，速度快
    return "wan2.6-t2i"               # 旗舰，同步直出


def get_recommended_video_model(is_i2v: bool = True, prefer_speed: bool = True) -> str:
    """返回推荐的视频生成模型 ID。"""
    if is_i2v:
        return "wan2.6-i2v-flash" if prefer_speed else "wan2.6-i2v"
    return "wan2.6-t2v"
