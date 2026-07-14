# V7 compact profiler evidence

This directory is a compact publication copy of the audited full V7 profiler bundle.
It preserves a length-stable sanitized NSYS report, four NSYS CSV exports, selected
NCU report data, collector logs, parsed summary values, and checksums needed to
inspect the evidence. Unrelated process-environment values are removed from the
NSYS copy before publication; each profile records the replacement count.

The following full-bundle content is intentionally omitted:

- `datasets/` and `evidence/` inputs.
- `profile.sqlite` and SQLite sidecars.
- `supported_metrics.txt`.
- The NCU metadata metric universe.
- Large collector metadata.json files.

Audit the retained full bundle before publication with exactly:

```text
python3 scripts/v7_profiler_bundle.py audit --directory FULL_BUNDLE
```
