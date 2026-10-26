# -*- coding: utf-8 -*-
"""
数据接入（ingress）
==================
对应开发指南 03：把平台导出的 xlsx 文件，变成归一化的 Record。

当前内容：
    ├── mapping.py    平台列名 → 统一字段 的映射（新增平台只加数据）
    ├── normalize.py  金额/评分/时间/整数的归一化
    ├── pii.py        PII：昵称丢弃、正文脱敏
    ├── reader.py     xlsx 读取 + 表头探测 + 空行/合计行识别
    └── adapter.py    主流程：文件 → RawBatch + Record + MappingReport

用法：
    from hub.ingress.adapter import read_file
    batch, records, report = read_file("xx.xlsx", platform="jd", data_type="review")
"""
