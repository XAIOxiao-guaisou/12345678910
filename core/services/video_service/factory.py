from .volcengine_service import VolcengineVideoAPI
from .aliyun_service import Wan2_6VideoAPI
from core.config import settings

class VideoServiceFactory:
    @staticmethod
    def get_service(gateway: str):
        gateway_lower = gateway.lower()
        if "seedance" in gateway_lower or "volcengine" in gateway_lower or gateway == "API_MODE":
            return VolcengineVideoAPI(api_key=settings.VOLCENGINE_API_KEY, model_id=gateway if gateway != "API_MODE" else "doubao-seedance-1-5-pro-251215")
        elif "wan2.6" in gateway_lower or "aliyun" in gateway_lower:
            return Wan2_6VideoAPI(api_key=settings.ALIYUN_API_KEY, model=gateway)
        else:
            raise ValueError(f"不支持的网关: {gateway}")
