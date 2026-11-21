# Keeping the spec honest (PM-facing)

`project-planner` writes `docs/specs/`, and the project's `CLAUDE.md` tells every future
session to read it before building a feature. That instruction is only safe if the spec
still describes the app. The PM makes decisions during the loop that change the product —
ship-partial calls, answers to parked questions, hotfixes that alter behaviour — and
none of them reach the spec on their own.

Skip the two spec mechanisms when there is no `docs/specs/spec.md`; issue-flow works
fine on a plain tracker. ADRs (mechanism 3) apply on any project — decisions with
lasting rationale happen with or without a spec.

These mechanisms are the PM's, for changes that happen **during a run**. When the user
wants the spec itself moved between runs — new scope, a retired feature, or an old
package migrated to the current format — that is the `/spec-update` skill, not a PM
mechanism; suggest it rather than absorbing planner-scale edits into the loop.

## Three mechanisms, different weights

**1. Changelog line — every scope decision, immediately.**

Cheap, always. At the batch gate, append to `docs/specs/spec.md` § Changelog:

```markdown
- 2026-07-27 — Batch #42 shipped without the CSV export sub-issue (#57). #57 is parked on
  a product question and moves to a later batch.
- 2026-07-27 — Answered #53. The project rejects a duplicate email address. It does not
  merge the two accounts.
```

**One entry per *decision* — never one per issue, and never one per batch member.** A batch
that shipped as planned earns no line. Closed issues are the `## Issue map`'s job, and
nothing in this plugin requires a change set to write a changelog entry to pass a gate. A
project that builds such a gate makes the two most-contended files in the repository a
mandatory edit for every member, and every batch then conflicts by construction. See
[finding-policy.md](../../../references/finding-policy.md).

One entry per decision, dated, naming the issue or PR. Write it in STE
([ste.md](../../../references/ste.md)): one sentence per fact, active voice, present
tense. Commit it with the batch. This is an audit trail, not a rewrite — it tells the next
reader that the spec and reality diverged here, and why.

**2. `type:spec-update` issue — when documented behaviour actually changed.**

A changelog line is not enough when anything under `docs/specs/` now *describes the wrong
product*: an FR was dropped, acceptance criteria were renegotiated, an interface contract
changed, a data-model field was added or removed. That is a `features/*.md` **or `spec.md`
itself** — its `## Terms` table, data model and cross-cutting concerns are read by every
worker and by `spec-to-issues`, so a contract that changed there misbuilds the next feature
as surely as one in a feature file. In that case file a `type:spec-update` issue:

- Title: `Spec update: <feature> — <what changed>`, or `Spec update: spec.md — <what
  changed>` for a spec-level divergence.
- Body: the spec file path, the sections that are now wrong, quotes of both the
  current spec text and what actually shipped, and links to the issues/PRs that caused
  it. Marker `<!-- spec:<slug> feature:<feature-id> -->`, and for a spec-level divergence
  that belongs to no feature, `<!-- spec:<slug> feature:none -->` — never an invented id,
  and never an omitted marker, because the next planning wave scopes from it.
- Label `type:spec-update` + `status:ready`.

It is worked like any other issue — a worker edits the spec files (and only the spec
files) and opens a normal PR. That keeps the deep edit out of the PM's context and gets
it reviewed like everything else.

Do **not** let a worker rewrite the spec as a side effect of building a feature. Spec
edits are their own issue, so the change is visible and reviewable rather than buried in
a feature diff.

**3. ADR — when the *why* must outlive the batch.**

The loop makes decisions whose rationale nothing above records: a gate dispute resolved
one way over another, a worker `finding:` flagged `adr-worthy`, an approach chosen
against a considered alternative. A changelog line records *that* something was decided;
a `Carried forward` comment records it only on the one issue that will look; the batch
findings log dies with the batch. A decision someone will re-litigate later needs a
durable record — an Architecture Decision Record:

- `docs/adr/NNNN-<slug>.md`, numbered sequentially. The planner scaffolds `docs/adr/`
  ([scaffold.md](../../project-planner/references/scaffold.md)); create it on first use
  when it is missing.
- Format, one page maximum: `# NNNN — <decision>`, `Date`, `Status: accepted` (or
  `superseded by NNNN`), `## Context` (the forces, with issue/PR links), `## Decision`
  (one sentence, active voice), `## Consequences` (what this rules out, what it commits
  the project to). STE throughout.
- Written by the PM at the batch gate (SKILL.md C2 step 8), committed with the batch's
  other spec bookkeeping — never rewritten later; a reversed decision gets a new ADR
  that supersedes the old one.
- What earns one: a decision that would be re-litigated by someone who cannot see this
  batch's comments. What does not: anything a changelog line already says, routine
  ship-partial calls, implementation detail.

## Feature status lifecycle

Feature files carry `status: planned | issued | built`:

| Transition | Who | When |
|---|---|---|
| (new) → `planned` | project-planner | the feature is written into the spec |
| `planned` → `issued` | spec-to-issues | its issues are created (records their numbers) |
| `issued` → `built` | the PM | its last issue closes at a batch gate |

Advance the status at the batch gate along with the changelog line. This is what lets a
later planning wave re-run `/project-planner` and `/spec-to-issues` without re-issuing
work that already shipped — dedup keys on the feature `id`, and `built` features are
skipped.

## What not to write back

- Implementation detail. The spec is product design; how it was built lives in the code
  and the PR.
- Every issue that closed — that's the `## Issue map`, already maintained by
  `spec-to-issues`.
- Anything on a feature another operator is actively re-planning (check for open
  `type:spec-update` issues first, and edit only your own marker blocks in shared
  bodies).
