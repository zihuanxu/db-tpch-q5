# V2.2 Arrow 原生 CPU 查询阶段

## 本阶段完成内容

- `ArrowQ5Dataset` 暴露经过 manifest/schema/checksum 校验的六张 typed Table。
- `cpu-specialized` 使用 `TableBatchReader` 的零拷贝 batch view，直接读取
  Arrow 的 Int32、Date32、Dictionary 和 Decimal128 buffer。
- `arrow-acero` 使用 Arrow Acero 完成 region/date filter 与 Q5 hash join。
- 两个引擎都按 scale-4 整数计算收入，检查乘法与累计溢出。
- 新增统一 CLI `memq5_arrow_query` 和输入/命中行数等 counters。

Acero 的 dictionary 名称列在进入算子图前通过 Arrow Compute 解码为 UTF-8；
join 结果的 Decimal128 收入由 C++ 做 checked accumulation。因此本阶段称为
“Acero 关系算子实现”，不声称所有聚合步骤都由 Acero 单个 aggregate node 完成。

## 构建与运行

```bash
cmake -S . -B build-arrow \
  -DMEMQ5_ENABLE_ARROW=ON \
  -DMEMQ5_ENABLE_CUDA=OFF \
  -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_PREFIX_PATH="$CONDA_PREFIX"
cmake --build build-arrow
ctest --test-dir build-arrow --output-on-failure
```

专用 Arrow CPU：

```bash
./build-arrow/memq5_arrow_query \
  --dataset tests/fixtures/tpch_q5_tiny_arrow \
  --engine cpu-specialized --threads 2 --format json
```

Acero：

```bash
./build-arrow/memq5_arrow_query \
  --dataset tests/fixtures/tpch_q5_tiny_arrow \
  --engine arrow-acero --threads 2 --format json
```

## 2026-07-14 验证记录

- Arrow Debug/Release CTest 13/13 通过，包含两个引擎、CLI 正常路径和三个
  非法参数拒绝路径。
- tiny 的两个引擎均得到 `JAPAN=190.00`、`INDIA=90.00`，hash 为
  `248d10b6ee352953`。
- SF1 的两个引擎 hash 均为 `542abf4003633c7c`，五行结果逐行通过官方
  TPC-H V3.0.1 `q5.out` 校验。
- Python oracle 14/14、Arrow/baseline Python 12/12 通过；默认 CPU/CUDA/tiny
  自检全部通过。

## 阶段边界

V2 只完成 Arrow 数据层和两个 CPU 查询。现有三种 CUDA backend 尚未消费
Arrow Table；把相同 Arrow batch 输入接到 `gpu-copy`、`gpu-managed` 和
`gpu-mapped` 是 V3 的任务。当前 specialized 的 `build_ms` 包含构造直接索引
映射的成本，不能只拿 `scan_ms` 与 Acero 总时间比较后宣称哪个方案整体更快。
