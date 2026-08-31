# A first publish, end to end

The full sequence for a Python package going to PyPI with a GitHub Release, the
way an unattended run should execute it. Every call is shown with the exact
arguments a host would send.

## 0. What you have and what you don't

You have: a buildable package, a clean tree, a workspace you can write. You do
**not** have the credentials — they arrive through `pub.store_put`, and you will
never see them again after that.

## 1. Take the credentials (write-only door, open once)

```
pub.store_put {id: "pypi.token",     kind: "token", value: "<the PyPI API token>",
               note: "created by user 2026-08-31; rotate quarterly"}
pub.store_put {id: "github.token",   kind: "token", value: "<fine-grained PAT>",
               note: "repo + packages:read/write, expires 2026-11-30"}
```

Confirm only the *shape*, never the value:

```
pub.store_list {}        # ids, kinds, notes, a short mask - that is all you get
```

If a credential is missing, stop and ask for it. Do **not** improvise a
placeholder value and store it.

## 2. Write the template with markers

```
fs.write {path: "publish/.env", content: "TWINE_PASSWORD={{PUB.pypi.token}}\nGH_TOKEN={{PUB.github.token}}\n"}
```

The marker's id is exactly the store id. This is the only place the two meet.

## 3. Scan until `ready_to_publish`

```
pub.placeholders {path: "publish"}
```

Read `missing_ids`. While it is non-empty, `pub.inject` will refuse — that is a
feature, not an obstacle. Store the missing ids and re-scan.

## 4. Dry-run, then inject

```
pub.inject {path: "publish", dry_run: true}   # the plan: files, markers, changed
pub.inject {path: "publish"}                    # the write: journaled, expect_sha
```

Keep the `data.undo.args.task_id`. If the next step (the build or upload)
discovers the secret is wrong, undo the injection before re-storing the correct
credential — the file goes back to markers, the new value goes in on the next
inject. Nothing leaks, nothing is half-updated.

## 5. Build, upload, verify — from the knowledge base

```
pub.packaging {target: "pypi"}          # the exact build + twine commands
pub.packaging {target: "github_release"} # git tag + gh release + SHA256SUMS
```

Follow the returned `steps` with `shell.run`. The commands in the entry already
use the env file you just injected.

## 6. Let the loop prove it

```
pub.testers {platform: "pypi", packaging: "pypi", version: "1.4.0"}
```

Run each returned step in order, checking each `accept` line. The plan's
`verify.1` step (clean machine: download, verify sha256, install, run,
uninstall) is the one that actually proves the publish — do not skip it because
the upload succeeded. An upload can succeed and still ship a broken artifact.

## 7. If you must stop

- Undo the publish: `fs.undo_task {task_id: <from step 4>}` — reverts the
  injected files only.
- Revoke, don't just delete, a credential you suspect leaked: rotate it at the
  source first, then `pub.store_put` the same id with the new value, then
  `pub.store_delete` is rarely needed (same-id put *is* the rotation).
