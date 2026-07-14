# 04 CUDA 三种内存模式

## 你需要先知道

UVA 是统一虚拟地址空间；managed memory 是由 CUDA runtime 管理迁移的内存；
mapped memory 是映射到 GPU 地址空间的锁页主机内存。三者不能混为一谈。

## 核心原理

- copy：CPU 准备主机列，`cudaMalloc` 分配显存，`cudaMemcpy` 做显式 H2D。
- managed：`cudaMallocManaged` 得到统一地址，CPU 填充后 prefetch 到 GPU；仍有迁移。
- mapped：`cudaHostAllocMapped` 分配 pinned host pages，GPU kernel 经 PCIe 远程读。

三种模式执行同一个“一线程处理一条 lineitem”的 kernel，并用 nation 数组
`atomicAdd` 聚合。mapped 没有大块显式 H2D，不代表没有传输；访问被移到 kernel
期间，而且代码仍要把 Arrow 列准备到 pinned buffer。

## 代码入口

- 实现：`src/cuda/q5_arrow_cuda.cu`
- 接口：`src/cuda/q5_arrow_cuda.hpp`
- 测试：`tests/test_q5_arrow_cuda.cpp`

## 自己检查

为什么 mapped 的 H2D 是 0，kernel 却比 copy 慢？

**检查答案：** 计时字段没有记录显式输入拷贝，但每次 GPU load 可能跨 PCIe 读
主机页；copy 的 kernel 读的是显存。

## 老师可能追问

managed 是不是“GPU 用到哪一页才从 CPU 内存传过去”？可能按需迁移，也可以像
本项目一样预取。核心是 runtime 管理位置，不是永远保留一份 CPU 副本供远程读。

Nsight 为什么显示 mapped 的 device DRAM 读取很少？因为mapped主要从映射主机
页经PCIe读取，不能把“device DRAM少”解释成“总数据访问少”。

## 一分钟复述

copy 在setup显式搬到显存，managed 由runtime迁移并预取，mapped让GPU远程读
pinned host memory。常驻请求中SF1三者为1.267、1.440、22.971 ms；SF10为
15.416、15.066、358.582 ms。copy和managed接近，mapped在本机明显较慢。
