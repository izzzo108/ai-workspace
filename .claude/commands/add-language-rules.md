---
name: add-language-rules
description: Workflow command scaffold for add-language-rules.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /add-language-rules

Use this workflow when adding **rules for a new programming language** to this project.

## Goal

Adds a new programming language to the rules system, including coding style, hooks, patterns, security, and testing guidelines.

## Common Files

- `.claude/rules/<lang>-coding-style.md`
- `.claude/rules/<lang>-hooks.md`
- `.claude/rules/<lang>-patterns.md`
- `.claude/rules/<lang>-security.md`
- `.claude/rules/<lang>-testing.md`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Add `<lang>-coding-style.md`, `<lang>-hooks.md`, `<lang>-patterns.md`, `<lang>-security.md`, and `<lang>-testing.md` under `.claude/rules/` with language-specific content
- Optionally reference or link to related skills under `.claude/skills/`

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.
- **Universal rules are NOT per-language** — they live in `/CLAUDE.md` (single source of truth for Claude Code). This command adds only **language-specific** rules under `.claude/rules/<lang>-*.md`. Do not duplicate the universal rules per language.