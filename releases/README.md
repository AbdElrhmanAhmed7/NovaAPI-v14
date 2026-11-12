# releases · Delivery package

The files here are **generated from a commit**, not from the working tree. The goal:
anyone can take the delivery and apply it to their copy without copying `.venv`, caches,
or a local database.

## Current delivery: repository layout fix + final verification

This package is generated above the GitHub baseline `11cefe5` (`final`) of
`AbdElrhmanAhmed7/Final`. That commit still wrapped the whole project in `repo/`,
so `.github/workflows/backend.yml` sat below the git root and **GitHub would not
have run a single CI job**.

* The project is promoted to the repository root with `git mv` (191 files, history
  preserved), so `bash tools/check_layout.sh` says `✓ layout ok`.
* Every gate was re-run on the migrated tree: **451 tests · 94% coverage ·
  15/15 missions · 67/67 operations audited for player text · 14 probes · 0 failures**
  (NASA POWER `live`). Evidence: `verification/2026-11-12-final/`.
* `reference/scratch/preview_smoke.py` is new: it smoke-tests a server that is
  already running (readiness, console with no external assets, the water-leak rule).
* Documentation (`README.md`, `PROJECT_STATUS.md` §9.6, `MIGRATION.md` §9,
  `verification/README.md`) records this pass and points at the current evidence.

The previous package (English localization, baseline `a2fd1cc`) is already merged in
this tree: all player-facing content and documentation are English, the legacy `ar`
locale field is retained as an English alias, and `tests/test_english_content.py`
guards against Arabic text returning to content, API payloads or the console.

## How they are generated

```bash
git commit -m "…"                        # the tree must be clean
bash tools/make_release.sh --base <commit>   # default: origin/main
```

The script refuses to run if there are uncommitted changes (`git status --porcelain`) —
because a delivery package that does not match any commit is not reviewable.

| File | What it is | How to use it |
| --- | --- | --- |
| `nova-backend-final.patch` | This delivery's commit delta above the base (`git format-patch`, `releases/` excluded) | `git am releases/nova-backend-final.patch` on a clean clone of `11cefe5` |
| `nova-backend-final.zip` | Full tree snapshot (`git archive HEAD`) without `releases/` | Extract into an **empty** folder, then `cd NOVA` and upload **everything inside it** — see the warning below |
| `SHA256SUMS.txt` | SHA-256 hashes of the files generated in the last run | `sha256sum -c releases/SHA256SUMS.txt` |

The patch contains the layout migration (`git mv`), documentation and verification changes. The
release artefacts themselves are packaged separately so the patch does not contain a
copy of itself.

## Why the zip is not committed to git

The `.zip` is placed in `.gitignore`: it is a copy of the same commit tree, so storing it
inside git duplicates history and grows the repo for no benefit, and every release can
regenerate it in a second. The **patch** is committed because it is reviewable text in
the diff, and `SHA256SUMS.txt` records the hash of the zip generated alongside it so you
can verify that your copy is identical.

```bash
bash tools/make_release.sh --base 11cefe5        # the baseline pushed to AbdElrhmanAhmed7/Final
cd releases && sha256sum -c SHA256SUMS.txt && cd ..
```

> **Note:** all filenames are lowercase (`nova-backend-final.*`) because
> `tools/make_release.sh` generates them that way. The documentation used to say
> `NOVA-backend-final.zip` with uppercase letters — fixed to match the script.

## Applying the patch

On a clean clone of the baseline:

```bash
git am releases/nova-backend-final.patch
bash tools/check_layout.sh
cd NOVA/backend && make install
bash tools/verify.sh          # all gates + campaign + probes
```

After applying, the repository should pass the English-content gates:

```bash
cd NOVA/backend
.venv/bin/pytest tests/test_english_content.py -q
```

## After applying

```bash
cd NOVA/backend && make install
bash tools/verify.sh          # all gates + campaign + probes
git push origin main          # requires your GitHub permissions
```

After pushing, open the **Actions** tab: a workflow named `backend` must appear and run.
If it does not, `.github/workflows/backend.yml` is not at the **root** of the repository —
see [`MIGRATION.md`](../MIGRATION.md).

## ⚠ The zip has a `NOVA/` prefix folder — do not upload the folder itself

`git archive --prefix=NOVA/` wraps the snapshot in a single top-level directory so
that extracting it never scatters files over your current folder. That convenience is
also the trap that produced **five** wrapped deliveries: if you extract the zip and
drag the `NOVA/` folder onto GitHub's upload page, GitHub stores the folder name in
every path, `.github/workflows/backend.yml` ends up below the git root, and CI runs
nothing at all.

```bash
unzip nova-backend-final.zip -d delivery   # → delivery/NOVA/…
cd delivery/NOVA                           # ← go INSIDE the folder
ls .github/workflows/backend.yml           # must exist here
# select everything here (Ctrl+A) and drag that, never the NOVA folder
```

Using git avoids the whole class of mistake:

```bash
git clone https://github.com/AbdElrhmanAhmed7/Final && cd Final
git am /path/to/releases/nova-backend-final.patch
bash tools/check_layout.sh                 # ✓ layout ok
git push origin main
```

After **any** upload or push, `bash tools/check_layout.sh` must say `✓ layout ok`, and
the **Actions** tab must show the `backend` workflow with 3 jobs.
