# Decision Log Section Template

**Purpose**: Capture significant architectural decisions with their context and rationale. This preserves institutional knowledge that would otherwise be lost when team members leave.

ADRs live in `docs/software-guidebook/adr/` and use the repository format in `docs/software-guidebook/adr/TEMPLATE.md`.

## ADR guidance

Record decisions that are architecturally significant, surprising, difficult to reverse, or constrain future options. Capture rejected alternatives and the context known when the decision was made. Keep superseded ADRs discoverable and link their replacements. Use the repository's [ADR template](../../../../../docs/software-guidebook/adr/TEMPLATE.md) for the ADR itself.

## Chapter guidance

- Index existing ADRs by ID, title, status, date, and link. Include only decisions supported by an ADR or other cited project evidence.
- Summarize why each decision matters to the architecture without duplicating its full text.
- Identify superseded decisions and link their replacements when the ADRs record that relationship.
- Treat the ADR files as the source of truth. The guidebook skill updates only the selected chapter; creating or editing an ADR is separate work.
- If no ADR exists yet, explain that the log has no recorded decisions rather than inventing entries.

## Chapter shape

```markdown
# Decision Log

Architecture decisions are recorded in [ADRs](adr/). This chapter indexes the decisions relevant to the system design.

## Decisions Index

| ID | Decision | Status | Date |
| --- | --- | --- | --- |
| [ADR-001](adr/ADR-001-example.md) | Example decision | Accepted | YYYY-MM-DD |
```

Replace the example row with supported entries. Omit the table when there are no ADRs.
