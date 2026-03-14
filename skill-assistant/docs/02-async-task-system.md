# 02 - 异步任务系统 ✅ 已实施

## 一、数据库设计

### 新建表：`sa_tasks`

```sql
CREATE TABLE IF NOT EXISTS sa_tasks (
    id            SERIAL PRIMARY KEY,
    task_type     VARCHAR(30) NOT NULL,       -- fund_holdings / chat_reply / ocr / fund_trade_run / fund_review
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending / processing / completed / failed
    progress      INT NOT NULL DEFAULT 0,     -- 0-100 进度百分比
    progress_msg  VARCHAR(200),               -- 当前步骤描述，如「正在 OCR 识别...」

    -- 输入
    input_type    VARCHAR(20),                -- screenshot / text / command
    input_data    TEXT,                        -- 截图 Base64 过大，此处存引用路径或简短文本
    image_path    VARCHAR(500),               -- 截图存储路径（如有）
    ocr_record_id INT,                        -- 关联的 sa_ocr_records.id（OCR 完成后填入）

    -- 输出
    title         VARCHAR(200),               -- 任务标题（用于列表显示），如「持仓分析 03-10 14:30」
    summary       VARCHAR(500),               -- 摘要文本（用于列表预览），如「总资产 12.3 万，今日 +0.5%」
    result        TEXT,                        -- 完整结果（Markdown 格式）
    result_data   JSONB,                       -- 结构化结果数据（可选，便于程序读取）

    -- 元数据
    client_id     VARCHAR(100),               -- 客户端标识
    error_msg     TEXT,                        -- 失败时的错误信息
    created_at    TIMESTAMP DEFAULT NOW(),
    started_at    TIMESTAMP,                  -- 开始处理时间
    completed_at  TIMESTAMP,                  -- 完成时间
    duration_sec  INT                         -- 耗时（秒）
);

CREATE INDEX idx_sa_tasks_status ON sa_tasks(status);
CREATE INDEX idx_sa_tasks_type ON sa_tasks(task_type);
CREATE INDEX idx_sa_tasks_created ON sa_tasks(created_at DESC);
```

### 状态流转

```
pending ──► processing ──► completed
                │
                └──────────► failed
```

### 与现有表的关系

```
sa_tasks (新)
  │
  ├── ocr_record_id ──► sa_ocr_records (已有，OCR 原始数据)
  │
  └── result_data ──► 可包含 ft_decisions / ft_alipay_positions 的引用
```

## 二、API 设计

### 2.1 创建任务

```
POST /api/tasks
```

**请求体**：
```json
{
  "task_type": "fund_holdings",
  "input_type": "screenshot",
  "imageBase64": "<base64>",
  "client_id": "pixel_8"
}
```

**响应**（立即返回）：
```json
{
  "status": "success",
  "task_id": 42,
  "message": "任务已提交，正在处理中"
}
```

**服务端处理**：
1. 保存截图到磁盘 → 记录 `image_path`
2. 创建 `sa_tasks` 记录（status=pending）
3. 启动后台线程处理
4. 立即返回 task_id

### 2.2 查询任务列表

```
GET /api/tasks?status=all&limit=20&offset=0
```

**响应**：
```json
{
  "status": "success",
  "data": {
    "total": 56,
    "items": [
      {
        "id": 42,
        "task_type": "fund_holdings",
        "status": "processing",
        "progress": 60,
        "progress_msg": "正在采集市场数据...",
        "title": "持仓分析 03-10 14:30",
        "summary": null,
        "created_at": "2026-03-10T14:30:00",
        "duration_sec": null
      },
      {
        "id": 41,
        "task_type": "chat_reply",
        "status": "completed",
        "progress": 100,
        "progress_msg": null,
        "title": "智能回复 03-10 14:20",
        "summary": "建议回复：好的，明天下午三点见。",
        "created_at": "2026-03-10T14:20:00",
        "duration_sec": 12
      }
    ]
  }
}
```

### 2.3 查询任务详情

```
GET /api/tasks/{id}
```

**响应**：
```json
{
  "status": "success",
  "data": {
    "id": 42,
    "task_type": "fund_holdings",
    "status": "completed",
    "progress": 100,
    "title": "持仓分析 03-10 14:30",
    "summary": "总资产 12.3 万，今日 +520 元 (+0.42%)，建议减仓纳指、加仓黄金",
    "result": "# 持仓分析报告\n\n## 持仓全貌\n...(完整 Markdown)...",
    "result_data": {
      "total_assets": 123000,
      "daily_pnl": 520,
      "holdings_count": 8
    },
    "image_path": "/images/screenshot_1710000000000.jpg",
    "created_at": "2026-03-10T14:30:00",
    "completed_at": "2026-03-10T14:33:45",
    "duration_sec": 225,
    "error_msg": null
  }
}
```

### 2.4 兼容旧的同步模式

对于轻量任务（ocr / table / search），保留 SSE 端点不变：

```
POST /api/screenshot   -- 旧端点，SSE 同步返回（保持兼容）
POST /api/tasks        -- 新端点，异步任务模式
```

App 端根据 `ActionConfig.captureType` 决定走哪个端点。

## 三、后台任务执行器

### 核心类：`TaskExecutor`

```python
# services/task_executor.py 伪代码

class TaskExecutor:
    """后台任务执行器，每个任务在独立线程中运行"""

    def submit(self, task_id: int):
        """提交任务到线程池"""
        thread = threading.Thread(target=self._run, args=(task_id,), daemon=True)
        thread.start()

    def _run(self, task_id: int):
        """任务执行主流程"""
        task = db.get_task(task_id)
        db.update_task(task_id, status="processing", started_at=now())

        try:
            handler = self._get_handler(task["task_type"])
            result = handler.execute(task, progress_callback=self._update_progress)

            db.update_task(task_id,
                status="completed",
                title=result["title"],
                summary=result["summary"],
                result=result["markdown"],
                result_data=result.get("data"),
                completed_at=now(),
                duration_sec=elapsed
            )
        except Exception as e:
            db.update_task(task_id, status="failed", error_msg=str(e))

    def _update_progress(self, task_id, progress, msg):
        db.update_task(task_id, progress=progress, progress_msg=msg)
```

### 任务处理器注册

```python
HANDLERS = {
    "fund_holdings": FundHoldingsHandler(),    # OCR → 结构化 → claude -p 分析
    "chat_reply":    ChatReplyHandler(),       # OCR → claude -p 生成回复
    "ocr":           OCRHandler(),             # 仅 OCR
    "table":         TableHandler(),           # OCR（表格模式）
    "fund_trade_run": FundTradeRunHandler(),   # 执行 /fund-trade run
    "fund_review":   FundReviewHandler(),      # 执行 /fund-trade review
}
```

---

## 实施状态

全部已于 2026-03-10 实施完成：

- [x] `sa_tasks` 表创建（`services/task_db.py`），含 CRUD 方法（create_task / update_task / get_task / list_tasks）
- [x] 状态流转 pending → processing → completed / failed
- [x] `POST /api/tasks` — 创建任务，立即返回 task_id
- [x] `GET /api/tasks` — 支持 status / task_type / limit / offset 筛选
- [x] `GET /api/tasks/{id}` — 含完整 result Markdown
- [x] `POST /api/screenshot` 旧端点保留（SSE 同步模式兼容）
- [x] `TaskExecutor` 类：daemon 线程 + `Semaphore(2)` 并发控制
- [x] 6 种 handler 全部实现（fund_holdings / chat_reply / ocr / table / fund_trade_run / fund_review）

**实际实现与设计的偏差**：
- 未采用独立 Handler 类，而是在 `TaskExecutor` 内部用方法实现（`_handle_fund_holdings` / `_handle_chat_reply` / `_handle_skill_command` / `_handle_simple_ocr` / `_handle_full_page`）
- `full_page` 类型未在设计文档中列出，但在实际实现中已支持（仅 OCR，Markdown 直接作为 result）
- 等待超时为 300s（5 分钟），异步调用超时 600s（10 分钟）
