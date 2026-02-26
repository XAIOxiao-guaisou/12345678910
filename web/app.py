from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import Response
import uvicorn
import os
import sys
import logging

# 导入核心模块 (将父目录加入路径)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.api.routes import router

# 日志配置
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
LOG_FILE = os.path.join(LOG_DIR, "app.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(app_dir, "templates")

if not os.path.exists(template_dir):
    logger.error(f"❌ [错误] 找不到模板目录: {template_dir}")
    sys.exit(1)

app = FastAPI(title="Aiduanju API Service")
templates = Jinja2Templates(directory=template_dir)

from fastapi.staticfiles import StaticFiles

# 注册拆分后的跨域路由
app.include_router(router)

# Mount Download folder for video access
download_dir = os.path.join(app_dir, "..", "Download")
if not os.path.exists(download_dir):
    os.makedirs(download_dir)
app.mount("/Download", StaticFiles(directory=download_dir), name="Download")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon")

@app.get("/")
async def read_root(request: Request):
    # 此前基于本地浏览器会话的账号管理已被废弃，统一使用纯 API 模式流转
    accounts = ["API_MODE"]
    return templates.TemplateResponse("index.html", {"request": request, "accounts": accounts})

if __name__ == "__main__":
    try:
        logger.info("--- [系统状态] ---")
        logger.info(f"工作目录: {os.getcwd()}")
        logger.info("运行模式: API 驱动微服务架构")
        logger.info("-----------------")
        uvicorn.run(app, host="127.0.0.1", port=8000)
    except Exception as e:
        logger.critical(f"🔥 [致命错误] Web 服务启动失败: {e}")
        import traceback
        traceback.print_exc()
        input("按回车键退出...")
