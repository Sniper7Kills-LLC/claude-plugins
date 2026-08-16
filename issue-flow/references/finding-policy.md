# What earns a tracker issue

A finding is anything a review, an audit, a worker, or the PM notices that is not already
on the tracker. This file decides which findings become issues and which do not.

Load it before you file an issue from a finding — in issue-flow Stage A triage, and in
project-review Phase 3.

## The problem this solves

Without a filter, every finding becomes an issue, and the repair of that issue produces
the next finding. A loop that files an issue for a stale citation edits the file the
citation names, which falsifies a second citation, which is a second finding. The backlog
then regenerates at about the rate it closes, and the loop never reaches "no workable
issues remain".

The failure is measurable, and the numbers below reproduce with the `## How to measure`
commands, run on each repo's default branch (merges counted once — a merge-inclusive
per-commit loop double-counts every batch member and inflated an earlier version of
these figures). The window is the last 300 **non-merge** commits, which is what
`--no-merges` on the `rev-list` makes it — the flag redefines the window rather than
filtering the old one, so every figure here is derived against that window and not
against the 300 most recent commits.

On one project that ran without this policy, the specification was edited in **82% of
those commits at 3.6 lines per edit** — a sentence at a time, not a section landing —
and **59% of commits touched documentation and no product code**. The bookkeeping had
become the deliverable. The sibling project that adopted the policy, on the same
library in another language, edited its specification in **25% of commits at 6.7 lines
per edit**, with bookkeeping-only at **47%**; a third, unrelated project measured
**4.6%** bookkeeping-only with a `+1310/−19` spec add/delete shape (that one figure
predates the window pin and was measured merge-inclusive — treat it as approximate;
at 4.6% no window choice moves the verdict).

**Edit share is the discriminator.** 82% against 25% is a **3.3x** separation. Every
other signal on the same pair is weaker: lines-per-edit **1.9x** (3.6 against 6.7, both
"low"), bookkeeping-only commit share **1.3x** (59% against 47%), and library-touching
commit share **none at all** — 26.7% against 25.7%. Lines-per-edit corroborates the
shape — four lines is a sentence, not a section — and the add/delete shape separates
growth (`+thousands/−tens`) from rewrite churn; neither carries the verdict alone.

A large test surface, by itself, is **not** evidence of over-guarding — TDD mandates and
test fakes standing in for absent external systems both inflate test LOC legitimately.
The only test that matters is case 3 below: does the guard assert behavior?

## The five cases

**A finding earns a tracker issue in five cases, and never otherwise.**

1. **Behavior.** What the software does changes, or is wrong.
2. **A user-visible output.** A value, a rendering, an API response, a file the program
   writes, or an exit code is wrong.
3. **A guard that guards nothing.** A test, a CI check, a lint rule, or a type
   constraint passes where it must fail, or covers nothing it claims to cover. **An app
   change a test needs also lands here** — a missing test-id, absent seed data, no
   state-reset hook — because without it the suite cannot guard the flow at all.
4. **A blocked epic.** Work already on the tracker cannot proceed until this is settled.
5. **A question the maintainer must rule.** A product or design decision that belongs to
   a person. It is labeled `status:needs-feedback` and carries the question.

Severity does not enter the test. A cosmetic behavior defect is case 1. A critical-sounding
documentation drift is none of the five.

### The one carve-out: `docs/specs/` describes the wrong product

Anything under `docs/specs/` that no longer describes what shipped — a dropped
requirement, renegotiated acceptance criteria, a changed interface contract or data model —
earns a `type:spec-update` issue, and this policy does not suppress it. That is
`features/*.md` and `spec.md` alike: the `## Terms` table, the data model and the
cross-cutting concerns of `spec.md` are read by every worker and by `spec-to-issues`, so a
changed contract there misbuilds as surely as one in a feature file.

The spec is not a record of a past state. Every future planning wave, every
`spec-to-issues` run, and every worker reads it as **the input that decides what gets
built**. A wrong spec builds the wrong thing, so it reaches case 1 through its readers.

The carve-out is narrow, and it is the divergence that qualifies, never the prose. A spec
sentence that is merely stale, imprecise, or inconsistent in wording is repaired in place
like any other record. Ask which is which this way: **would a reader who trusted this
sentence build the wrong thing?** Yes is an issue; no is a repair.

## Everything else is repaired in place

**A finding outside the five cases is repaired by the change set that found it, and it is
filed nowhere.** These reach the repair, not the tracker:

- A sentence that a later change falsified.
- A citation, path, or line number that moved.
- A stale count, date, or version reference in prose.
- A missing or inconsistent term.
- Spelling, formatting, and prose-standard drift.
- A record that describes a past state and that nothing reads.

Where to put the repair:

| Who found it | Where it is repaired |
|---|---|
| An issue-flow worker, mid-issue | The worker's own PR, in the same change set, kept to files that change already touches |
| The PM at a sub-merge gate | A documentation commit on the integration branch, pushed **before** the batch PR's CI-trigger commit |
| The PM at a batch gate, CI already green | The **next** batch. Never push it onto the integration branch after the trigger — that makes a new head, the green verdict belongs to the old SHA, and the merge would ship uncovered |
| The PM with no batch open | Recorded in the `flow:status` marker block, then taken by the next batch |
| project-review | The Phase 4 deliverables PR when it is documentation the scribe already writes, else the run ledger only |

Two of those rows exist because a repair is still a commit. It obeys every rule a commit
obeys: it never lands after the trigger that a green batch verdict was measured against,
and it never widens a diff past the files its own change touched.

**A repair with no durable home is recorded, not dropped.** The digest is transient prose
and the PM drops finished work from memory, so a repair named only in a digest is lost at
the next compaction. Put it in the `flow:status` marker block, which Phase 0 recovery
re-reads, and clear it when a batch takes it.

**A repair that turns out to touch behavior stops and becomes an issue** under case 1. The
repair is the default, not the ceiling — finding that a "stale" sentence was accurate and
the code is wrong is exactly the case the tracker is for.

## What this does not change

- It does not suppress evidence. Every finding is still reported by the sub-agent that
  found it, still reaches the PM, and still appears in the run ledger and the digest.
  The gate decides where a finding is *worked*, never whether it is *known*.
- It does not park real work. A finding that meets any of the five cases is filed the same
  way it always was.
- It does not apply to work the user asks for directly. A user asking for a documentation
  pass gets one.

## How to measure

The numbers above came from these commands. Two preconditions before trusting any
output:

- **Run with `bash`, not zsh** — zsh does not word-split unquoted variables, so the
  loops silently iterate once over the whole blob and die. The script below uses
  process substitution, which also requires bash.
- **Run on the repo's default branch.** The ref defines the window the numbers
  describe; pin it and the numbers become reproducible instead of merely plausible.
- **Count merges once.** Both halves below exclude merge commits (`--no-merges`;
  `git log --numstat` skips them natively). A merge-inclusive per-commit `git show`
  loop counts every batch member's changes twice — once in the member, once in the
  merge that carried it — which inflates lines-per-edit severalfold on a
  merge-heavy history and dilutes the share metrics with file-less merge slots.

Configure the three regexes per repo — a product-dir set that misses `api/`, `cmd/`,
`web/src/`, or the package name itself silently reports a false "healthy" reading. A
library rooted at the repo top (common in Go) needs its root files in the set, e.g.
`'(^(internal|cmd)/|^[^/]+\.go$)'`.

```bash
#!/usr/bin/env bash
# Configure per repo. This is the one thing that must be right.
PRODUCT_RE='^(src|lib|pkg|internal|api|cmd|app|web/src)/'
TEST_RE='(_test\.go$|\.test\.[jt]sx?$|^tests?/|/tests?/|test_.*\.py$|_spec\.rb$)'
DOC_RE='(\.md$|^docs/)'

N=300
COUNT=$(git rev-list --count --no-merges HEAD)
[ "$COUNT" -lt "$N" ] && N=$COUNT          # shallow or young history is the common case

lib=0; bookonly=0
while read -r s; do
  f=$(git show --name-only --format= "$s")
  if echo "$f" | grep -vE "$TEST_RE" | grep -qE "$PRODUCT_RE"; then
    lib=$((lib+1))
  elif echo "$f" | grep -qE "$DOC_RE"; then
    bookonly=$((bookonly+1))               # touched docs, touched no product code
  fi
done < <(git rev-list -n "$N" --no-merges HEAD)
echo "bookkeeping-only:  $bookonly/$N"
echo "library-touching:  $lib/$N"

# Per-file: edit count x lines-per-edit x add/delete shape
# (git log --numstat skips merges natively — same population as the loop above)
git log -n "$N" --no-merges --numstat --format= \
  | awk '$3!=""{a[$3]+=$1; d[$3]+=$2; n[$3]++}
         END{for(f in n) printf "%4d edits  +%-6d -%-6d  %5.1f ln/edit  %s\n",
                                n[f], a[f], d[f], (a[f]+d[f])/n[f], f}' \
  | sort -rn | head -15
```

Read the signals in this order, strongest first:

1. **Per-file edit share** (the table's edit count against `N`): a bookkeeping file
   edited in more than half of all commits is the strong signal — it separated the
   measured failing and healthy repos 3.3x where no other signal managed 2x.
   Corroborate with the same row's **lines-per-edit and add/delete shape**: a few
   lines per edit with balanced adds/deletes is record-patching; `+thousands/−tens`
   is honest growth. The per-file table is also the one signal that **survives the
   gate itself** — once repairs ride inside product PRs, bookkeeping-only commit
   share and the convergence check both read healthy whether or not the churn
   stopped, but a spec file touched by most commits stays visible here. Judge each
   file by its own row, not by which file it is: on the measured failing repo the
   specification carried the record-patching signature while the *changelog*, at 33.5
   lines per edit, read as honest growth — the same file class can sit on either
   side.
2. **Bookkeeping-only commit share**: commits touching docs and no product code. Weak
   on its own — 59% against 47% on the measured pair, a 1.3x separation that no
   threshold can be drawn across. Read it as corroboration of the per-file table.
3. **Library-touching commit share**: the coarsest signal, and on the measured pair it
   separated nothing — 26.7% failing against 25.7% healthy. It also moves severalfold
   on the `PRODUCT_RE` choice alone. Quote it only with its regex, only as a trend
   against the same repo's own history, and never as a verdict.

No LOC ratio appears here on purpose. Guard-LOC vs product-LOC moves severalfold on the
choice of `PRODUCT_RE` alone and false-positives on TDD-heavy or fake-heavy repos, so it
cannot be a threshold; case 3 of the gate is the test that replaces it.
