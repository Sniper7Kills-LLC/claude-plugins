---
name: spec-to-issues
description: Turn an approved project-planner spec into tracker epics and sub-issues (GitHub or Gitea) shaped for the issue-flow loop. Use when the user says "/spec-to-issues", "create issues from the spec", "push the spec to GitHub", "push the spec to Gitea", optionally with a spec path. Reads docs/specs/spec.md plus docs/specs/features/*.md, verifies approval and that the spec is committed and pushed, then decomposes each epic into labeled, linked, dependency-annotated sub-issues sized for issue-flow's batch model.
---

# Spec → Issues — materialize an approved spec on the tracker

Input: the spec package produced by `project-planner`. Output: tracker epics + sub-issues
that `issue-flow` can pick up with zero re-interpretation. You create issues; you never
implement, and you never invent scope that isn't in the spec.

**You decompose, you don't transcribe.** `spec.md` deliberately contains no sub-issue
checklist. The engineering breakdown is *your* job, derived from the detail in
`docs/specs/features/*.md`.

**Every issue you create is written in Simplified Technical English.** Read the standard
in [`../../references/ste.md`](../../references/ste.md) — or the project's own
`.claude/rules/ste.md`, which the planner wrote from it — before you draft a single title
or body. Titles, bodies, acceptance criteria and comments all follow it; § 4 has the
patterns for each. Two rules carry the most weight here:

- **Write from the spec's `## Terms` table.** It is the project's controlled vocabulary.
  Use its words, with its meanings, and never a synonym it rejects. A worker reading your
  issue and a spec reader must be using the same word for the same thing.
- **Quote, don't paraphrase.** FR text, acceptance criteria and edge cases you lift from
  a feature file go in **verbatim**. Rewriting a requirement while decomposing it is how
  scope drifts. Your own added prose (context, hints, out-of-scope) is what you write in
  STE.

## 1 — Locate & validate the spec

- Spec path from the user's argument, else `docs/specs/spec.md` at the project root
  (one project per repo). Older layouts may nest under `docs/specs/<slug>/spec.md` —
  accept those too; several candidates → ask which.
- Read the front-matter. **`status: approved` is the gate.** If it's `draft`, stop and
  ask: the user can approve it now (confirm explicitly — then set `status: approved` +
  date, same as the planner would) or go back to `/project-planner`. Never silently
  treat a draft as approved.
- Read `spec.md` **and every file in its `features:` list** (entries are
  `{ id, file }` pairs). A missing or unreadable feature file is a hard stop — report exactly which, and send the user back to the
  planner rather than guessing the feature's contents.
- **Work only `status: planned` features.** Each feature file carries a stable `id:` and
  a `status:` of `planned | issued | built`. Create issues for the `planned` ones; skip
  `issued`/`built` and say so. This is what makes second and third planning waves safe —
  a re-approved spec re-issues only what actually changed.
  (A `spec_version: 1` spec has no ids or statuses. Treat every feature as `planned`,
  fall back to the marker/title dedup in Preflight, and tell the user their spec predates
  per-feature tracking.)
- Parse `## Epics`: epic names, goals, the feature files each covers, `Depends on:`
  lines, exit criteria. **`Epic 0: Foundation` comes first and blocks everything** — its
  sub-issues build the test harness, CI workflow, branch model and deploy wiring the
  rest of the pipeline depends on. If a greenfield spec has no foundation epic, stop and
  send the user back to the planner; do not file feature issues into an empty repo.

## 2 — Preflight

This skill runs on GitHub or Gitea. Tracker commands are named as abstract operations and
resolved in [../../references/forge.md](../../references/forge.md).

- `forge.auth.check`; `forge.repo.view` (no repo/remote → same bootstrap flow as
  issue-flow Phase 0: offer `git init` / `forge.repo.create`, confirm public/private).
  Write the resolved `owner/name` back to `spec.md`'s `repo:` front-matter field.
- **The spec must be committed and pushed.** Issue bodies link `docs/specs/...` paths,
  and issue-flow's workers run in git worktrees that contain tracked files only — an
  unpushed spec means dead links and blind workers. Check for uncommitted or unpushed
  changes under `docs/specs/` (`git status --porcelain docs/specs`,
  `git log origin/<default>..HEAD -- docs/specs`). If anything is outstanding, stop and
  offer to commit and push it before creating a single issue.
- **Label bootstrap** — run issue-flow's idempotent label block (see the issue-flow
  skill's `references/labels.md`; same label set, created with `forge.label.create`).
  On Gitea, `forge.label.create` is not idempotent on its own — check
  `forge.label.list` first and create only what is missing, exactly as `labels.md`
  documents.
- **Dedup check — on ids, not titles.** Every issue this skill creates carries a marker
  naming its feature id: `<!-- spec:<slug> feature:<feature-id> -->`. Search existing
  issue bodies for that marker before creating anything (`forge.issue.list` filtered on
  `feature:<id>` in the body, plus a title search as a secondary signal). Titles get
  renamed between planning waves; ids do not, which is why they are the dedup key. Skip
  or update duplicates instead of double-creating; report what was skipped.

## 3 — Decompose each epic into sub-issues

For every epic, read the feature files it covers and derive the work. A sub-issue is a
unit of *engineering*, not a heading in the spec:

- **3–6 sub-issues per epic**, each **≤ ~1 focused day** for one engineer. Fewer than 3
  → the epic is too small, fold it and say so. More than 6 → split the epic and say so;
  never create an unbatchable epic.
- Slice along **deliverable seams** — data model/migration, API contract, UI screen and
  its states, wiring, tests — not along spec section names. One sub-issue should be
  reviewable on its own.
- Every FR in the epic's feature files must be covered by **at least one** sub-issue;
  every sub-issue must cite the FRs it satisfies. Check both directions before you
  create anything, and report any FR you could not place.
- Acceptance criteria come from the feature file's `Acceptance criteria`, narrowed to
  the slice. Edge cases and error states from `Edge cases & failures` belong in the
  sub-issue that owns that behaviour — they are not a separate issue.
- Sequence within the epic with `Depends on`, keeping dependencies inside the epic
  wherever the spec allows.
- Anything the feature file leaves in `Open questions`, or that you would have to invent
  to write acceptance criteria, becomes `status:needs-feedback` with the question — not
  a guess.

## 4 — Confirm the plan, then create

Show the user a compact table first — epics, derived sub-issue titles and counts, ready
vs needs-feedback, detected dependencies, FR coverage — and confirm once
(`AskUserQuestion`: create all / adjust / abort). Then, per epic in milestone order:

1. **Epic issue.** Title `Epic: <title>`; labels `type:epic`, `status:blocked` (blocked
   on children); body = epic goal, exit criteria, `Depends on: #<other epic>` when the
   spec says so, a task-list checklist of its sub-issues (fill in numbers as you create
   them), spec pointers (`docs/specs/spec.md` + the feature files it covers), and the
   marker `<!-- spec:<slug> feature:<feature-id> -->` (the epic's marker names the first
   feature it covers).
2. **Sub-issues.** For each, `forge.issue.create` with body:
   - Context: the FR texts it satisfies (quote them — workers shouldn't need the spec
     open), plus links to its `docs/specs/features/NN-*.md` and any relevant
     `docs/specs/mockups/*.html`.
   - **Acceptance criteria** — the worker's definition of done.
   - Behaviour and edge cases it owns, quoted from the feature file.
   - `Part of #<epic>` and, when sequenced, `Depends on #<previous>` (issue-flow's
     triage reads exactly this phrase).
   - Hints (files/areas/libraries) where the spec gives them; marker
     `<!-- spec:<slug> feature:<feature-id> -->`.
   - Label `status:ready` — or `status:needs-feedback` (+ the open question as a
     comment) if a product question is still open on this slice.
   - Link as a real **sub-issue** of the epic where the forge supports it — GitHub does;
     Gitea does not (see the capability gap in
     [../../references/forge.md](../../references/forge.md)) — falling back to the
     `Part of #` convention wherever native sub-issue linking is unavailable.
3. **Priorities.** First milestone's epic children get `priority:high` if the user wants
   a fast first demo (ask in the confirm step); otherwise leave normal.
4. Tick the epic's checklist with the created numbers.

## 5 — Write back & report

- **Traceability:** append an `## Issue map` section to `spec.md` (epic → issue numbers,
  and FR → issue numbers) and commit it.
- **Advance each feature's status:** set `status: issued` in the front-matter of every
  feature file you created issues for, and record its issue numbers there
  (`issues: [12, 13, 14]`). This is what keeps the next planning wave from re-issuing
  work. The PM moves a feature to `built` when its last issue closes.
- Report a final table: epics created, sub-issues created (ready / needs-feedback
  counts), FR coverage, dependencies wired, anything skipped as duplicate or already
  `issued`/`built`.
- Point at the next step:

```
/issue-flow        # the PM will batch each epic onto an integration branch and build it
```

## Rules

- **Approved specs only** (explicit user override counts as approval — record it).
- **Every issue is STE**, written from the spec's `## Terms` vocabulary. Quoted FRs and
  acceptance criteria stay verbatim; only your own prose is yours to word.
- **Committed and pushed spec only** — see Preflight. No exceptions; the links are how
  workers read the design.
- **Never invent scope.** Anything unclear in the spec → `status:needs-feedback` with
  the question, not a guess. Decomposing *how* the spec's work is sliced is your job;
  deciding *what* the product does is not.
- **Carry the external-API documentation links into the issue body.** Where a slice calls
  a cloud service or third-party API, copy the doc URL and the pinned API/SDK version out
  of the spec's `Interfaces` section. The worker must not have to guess which version the
  spec meant, and must not describe the API from memory
  ([`../../references/external-apis.md`](../../references/external-apis.md)). A slice
  whose interface the spec never confirmed is `status:needs-feedback`, not a guess.
- **Sized for batching:** one epic = one issue-flow batch, 3–6 sub-issues.
- Every created issue carries the `<!-- spec:<slug> feature:<feature-id> -->` marker, and
  dedup keys on the **id**, so reruns and later planning waves are idempotent even when
  titles change.
- Confirm once before bulk creation; never mass-create silently.
