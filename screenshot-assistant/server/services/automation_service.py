"""自动化指令生成服务"""
import logging

logger = logging.getLogger(__name__)


class AutomationService:
    """生成自动化操作指令序列"""

    @staticmethod
    def fund_holdings_sequence() -> dict:
        """生成基金持仓采集的自动化指令序列"""
        return {
            "type": "command",
            "action": "automation_sequence",
            "params": {
                "sequence": [
                    {"action": "open_app", "package": "com.eg.android.AlipayGphone"},
                    {"action": "wait", "duration": 3000},
                    {"action": "click_text", "text": "我的"},
                    {"action": "wait", "duration": 1500},
                    {"action": "click_text", "text": "基金"},
                    {"action": "wait", "duration": 2000},
                    {"action": "click_text", "text": "持仓"},
                    {"action": "wait", "duration": 1500},
                    {"action": "long_capture", "max_scrolls": 10}
                ],
                "callback_action": "fund_holdings"
            }
        }

    @staticmethod
    def custom_sequence(steps: list, callback_action: str = "ocr") -> dict:
        """生成自定义操作指令序列"""
        return {
            "type": "command",
            "action": "automation_sequence",
            "params": {
                "sequence": steps,
                "callback_action": callback_action
            }
        }
