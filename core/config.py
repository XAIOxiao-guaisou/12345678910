import os
from dotenv import load_dotenv
load_dotenv()

class Settings:
    # 飞书配置
    FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a914c526d5f8dbc6")
    FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
    
    FEISHU_APP_TOKEN_FACTORY = os.environ.get("FEISHU_APP_TOKEN_FACTORY", "Cu75bLeuJarqg1s7ysscaNolnPg")
    FEISHU_TABLE_FACTORY = os.environ.get("FEISHU_TABLE_FACTORY", "tbloUrdwqG47ZmgI")
    
    FEISHU_APP_TOKEN_SCRIPT = os.environ.get("FEISHU_APP_TOKEN_SCRIPT", "J7OPbwEHqaJMefs1NLecTvA1n2e")
    FEISHU_TABLE_SCRIPT = os.environ.get("FEISHU_TABLE_SCRIPT", "tbluFmGLkmqPTd9S")
    
    FEISHU_APP_TOKEN_MEMORY = os.environ.get("FEISHU_APP_TOKEN_MEMORY", "XfWibZ0RjaPD1psTFwscKP1VnUb")
    FEISHU_TABLE_MEMORY = os.environ.get("FEISHU_TABLE_MEMORY", "tblcFydnJuwD8cIy")

    # 飞书列名到内部字段映射
    FEISHU_FIELD_MAPPING_MEMORY = {
        "entity_id": ["实体标识符", "实体ID", "Entity ID", "entity_id"],
        "lore": ["深层设定与逻辑", "深层设定逻辑", "设定内容", "Lore"],
        "visual_constraints": ["视觉约束", "视觉排他性约束", "Visual Constraints", "视觉感官指纹"],
        "dependencies": ["依赖关系", "Dependencies"],
        "aliases": ["代称标签", "代称", "Aliases"],
        "category": ["记忆维度", "类别", "Category"],
        "name": ["词条名称", "词条名", "名称", "Name", "name"]
    }
    
    FEISHU_FIELD_MAPPING_SCRIPT = {
        "episode": ["集数/场次", "集数", "Episode"],
        "text": ["小说原文（内容）", "小说原文", "Text"],
        "status": ["状态", "Status"],
        "desc": ["场景描述", "剧本拆解", "Description", "镜头详情"],
        "visual": ["视觉提示词", "Visual Prompt", "视频提示词"],
        "audio": ["音频提示词", "Audio Prompt", "语音提示词"]
    }

    # 视频大模型配置
    VOLCENGINE_API_KEY = os.environ.get("VOLCENGINE_API_KEY", "")
    ALIYUN_API_KEY = os.environ.get("ALIYUN_API_KEY", "")

    # LLM (DeepSeek) 配置
    SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

    # 通知机器人配置
    WX_BOT_WEBHOOK = os.environ.get("WX_BOT_WEBHOOK", "")

    # MOCK_MODE 沙盒测试模式 (设置为 true 取消真实的 API 视频生成)
    MOCK_MODE = os.environ.get("MOCK_MODE", "False").lower() == "true"

    @classmethod
    def validate_mappings(cls):
        """逆向唯一性校验：由于用户可能配错字段映射，此处确保任意不同的内部 keys 不会映射到同一个飞书列名。"""
        def _check_unique(mapping_dict, mapping_name):
            seen_cols = set()
            for internal_key, feishu_cols in mapping_dict.items():
                for col in feishu_cols:
                    if col in seen_cols:
                        raise ValueError(f"配置冲突：飞书列名 '{col}' 被映射到了多个不同的内部字段 (in {mapping_name})！请检查 config.py 或 .env 并修正。")
                    seen_cols.add(col)
                    
        _check_unique(cls.FEISHU_FIELD_MAPPING_MEMORY, "FEISHU_FIELD_MAPPING_MEMORY")
        _check_unique(cls.FEISHU_FIELD_MAPPING_SCRIPT, "FEISHU_FIELD_MAPPING_SCRIPT")


settings = Settings()
settings.validate_mappings()
