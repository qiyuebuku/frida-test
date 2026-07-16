-- 为既有 llm_call_logs 增加模型思考过程，基线定义见 16_llm_call_logs.sql。

ALTER TABLE llm_call_logs
    ADD COLUMN IF NOT EXISTS reasoning_content TEXT;

COMMENT ON COLUMN llm_call_logs.reasoning_content IS
    'Provider 返回的完整模型思考过程；逻辑调用包含多次物理请求时按阶段拼接';
