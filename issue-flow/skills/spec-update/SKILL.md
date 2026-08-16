---
name: spec-update
description: Change or refresh an existing docs/specs/ package outside an issue-flow run. Use when the user says "/spec-update", "add a feature to the spec", "update the spec", "change the spec", "retire a feature", or "refresh the spec" / "migrate the spec to the new format" after a plugin upgrade. Two modes - change (add, alter or retire features at full planner depth, then hand the new planned work to spec-to-issues) and refresh (mechanically bring an older spec package up to the current format contract - diagrams, generated spec.html, quality rules, ADR and external scaffolding - without inventing product content). Mid-run spec edits stay with the issue-flow PM's spec-maintenance mechanisms, not this skill.
---

# Spec Update — evolve an approved spec without replanning the project

You are the **spec editor**. `project-planner` and `project-inherit` create the
package; the issue-flow PM maintains it *during* runs
([`../issue-flow/references/spec-maintenance.md`](../issue-flow/references/spec-maintenance.md));
you handle the third case: **the user, between runs, wants the spec itself moved** —
new scope, changed scope, or an old package brought up to the current format. You never
create tracker issues and you never touch product code.

**Preconditions.** `docs/specs/spec.md` exists (else point at `/project-planner` for
greenfield or `/project-inherit` for an existing codebase, and stop) and no issue-flow
session is mid-run on this repo (a live run's spec changes belong to its PM — check
for a `flow:status` issue with recent activity and ask before proceeding under one).

Pick the mode from the request; when a request mixes both ("refresh the format and add
X"), run **refresh first, then change**, as two separately reported passes.

## Change mode — add, alter, or retire scope

The planner's rules apply at planner depth: STE
([`../../references/ste.md`](../../references/ste.md)), the format contract
([`../project-planner/references/spec-format.md`](../project-planner/references/spec-format.md)),
external interfaces from fetched docs only, mockup and diagram obligations included.

1. **Scope the delta.** Read `spec.md`, the touched `features/*.md`, and — when the
   change lands on built behaviour — the relevant code, so the delta is written
   against reality. Interview with `AskUserQuestion` only for product intent the
   request leaves open; planner Phase 1 altitude, usually one round.
2. **Write the delta by kind:**
   - **New feature** — a new `features/NN-*.md` at full depth (stable new `id`,
     `status: planned`, `issues: []`, flow diagrams, mockups, FRs, acceptance
     criteria), an epic entry (existing epic if it fits the sizing rules, else a new
     one), and every `spec.md` section the feature touches: `Terms`, feature map,
     architecture/data-model **diagrams**, data model, milestones.
   - **Change to a built feature** — never rewrite history into fiction: update the
     built feature file to keep describing current reality, and express the change as
     a **separate delta feature file** (new stable `id`, `status: planned`, referencing
     the built feature it modifies) with its own FRs and acceptance criteria. Always a
     separate file, never a delta section inside the built file: `spec-to-issues` gates
     on the file-level `status:`, so planned work inside a `built` file is skipped
     whole — silently, on a run that reads as clean.
   - **Retire a feature** — do not delete the file (`id`s are permanent, and
     spec-to-issues dedups on them). Set `status: retired` in its front-matter — from
     any state; spec-to-issues creates nothing for it — mark the feature map, move the
     epic entry accordingly, and record why in the `Changelog`. The file stays as the
     record of what the feature was. If removal requires engineering, that removal is
     itself a new planned feature.
3. **Keep the invariants.** Never renumber or re-`id` existing features; never edit
   `issues:` lists by hand; never silently regenerate a hand-edited mockup; keep
   `Terms` free of synonym drift.
4. **Review cycle.** Regenerate `spec.html` (`python3 docs/specs/render-spec.py`),
   open it, and run the same approve/revise loop as planner Phase 4. A spec that was
   `approved` returns to `approved` only through the user's explicit approval of this
   round; record the round in `## Changelog`.
5. **Handoff.** Commit and push, then point at `/spec-to-issues` — its `id` dedup
   means only the new or changed `planned` work becomes issues.

## Refresh mode — migrate the package to the current format

Mechanical modernization after a plugin upgrade (the preflight digest's
plugin-version drift is the usual trigger). **The product content is frozen**: you may
restructure, generate, and backfill *form*; any gap that needs product knowledge
becomes a question or an open-questions entry, never an invention.

1. **Diff the package against the current contracts** —
   [`spec-format.md`](../project-planner/references/spec-format.md) and
   [`scaffold.md`](../project-planner/references/scaffold.md). Typical findings, oldest
   packages first:
   - No mermaid diagrams: draw the architecture flowchart, data-model erDiagram, and
     per-flow diagrams **from the existing prose**. Prose too thin to diagram → ask,
     or file it in `Open questions`; never guess a flow into a picture.
   - Hand-written `spec.html`: copy `render-spec.py` in, vendor
     `assets/mermaid.min.js`, delete the hand-written page, regenerate.
   - Missing `docs/adr/` (template), `docs/external.md`, `.claude/rules/quality.md`,
     `.claude/rules/ste.md`: scaffold per `scaffold.md` — proposing before writing
     anything under `.claude/`, as always.
   - Front-matter drift: missing `branch_model`, `spec_version`, `html_generated`,
     feature `id`s, or a `features:` list that disagrees with the directory. Ask for
     anything you cannot derive (a wrong `branch_model` guess would misroute every
     batch).
   - Orphaned mockups or dead mockup references; STE violations in headings and FRs.
2. **Apply, then report.** One pass, then a plain list: what was generated, what was
   moved, what was asked, what remains open. Add a `Changelog` entry naming the plugin
   version refreshed against.
3. **Approval stance.** A refresh does not change what the project builds, so an
   `approved` spec **stays approved** — provided every content-affecting fix went
   through an explicit question. If any did not fit that bar, drop to `draft` and run
   the review cycle.
4. **Commit.** Offer to commit (and push) the refreshed package as one commit; no
   spec-to-issues handoff — refresh never creates plannable work.

## Output checklist

- [ ] Mode(s) stated up front; refresh ran before change when both applied.
- [ ] No feature `id` renamed or renumbered; no `issues:` list hand-edited;
      `built` features still describe reality.
- [ ] Every new or changed `planned` item carries FRs, acceptance criteria, and its
      flow diagram; touched `spec.md` diagrams updated with the prose.
- [ ] `spec.html` regenerated by `render-spec.py` after the last edit;
      `html_generated` stamped.
- [ ] `## Changelog` has one entry per pass; approval status handled per mode.
- [ ] Refresh invented zero product content — every gap it could not fill mechanically
      is a recorded question.
