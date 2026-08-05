# STE — the writing standard for everything this plugin produces

Every document and every tracker issue this plugin writes uses **Simplified Technical
English (STE)**. This file is the standard. Load it before you write a spec, a feature
file, an issue body, a `CLAUDE.md`, a project rule, or a user manual page.

STE exists because the readers of these artifacts — engineers, agents, and future
planning waves — must all extract the *same* meaning from a sentence. A requirement that
reads two ways gets built two ways.

> **About the source.** ASD-STE100 is the aerospace specification this standard derives
> from. Its approved-word dictionary is copyrighted and is **not** reproduced here. What
> follows is the enforceable subset: the writing rules, plus a per-project Terms list that
> does the job the dictionary does. Where this file and ASD-STE100 differ, this file wins
> for this plugin's output.

---

## 1 — What must be STE, and what must not

**Write in STE:**

| Artifact | Written by |
|---|---|
| `docs/specs/spec.md` and every `docs/specs/features/*.md` | project-planner |
| `docs/specs/spec.html` prose | project-planner |
| `CLAUDE.md`, `.claude/rules/*.md`, `.claude/skills/*/SKILL.md` | project-planner |
| Every tracker issue title and body (epic, sub-issue, hotfix, spec-update, review finding) | spec-to-issues, issue-flow PM, project-review PM |
| Issue comments that state a decision, a plan, or a question | issue-flow PM |
| Spec `## Changelog` lines | issue-flow PM |
| `docs/manual/*.md` user manual pages | review-scribe |
| **Code comments and doc comments** — inline comments, docstrings, JSDoc/TSDoc, module headers, test descriptions | issue-worker, review-scribe |

**Never rewrite into STE — reproduce verbatim:**

- Anything quoted from a person: user answers, review comments, requirements the user
  dictated. Quote it, then restate it in STE underneath if it needs clarifying.
- Evidence: error messages, log excerpts, console output, test names, stack traces,
  command output, `file:line` references.
- Code, config, commands, JSON, file paths, identifiers, label names.
- Third-party product names and API field names.
- UI copy inside mockups — that is product content, and the design decides it.

Rewriting evidence destroys it. Rewriting a quote misrepresents the person.

---

## 2 — The rules

### Sentences

1. **A procedure sentence is 20 words or fewer. A description sentence is 25 or fewer.**
   Count the words. Over the limit, split it.
2. **One instruction per sentence.** "Create the branch and copy the env files" is two
   steps. Make it two.
3. **One topic per paragraph. Six sentences maximum.**
4. **Put the condition first, then the action.** "If the test fails, revert the commit" —
   not "Revert the commit if the test fails." The reader must know whether to keep reading
   before they act.
5. **Put the warning before the step it applies to**, never after. A caution the reader
   meets after acting is not a caution.

### Words

6. **One word, one meaning, one part of speech.** Choose the meaning the project needs and
   never use that word another way. If `batch` is a noun in this project, do not write
   "batch the issues" — write "group the issues into a batch".
7. **One concept, one word.** Never rotate synonyms for variety. If it is a "sub-issue",
   it is never also a "child issue", a "member", or a "task".
8. **Keep noun clusters to three words.** "The batch integration branch merge gate" is
   five. Write "the merge gate for the batch's integration branch".
9. **No metaphor, idiom, or slang.** Not "rebase storm", "burn CI", "the pipeline
   starves", "clobber", "stomp", "in the weeds". State the mechanism instead.
10. **No abbreviation the project has not defined**, and define each one once, on first
    use, in the Terms list.

### Grammar

11. **Active voice.** "The PM merges the batch PR" — not "The batch PR is merged."
    The reader must know who acts.
12. **Present tense** for behaviour; **imperative** for instructions.
13. **Keep the articles.** Write "the branch", not "branch". Dropped articles create
    genuine ambiguity about whether one or many are meant.
14. **No `-ing` form as a noun or as a heading.** "Batching" → "How to form a batch".
    "Grouping heuristics" → "How to group loose issues".
15. **Write positively.** State what to do. Keep a prohibition only when the wrong action
    is likely and costly, and then state it once, plainly.
16. **Use a vertical list** whenever a sentence would carry more than two conditions,
    parameters, or alternatives.

---

## 3 — The Terms list (this replaces the dictionary)

Every spec carries a `## Terms` section, and it is the project's controlled vocabulary.

```markdown
## Terms

| Term | Part of speech | Meaning in this project | Do not use |
|---|---|---|---|
| project | noun | The whole product this spec describes. | app, system, product |
| account | noun | A person's login identity. | user record, profile, login |
| create | verb | To make a new record that did not exist. | add, register, provision |
```

Rules for the list:

- Add a row the first time a domain noun or verb appears in the spec.
- The `Do not use` column is the enforcement — it names the synonyms this project rejects.
- Every later artifact reads from this list: `spec-to-issues` writes issue bodies with
  these words, workers build against them, and the manual uses them. A term renamed here
  is a spec change, not a wording change.
- When an issue or a manual page needs a word that is not in the list, add it to the list.

---

## 4 — Patterns for the artifacts

### A functional requirement

One testable statement, active voice, present tense, no conjunction.

```
Bad:  FR-auth-3 — Users should be able to reset their password and the system will
                  email them a link that expires after a while.
Good: FR-auth-3 — The project sends a password-reset link to the account's email address.
      FR-auth-4 — A password-reset link expires 60 minutes after the project sends it.
```

"should be able to" is not testable. "after a while" is not a value. Two requirements
were hiding in one sentence.

### An acceptance criterion

Write the observable result, not the implementation.

```
Bad:  Password reset works correctly.
Good: The account receives an email that contains a reset link.
      The reset link opens the reset form.
      The reset link shows an expiry message 60 minutes after the project sends it.
```

### An issue title

Imperative verb, one deliverable, 10 words or fewer, no ticket-speak.

```
Bad:  Auth stuff / password reset improvements (part 2)
Good: Send a password-reset email to the account
```

### An issue body

Each heading holds one kind of information. No prose that spans headings.

```markdown
## Context
The project has no way to recover an account. FR-auth-3 and FR-auth-4 define the flow.

## What to build
1. Add a reset-request form at `/account/reset`.
2. Send an email that contains a single-use reset link.
3. Expire the link 60 minutes after the project sends it.

## Acceptance criteria
- [ ] The account receives an email that contains a reset link.
- [ ] The reset link opens the reset form.
- [ ] The reset link shows an expiry message after 60 minutes.

## Out of scope
This issue does not change the sign-in form.
```

### A user manual step

Second person, present tense, one action per step, the screenshot after the step.

```
Bad:  After navigating to the settings area, you'll want to hit Save once you've made
      whatever changes you need.
Good: 1. Select **Settings**.
      2. Change the fields you want to change.
      3. Select **Save**. The project shows a confirmation message.
```

### A code comment

The rules in § 2 apply unchanged. Three of them do the most work in code, because a
comment is read in a hurry, out of context, months later.

**One sentence, one fact. Active voice. Say why, not what.**

```
Bad:  // Here we're basically just looping through all the users and, if they haven't
      // been seen in a while, we go ahead and clean them up (this was added because
      // of the memory thing).
Good: // Remove accounts that have been inactive for 90 days.
      // The session cache holds every account it has seen, so inactive accounts leak.
```

**Name the reason a reader cannot see.** Code already states what it does. A comment that
repeats the code is noise; a comment that states the constraint, the reason, or the
consequence is the one worth reading.

```
Bad:  // increment the counter
      counter += 1
Good: // The provider rejects a batch larger than 500 items.
      const MAX_BATCH = 500
```

**A docstring opens with one sentence that states the result.** Then the parameters, then
the failure modes. No metaphor, no `-ing` opener.

```
Bad:  /** Handles doing the thing with the user's stuff and returns whatever it finds. */
Good: /**
       * Return the account that owns the session token.
       *
       * @param token - A session token from the `Authorization` header.
       * @returns The account, or `null` when the token is expired or unknown.
       * @throws StoreError - The account store is unreachable.
       */
```

**Test names are sentences too.** One behaviour per name, present tense, active voice:
`rejects a reset link older than 60 minutes`, not `test reset link expiry stuff 2`.

Verbatim in comments, as everywhere: quoted error strings, URLs, identifiers, spec IDs
(`FR-auth-3`), issue references (`#123`), and any text copied from a specification.

Marker comments keep their conventional keyword — `TODO`, `FIXME`, `HACK` — because
tooling and the `code-auditor` match on it. Write the body of the marker in STE, and name
the issue: `// TODO(#412): Replace the fixed 60-minute expiry with the configured value.`

### A PM decision comment

State the decision, then the reason. One sentence each.

```
Good: Shipping batch #42 without sub-issue #57. #57 is parked on a product question,
      and the other four members do not depend on it. #57 moves to the next batch.
```

---

## 5 — Check before you write the file

- [ ] No sentence is longer than 25 words. No instruction is longer than 20.
- [ ] Every step holds one instruction.
- [ ] Every condition comes before its action; every warning comes before its step.
- [ ] Every domain word is in the Terms list, used with one meaning and one part of speech.
- [ ] No synonym rotation: one concept, one word, throughout the document.
- [ ] No noun cluster longer than three words.
- [ ] No metaphor, idiom, or undefined abbreviation.
- [ ] Active voice, present tense, articles present, no `-ing` nouns or headings.
- [ ] Lists carry anything with more than two conditions or parameters.
- [ ] Quotes, evidence, code, paths and identifiers are verbatim and unedited.
- [ ] Code comments state the reason, not the code. Docstrings open with the result.
      Test names read as one behaviour. Marker keywords (`TODO`, `FIXME`) kept, bodies
      written to the standard and carrying an issue number.
