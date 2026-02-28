# Smart Presets for different video gateways
SMART_PRESETS = {
    "wan_2_6": {
        "quick": {
            "resolution": "480p",
            "fps": 15,
            "sampling_steps": 30,
            "cfg_scale": 7.0
        },
        "standard": {
            "resolution": "720p",
            "fps": 24,
            "sampling_steps": 50,
            "cfg_scale": 7.0
        },
        "cinematic": {
            "resolution": "1080p",
            "fps": 30,
            "sampling_steps": 75,
            "cfg_scale": 8.0
        }
    },
    "seedance-1.5-pro": {
        "quick": {
            "resolution": "480p",
            "fps": 15,
            "sampling_steps": 20,
            "cfg_scale": 6.5
        },
        "standard": {
            "resolution": "1080p",
            "fps": "60",
            "sampling_steps": 25
        },
        "cinematic": {
            "resolution": "1080p",
            "fps": 30,
            "sampling_steps": 60,
            "cfg_scale": 7.5
        }
    }
}

GATEWAY_SPECS = {
    "wan_2_6": {
        "name": "Aliyun Wan 2.6",
        "controls": [
            {"field": "resolution", "label": "输出分辨率", "type": "select", "options": ["480p", "720p", "1080p"], "default": "720p"},
            {"field": "fps", "label": "生成帧率", "type": "slider", "min": 15, "max": 30, "step": 1, "default": 24},
            {"field": "sampling_steps", "label": "采样步数", "type": "slider", "min": 20, "max": 100, "step": 5, "default": 50},
            {"field": "cfg_scale", "label": "服从度 (CFG Scale)", "type": "slider", "min": 1.0, "max": 15.0, "step": 0.5, "default": 7.0}
        ]
    },
    "seedance-1.5-pro": {
        "name": "Volcengine Seedance 1.5 Pro",
        "controls": [
            {"field": "resolution", "label": "输出分辨率", "type": "select", "options": ["480p", "720p", "1080p"], "default": "720p"},
            {"field": "fps", "label": "生成帧率 (高帧护航)", "type": "slider", "min": 15, "max": 60, "step": 1, "default": 24},
            {"field": "sampling_steps", "label": "采样步数", "type": "slider", "min": 20, "max": 100, "step": 5, "default": 50},
            {"field": "cfg_scale", "label": "服从度 (CFG Scale)", "type": "slider", "min": 1.0, "max": 15.0, "step": 0.5, "default": 7.0}
        ]
    }
}
