# Issue tracker: GitHub

Issues and specs for this repo live in GitHub Issues at `AIM-kennisplatformen/database-builder-HAN`. Use the `gh` CLI from this repository.

## Issue operations

- Create issues with `gh issue create`.
- Read issues with `gh issue view <number> --json number,title,body,labels,comments`.
- Find issues with `gh issue list`, applying state and label filters as needed.
- Update labels, assignees, and issue bodies with `gh issue edit`.
- Add comments with `gh issue comment`; close completed issues with `gh issue close`.
- Link child issues with GitHub sub-issues when available. If unavailable, put `Part of #<parent>` in the child body.

When a skill says to publish to the issue tracker, create a GitHub issue. When it says to fetch a ticket, read the GitHub issue.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Wayfinding

For `/wayfinder`, use one issue labelled `wayfinder:map` as the map and link its tickets as child issues. Label tickets `wayfinder:<type>` and use GitHub issue dependencies for blockers when available. If dependencies are unavailable, record `Blocked by: #<number>` in the child body. Claim a ticket by assigning it to the current developer.
