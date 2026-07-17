# `main` branch ruleset

`main.json` is a **mirror of the live ruleset**, not a live control. GitHub does not read rulesets out
of a repository — editing this file changes nothing until it is `PUT` back. It exists so the rules are
reviewable in a diff instead of living as clicks someone made once in the UI, and so a drift between
the two is visible.

`main.json` is kept as **pure API payload** — no comments inside it. The GitHub docs do not say
whether the endpoint rejects unknown properties, and a `_comment` key that 422s would break the one
command this file exists to make safe. The commentary is therefore here.

## Applying a change

The ruleset already exists (id **18233952**, name **Protect Main**), so this is a `PUT` of the whole
payload — **not** a `POST`. A `POST` would create a *second* ruleset on `main`: GitHub applies every
matching ruleset and takes the union, so protection would still hold, but `main`'s rules would then be
split across two objects and neither this file nor either ruleset would describe the whole picture.

```sh
gh api -X PUT repos/utyagi24/klarpdf/rulesets/18233952 --input .github/rulesets/main.json

# verify — enforcement must be "active" and both checks must be listed
gh api repos/utyagi24/klarpdf/rulesets --jq '.[] | {id, name, enforcement}'
gh api repos/utyagi24/klarpdf/rulesets/18233952 --jq '.rules'
```

There is a second ruleset, **Protect Tags** (id 18234032, `~ALL` tags: `deletion`, `non_fast_forward`,
`update`). It is not mirrored here — it has no history of needing review, and nothing in the release
flow changes it. Read it from the API if you need it.

### History: the 403 that was not what it looked like

G7 concluded rulesets **could not exist** on this repo, because `GET .../rulesets` returned *"403 —
Upgrade to GitHub Pro or make this repository public"*. That read the error as "no rulesets are
possible here" when it only ever meant "this **API** is not available on a private free repo".
Protect Main and Protect Tags had been active since 2026-06-28 the whole time. The flip to public
revealed them, and `main.json` — written as a from-scratch `POST` payload against the assumption of
an empty slate — was reconciled into the mirror it is now. **Lesson: a 403 on a read is not evidence
of absence.**

## What it does, and what it deliberately does not

Full rationale: `PLAN.md` §Public-release readiness. In short:

| Rule | | Why |
|---|---|---|
| `deletion` | ✅ | `main` should not be deletable by accident. |
| `non_fast_forward` | ✅ | Blocks force-push. G1's history scrub and 15 rewritten tags are only permanent if history is. |
| `pull_request`, **0 approvals** | ✅ | Requires every change to reach `main` through a PR, but does not require an approval — so the solo flow (open, self-merge) still works while direct pushes to `main` are blocked server-side. This is `CLAUDE.md`'s "never leave edits on `main`" convention, enforced rather than remembered. Note this is *required PR*, which is kept; *required review* is the separate thing dropped below. |
| `required_status_checks`: `pytest`, `emails` | ✅ | Stops a red merge mechanically rather than by noticing a ❌. `emails` is the author-email guard — it is what keeps G1's scrub true. |
| Required **reviews** (`required_approving_review_count` > 0) | ❌ | Solo project: approving your own PRs protects against nobody (`PLAN.md` §Governance). Add it the moment a collaborator is. |
| Linear history | ❌ | The project merges PRs with merge commits. This would break that to buy tidiness. |
| Signed commits | ❌ | Commits are unsigned today; needs GPG/SSH signing set up for the no-reply identity first. A prerequisite, not a rule to flip. |
| **`bypass_actors: []`** | — | **Deliberately empty.** A bypass for the repo admin, on a repo whose only pusher *is* the admin, means the force-push rule protects against nobody — the same reasoning that dropped required reviews above. The realistic threat is a fat-fingered `git push --force`, and that is precisely what an empty bypass list stops. A genuine history repair is still possible: flip `enforcement` to `disabled`, push, flip it back — deliberate, and it leaves a trail. (It also avoids guessing a magic number: GitHub's REST docs do **not** publish the numeric `actor_id` for the built-in `RepositoryRole` values, so any admin-bypass entry here would be unverifiable until the day it is run.) |

## Gotchas

- **Required checks are addressed by *job* name** — `pytest` (`test.yml`) and `emails`
  (`author-email-guard.yml`), not by workflow name. Renaming either job silently un-enforces it: a
  required check naming a job that no longer exists is simply never evaluated. Grep before renaming.
- **Both checks must report on every PR, or PRs wedge.** A required check that never reports leaves a
  PR on *"Expected — waiting for status"* forever, because GitHub cannot distinguish "skipped as
  unnecessary" from "not finished yet". This is why `test.yml` has **no path filter** on its
  `pull_request` trigger and decides docs-only *inside* the job. Do not "optimise" that back into a
  `paths-ignore`.
- **`strict_required_status_checks_policy: false`** — does not force a branch to be up to date with
  `main` before merging. On a solo repo that would just mean rebasing to satisfy a rule nobody needs.
- **This file drifts silently.** Nothing reconciles it against the live ruleset. If you change rules in
  the UI, `PUT` this file or edit it to match in the same sitting — otherwise the next reader trusts a
  fiction, which is exactly what the 403 story above cost.
