# -*- coding: utf-8 -*-
"""
多模态沙箱（multimodal）
=======================
对应开发指南 05：让系统"看懂"买家秀图片，同时保证**图片阅后即焚**。

模块：
    ├── sandbox.py  图片沙箱（tmpfs + 覆写销毁）
    ├── phash.py    感知哈希（图片去重）
    ├── vision.py   Vision 打标（Mock / 真实）
    └── engine.py   主流程（读 record → 处理 → 写 review_feature）

核心原则：
    1. 图片只在沙箱里存在，处理完**覆写销毁**
    2. 只留标签（VisionTags）+ 哈希，不留原图
    3. 图片来源 = 商家上传的图片包（零法律风险）
"""
