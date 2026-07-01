# EFFICIENT MAIN-MEMORY HASH JOINS ON MULTI-CORE CPUS: TUNING TO THE UNDERLYING HARDWARE

[![Build](https://github.com/mars-research/vldb13-eth-hashjoin/actions/workflows/build.yml/badge.svg)](https://github.com/mars-research/vldb13-eth-hashjoin/actions/workflows/build.yml)

[![Docs](https://github.com/mars-research/vldb13-eth-hashjoin/actions/workflows/docs.yml/badge.svg)](https://github.com/mars-research/vldb13-eth-hashjoin/actions/workflows/docs.yml)

Change the join/hardware parameters at [src/prj_params.h](https://github.com/mars-research/vldb13-eth-hashjoin/blob/master/src/prj_params.h)

[Documentation](http://mars-research.github.io/vldb13-eth-hashjoin)

A fork of the ETH Zurich's VLDB'13 hashjoin obtained from [web archive](https://web.archive.org/web/20220414154544/https://systems.ethz.ch/research/data-processing-on-modern-hardware/projects/parallel-and-distributed-joins.html) (Under _Hash joins source code for the VLDB'13 paper_)

The original publication can be found at https://dl.acm.org/doi/abs/10.14778/2732219.2732227.

**WARNING**: run `enable_hugepages.sh` and `constant_frequency.sh` from kvstore repo before running your benchmarks!!!

## Course Assignment Extensions

This working tree extends the original ETH hash join code for the memory join
assignment:

- VJ: vector-index join with `--payload-width=1|2|4` and optional `--vj-hugepage`.
- PRVJ: radix partitioning followed by vector join inside each partition.
- Built-in `sort-merge` baseline for the extended comparison script.
- NPO memory accounting, including primary buckets and overflow bucket buffers.
- Star join modes: `--starjoin=npo|pro|vj --sf=<N> -n <threads>`.
- Experiment scripts for PRVJ tuning, algorithm comparison, and plotting.

Quick local validation:

```bash
scripts/self_check_assignment.sh
```

Representative commands:

```bash
./src/mchashjoins -a VJ --payload-width=1 -n 64 -r $((1 << 24)) -s $((1 << 30)) --basic-numa
./src/mchashjoins -a VJ --payload-width=1 --vj-hugepage -n 16 -r $((1 << 24)) -s $((1 << 24))
./src/mchashjoins -a PRVJ --payload-width=4 -n 64 -r $((1 << 24)) -s $((1 << 30)) --basic-numa
./src/mchashjoins -a sort-merge -n 64 -r $((1 << 20)) -s $((1 << 30)) --basic-numa
./src/mchashjoins --starjoin=vj --payload-width=1 --sf=100 -n 64 --basic-numa
```

Full comparison workflow:

```bash
scripts/run_prvj_tuning.sh --run
scripts/run_extended_algo_comparison.sh --run --prvj-tuning-csv path/to/prvj-tuning.csv
scripts/plot_extended_algo_comparison.py extended-algo-comparison.summary.csv --format svg
```

The assignment status and remaining experimental caveats are summarized in
[`docs/FINAL_REPORT.md`](docs/FINAL_REPORT.md).
The integrated course report is [`docs/COURSE_REPORT.md`](docs/COURSE_REPORT.md)
and [`docs/COURSE_REPORT.docx`](docs/COURSE_REPORT.docx).
