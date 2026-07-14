# hashjoin-cpu 来源与许可说明

这个目录是前期课程作业，基于 ETH Zurich 的 VLDB'13 内存哈希连接代码及其
公开 fork：

- https://github.com/mars-research/vldb13-eth-hashjoin
- https://web.archive.org/web/20220414154544/https://systems.ethz.ch/research/data-processing-on-modern-hardware/projects/parallel-and-distributed-joins.html

上游 fork 没有给出覆盖整个仓库的统一许可证，本目录也没有对上游代码重新许可。
这里保留该目录是为了保留前期课程作业，不能据此推断上游代码获得了新的再许可。

本目录中能够从文件头确认的许可如下：

- `src/lock.h`、`src/rdtsc.h`：GPL-3.0-or-later，GPL v3 正文见 `COPYING`。
- `compile`、`depcomp`、`missing`：GPL-2.0-or-later，并带有各文件头所写的
  Autoconf 特殊例外。本仓库附带的 `COPYING` 是其中可选择的 GPL v3 正文。
- `config.guess`、`config.sub`：GPL-3.0-or-later，并带有各文件头所写的
  Autoconf 特殊例外。
- `configure`、`aclocal.m4`、根目录和 `src/` 下的 `Makefile.in`：Free
  Software Foundation unlimited-permission notice，完整文字保留在文件头中。
- `install-sh`：X Consortium 许可，完整许可文字保留在该文件头中。

其余上游文件继续保留原文件头和来源链接；如需在课程提交之外再次分发，应先
向原作者或上游项目确认适用条款。
