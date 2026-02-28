from .volcengine_service import VolcengineVideoAPI
from .aliyun_service import Wan2_6VideoAPI
from core.models.gateways.wan26 import Wan26Params
from core.models.gateways.seedance import SeedanceParams
from core.config import settings
import logging

logger = logging.getLogger(__name__)

# Registry linking UI specs, Schemas, and Service logic for each plugin gateway
GATEWAY_REGISTRY = {
    "wan_2_6": {
        "service_class": Wan2_6VideoAPI,
        "api_key_env": "ALIYUN_API_KEY",
        "model_kwargs": {"model": "wan2.6-t2v"},
        "schema": Wan26Params
    },
    "seedance-1.5-pro": {
        "service_class": VolcengineVideoAPI,
        "api_key_env": "VOLCENGINE_API_KEY",
        "model_kwargs": {"model_id": "doubao-seedance-1-5-pro-251215"},
        "schema": SeedanceParams
    }
}

class VideoServiceFactory:
    @staticmethod
    def get_service(gateway: str, config: dict = None):
        if config is None:
            config = {}
            
        if config.get("_sandbox_mode") is True:
            from .mock_service import MockVideoService
            return MockVideoService(gateway_name=gateway)
            
        registry_entry = GATEWAY_REGISTRY.get(gateway)
        if not registry_entry:
            raise ValueError(f"不支持的网关: {gateway}")
            
        api_key_name = registry_entry["api_key_env"]
        api_key = getattr(settings, api_key_name, "")
        
        if not api_key or "YOUR_" in api_key:
            # Simplistic fallback check if one token is missing but user picked the wrong one can be implemented here
            # For now, strict isolation enforces precise keys.
            raise ValueError(f"未配置有效的视频生成 API Key for {gateway}, 请检查环境变量 {api_key_name}")
            
        return registry_entry["service_class"](api_key=api_key, **registry_entry["model_kwargs"])
