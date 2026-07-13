# Paper, Learning, Process, And Open-Source Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the verified implementation and experiment evidence into a course-compliant paper, process record, learning/defense package, and reproducible public repository.

**Architecture:** A claim ledger is the control plane between engineering evidence and prose. Method/process/learning material can be written early from code, while one import script supplies final numerical macros and figure provenance to the paper only after evidence audits pass; release checks reject missing metadata, pending result markers, stale figures, untracked artifacts, and unverifiable commands.

**Tech Stack:** Markdown, LaTeX/计算机学报 template, BibTeX, XeLaTeX, Python 3.11, SVG/PDF figures, GitHub Actions, CMake/Conda/Docker, Tencent Docs source copies.

## Global Constraints

- Existing user-modified report, DOCX, handover guide, and defense cheatsheet are read and merged deliberately; they are never overwritten wholesale.
- Result statements and numeric macros require linked `VERIFIED` or `REJECTED` claim-ledger entries.
- Honest negative results and limitations remain in the paper; hypotheses are not rewritten after seeing data.
- The paper uses the course-accepted 计算机学报 template and contains Chinese/English title, abstract, keywords, author, student id, and affiliation.
- Final PDF and release docs contain no pending-result marker, fake citation, unsupported speedup, hidden failure, or claim that mapped/managed memory eliminates transfer.
- Tencent source Markdown is versioned in Git; the user-created Tencent shared-space link is recorded after permission is granted to the teacher.
- TPC-H tools and large raw/Arrow data are not redistributed; generator instructions, checksums, tiny fixture, synthetic generator, and publishable evidence are redistributed.
- Learning material explains what the code actually does, including CPU-built maps, GPU lineitem scope, exact revenue semantics, and project limitations.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `docs/research/CLAIM_LEDGER.md` | Claim id, hypothesis, state, code/evidence link, wording, and limitations. |
| `scripts/validate_claim_ledger.py` | State transition, evidence existence, and paper-reference checks. |
| `docs/process/01-选题与研究问题.md` through `07-最终完成情况.md` | Tencent-copyable process history. |
| `docs/learning/01-Q5与六表连接.md` through `07-论文与答辩问答.md` | Progressive takeover course. |
| `paper/main.tex` | Course paper root. |
| `paper/sections/*.tex` | Focused paper sections. |
| `paper/references.bib` | Verified primary references. |
| `paper/generated/results.tex` | Evidence-derived numeric macros. |
| `paper/generated/figures.tex` | Evidence-derived figure paths/captions. |
| `scripts/import_paper_evidence.py` | Audit then generate LaTeX values/tables/figures. |
| `scripts/check_paper.py` | Template, metadata, citation, marker, claim, and PDF checks. |
| `docs/defense/5-minute-script.md` | Short presentation script. |
| `docs/defense/10-minute-script.md` | Full presentation script. |
| `docs/defense/high-risk-questions.md` | Difficult questions and evidence-based answers. |
| `LICENSE`, `CITATION.cff`, `CONTRIBUTING.md`, `CHANGELOG.md` | Open-source metadata. |
| `Dockerfile`, `environment-cpu.yml`, `environment-gpu.yml`, `CMakePresets.json` | Reproducible environments. |
| `.github/workflows/cpu-ci.yml` | CPU build/test/converter/oracle CI. |
| `scripts/release_audit.py` | Final course/repository acceptance audit. |

## Task 1: Establish The Claim Ledger Before Writing Results

**Files:**
- Create: `docs/research/CLAIM_LEDGER.md`
- Create: `scripts/validate_claim_ledger.py`
- Create: `tests/python/test_claim_ledger.py`
- Modify: `docs/CURRENT_STATUS.md`

**Interfaces:**
- Consumes: Claim rows and code/evidence paths.
- Produces: `validate_ledger(path: Path, repo_root: Path) -> LedgerReport` and stable claim ids usable from LaTeX comments.

Required columns:

```markdown
| ID | RQ/H | Statement | State | Code | Test | Evidence | Paper Location | Limitation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C001 | RQ1/H1 | 专用 CPU 计划预计比 Arrow Acero 更快。 | PLANNED | `src/cpu/q5_specialized.cpp` | `tests/test_q5_specialized.cpp` | - | `paper/sections/06-results.tex` | 只针对固定 Q5。 |
```

- [ ] **Step 1: Write ledger validation tests**

```python
def test_verified_claim_requires_evidence(tmp_path):
    ledger = ledger_row(state="VERIFIED", evidence="-")
    with pytest.raises(ValueError, match="VERIFIED.*evidence"):
        validate_ledger(write_ledger(tmp_path, ledger), tmp_path)

def test_planned_claim_cannot_appear_as_final_result(tmp_path):
    ledger = ledger_row(state="PLANNED", paper_location="paper/sections/06-results.tex")
    report = validate_ledger(write_ledger(tmp_path, ledger), tmp_path)
    assert report.ok is False
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_claim_ledger.py
```

Expected: import failure.

- [ ] **Step 3: Implement parser and transition rules**

Accept only `PLANNED`, `IMPLEMENTED`, `VERIFIED`, `REJECTED`, and `SUPERSEDED`. `IMPLEMENTED` requires code and test paths; `VERIFIED`/`REJECTED` require evidence plus limitation text; `SUPERSEDED` requires the replacing claim id. Resolve every repository/evidence path and validate the referenced experiment checksum.

- [ ] **Step 4: Seed the ledger from approved RQs and hypotheses**

Create entries for Arrow canonical storage, exact SF1 correctness, Acero versus specialized CPU, copy/managed/mapped costs, cuDF comparison, hybrid overlap, scale-dependent ratio, cold/resident difference, and all known limitations. Initial states reflect the repository on that commit, not the desired final state.

- [ ] **Step 5: Run validation and commit**

Run:

```bash
pytest -q tests/python/test_claim_ledger.py
python scripts/validate_claim_ledger.py docs/research/CLAIM_LEDGER.md
```

Expected: tests pass and the seeded ledger is structurally valid.

```bash
git add docs/research/CLAIM_LEDGER.md docs/CURRENT_STATUS.md scripts/validate_claim_ledger.py tests/python/test_claim_ledger.py
git commit -m "docs: add evidence-controlled research claim ledger"
```

## Task 2: Build The Versioned Process Record

**Files:**
- Create: `docs/process/01-选题与研究问题.md`
- Create: `docs/process/02-系统设计方案.md`
- Create: `docs/process/03-实验设计.md`
- Create: `docs/process/04-阶段讨论记录.md`
- Create: `docs/process/05-实验执行日志.md`
- Create: `docs/process/06-问题与决策记录.md`
- Create: `docs/process/07-最终完成情况.md`
- Create: `docs/process/TENCENT_DOCS.md`
- Create: `scripts/validate_process_docs.py`
- Create: `tests/python/test_process_docs.py`

**Interfaces:**
- Consumes: Git history, approved design, plan tasks, experiment manifests, and decision log.
- Produces: Seven copy-ready documents and a Tencent sharing record.

- [ ] **Step 1: Write process-document structure tests**

Require every file to contain title, date range, objective, activity/evidence table, unresolved issues, and next decision. Require the experiment log to link every formal experiment id; require final completion to distinguish complete, failed, excluded, and externally pending items.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_process_docs.py
```

Expected: missing-file failures.

- [ ] **Step 3: Write research/design/experiment source documents**

Derive the first three from the approved spec using student-readable wording. Include exact RQ1-RQ5, H1-H5, matrix, metric definitions, oracle hierarchy, and why the project is a fixed-query executor rather than a DBMS.

- [ ] **Step 4: Reconstruct dated discussion and decision records honestly**

Use Git commit dates and existing docs for chronology. Each reconstructed entry is marked `根据仓库记录整理` and links the commit/document that supports it. Do not invent meetings, teacher comments, dates, or decisions absent from evidence.

- [ ] **Step 5: Generate experiment log and final status from manifests**

`validate_process_docs.py --sync-experiments` adds one row per evidence bundle with id, date, commit, dataset, status, correctness, and main observation. It never edits narrative conclusions automatically.

- [ ] **Step 6: Record Tencent sharing state**

`TENCENT_DOCS.md` stores document title, repository source path, Tencent URL, last sync commit, last sync date, and teacher-access verification. Before the user creates the share, values are represented by state `EXTERNAL_ACTION_REQUIRED`; the release audit treats that as blocking final course delivery but not engineering work.

- [ ] **Step 7: Validate and commit process documents**

Run:

```bash
pytest -q tests/python/test_process_docs.py
python scripts/validate_process_docs.py docs/process
```

Expected: all structural/history checks pass; only Tencent sharing may remain externally required.

```bash
git add docs/process scripts/validate_process_docs.py tests/python/test_process_docs.py
git commit -m "docs: add versioned research process record"
```

## Task 3: Write The Progressive Takeover Course

**Files:**
- Create: `docs/learning/01-Q5与六表连接.md`
- Create: `docs/learning/02-Apache-Arrow内存布局.md`
- Create: `docs/learning/03-CPU物理计划.md`
- Create: `docs/learning/04-CUDA三种内存模式.md`
- Create: `docs/learning/05-CPU-GPU混合执行.md`
- Create: `docs/learning/06-实验方法与统计.md`
- Create: `docs/learning/07-论文与答辩问答.md`
- Create: `scripts/check_learning_links.py`
- Create: `tests/python/test_learning_materials.py`
- Modify: `docs/PROJECT_HANDOVER_GUIDE.md`
- Modify: `docs/DEFENSE_CHEATSHEET.md`

**Interfaces:**
- Consumes: Final source entry points, tests, diagrams, evidence, and limitations.
- Produces: Seven lessons where each code link resolves and each exercise has a checkable answer section.

- [ ] **Step 1: Write learning-material tests**

Require each lesson to contain `你需要先知道`, `核心原理`, `代码入口`, `自己检查`, `老师可能追问`, and `一分钟复述`. Resolve every `path:line` reference against the current commit and reject references to removed custom-store entry points.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_learning_materials.py
```

Expected: missing-file failures.

- [ ] **Step 3: Write lessons 1-3 from query to CPU execution**

Explain lineitem as an order line, each Q5 condition and its business meaning, Arrow buffers/schema/chunks/dictionary/Decimal128, filter propagation maps, date half-open interval, per-thread local aggregation, and Acero versus specialized plan. Include hand-tracing of one accepted and one rejected lineitem.

- [ ] **Step 4: Write lessons 4-5 on GPU memory and hybrid execution**

Distinguish:

- copy: host data staged then explicitly transferred to device memory;
- managed: one virtual address with runtime-managed migration/prefetch, not no-transfer;
- mapped: pinned host pages mapped into UVA and read remotely over PCIe;
- hybrid: disjoint Arrow batch slices processed concurrently, then exact local results merged.

Tie each statement to code and one measured/profiler artifact.

- [ ] **Step 5: Write lessons 6-7 on experiments and defense**

Explain warmup/repeat, cold/resident, median/p95/stddev, throughput, result hash versus oracle, failure preservation, claim ledger, limitations, and how to answer when a hypothesis is rejected.

- [ ] **Step 6: Merge, not replace, existing handover documents**

Read the user's current versions, preserve useful explanations and tone, update only obsolete technical claims/links, and add a generated `Reviewed against commit` line. Keep a `git diff --word-diff` artifact during review.

- [ ] **Step 7: Validate and commit**

Run:

```bash
pytest -q tests/python/test_learning_materials.py
python scripts/check_learning_links.py docs/learning docs/PROJECT_HANDOVER_GUIDE.md docs/DEFENSE_CHEATSHEET.md
```

Expected: every code/evidence link resolves and lessons reflect final code.

```bash
git add docs/learning docs/PROJECT_HANDOVER_GUIDE.md docs/DEFENSE_CHEATSHEET.md scripts/check_learning_links.py tests/python/test_learning_materials.py
git commit -m "docs: add Q5 project takeover course"
```

## Task 4: Establish The Course-Accepted Paper Template And Skeleton

**Files:**
- Create: `paper/main.tex`
- Create: `paper/metadata.tex`
- Create: `paper/sections/01-introduction.tex`
- Create: `paper/sections/02-background.tex`
- Create: `paper/sections/03-design.tex`
- Create: `paper/sections/04-implementation.tex`
- Create: `paper/sections/05-methodology.tex`
- Create: `paper/sections/06-results.tex`
- Create: `paper/sections/07-limitations.tex`
- Create: `paper/sections/08-conclusion.tex`
- Create: `paper/references.bib`
- Create: `paper/Makefile`
- Create: `paper/template/README.md`
- Create: `scripts/check_paper.py`
- Create: `tests/python/test_paper_structure.py`

**Interfaces:**
- Consumes: Teacher-provided or officially published 计算机学报 template and confirmed author metadata.
- Produces: `paper/main.pdf` and a template provenance record with source URL/file, retrieved date, SHA-256, and course acceptance confirmation.

- [ ] **Step 1: Write paper-structure tests**

Require all section files, Chinese/English title/abstract/keywords, nonempty author/student-id/affiliation fields, bibliography entries with DOI or official URL, generated evidence includes, and template provenance. Reject the strings `RESULT-PENDING`, `EXPERIMENT-NOT-RUN`, empty citation keys, and undefined LaTeX references in final mode.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_paper_structure.py
```

Expected: missing-file failures.

- [ ] **Step 3: Archive and identify the accepted template**

Prefer the exact file supplied by the course. Record its original filename, SHA-256, source, date, and a statement that it is the version accepted by the teacher. If the course file is unavailable, use the official 计算机学报 author template and mark course acceptance as externally required; do not claim compliance until confirmed.

- [ ] **Step 4: Populate verified author metadata**

Read name, student id, and affiliation from the user's course registration/submission record. `check_paper.py` fails when any field is absent or contains generic sample values. This is an external-data check, not a value the implementation may infer.

- [ ] **Step 5: Build the section skeleton with real method content**

Write complete introduction, background, system design, implementation, methodology, and limitations from approved design/code. The background may use `hashjoin-cpu` only as documented prior-course motivation for cache/TLB, radix partitioning, direct addressing, and NUMA; it must not present that code as part of the final Q5 executable. Results contains tables/figure includes driven by generated macros and guarded by `\IfFileExists`; draft mode prints a conspicuous pending-evidence box, while final mode treats missing generated evidence as a LaTeX error.

- [ ] **Step 6: Add verified primary references**

Include the TPC-H specification, Apache Arrow format/C++ documentation or paper, CUDA programming guide, CUDA unified memory/UVA documentation, cuDF/RAPIDS documentation or paper, DuckDB paper, and directly relevant CPU-GPU database research. Verify titles/authors/year/DOI against primary publisher/project sources before adding BibTeX.

- [ ] **Step 7: Build draft and run structural checks**

Run:

```bash
pytest -q tests/python/test_paper_structure.py
make -C paper draft
python scripts/check_paper.py --mode draft paper/main.tex paper/main.pdf
```

Expected: draft PDF builds; method sections and metadata are valid; draft may explicitly state evidence not yet imported.

- [ ] **Step 8: Commit the paper foundation**

```bash
git add paper scripts/check_paper.py tests/python/test_paper_structure.py
git commit -m "docs: add Computer Journal paper foundation"
```

## Task 5: Import Verified Evidence And Finish The Paper

**Files:**
- Create: `scripts/import_paper_evidence.py`
- Create: `tests/python/test_paper_evidence.py`
- Create through script: `paper/generated/results.tex`
- Create through script: `paper/generated/figures.tex`
- Modify: `paper/sections/06-results.tex`
- Modify: `paper/sections/08-conclusion.tex`
- Modify: `paper/main.tex`
- Modify: `docs/research/CLAIM_LEDGER.md`

**Interfaces:**
- Consumes: Audited SF1/SF10 evidence ids and valid claim ledger.
- Produces: deterministic LaTeX macros/tables/figures and final evidence-aware prose.

- [ ] **Step 1: Write evidence-import tests**

```python
def test_import_rejects_unverified_claim(tmp_path):
    with pytest.raises(ValueError, match="C005.*VERIFIED"):
        import_evidence(fixture_experiment(), ledger_with("C005", "IMPLEMENTED"), tmp_path)

def test_generated_macro_has_provenance(tmp_path):
    import_evidence(fixture_experiment(), verified_ledger(), tmp_path)
    text = (tmp_path / "results.tex").read_text()
    assert "\\newcommand{\\SpecializedCpuMedianSfOne}" in text
    assert "% experiment_id=" in text
    assert "% summary_sha256=" in text
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_paper_evidence.py
```

Expected: import failure.

- [ ] **Step 3: Implement audited macro/table generation**

Call `audit_experiment.py`; require correctness pass and exact matrix coverage; require referenced claim states. Generate macros for medians, p95, speedups computed from medians, memory, transfer breakdown, best hybrid ratio, and overlap ratio. Generate tables from summary CSV and include figure files by checksum.

- [ ] **Step 4: Write results by research question**

For RQ1-RQ5, report evidence, interpretation, whether its hypothesis is supported/rejected, and limitations. Distinguish cold/resident, SF1/SF10, total/kernel time, and statistical variation. Explain negative or inconclusive results without changing the preregistered hypothesis text. Rewrite transitions and interpretation in a natural student research voice: prefer concrete observations such as “本次实验中” and “我认为可能的原因是”, keep uncertainty where evidence is incomplete, and retain real design weaknesses; do not deliberately insert factual errors or artificial defects.

- [ ] **Step 5: Rewrite abstract and conclusion after results**

State concrete implemented scope, dataset/method, two or three verified findings, and main limitation. Avoid unsupported generalization beyond Q5, the tested hardware, and SF1/SF10.

- [ ] **Step 6: Build and audit final PDF twice**

Run:

```bash
python scripts/import_paper_evidence.py --sf1 results/experiments/sf1-final-20260713 --sf10 results/experiments/sf10-final-20260713 --ledger docs/research/CLAIM_LEDGER.md --output paper/generated
make -C paper clean final
make -C paper final
python scripts/check_paper.py --mode final paper/main.tex paper/main.pdf
pdftotext paper/main.pdf /tmp/memq5-paper.txt
rg -n 'RESULT-PENDING|EXPERIMENT-NOT-RUN|undefined|Citation.*undefined' paper /tmp/memq5-paper.txt
```

Expected: both builds succeed; final check passes; final `rg` returns no matches.

- [ ] **Step 7: Commit final paper sources and PDF**

```bash
git add paper docs/research/CLAIM_LEDGER.md scripts/import_paper_evidence.py tests/python/test_paper_evidence.py
git commit -m "docs: finalize evidence-backed Q5 paper"
```

## Task 6: Complete Open-Source Reproducibility Metadata

**Files:**
- Create: `LICENSE`
- Create: `CITATION.cff`
- Create: `CONTRIBUTING.md`
- Create: `CHANGELOG.md`
- Create: `Dockerfile`
- Create: `.dockerignore`
- Modify: `README.md`
- Modify: `.gitignore`
- Modify: `scripts/prepare_tpch_q5_data.py`
- Modify: `scripts/package_submission.py`
- Create: `tests/test_release_files.py`

**Interfaces:**
- Consumes: Final build/data/experiment/paper commands.
- Produces: Public repository onboarding, Apache-2.0 licensing, citation metadata, CPU/GPU setup, and legal data-generation flow.

- [ ] **Step 1: Write release-file tests**

Check SPDX license identity, CITATION required fields, README commands against existing CLI flags/files, Docker build stages, no tracked `.tbl` outside tiny fixtures, no large Arrow IPC, no absolute private paths, and package content allowlist.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/test_release_files.py
```

Expected: failures for missing release files.

- [ ] **Step 3: Add license, citation, contribution, and changelog**

Use Apache License 2.0 unless the repository's dependency/legal audit requires a compatible alternative. CITATION includes project title, version, authors from verified metadata, release date, repository URL, and preferred citation. Changelog separates baseline prototype from Arrow final implementation and evidence release.

- [ ] **Step 4: Add reproducible Docker and data instructions**

Use an NVIDIA CUDA 12 development image, install pinned build dependencies, build Arrow 23.0.1 with the committed script, configure `gpu-release`, and set a self-check entrypoint. README documents CPU-only quickstart, GPU setup, legal TPC-H tool acquisition, dbgen commands, Arrow conversion, each backend, correctness checks, smoke/formal experiments, paper build, evidence layout, and limitations.

- [ ] **Step 5: Harden packaging**

Package source, tiny fixture, generator, environment files, compact final evidence, paper sources/PDF, process/learning docs, and checksums. Exclude credentials, caches, full `.tbl`, Arrow IPC, build trees, `.nsys-rep`, `.ncu-rep`, and unrelated hashjoin build outputs.

- [ ] **Step 6: Run release-file and package tests**

Run:

```bash
pytest -q tests/test_release_files.py
python scripts/package_submission.py --output /tmp/memq5-submission.zip
unzip -l /tmp/memq5-submission.zip
```

Expected: tests pass; package contains required items and no prohibited data.

- [ ] **Step 7: Commit open-source packaging**

```bash
git add LICENSE CITATION.cff CONTRIBUTING.md CHANGELOG.md Dockerfile .dockerignore README.md .gitignore scripts/prepare_tpch_q5_data.py scripts/package_submission.py tests/test_release_files.py
git commit -m "docs: complete open-source reproducibility package"
```

## Task 7: Add CPU CI And GPU Runbook Gates

**Files:**
- Create: `.github/workflows/cpu-ci.yml`
- Create: `scripts/ci_cpu.sh`
- Modify: `docs/GPU_SERVER_RUNBOOK.md`
- Create: `tests/test_ci_config.py`

**Interfaces:**
- Consumes: CPU environment/preset, converter fixture, CTest, pytest, and tiny oracle.
- Produces: deterministic GitHub CPU CI and an exact manual GPU validation command sequence.

- [ ] **Step 1: Write CI configuration tests**

Parse workflow YAML and assert checkout, environment cache key includes lock hash, CPU preset build, CTest, pytest, tiny Arrow conversion, tiny oracle, and artifact upload on failure. Assert no GPU requirement in ordinary CI.

- [ ] **Step 2: Verify tests fail and implement CI**

Run:

```bash
pytest -q tests/test_ci_config.py
```

Expected before implementation: missing workflow failure.

`scripts/ci_cpu.sh` performs configure, build, CTest, pytest, tiny conversion with batch size 2, and Acero/specialized oracle comparison using `set -euo pipefail`.

- [ ] **Step 3: Run CI commands locally**

Run:

```bash
bash scripts/ci_cpu.sh
pytest -q tests/test_ci_config.py
```

Expected: full CPU CI passes.

- [ ] **Step 4: Update GPU runbook**

Document Arrow CUDA verification, GPU preset, all CTest, cuDF tests, sanitizer commands, validation gate, formal matrix, profiling, artifact checksums, and explicit failure classification.

- [ ] **Step 5: Commit CI**

```bash
git add .github/workflows/cpu-ci.yml scripts/ci_cpu.sh docs/GPU_SERVER_RUNBOOK.md tests/test_ci_config.py
git commit -m "ci: validate Arrow CPU Q5 path"
```

## Task 8: Produce Defense Material And Final Release Audit

**Files:**
- Create: `docs/defense/5-minute-script.md`
- Create: `docs/defense/10-minute-script.md`
- Create: `docs/defense/high-risk-questions.md`
- Create: `docs/defense/final-architecture.md`
- Create: `scripts/release_audit.py`
- Create: `tests/python/test_release_audit.py`
- Modify: `docs/SUBMISSION_CHECKLIST.md`
- Modify: `docs/COMPLETION_AUDIT.md`

**Interfaces:**
- Consumes: Final code, evidence, paper, process record, learning material, repository metadata, and Tencent link state.
- Produces: Presentation scripts and one machine-readable `release-audit.json`.

- [ ] **Step 1: Write release-audit tests**

Use fixture repositories to test missing license, broken code link, unverified paper claim, absent SF10 evidence, failed core backend, missing overlap proof, paper marker, package containing `.tbl`, and Tencent external-action state.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_release_audit.py
```

Expected: import failure.

- [ ] **Step 3: Write 5-minute and 10-minute scripts**

Both scripts follow: problem and Q5, Arrow canonical data, specialized/Acero/GPU memory modes, hybrid, experimental method, verified findings, limitations, conclusion. The short script has one main figure; the long script covers RQ1-RQ5. Every number includes its claim id in an HTML comment for audit.

- [ ] **Step 4: Write high-risk answers**

Cover at least: why this is not a DBMS; why six tables; why same nation; Arrow's actual role; copy/mapped/managed differences; managed migration; mapped preparation copy; why CPU builds maps; cuDF fairness; exact decimal bug; result hash limits; cold/resident; thread-label bug; hybrid overlap proof; negative result validity; SF1/SF10 sufficiency; reproducibility; project limitations; and what the student personally understands now.

- [ ] **Step 5: Implement final audit**

The audit runs/reads:

- clean Git and branch/commit;
- CPU CI and GPU validation summaries;
- SF1/SF10 evidence audits;
- claim ledger validation;
- paper final check and PDF hash;
- process/learning link checks;
- license/citation/environment/README/package tests;
- Tencent sharing state;
- public GitHub remote visibility check when network is available.

Write JSON with `pass`, `fail`, and `external_action_required` arrays. Final course readiness requires `fail=[]` and `external_action_required=[]`.

- [ ] **Step 6: Run the final audit**

Run:

```bash
pytest -q tests/python/test_release_audit.py
python scripts/release_audit.py --sf1 results/experiments/sf1-final-20260713 --sf10 results/experiments/sf10-final-20260713 --paper paper/main.pdf --output results/release-audit.json
```

Expected: engineering checks pass; Tencent/GitHub external actions are explicitly listed until the user completes them.

- [ ] **Step 7: Commit defense and audit material**

```bash
git add docs/defense docs/SUBMISSION_CHECKLIST.md docs/COMPLETION_AUDIT.md scripts/release_audit.py tests/python/test_release_audit.py results/release-audit.json
git commit -m "docs: add final defense and release audit"
```

## Plan Acceptance

Run:

```bash
bash scripts/ci_cpu.sh
python scripts/run_gpu_validation.py --preset gpu-release --tiny-dataset /tmp/memq5-tiny-arrow --sf1-dataset /tmp/memq5-sf1-arrow --official-q5 'data/tpch_tools/TPC-H V3.0.1/dbgen/answers/q5.out' --output results/validation/gpu-final
python scripts/validate_claim_ledger.py docs/research/CLAIM_LEDGER.md
python scripts/validate_process_docs.py docs/process
python scripts/check_learning_links.py docs/learning docs/PROJECT_HANDOVER_GUIDE.md docs/DEFENSE_CHEATSHEET.md
make -C paper clean final
python scripts/check_paper.py --mode final paper/main.tex paper/main.pdf
python scripts/package_submission.py --output /tmp/memq5-submission.zip
python scripts/release_audit.py --sf1 results/experiments/sf1-final-20260713 --sf10 results/experiments/sf10-final-20260713 --paper paper/main.pdf --output results/release-audit.json
```

Expected:

- Code, data preparation, correctness, evidence, paper, process, learning, and package checks pass.
- Paper numbers resolve through generated macros to audited evidence and verified claims.
- Final PDF contains honest limitations and no pending evidence markers.
- The repository can be reproduced without redistributing prohibited TPC-H data.
- Any remaining Tencent share or public GitHub action is visible as external, never silently marked complete.
- The student has a progressive explanation path plus rehearsable short/long defenses grounded in the final implementation.
