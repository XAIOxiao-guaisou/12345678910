from .aliyun_service import Wan2_6VideoAPI
from .volcengine_service import VolcengineVideoAPI
from pydantic import BaseModel

class DefaultParams(BaseModel):
    gateway: str

GATEWAY_REGISTRY = {
    "wan_2_6": {
        "class": Wan2_6VideoAPI,
        "schema": DefaultParams, 
    },
    "seedance-1.5-pro": {
        "class": VolcengineVideoAPI,
        "schema": DefaultParams,
    },
    "seedance-1.0-pro-fast": {
        "class": VolcengineVideoAPI,
        "schema": DefaultParams,
    }
}
