# `main` branch ruleset

**Nothing in this folder is live.** GitHub does not read rulesets out of a repository — `main.json`
has no effect until it is `POST`ed. It lives here so the flip (G8) is a *reviewed command* instead of
a clicking session performed once, from memory, at the exact moment the repo becomes visible to
strangers — and so the rules are diffable afterwards.

`main.json` is kept as **pure API payload** — no comments inside it. The GitHub docs do not say
whether the endpoint rejects unknown properties, and a `_comment` key that 422s would break the one
command this file exists to make safe. The commentary is therefore here.

## Apply (G8, after the repo is public)

Rulesets **403** on a private free repo — *"Upgrade to GitHub Pro or make this repository public"* —
which is why this is a G8 step, not a G7 one. Once public, they are free:

```sh
gh api -X POST repos/utyagi24/klarpdf/rulesets --input .github/rulesets/main.json

# verify — enforcement must be "active" and both checks must be listed
gh api repos/utyagi24/klarpdf/rulesets --jq '.[] | {id, name, enforcement}'
gh api repos/utyagi24/klarpdf/rulesets/<id> --jq '.rules'
```

## What it does, and what it deliberately does not

Full rationale: `PLAN.md` §Public-release readiness. In short:

| Rule | | Why |
|---|---|---|
| `deletion` | ✅ | `main` should not be deletable by accident. |
| `non_fast_forward` | ✅ | Blocks force-push. G1's history scrub and 15 rewritten tags are only permanent if history is. Safe to enable **only now** — as a rule it would have blocked the scrub's own force-push. |
| `required_status_checks`: `pytest`, `emails` | ✅ | Stops a red merge mechanically rather than by noticing a ❌. `emails` is the author-email guard — it is what keeps G1's scrub true. |
| Required PR + reviews | ❌ | Solo project: approving your own PRs protects against nobody (`PLAN.md` §Governance). Add it the moment a collaborator is. |
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
