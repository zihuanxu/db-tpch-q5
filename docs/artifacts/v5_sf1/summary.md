# SF1 Formal Summary

All 19 configurations completed 10 measured runs with the same official result
hash. The lowest median internal query time was specialized CPU with 16 threads
at 61.414 ms. Its scaling flattened after 8 threads and slightly regressed at
32 threads.

The three CUDA memory modes ranked copy, managed, then mapped by median internal
time. Mapped avoided an explicit H2D phase but had a longer kernel and total
time, so `h2d_ms=0` did not mean transfer was free. Managed prefetch did not
outperform explicit copy in this SF1 setup.

cuDF's query phase was faster than the three custom full-GPU paths, but its
median process time was much larger because every cold sample starts Conda,
loads Arrow, converts six tables, runs generic joins/groupby, and exits. These
two timing columns answer different questions and must not be mixed.

Hybrid improved as the CPU share increased from 25% to 75%, but even the best
ratio remained slower than pure specialized CPU. The measured backend-duration
overlap was positive; without an Nsight timeline it is not evidence that the
CPU scan overlapped the CUDA kernel specifically.
