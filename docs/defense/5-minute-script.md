# 五分钟答辩稿

## 0:00-0:40 研究问题

我的项目不是通用数据库，而是固定实现TPC-H Q5。Q5连接六张表，统计ASIA地区
1994年、客户与供应商同国家的收入。我想比较同一Arrow输入下CPU、三种GPU内存
模式和CPU--GPU混合执行，并区分一次准备成本和重复请求成本。

## 0:40-1:20 数据与正确性

六表转换为Arrow IPC，manifest记录schema、batch和SHA256。CPU先把维表条件
传播成按key直接索引，最后扫描lineitem。收入用`revenue_1e4`定点整数聚合，
避免旧版逐行截断。SF1和SF10各18组配置、180次正式请求，全部通过独立oracle；
结果hash分别为`542abf4003633c7c`和`b1351a421ba8dcfd`。<!-- C011 C012 C017 -->

## 1:20-2:15 执行路径

CPU有specialized和Acero两条路径。GPU使用同一个kernel：copy在setup显式搬到
显存；managed由runtime迁移并预取；mapped让GPU经PCIe远程读pinned host
memory。cuDF是通用GPU算子库对照。hybrid按Arrow batch把lineitem分给CPU和
GPU并发处理，再精确合并。<!-- C013 C016 C018 C021 -->

## 2:15-3:50 实验结果

V7每组建立一个resident session，3次warmup后测10次request。copy、managed、
mapped在SF1是1.267、1.440、22.971 ms，在SF10是15.416、15.066、358.582 ms。
copy和managed很接近，mapped明显慢。Nsight显示mapped没有显式输入H2D，但
SF10核函数约130 ms，device DRAM读取很少，因为主要数据来自映射主机页。
<!-- C013 C018 -->

fixed hybrid在SF1最佳CPU比例0.125、1.160 ms；SF10最佳比例0.375、10.054 ms，
都低于专用CPU request。auto能随规模调比例，但为1.553和10.980 ms，相对离线
最佳点仍有33.89%和9.21% regret。这个负结果没有从报告里删掉。
<!-- C014 C015 C019 C020 -->

## 3:50-5:00 setup、限制与结论

最快request不等于最快第一次查询。SF10最佳hybrid setup约4997 ms，100次请求
摊销仍约60 ms；专用CPU约50 ms。项目也只有固定Q5、SF1/SF10、一台RTX 4090，
没有并发和完整GPU六表计划。我的结论是：内存模式会改变PCIe访问位置；常驻
复用后CPU--GPU分片有收益，但比例选择和setup同样重要，不能只看一个kernel。
