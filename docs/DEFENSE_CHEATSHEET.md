# 答辩速查表

> V7 口径。证据见 `v7_sf1_resident`、`v7_sf10_resident`、
> `v7_hybrid_model` 和 `v7_profiler`。

## 30秒版本

我实现的是固定TPC-H Q5的Arrow CPU--GPU执行器。SF1和SF10各18组配置、180次
resident请求全部通过oracle。copy和managed接近，mapped因远程PCIe访问明显
较慢；fixed hybrid稳态请求在两个规模都比专用CPU低，但setup很高；auto能跟随
规模改变比例，却有33.89%和9.21% regret。结论只覆盖固定Q5、两个规模和本机
RTX 4090。

## 必画数据流

```text
Arrow six tables
  -> CPU region/nation/date filter propagation
  -> supplier_nation[] + customer_nation[] + order_nation[]
  -> resident setup: indexes + CPU/GPU buffers
  -> repeated lineitem request: CPU / GPU / hybrid
  -> exact revenue_1e4 + sort + result hash + oracle
```

## 必记数字

| 项 | SF1 | SF10 |
| --- | ---: | ---: |
| 配置 / warmup / measured | 18 / 54 / 180 | 18 / 54 / 180 |
| 结果hash | `542abf4003633c7c` | `b1351a421ba8dcfd` |
| specialized CPU | 3.201 ms (16t) | 14.955 ms (32t) |
| Acero | 311.461 ms | 3124.388 ms |
| cuDF | 12.773 ms | 27.906 ms |
| copy / managed / mapped | 1.267 / 1.440 / 22.971 | 15.416 / 15.066 / 358.582 |
| best fixed hybrid | 1.160 ms，CPU=0.125 | 10.054 ms，CPU=0.375 |
| auto / regret | 1.553 ms / 33.89% | 10.980 ms / 9.21% |

## 三种GPU模式

| 模式 | setup与request中的数据位置 |
| --- | --- |
| copy | setup显式H2D到device，request读显存 |
| managed | unified allocation，runtime迁移，本项目request前prefetch |
| mapped | pinned host pages映射进UVA，kernel经PCIe远程读 |

mapped不是“没有传输”；它只是没有大块显式输入H2D。SF10 NCU kernel约130 ms，
而device DRAM读取只有约2.89 MB，说明主要输入不在显存。

## 四种时间

- V5 cold process：启动、加载、准备、查询、退出；
- V7 setup：一次性加载、计划、分配、首次传输或校准；
- V7 request：复用session后的查询；
- profiler时间：插桩/重放后的机制证据，不参加普通排名。

## 五个危险问题

### 为什么客户和供应商必须同国家

这是Q5语义，用来统计地区内各国家的国内供应收入；只要求都在ASIA会混入跨国
供应。

### hybrid到底有没有加速

稳态request有，但第一次查询不一定有。SF10 fixed request 10.054 ms低于CPU
14.955 ms，可setup约4997 ms，100次摊销仍约60 ms，高于CPU约50 ms。

### auto为什么不是最佳

只用一次简单CPU/GPU校准，没有建模缓存、PCIe、batch边界和干扰。方向大致对，
精度不够。

### hash一样是否足够

不够。hash检查后端一致，独立oracle再按定点整数逐行检查标准答案。

### profiler证明了什么

证明NVTX阶段、内存操作、kernel duration、DRAM指标和hybrid阶段重叠。profiler
会扰动时间，因此不证明普通request就是profile中的墙钟。

## 必须主动承认的限制

- 固定Q5，不是DBMS；GPU只扫描聚合lineitem；
- 只有SF1/SF10、单机RTX 4090，无并发和NUMA；
- Arrow统一输入，但各后端内部准备不完全相同；
- setup每配置只建立一次；
- auto在SF1的regret较大。

## AI参与的诚实表述

> 项目代码和早期文档主要由AI辅助生成。我现在按源码、测试、证据bundle和这套
> 接手材料重新学习，不把自己没做过的过程说成亲手完成。答辩时我负责解释最终
> 数据流、实验门禁、结果和限制。

## 上台前自检

- [ ] 能从六表条件讲到三个nation映射和lineitem scan；
- [ ] 能区分Arrow、UVA、managed和mapped；
- [ ] 能区分cold、setup、request和profiler；
- [ ] 能说出两规模三种内存和hybrid结果；
- [ ] 能解释auto regret和setup成本；
- [ ] 能主动列出至少四项限制。
