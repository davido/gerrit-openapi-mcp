---
name: gerrit-change-screen
description: Render the Gerrit "change screen" for a change number using the gerrit-openapi OpenAPI→MCP bridge. Use whenever the user asks to "show the change screen", "show change screen for change N", "render change N", or otherwise display a Gerrit change's info, votes/submit-requirements, files, and comments. Read-only; never posts votes or comments.
---

# Gerrit change screen (via the OpenAPI → MCP bridge)

Trigger: the user asks to show/render the change screen for a Gerrit change, e.g.
`show change screen for change 4711`.

## Steps

1. Parse the change number `N` from the request (accept `637823`, `#637823`, or a
   full `…/c/project/+/637823/…` URL — use the numeric id).
2. Fetch via the mounted **gerrit** bridge (`serve-all`): core read tools are
   unprefixed, and each plugin's tools are under its plugin name (`checks_…`).
   A single-API `gerrit-openapi` core server also serves the core reads. Prefer
   a single composite call if present, else the individual reads:
   - If a `getChangeScreen` tool exists → call it with `change-id: N`.
   - Otherwise:
     - `getChangesChangeIdDetail` — `change-id: N`,
       `o: ["LABELS","DETAILED_LABELS","CURRENT_REVISION","CURRENT_COMMIT","MESSAGES","DETAILED_ACCOUNTS","SUBMIT_REQUIREMENTS"]`
       → subject, status, owner, repo/branch, parent, strategy, labels + votes,
       submit requirements, commit message, messages. Capture `current_revision`
       from this response and use it as the `revision-id` for every
       revision-scoped request below, so files, related and checks all match the
       patch set the header shows (a patch set uploaded mid-render would
       otherwise shift the literal `current`).
     - `getChangesChangeIdComments` — `change-id: N` → published review comments.
     - `getChangesChangeIdRevisionsRevisionIdFiles` — `change-id: N`,
       `revision-id: <current_revision>` → the file list (+ insertions/deletions).
     - `getChangesChangeIdRevisionsRevisionIdRelated` — `change-id: N`,
       `revision-id: <current_revision>` → relation chain. Shape: `{"changes": [...]}`; each
       entry has `_change_number`, `commit.subject`, `status`; the current change is
       the one whose `_change_number` matches the change's `_number`.
     - `getChangesChangeIdSubmittedTogether` — `change-id: N` → submitted together.
       Shape: `{"result": [...]}` (note: `result`, **not** `changes`); each entry has
       `_number`, `subject`, `project`, `branch`.
     - **Checks (only if the checks plugin is mounted — the `checks_*` tools are
       present):** `checks_getChangesChangeIdRevisionsRevisionIdChecks` —
       `change-id: N`, `revision-id: <current_revision>` → `{"result": [...]}`, one entry per
       run. On gerrit-review.googlesource.com each entry already carries what the
       screen needs: `checker_name`, `state` (`SUCCESSFUL`/`FAILED`/`RUNNING`/
       `SCHEDULED`/`NOT_STARTED`/`NOT_RELEVANT`), `url` (CI console log link),
       `checker_description`, and `submit_impact` (`submit_impact.required` drives
       the REQUIRED chip). Render the Checks tab straight from this one response.
       **Fallback for older/local checks builds:** these checker fields are
       documented "only set if checker details are requested", and this list tool
       has **no `o` parameter**, so some builds omit them. If a run comes back
       **without `checker_name`**, fetch only that run's detail —
       `checks_getChangesChangeIdRevisionsRevisionIdChecksCheckId`, `change-id: N`,
       `revision-id: <current_revision>`, `check-id: <the run's checker_uuid>`, `o: ["CHECKER"]`
       — and merge `checker_name`/`checker_description`/`submit_impact` onto it. Only
       call this for runs actually missing the fields; do not fan out when the list
       already has them.
       Treat a missing-plugin or permission error as an **error**, not an
       empty-successful list; if the checks plugin is not mounted (no `checks_*`
       tools), skip checks entirely and keep the template's plugin note.
   Tool params use the spec's literal hyphenated names (`change-id`, `revision-id`),
   and list params are arrays (`o: [...]`), not strings. Relation chain and submitted
   together each return nothing/404 for a standalone change — tolerate that and
   **omit the entire panel (heading included) when the list is empty**, exactly as Gerrit
   does. Never render a placeholder or a note explaining the emptiness — a hidden panel is
   the correct output, not junk text.
3. Render an **Artifact** (HTML) from the bundled template — **do not re-author the
   markup or CSS.** This is what keeps every render pixel-identical across sessions.
   - Copy `template.html` (sibling of this file, in the skill directory) into the
     scratchpad as the starting point. **Keep the entire `<style>` block byte-for-byte.**
   - Fill the `{{TOKENS}}` with live data and repeat each `<!-- REPEAT ... -->` block
     once per item (reviewers, submit requirements, files, relation-chain rows,
     submitted-together rows, comments, checks runs). Token list and per-section rules
     are documented in the template's top comment.
   - Apply the empty rules from step 2: omit the Relation chain / Submitted together
     panel entirely (heading included) when its list is empty.
   - Checks tab: keep the template's plugin note unless the checks plugin is mounted
     (the `checks_*` tools are present); then replace the note with the **Results
     summary + per-run rows** per the template's commented pattern:
     - Summary: bucket runs by `state` and fill the four category counts
       (`{{CHK_ERROR}}`=FAILED, `{{CHK_WARNING}}`/`{{CHK_INFO}}`=reported results at
       that severity — usually 0, `{{CHK_SUCCESS}}`=SUCCESSFUL); add class `zero` to any
       count that is 0. State → sev color; REQUIRED chip when `submit_impact.required`.
     - Timing: each row shows a duration from `finished − started`, formatted like
       Gerrit — `Passed (M min S sec)` / `Failed (…)` for finished runs, `Running` when
       unfinished; omit `.dur` when either timestamp is absent.
     - The checks REST endpoint only returns **checker-backed** runs. UI-only
       `ChecksProvider`s (e.g. "Conflict Markers", "If This Then That") register in
       PolyGerrit directly and never hit REST, so the render legitimately lists fewer
       runs than the live web UI — keep the template's `.checks-foot` note saying so.
   - Publish with the Artifact tool, favicon `🔭`. The template is already self-contained
     (inline CSS, no external assets) — keep it that way.
   If `template.html` is ever missing, fall back to authoring the same sections by hand:
   header (`#<number> · <subject>` + status chip), Change Info (owner, updated,
   repo | branch, parent, strategy, hashtags), Submit Requirements (vote color: +1/+2
   green, −1/−2 red, 0 neutral; satisfied state), commit message (monospace), files
   (path · size · delta), comments, relation chain, submitted together.
4. In chat, add one line linking the artifact.

## Rules

- **Read-only.** Never call a write tool (review/submit/abandon/edit); if the user
  asks to *vote* or *comment*, stop and confirm — that's a real publish.
- Requires the `gerrit-openapi` bridge connected and `GERRIT_COOKIE` exported for
  authenticated/private changes (public changes work anonymously).
- If the change isn't found, report the id and the server's error; don't invent data.
