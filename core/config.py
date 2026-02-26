import os
from dotenv import load_dotenv
load_dotenv()

class Settings:
    # 飞书配置
    FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a914c526d5f8dbc6")
    FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
    
    FEISHU_APP_TOKEN_ASSETS = os.environ.get("FEISHU_APP_TOKEN_ASSETS", "Oj00bIhGVaq1cNsZsJhcMC58ndd")
    FEISHU_TABLE_ASSETS = os.environ.get("FEISHU_TABLE_ASSETS", "tblC6L0fO7FXP3fI")
    
    FEISHU_APP_TOKEN_FACTORY = os.environ.get("FEISHU_APP_TOKEN_FACTORY", "Cu75bLeuJarqg1s7ysscaNolnPg")
    FEISHU_TABLE_FACTORY = os.environ.get("FEISHU_TABLE_FACTORY", "tbloUrdwqG47ZmgI")
    
    FEISHU_APP_TOKEN_SCRIPT = os.environ.get("FEISHU_APP_TOKEN_SCRIPT", "J7OPbwEHqaJMefs1NLecTvA1n2e")
    FEISHU_TABLE_SCRIPT = os.environ.get("FEISHU_TABLE_SCRIPT", "tbluFmGLkmqPTd9S")
    
    FEISHU_APP_TOKEN_MEMORY = os.environ.get("FEISHU_APP_TOKEN_MEMORY", "XfWibZ0RjaPD1psTFwscKP1VnUb")
    FEISHU_TABLE_MEMORY = os.environ.get("FEISHU_TABLE_MEMORY", "tblcFydnJuwD8cIy")

    # 视频大模型配置
    VOLCENGINE_API_KEY = os.environ.get("VOLCENGINE_API_KEY", "")
    ALIYUN_API_KEY = os.environ.get("ALIYUN_API_KEY", "")

    # LLM (DeepSeek) 配置
    SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

settings = Settings()
