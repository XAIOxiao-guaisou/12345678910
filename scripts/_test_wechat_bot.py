import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.services.notify_service import WeChatNotifier
import logging

logging.basicConfig(level=logging.INFO)

def test_wechat_notification():
    """
    Mock test for WeChat robot integration
    """
    print("="*40)
    print("🚀 开始企微机器人汇报测试...")
    print("="*40)
    
    # Send a mock completion event
    WeChatNotifier.send_task_completion(
        novel_id="test_wechat_mock",
        scenes_count=10,
        success_count=10,
        task_id="mock-" + os.urandom(4).hex()
    )
    
    print("\n✅ 测试脚本执行完毕。请检查 .env 中的 WX_BOT_WEBHOOK 设置以及对应的企微群。")

if __name__ == "__main__":
    test_wechat_notification()
