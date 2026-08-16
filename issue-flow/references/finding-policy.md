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

The failure is measurable. On one project that ran without this policy, 19% of the last
300 commits touched library code, and the two most-edited files in the repository were
the specification and the changelog — each edited in more than 60% of commits **at under
four lines per edit**. The pairing is the discriminator, not either number alone: a
healthy repo can reach a high edit share honestly when each edit is a section landing
(a third, unrelated project measured its spec at 21% of commits, 27 lines per edit,
`+1310/−19` add/delete shape — growth, not record-patching), but high edit count times
low lines-per-edit is a file being patched a sentence at a time. The bookkeeping had
become the deliverable. A sibling project that adopted the policy held library-touching
at 51%, with a comparable issue count and a larger library.

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

The numbers above came from these commands. Configure the three regexes per repo before
trusting any output — a product-dir set that misses `api/`, `cmd/` or `web/src/` silently
reports a false "healthy" reading.

```bash
# Configure per repo. This is the one thing that must be right.
PRODUCT_RE='^(src|lib|pkg|internal|api|cmd|app|web/src)/'
TEST_RE='(_test\.go$|\.test\.[jt]sx?$|^tests?/|/tests?/|test_.*\.py$|_spec\.rb$)'
DOC_RE='(\.md$|^docs/)'

N=300
COUNT=$(git rev-list --count HEAD)
[ "$COUNT" -lt "$N" ] && N=$COUNT          # shallow or young history is the common case
SHAS=$(git rev-list -n "$N" HEAD)

lib=0; bookonly=0
for s in $SHAS; do
  f=$(git show --name-only --format= "$s")
  if echo "$f" | grep -vE "$TEST_RE" | grep -qE "$PRODUCT_RE"; then
    lib=$((lib+1))
  elif echo "$f" | grep -qE "$DOC_RE"; then
    bookonly=$((bookonly+1))               # touched docs, touched no product code
  fi
done
echo "bookkeeping-only:  $bookonly/$N"
echo "library-touching:  $lib/$N"

# Per-file: edit count x lines-per-edit x add/delete shape
for s in $SHAS; do git show --numstat --format= "$s"; done \
  | awk '$3!=""{a[$3]+=$1; d[$3]+=$2; n[$3]++}
         END{for(f in n) printf "%4d edits  +%-6d -%-6d  %5.1f ln/edit  %s\n",
                                n[f], a[f], d[f], (a[f]+d[f])/n[f], f}' \
  | sort -rn | head -15
```

Read the signals in this order, strongest first:

1. **Per-file lines-per-edit** (the table): a most-edited file at a few lines per edit
   with balanced adds/deletes is record-patching; `+thousands/−tens` is honest growth.
   This is the one signal that **survives the gate itself** — once repairs ride inside
   product PRs, bookkeeping-only commit share and the convergence check both read
   healthy whether or not the churn stopped, but a spec file being patched four lines
   at a time stays visible here.
2. **Bookkeeping-only commit share**: commits touching docs and no product code.
3. **Library-touching commit share**: the coarsest signal; use it for trend, not verdict.

No LOC ratio appears here on purpose. Guard-LOC vs product-LOC moves severalfold on the
choice of `PRODUCT_RE` alone and false-positives on TDD-heavy or fake-heavy repos, so it
cannot be a threshold; case 3 of the gate is the test that replaces it.
