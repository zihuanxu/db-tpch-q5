# 高风险问题与回答

## 这是不是数据库

不是完整DBMS，是固定Q5物理执行器。没有SQL parser、代价优化和事务，但包含
列式数据、连接过滤传播、并行聚合、CPU/GPU数据移动和实验优化问题。

## 为什么必须六表和同国家

region/nation确定ASIA，customer/orders确定客户和1994年订单，supplier/
lineitem确定供应明细。同国家条件来自Q5本身，用来排除跨国供应。

## Arrow到底做了什么

统一六表schema、列buffer、record batch和跨语言输入。它不自动执行查询，也
不保证specialized、Acero、CUDA和cuDF的内部准备成本相同。<!-- C001 -->

## copy、managed、mapped的一句话区别

copy显式H2D到显存；managed使用统一地址并由runtime迁移/预取；mapped让GPU
远程读pinned host pages。三种方式都有数据移动或远程访问。

## managed是不是复制一份CPU内存等GPU用

不是。managed allocation提供统一虚拟地址，物理页可迁移或预取。CPU和GPU
看到同一分配，不等于永远维护两份同步副本。

## mapped为什么H2D为0还很慢

Arrow列先准备到mapped pinned buffer；没有大块显式输入cudaMemcpy，但kernel
读取时仍产生PCIe事务。NCU中device DRAM读取少，恰好说明数据主要不在显存。
<!-- C013 C018 -->

## cold、setup、resident request和profiler有什么区别

cold包含进程启动、加载、准备、执行和退出；setup是常驻会话的一次性准备；
request复用计划与buffer；profiler会重放或插桩，时间只用来解释机制。四者不能
放进同一列排名。<!-- C009 C011 -->

## hybrid到底有没有加速

在V7稳态request中有：SF1 fixed 1.160 ms低于CPU 3.201 ms，SF10 10.054 ms
低于14.955 ms。但setup高，少量请求的摊销仍可能输给CPU，所以要带生命周期
说明。<!-- C014 C019 -->

## auto为什么没选到最佳

它只用一次CPU/GPU校准和简单吞吐模型，没有建模缓存、PCIe、batch边界和干扰。
SF1/SF10 regret为33.89%/9.21%，说明方向大致对，精度还不够。<!-- C015 C020 -->

## overlap证据够吗

SF10 fixed-0.5 NSYS中CPU scan约12.31 ms、GPU request约8.27 ms，都位于约
12.51 ms的measured request范围，支持阶段重叠。但profiler会扰动时间，不能
把这组profile直接当普通延迟。

## cuDF比较公平吗

输入和语义相同，但执行层级不同。cuDF是通用DataFrame算子，手写CUDA主要扫描
lineitem。报告同时给setup/request并明确这个限制。<!-- C016 C021 -->

## hash一样能否证明一定正确

不能，所以项目还用独立oracle按定点整数逐行比较。hash用于快速发现不一致，
oracle用于检查标准答案。<!-- C012 C017 -->

## 项目最大缺陷是什么

固定Q5、两个规模、单机；GPU不是完整六表计划；没有并发和NUMA；setup每配置
只建立一次；auto模型特征少。最直接的改进是复用两侧过滤计划并重复测量setup。

## 哪部分你现在能自己讲清楚

能从Q5条件讲到过滤传播数组，从Arrow batch讲到三种CUDA内存和hybrid分片，
区分四种计时，运行正式矩阵和审计，并追到论文每个数字的证据行。
