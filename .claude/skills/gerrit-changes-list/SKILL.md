---
name: gerrit-changes-list
description: Render the Gerrit "changes list" dashboard for a query using the gerrit-openapi OpenAPI→MCP bridge. Use whenever the user asks to "show my changes", "show open changes", "show the dashboard", "list changes matching <query>", or otherwise display a table of Gerrit changes with owner, reviewers, repo/branch, size, status, and label votes. Read-only; never posts votes or comments.
---

# Gerrit changes list (via the OpenAPI → MCP bridge)

Trigger: the user asks to show/render a list or dashboard of Gerrit changes, e.g.
`show my open changes`, `show the dashboard`, `list changes for project:gerrit status:open`.

## Steps

1. Resolve the **query** `Q` from the request:
   - An explicit Gerrit query passes through (`status:open -is:wip`,
     `project:gerrit owner:self`, `is:open assignee:self`, …).
   - "my changes" / "my open changes" → `owner:self status:open`.
   - "the dashboard" / "incoming" → `status:open -is:wip`.
   - Default when unspecified: `status:open -is:wip`.
2. Fetch via the mounted **gerrit** bridge (core tools are unprefixed) with **one**
   call:
   - `getChanges` — `query: [Q]`,
     `o: ["LABELS","DETAILED_LABELS","DETAILED_ACCOUNTS","SUBMIT_REQUIREMENTS"]`,
     and `limit: 25` (raise it only if the user asks for more).
   - Shape: a **flat array** of `ChangeInfo` for a single query string. (If you ever
     pass several query strings, Gerrit returns an array *per* query — here pass one.)
   - Per change, read:
     - `_number` → the `#` column; `subject`; `project` → Repo; `branch`; `updated`.
     - `owner.name` (needs `DETAILED_ACCOUNTS`) → Owner.
     - `reviewers.REVIEWER[].name` (needs `DETAILED_LABELS` + `DETAILED_ACCOUNTS`) →
       Reviewers; show the first two, then `+N`. Omit the owner from that list.
     - `insertions` + `deletions` → Size letter: `XS` ≤ 10, `S` ≤ 50, `M` ≤ 250,
       `L` ≤ 1000, else `XL` (total changed lines). `n/a` when both are absent.
     - `submit_requirements` (needs `SUBMIT_REQUIREMENTS`) → Status: count entries whose
       `status` is `UNSATISFIED`; show `N missing` when N > 0, `Ready` when 0 unsatisfied
       requirements apply, `n/a` when the list is empty/none applicable.
     - `labels` (needs `LABELS`) → the vote columns `CR` (Code-Review), `V` (Verified),
       `CS` (Code-Style), `FV` (Frontend-Verified). For each label object:
       `approved` → green check; `rejected` → red `✕`; a numeric `value` (from
       `DETAILED_LABELS`, e.g. `-1`) → red `−1` / green `+1` by sign; otherwise, when the
       label exists but is unset → grey `⊘`; when the label is absent for that change →
       empty cell. Only these four columns are shown; other labels are ignored here.
   The `query` and `o` params are arrays, not strings. A query that matches nothing
   returns `[]` — render the empty-state row, not an error.
3. Render an **Artifact** (HTML) from the bundled template — **do not re-author the
   markup or CSS.** This keeps every render consistent across sessions.
   - Copy `template.html` (sibling of this file) into the scratchpad. **Keep the entire
     `<style>` block byte-for-byte.**
   - Fill `{{QUERY}}` and `{{COUNT}}` in the header, then repeat the
     `<!-- REPEAT row -->` block once per change, filling its tokens per the mapping
     above. The template's top comment documents every token and the vote-cell /
     status-chip / size-badge rules.
   - When the result is empty, show the single empty-state row the template provides and
     omit the repeat block.
   - Publish with the Artifact tool, favicon `🔭`. The template is self-contained (inline
     CSS, no external assets) — keep it that way.
   If `template.html` is missing, fall back to a plain table with the same columns:
   `#`, Subject, Owner, Reviewers, Repo, Branch, Updated, Size, Status, CR, V, CS, FV.
4. In chat, add one line linking the artifact and the query it rendered.

## Rules

- **Read-only.** Never call a write tool (review/submit/abandon/edit); if the user asks
  to *vote* or *comment* on a listed change, stop and confirm — that is a real publish.
- Requires the `gerrit` bridge connected and `GERRIT_COOKIE` exported for
  authenticated/private changes (public changes work anonymously).
- For a single change's full screen (files, comments, submit requirements, checks), use
  the `gerrit-change-screen` skill instead.
