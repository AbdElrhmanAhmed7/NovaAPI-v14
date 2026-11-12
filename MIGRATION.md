# MIGRATION · Moving the project to the repository root

**Date: 2026-11-12** · Branch `main` · Base commit `a645012` (`v10.5`)

> A one-time file: it explains what was moved, why, and how to apply the same change
> to any other copy you have and push it to GitHub. If your repo already has the new
> layout, this file is just a record.

---

## 1. The problem that existed (not cosmetic)

The tree pushed to GitHub looked like this:

```
<git root>/
├── repo/
│   ├── .github/workflows/backend.yml     ← GitHub never read this
│   └── NOVA/backend/…                    ← all the code
├── uploads/…                             ← a text file copied from the GitHub UI
├── verification/…
├── NOVA-backend-final.zip
└── nova-backend-finish.patch
```

GitHub reads workflows **only from `<git root>/.github/workflows`**, and the workflow
itself says `paths: ["NOVA/backend/**"]` and `working-directory: NOVA/backend`.
Both were one level too deep, so the result was:

* **Not a single job ran on any push.** Every gate (ruff · mypy · content · audit ·
  contract · pytest · campaign) was decoration on GitHub.
* The tests could not see the defect, because they looked for the **nearest** directory
  containing `.github/workflows` and treated it as the repository root — so they found
  `repo/` and accepted it. In other words, the gate confirmed that it was correct inside
  the wrong wrapper.

## 2. The new layout

```
<git root>/
├── README.md · PROJECT_STATUS.md · MIGRATION.md
├── .github/workflows/backend.yml         ← GitHub sees it now
├── NOVA/backend/…                        ← unchanged, not a line of code moved
├── tools/                                ← verify.sh · make_release.sh · restructure_repo.sh
├── verification/                         ← run evidence (dated) + archive/
├── releases/                             ← generated patch and zip
└── reference/scratch/                    ← payload-capture harness
```

### Migration table

| Before | After | Note |
| --- | --- | --- |
| `repo/.github/` | `.github/` | This is what actually runs CI |
| `repo/NOVA/` | `NOVA/` | The same path the workflow points to |
| `repo/README.md` | `README.md` | |
| `repo/PROJECT_STATUS.md` | `PROJECT_STATUS.md` | |
| `repo/reference/` | `reference/` | |
| `repo/.gitignore` | `.gitignore` | |
| `verification/*.log`, `campaign.json` | `verification/archive/` | Previous session runs (history) |
| `NOVA-backend-final.zip` | `releases/nova-backend-final.zip` | Regenerated after the move |
| `nova-backend-finish.patch` | *(removed)* | Already applied in `main` — see §3 |
| `uploads/…txt` | *(removed)* | Text copied from the GitHub UI, not project content |

Every move used `git mv`, so file history (`git log --follow`) is preserved.

## 3. Why was the old patch removed?

`nova-backend-finish.patch` was the previous session's delta, and it is **already
applied** in `main` (commit `a645012`). The `git am nova-backend-finish.patch`
instructions that were in `PROJECT_STATUS.md` would have failed or duplicated work.
The new patch in `releases/` is **this session's delta only** above `a645012`, and the
instructions were updated. The old file is still in git history if anyone needs it:

```bash
git show a645012:nova-backend-finish.patch > /tmp/old.patch
```

## 4. How to apply the migration to another copy

If you have an old clone that still contains `repo/` (or any other wrapper — see §7):

```bash
bash tools/restructure_repo.sh --dry-run   # show the steps without executing
bash tools/restructure_repo.sh             # execute (idempotent – safe to repeat)
```

The script is not tied to the name `repo/`: it looks for any immediate directory inside
the git root that contains `NOVA/backend` and treats it as the wrapper. If it finds more
than one, it refuses to guess.

Or simply pull the new commit: `git pull origin main` — the migration is inside it.

**Practical note:** if you have an old `.venv` inside `repo/NOVA/backend/.venv`, the
absolute paths inside its scripts will be wrong after the move. The easiest fix:

```bash
cd NOVA/backend && rm -rf .venv && make install
# or without make:
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

## 5. Verify before pushing

```bash
bash tools/verify.sh          # all gates + campaign, writes verification/<date>/gates.log
```

What you must see: `✓ all gates passed`, and `15 / 15` missions in the campaign report.
After pushing, open the **Actions** tab on GitHub: a workflow named `backend` should
appear and run — this is the first time it actually runs.

## 6. Pushing

```bash
git push origin main
```

This requires your GitHub permissions (the environment where the migration was done
could not push). If `git` asks for credentials or returns an authentication error,
reconnect your GitHub account in Arena.

---

## English summary

The delivered tree wrapped the project in `repo/`, so `.github/workflows` sat one
level below the git root and the workflow's own filters (`NOVA/backend/**`) did
not match the real paths: **CI never ran**. This migration promotes the project
to the git root with `git mv` (history preserved), files the previous run logs
under `verification/archive/`, regenerates the delivery artefacts into
`releases/`, and drops a stray `uploads/` text file plus the already-merged
patch. The repository gates now anchor on the **git root**
(`tests/conftest.py::repository_root`) instead of the nearest `.github`
directory, so the same mistake fails the suite instead of passing quietly.
Apply it elsewhere with `bash tools/restructure_repo.sh`, verify with
`bash tools/verify.sh`, then `git push origin main`.

---

## 7. The problem repeated: the `upstream/` wrapper (third session)

**Date: 2026-11-12** · The commit that arrived from GitHub: `9c4873f` (`v11`)

The version pushed to `AbdElrhmanAhmed7/FastAPI-v11` looked like this:

```
<git root>/
└── upstream/
    ├── .github/workflows/backend.yml   ← GitHub never read this (again)
    ├── NOVA/backend/…
    ├── README.md · PROJECT_STATUS.md · MIGRATION.md
    ├── tools/ · verification/ · reference/
```

Exactly the same defect, just with a different wrapper name (`upstream/` instead of
`repo/`). The result:

* **Not a single job would run** on any push — the workflow was one level below the git
  root, and its filters (`paths: ["NOVA/backend/**"]`) did not match the real path
  (`upstream/NOVA/backend/**`).
* This time **the tests caught the defect immediately**: the first `pytest` run on a
  clean clone from GitHub returned **8 failed + 4 errors** — all in
  `test_project_docs.py` and `test_ci_workflow.py`. The failure happened because
  `conftest.repository_root()` anchors on `.git` (as introduced in §1), so it found the
  real root empty of `README.md` and `.github/workflows`. In other words, the gate built
  to prevent this mistake actually worked.

### What was done

| Step | Command |
| --- | --- |
| Detect the wrapper and move everything to the root with `git mv` | `bash tools/restructure_repo.sh` |
| Generalise the script to any wrapper name (not just `repo/`) | `tools/restructure_repo.sh` |
| Rebuild `.venv` after the move (absolute paths break) | `cd NOVA/backend && make install` |
| Re-run every gate | `bash tools/verify.sh --live` |

After the move: **437 tests passing, 0 failures**, and `✓ all gates passed`.

### The lesson

The wrapper is not fixed by renaming the folder; it is fixed by making
**git root = project root**. If you upload the tree to GitHub manually, upload the
**contents** of the folder, not the folder itself:

```bash
cd <git root>
git add -A && git commit -m "…" && git push origin main
ls .github/workflows/backend.yml   # must exist
```

And after any push: the **Actions** tab must show the `backend` workflow. If it is empty,
there is another wrapper.

---

## 8. The wrapper returned a third time: `repo/` in `FastAPI-v11.5` (fourth session)

**Date: 2026-11-12** · The commit that arrived from GitHub: `434b2e3` (`v11.5`) ·
Repo: `AbdElrhmanAhmed7/FastAPI-v11.5`

```
<git root>/
└── repo/
    ├── .github/workflows/backend.yml   ← GitHub never read this (third time)
    ├── NOVA/backend/…
    ├── README.md · PROJECT_STATUS.md · MIGRATION.md
    ├── tools/ · verification/ · releases/ · reference/
```

So after two full sessions fixing the same defect, the tree was pushed inside a wrapper
again. The confirmed result: **not a single job would run** on that repo — no lint, no
tests, no campaign. Every number in the documentation was correct *locally*, and CI
could not see it.

### Why does it keep happening?

The mistake is not in the code; it is in **the upload method**. Uploading a whole folder
through the GitHub UI (Add file → Upload files), or extracting an archive that contains a
root folder, makes GitHub store the folder itself as part of the path. The project then
becomes `<root>/repo/…` instead of `<root>/…`. The fix is not renaming the folder — the
fix is **git root = project root**.

### What was done

| Step | Command |
| --- | --- |
| Detect the wrapper and move everything to the root with `git mv` (history preserved) | `bash tools/restructure_repo.sh` |
| New layout gate needing only bash + git (0.1 seconds) | `bash tools/check_layout.sh` |
| Wire it as the first verification gate, before the `.venv` check | `tools/verify.sh` |
| 9 regression tests that plant the mistake in temporary repositories | `tests/test_repo_layout_guard.py` |
| Re-run all gates + campaign + probes | `bash tools/verify.sh --live` |

After the move: **446 tests passing, 0 failures**, `✓ all gates passed`, and
**14 probes · 0 failures** against a real uvicorn server.

### What exactly does the new gate reject?

1. `NOVA/backend` is not at the git root.
2. `.github/workflows/backend.yml` is missing from the root.
3. Any subdirectory containing a second copy of the project (**any name** — `repo/`,
   `upstream/`, `NOVA-main/` … not a saved list of names).
4. Any workflow buried below the root (GitHub ignores it silently).
5. `paths:` filters or `working-directory` in the workflow that do not match the real
   path.

If you copied the tree without `.git` (zip), the gate says “skipped” instead of failing
for the wrong reason.

### The correct way to push (choose one)

```bash
# (a) git — preferred, history preserved
git clone https://github.com/AbdElrhmanAhmed7/FastAPI-v11.5 && cd FastAPI-v11.5
git am /path/to/releases/nova-backend-final.patch
bash tools/check_layout.sh                  # ✓ layout ok
git push origin main

# (b) Manual upload through the GitHub UI — drag the *contents* of the folder, not the folder
#     Open the folder on your machine, select all files (Ctrl+A), and drag them onto the page.
#     Do not drag the folder itself.
```

**After any push:** the **Actions** tab must show a workflow named `backend` running
(3 jobs). If it is empty, run `bash tools/check_layout.sh` — it will tell you where the
defect is and name the fix.

---

## 9. Fifth occurrence: `repo/` in `AbdElrhmanAhmed7/Final` (final pass)

**Date: 2026-11-12** · The commit that arrived from GitHub: `11cefe5` (`final`) ·
Repo: `AbdElrhmanAhmed7/Final`

```
<git root>/
└── repo/
    ├── .github/workflows/backend.yml   ← GitHub never read this (fifth time)
    ├── NOVA/backend/…
    ├── README.md · PROJECT_STATUS.md · MIGRATION.md
    ├── tools/ · verification/ · releases/ · reference/
```

The first command run on the fresh clone was the layout gate, and it failed
immediately — exactly as designed:

```
$ bash tools/check_layout.sh
✗ NOVA/backend is not at the git root (<clone>)
  Fix: bash tools/restructure_repo.sh
```

Everything else in the tree was clean: `git status` empty, no stray `*.db`, no
leftover translation script, no stale patch. The defect was **only** the wrapper.

### What was done

| Step | Command |
| --- | --- |
| Promote the project to the git root (191 files, `git mv`, history preserved) | `bash tools/restructure_repo.sh` |
| Confirm GitHub will now see the workflow | `bash tools/check_layout.sh` → `✓ layout ok` |
| Rebuild the virtualenv (moved paths) | `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt` |
| Re-run every gate with a dated log | `bash tools/verify.sh --label 2026-11-12-final` → **10/10 PASS** |
| Keep a live preview for the user, database under `data/` (ignored) | `NOVA_DATABASE_URL=sqlite+aiosqlite:///./data/nova-preview.db uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| Smoke-test that preview server | `.venv/bin/python ../../reference/scratch/preview_smoke.py --base-url http://127.0.0.1:8000` |
| Regenerate the delivery artefacts from the committed tree | `bash tools/make_release.sh --base 11cefe5` |

Result: **451 tests passing**, coverage **94%**, campaign **15/15 · 93 actions ·
0 mistakes**, player-text audit **67/67 operations · 0 violations** (in-process and
over a real socket), **14 probes · 0 failures** with NASA POWER `live`.

### Why the database belongs in `data/`

A preview server started with `NOVA_DATABASE_URL=sqlite+aiosqlite:///./nova-preview.db`
drops `nova-preview.db`, `-wal` and `-shm` next to the source, where the ignore rules
do not reach them: they show up as untracked files and get committed by `git add -A`.
The default (`./data/nova.db`) and any `data/*.db` path are already ignored by
`NOVA/backend/.gitignore`, and `app/db/session.py` creates the directory itself.
**Keep every local database under `data/`** — then a running server never dirties the
tree, and no ignore rule has to be widened.

### The lesson, one more time

The wrapper is not a code problem, it is an **upload** problem. Five deliveries, five
wrappers (`NOVA/`, `repo/`, `upstream/`, `repo/`, `repo/`). Push with git:

```bash
git clone https://github.com/AbdElrhmanAhmed7/Final && cd Final
git am /path/to/releases/nova-backend-final.patch
bash tools/check_layout.sh        # must say ✓ layout ok
git push origin main
```

If you must use the GitHub UI: open the folder on your machine, select **everything
inside it** (Ctrl+A) and drag that — never the folder itself. Then check the
**Actions** tab: a workflow named `backend` must appear with 3 jobs. If the tab is
empty, there is another wrapper, and `bash tools/restructure_repo.sh` removes it.
