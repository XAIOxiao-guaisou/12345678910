import requests
import json
import logging
from core.config import settings

logger = logging.getLogger(__name__)

class WeChatNotifier:
    """
    企微机器人通知服务
    """
    @staticmethod
    def send_task_completion(novel_id: str, scenes_count: int, success_count: int, task_id: str):
        """
        全量任务完成时，向企微 webhook 发送卡片/文本通知
        """
        webhook_url = settings.WX_BOT_WEBHOOK
        if not webhook_url:
            logger.warning("[NotifyService] 未配置 WX_BOT_WEBHOOK，跳过发送企微通知。")
            return

        message = (
            f"🎉 **Aiduanju 工作流播报**\n"
            f">任务标识: {task_id}\n"
            f">小说剧本: {novel_id}\n"
            f">产出分镜: {scenes_count} 个\n"
            f">视频完成: {success_count}/{scenes_count} 个\n"
            f"✅ 所有流程流转已结束！"
        )

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": message
            }
        }

        try:
            headers = {"Content-Type": "application/json"}
            response = requests.post(webhook_url, headers=headers, data=json.dumps(payload), timeout=5)
            if response.status_code == 200:
                logger.info(f"🟢 [NotifyService] 企微通知发送成功！({task_id})")
            else:
                logger.error(f"🔴 [NotifyService] 企微通知发送异常，状态码: {response.status_code}, 内容: {response.text}")
        except Exception as e:
            logger.error(f"🔴 [NotifyService] 尝试发送企微通知时崩溃: {e}")
