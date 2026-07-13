# 02 Apache Arrow 内存布局

## 你需要先知道

Arrow 是列式内存格式和跨语言数据接口，不是数据库，也不会自动把查询搬到 GPU。

## 核心原理

项目把 Q5 需要的列写成六个 Arrow IPC 文件。整数列在连续 buffer 中，字符串
列使用字典编码，表由 record batch 组成。manifest 保存 schema、行数、batch
数、字节数和 SHA256。C++ loader 会检查这些约束，避免“文件能读”却不是同一
份数据。正式 CPU、CUDA、hybrid 和 cuDF 都从这个目录开始。

## 代码入口

- schema：`scripts/tpch_arrow_schema.py`
- 转换器：`scripts/prepare_arrow_dataset.py`
- C++ loader：`src/io/arrow_q5_loader.cpp`
- loader 测试：`tests/test_arrow_q5_loader.cpp`

## 自己检查

打开 `tests/fixtures/tpch_q5_tiny_arrow/manifest.json`，确认 lineitem 有 6 行并由
多个 batch 构成。

**检查答案：** batch 只是物理分块，不改变逻辑行顺序；混合执行正是利用 batch
边界切分不重叠的行区间。

## 老师可能追问

统一 Arrow 后比较就完全公平了吗？没有。输入格式统一了，但 Acero、专用数组、
CUDA staging 和 cuDF DataFrame 的准备工作仍不同，所以还要分别报告计时阶段。

## 一分钟复述

Arrow 在项目里负责统一 schema、列 buffer、batch 和可校验的数据交换。它减少
输入口径差异，但不同执行器仍会做各自的数据准备。
