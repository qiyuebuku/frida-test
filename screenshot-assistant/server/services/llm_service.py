import logging
import os

import httpx

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        self.model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    async def chat(self, prompt: str, system_prompt: str = "") -> str:
        """调用 LLM 生成回复"""
        if not self.api_key:
            logger.warning("LLM API key not set, returning placeholder")
            return f"[LLM未配置] 请设置 OPENAI_API_KEY 环境变量"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 2000
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    logger.error(f"LLM API failed: {response.status_code} {response.text}")
                    return f"LLM 请求失败: {response.status_code}"
        except Exception as e:
            logger.error(f"LLM error: {e}")
            return f"LLM 错误: {str(e)}"

    async def analyze_chat(self, chat_text: str) -> str:
        """分析聊天记录并生成回复建议"""
        prompt = f"""以下是一段聊天记录的截图识别结果，请帮我生成一个合适的回复：

{chat_text}

请直接给出回复内容，不需要解释。"""
        return await self.chat(prompt)

    async def analyze_fund_holdings(self, holdings_text: str) -> str:
        """分析基金持仓数据"""
        prompt = f"""以下是基金持仓页面的 OCR 识别结果，请分析持仓情况：

{holdings_text}

请提取以下信息：
1. 各基金名称和持仓金额
2. 各基金的收益率
3. 总持仓金额和总收益
4. 投资建议（如有明显的集中风险等）

以结构化的格式返回。"""
        return await self.chat(prompt, system_prompt="你是一个专业的基金投资分析师。")
