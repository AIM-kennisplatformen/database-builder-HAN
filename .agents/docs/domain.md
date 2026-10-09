# Domain docs

Use one root `GLOSSARY.md` for shared domain terms and `docs/software-guidebook/adr/` for architecture decisions. Create the glossary and individual ADRs only when their content is established.

For each new ADR, use `docs/software-guidebook/adr/TEMPLATE.md` as the format and save it in the same directory as `ADR-001-<slug>.md`, incrementing the highest existing number. This repository rule takes precedence over an installed skill's default ADR directory or format.

Before exploring a relevant area, read the glossary and applicable ADRs if they exist. Use the glossary's terms in issues, proposals, tests, and code discussions. Surface any conflict with an existing ADR explicitly.
