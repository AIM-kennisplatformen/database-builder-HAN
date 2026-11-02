You will behave as following:

1. I will give you the information in chunks
2. You will give a very condensed opinion on every addition
3. Never write more than 1000 characters in any response
4. You will write the file when instructed

# Repository Instructions

## Coding Style

Follow the repository's [Markdown instructions](docs/standards/markdown.md) when creating or editing markdown files.

## Domain docs

Use the [Functional Overview glossary](docs/software-guidebook/02-functional-overview.md#domain-glossary) for shared domain terms and `docs/software-guidebook/adr/` for architecture decisions.
Read the glossary and relevant ADRs before working in a domain area.
Create an ADR from `docs/software-guidebook/adr/TEMPLATE.md` in that directory, using the next `ADR-<number>-<slug>.md` number.
These repository paths and format take precedence over installed skill defaults.

## Software Guidebook

Use the [Software Guidebook skill](.agents/skills/software-guidebook/SKILL.md) when making or updating a chapter in `docs/software-guidebook/`. Read the selected chapter and nearby documentation before editing. Use the corresponding file in `references/sections/` for chapter-specific guidance, and `references/c4-mermaid.md` when a C4 diagram is useful.

### Content that does not fit in a chapter

If a topic is important but does not fit in the template of the chapter when it should be included, ask the user for guidance on where to place it. If the user does not provide guidance, do not invent a new chapter or section. Instead, report the topic as unresolved and leave it for the user to decide where to place it.
