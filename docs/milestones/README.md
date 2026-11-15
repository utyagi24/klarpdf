# Milestones

One file per milestone. A milestone's file is created the first time the milestone is written down:
when it is planned, or, for an unplanned one, in the PR that builds it. `M152.md` holds M152 and its
parts, M152.1 and M152.2. A group planned as
one, such as M133–M136, has one file, `M133-M136.md`. The files whose names start with `G` are the
public-release milestones. G6 is still open, so it is in `PROGRESS.md` and has no file yet.

A file has up to two parts:

- **`## Design`**: why the milestone exists, what was measured, what was decided, and how it was
  built.
- **`## Progress`**: the PR details: what the PR did, and a link to it.

A milestone that is planned but not done has only `## Design`. Milestones M0 to M79 were planned in
the roadmap sections of [`PLAN.md`](../../PLAN.md), so their files have only `## Progress`.

When to write what, and which file holds the rest: [`CLAUDE.md`](../../CLAUDE.md) §How we work →
*Where things live*.
