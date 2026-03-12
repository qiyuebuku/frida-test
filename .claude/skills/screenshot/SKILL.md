---
name: screenshot
display_name: 截屏工具箱
icon: screenshot
description: 截屏识别、智能回复、表格提取等通用截屏分析能力
category: tools
commands:
  - id: ocr
    name: 识别文字
    description: 提取屏幕中的文字内容，保持原始格式
    input: screenshot
    capture_types: [normal, long_scroll, manual_scroll]
    executor: pipeline
    estimated_time: 10
    pipeline:
      - step: recognize
        handler: llm_call
        description: OCR 识别截图文字
        prompt_template: "识别图片中的文字，保持原始格式"

  - id: chat_reply
    name: 智能回复
    description: 分析聊天截图，生成多种风格的回复建议
    input: screenshot
    capture_types: [normal]
    executor: pipeline
    estimated_time: 15
    pipeline:
      - step: reply
        handler: llm_call
        description: 生成回复建议
        prompt_template: "根据聊天截图生成 3 种风格回复"

  - id: table
    name: 表格识别
    description: 识别图片中的表格，输出 Markdown 表格
    input: screenshot
    capture_types: [normal]
    executor: pipeline
    estimated_time: 10
    pipeline:
      - step: extract
        handler: llm_call
        description: 表格结构提取
        prompt_template: "识别图片中的表格，输出 Markdown 表格"

  - id: search
    name: 搜索内容
    description: 识别文字并在内容中搜索
    input: screenshot
    capture_types: [normal]
    executor: pipeline
    estimated_time: 10
    pipeline:
      - step: recognize
        handler: llm_call
        description: 识别并搜索
        prompt_template: "识别文字并搜索相关内容"

  - id: full_page
    name: 完整页面
    description: 自动滚动截取完整页面内容
    input: screenshot
    capture_types: [normal, long_scroll]
    executor: pipeline
    estimated_time: 30
    pipeline:
      - step: capture_frames
        handler: scroll_capture
        description: 自动滚动截取多帧
      - step: stitch
        handler: image_stitch
        description: 图片拼接
      - step: ocr
        handler: llm_call
        description: 识别完整页面内容
        prompt_template: "识别完整页面内容"

  - id: manual_scroll
    name: 手动长截
    description: 手动滑动，自动采集每一帧
    input: screenshot
    capture_types: [manual_scroll]
    executor: pipeline
    estimated_time: 30
    pipeline:
      - step: capture_frames
        handler: manual_capture
        description: 手动滑动采集多帧
      - step: stitch
        handler: image_stitch
        description: 图片拼接
      - step: ocr
        handler: llm_call
        description: 识别完整页面内容
        prompt_template: "识别完整页面内容"
---

# 截屏工具箱

通用截屏分析 Skill，提供 OCR 文字识别、智能回复、表格提取等能力。

所有命令都接受截图作为输入，通过悬浮球或 App 内触发。
