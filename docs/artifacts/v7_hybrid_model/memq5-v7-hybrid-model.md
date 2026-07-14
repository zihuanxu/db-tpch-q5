# Hybrid Model Offline Evaluation

Break-even means an observed adjacent fixed-ratio segment crosses the auto p50 latency.
No extrapolation is used; unavailable intervals remain null in JSON and CSV.

| SF | auto predicted | auto selected/realized | auto p50 ms | best fixed ratio | best fixed p50 ms | regret ms | regret % | break-even |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.24319614604075737 | 0.26209092658736605/0.26209092658736605 | 1.5528105 | 0.125 | 1.1597629999999999 | 0.3930475000000002 | 33.890329317282955 | [[0.25,0.375]] |
| 10 | 0.2887329010669868 | 0.28842544930278124/0.28842544930278124 | 10.9799595 | 0.375 | 10.054269 | 0.9256905 | 9.206939858084164 | [[0.25,0.375],[0.375,0.5]] |
