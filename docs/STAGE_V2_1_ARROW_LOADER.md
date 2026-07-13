# V2.1 C++ Arrow IPC 加载阶段

## 本阶段完成内容

- 新增可选 `MEMQ5_ENABLE_ARROW=ON` 构建，不改变默认 CPU/CUDA 构建。
- 使用 Apache Arrow C++ 23.0.1 读取 Q5 六张表的 IPC file。
- 校验 `manifest.json` 的格式版本、六表集合、文件名、行数、record batch 数、
  文件字节数、schema、SHA256 和必需列非空约束。
- 新增 tiny Arrow IPC fixture 和 `test_arrow_q5_loader`。
- 新增 `memq5_arrow_check`，可以独立校验 tiny 或正式 SF1 Arrow 数据集。

## 构建

先根据 `environment-arrow-cpu.yml` 创建 Arrow C++ 环境，然后执行：

```bash
cmake -S . -B build-arrow \
  -DMEMQ5_ENABLE_ARROW=ON \
  -DMEMQ5_ENABLE_CUDA=OFF \
  -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_PREFIX_PATH="$CONDA_PREFIX"
cmake --build build-arrow
ctest --test-dir build-arrow --output-on-failure
```

校验 tiny 数据：

```bash
./build-arrow/memq5_arrow_check \
  --dataset tests/fixtures/tpch_q5_tiny_arrow
```

校验正式 SF1：

```bash
./build-arrow/memq5_arrow_check --dataset data/tpch_sf1_arrow
```

## 2026-07-14 验证记录

- Arrow 23.0.1 CPU 构建成功，CTest 6/6 通过。
- tiny Arrow fixture 的六张表、schema、行数和 checksum 全部通过。
- SF1 Arrow 数据集的六张表全部通过；其中 `lineitem` 为 6,001,215 行，
  23 个 record batch，文件大小 240,056,458 字节。
- `python3 scripts/self_check.py` 通过，覆盖默认 CPU 构建/测试、真实 GPU 上的
  CUDA 构建/测试、tiny 数据校验和实验流水线。
- Python oracle 测试 8/8 通过，Arrow 数据与 PyArrow/cuDF baseline 测试
  11/11 通过。

## 阶段边界

这一阶段只完成 Arrow IPC 存储的 C++ 读取和验证。现有 `cpu`、`gpu-copy`、
`gpu-managed`、`gpu-mapped` 仍从 `.tbl` 构造自定义连续数组。让 CPU 查询直接
消费这里读取的 Arrow Table 属于 V2.2，不在 V2.1 中提前声明完成。
