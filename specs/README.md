# Spec convention

One file per phase or significant feature: `specs/<slug>.md` (e.g. `specs/phase-2-embeddings.md`).
Small fixes and one-off corrections don't need a spec — this is for work where "what are we
actually building" benefits from being written down and agreed on before code exists.

## Workflow

1. **Draft.** Write the spec (template below), status `Draft`. This can be collaborative — start
   from a rough ask, ask clarifying questions, propose the design.
2. **Approve.** Get explicit sign-off from the user on the spec — in particular on schema/DDL,
   public interfaces, and anything else expensive to change later. Flip status to `Approved`.
   Do not start writing implementation code before this step.
3. **Implement.** Break the approved spec into tasks (TaskCreate) and build incrementally,
   checking in against the spec as you go — if reality diverges from the spec (a design decision
   turns out to be wrong, a scope item turns out to be harder/easier than expected), update the
   spec file too, don't let it silently go stale.
4. **Done.** Flip status to `Implemented` once shipped. Leave the file in place — it's living
   documentation of what the phase/feature is and why, not a disposable planning doc.

## Template

```markdown
# <Phase/feature name>

Status: Draft | Approved | Implemented

## Goal

What this accomplishes and why, in a couple sentences.

## Scope

- In scope: ...
- Out of scope: ... (explicitly naming what you're *not* building is as important as what you are)

## Design

Whatever the load-bearing technical decisions are: module layout, schema/DDL, public interfaces,
data flow. Doesn't need to be exhaustive — enough that someone could implement it without
re-deriving the decisions, and enough that a reviewer can spot problems before code exists.

## Acceptance criteria

Concrete, checkable statements of "done". Prefer things that can be verified (a command that
succeeds, a test that passes, a query that returns the right shape) over vague adjectives.
```

## Existing specs

- [phase-1-indexing-foundation.md](phase-1-indexing-foundation.md) — Implemented
- [phase-2-embeddings.md](phase-2-embeddings.md) — Implemented
