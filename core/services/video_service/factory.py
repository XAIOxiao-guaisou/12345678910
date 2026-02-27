from .volcengine_service import VolcengineVideoAPI
from .aliyun_service import Wan2_6VideoAPI
from core.config import settings

class VideoServiceFactory:
    @staticmethod
    def get_service(gateway: str):
        import logging
        logger = logging.getLogger(__name__)
        gateway_lower = gateway.lower()
        
        volc_valid = bool(settings.VOLCENGINE_API_KEY and "YOUR_" not in settings.VOLCENGINE_API_KEY)
        aliyun_valid = bool(settings.ALIYUN_API_KEY and "YOUR_" not in settings.ALIYUN_API_KEY)

        if "seedance" in gateway_lower or "volcengine" in gateway_lower or gateway == "API_MODE":
            if volc_valid:
                return VolcengineVideoAPI(api_key=settings.VOLCENGINE_API_KEY, model_id=gateway if gateway != "API_MODE" else "doubao-seedance-1-5-pro-251215")
            elif aliyun_valid:
                logger.warning(f"🔥 Volcengine 密钥未配置或无效。启动自动切换 -> 阿里云 Wan2.6")
                return Wan2_6VideoAPI(api_key=settings.ALIYUN_API_KEY, model="wan2.6-t2v")
            else:
                raise ValueError("未配置有效的视频生成 API Key (Volcengine/Aliyun 均无效)")
        elif "wan2.6" in gateway_lower or "aliyun" in gateway_lower:
            if aliyun_valid:
                return Wan2_6VideoAPI(api_key=settings.ALIYUN_API_KEY, model=gateway)
            elif volc_valid:
                logger.warning(f"🔥 阿里云 密钥未配置或无效。启动自动切换 -> Volcengine Seedance")
                return VolcengineVideoAPI(api_key=settings.VOLCENGINE_API_KEY, model_id="doubao-seedance-1-5-pro-251215")
            else:
                raise ValueError("未配置有效的视频生成 API Key (Volcengine/Aliyun 均无效)")
        else:
            raise ValueError(f"不支持的网关: {gateway}")
