# Integrated Course Submission

This repository combines two deliverable parts of the course project.

## Repository Layout

| Path | Purpose |
|---|---|
| `hashjoin-cpu/` | CPU hash join framework based on ETH Zurich VLDB 2013 hashjoin |
| repository root | TPC-H Q5 CPU/GPU query engine |
| `docs/` | TPC-H Q5/GPU documentation |
| `hashjoin-cpu/docs/` | CPU hashjoin reports, figures, and raw experiment data |

## Main Documents

| Document | Path |
|---|---|
| Integrated CPU hashjoin course report | `hashjoin-cpu/docs/COURSE_REPORT.docx` |
| CPU hashjoin final report | `hashjoin-cpu/docs/EXPERIMENT_REPORT.docx` |
| Starjoin midterm report | `hashjoin-cpu/docs/MIDTERM_REPORT.docx` |
| CPU hashjoin delivery index | `hashjoin-cpu/docs/FINAL_REPORT.md` |
| TPC-H Q5/GPU final report | `docs/FINAL_REPORT.md` |

## CPU Hashjoin Reproduction

```bash
cd hashjoin-cpu
scripts/self_check_assignment.sh
```

Representative full-sweep and starjoin scripts are in:

- `hashjoin-cpu/scripts/run_extended_algo_comparison.sh`
- `hashjoin-cpu/scripts/run_starjoin_comparison.sh`
- `hashjoin-cpu/scripts/run_prvj_tuning.sh`

Final CPU hashjoin data and figures are in:

- `hashjoin-cpu/docs/data/`
- `hashjoin-cpu/docs/assets/`

## TPC-H Q5/GPU Reproduction

```bash
cmake -S . -B build -DMEMQ5_ENABLE_CUDA=OFF -DMEMQ5_ENABLE_TESTS=ON
cmake --build build
ctest --test-dir build --output-on-failure
python3 scripts/self_check.py
```

CUDA builds require a machine with `nvcc` and a working NVIDIA driver:

```bash
cmake -S . -B build-cuda -DMEMQ5_ENABLE_CUDA=ON -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-cuda
ctest --test-dir build-cuda --output-on-failure
```

## Notes

The CPU hashjoin framework is integrated as a subdirectory so that its autotools
build, scripts, reports, and raw data remain self-contained. The TPC-H Q5/GPU
code remains at the repository root because it uses a separate CMake build.
