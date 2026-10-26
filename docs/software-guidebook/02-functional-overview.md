# Functional Overview

The initial demonstration centers on colleague and project discovery through a locally run Studio instance.
A functional management API and developer-only UI proof of concept support manual correction of source links.
The [Context chapter](01-context.md) shows the actors and system boundaries.
These are planned capabilities; this repository does not yet implement them.

## Core Capabilities

1. **Find colleagues by topic**
   - Authenticated HAN staff can find contactable colleagues with a source-backed match reason and a clear connection type. Simon reports that a talk with Marijn Siebel confirmed this user goal and its candidate data points; source coverage and match quality still need validation.
   - Key workflow: [Expert Discovery Journey](#expert-discovery-journey).
2. **Find related HAN projects**
   - Staff can inspect projects related to a topic to judge whether a new project in that area makes sense. Simon reports that the talk with Marijn confirmed this goal and its candidate data points. Work that HAN employees can bill hours to is a provisional criterion for a project; other criteria remain undefined. Available project data and source links still need validation under [source research issues #6](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6), [#7](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/7), and [#9](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9).
   - Key workflow: [Project Discovery Journey](#project-discovery-journey).
3. **Correct source links**
   - A developer or designated operator can resolve ambiguous or wrong links through a functional management API and developer-only UI proof of concept, so later discovery results reflect the correction. [Pipeline issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16) must select the first supported link types and correction behavior.
   - Key workflow: [Manual Mapping Journey](#manual-mapping-journey).

## Key Screens

## Feature Summary

| Feature                | Description                                                                          | Status      |
| ---------------------- | ------------------------------------------------------------------------------------ | ----------- |
| Expert discovery       | Find colleagues relevant to a topic, with contact details and source-backed reasons. | Planned     |
| Project discovery      | Find related HAN projects and inspect their context.                                 | Planned     |
| Manual mapping         | Correct source links and see the correction in a later result.                       | Planned     |
| Personal project hours | Review hours spent and remaining allocation on projects.                             | Unscheduled |
| Colleague capacity     | Check a colleague's available capacity.                                              | Unscheduled |
| Team billability       | Review billability for a team.                                                       | Unscheduled |

Simon reports that a talk with Marijn confirmed the three HR-related user goals and candidate data points; all three features remain unscheduled.
An earlier proposal to demonstrate hours spent has no scheduled implementation.
Real UBW FRIS access is unavailable for this project, and hours spent alone cannot establish remaining allocation or weekly capacity.
Source meaning and access rules for these features remain unresolved; any later work would need to validate them.
The project is not defining detailed rules for who may view another person's hours, capacity, or billability from unavailable real data.
The MCP interaction must carry the requesting user's identity so that later permission or role-based controls can be applied; access policy and enforcement remain open in [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).

## User Journeys

### Expert Discovery Journey

1. An authenticated HAN staff member asks in a locally run Studio instance for colleagues relevant to a topic or work problem.
2. Studio requests graph context through the HAN MCP server and conveys which authenticated user made the request.
3. The response presents a list of relevant colleagues. Each visible result has a name, work email address, a reason for the match, and a traceable source for its connection to the topic.
4. The result distinguishes a recorded skill from a project association or another kind of expertise connection. Department and function are valuable additional context when available.
5. The staff member uses the information to decide whom to contact outside the system.

A source-backed explanation is required for each match; an unverified expertise label alone is insufficient.
Richer supporting evidence, such as a publication or project history, is desirable if suitable data can be obtained.
A colleague without enough information to contact them is omitted from staff-facing results.
The list need not be ranked.
The selected enrichment source may be a trusted API, dataset, or approved manual import, as investigated in [issue #7](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/7).
No document-text extraction is planned for finding expertise.

This journey is a locally run demonstration, not a claim of production deployment.
Microsoft Graph has production access for this project, while UBW FRIS is available only as demo data.
The actual fields, coverage, source joins, and result quality require validation under [issues #6](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6), [#7](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/7), and [#9](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9).
The MCP identity and authorization design remains open under [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).

### Project Discovery Journey

1. An authenticated HAN staff member asks in Studio which projects exist related to a topic.
2. Studio requests graph context through the HAN MCP server and conveys which authenticated staff member made the request.
3. The response lists relevant projects. Each result has a title, a short description, important metadata such as a start date and, when applicable, an end date, a reason for the match, and a traceable source for the connection to the topic.
4. The staff member uses the information to decide whether a new project in that topic makes sense.

Project eligibility beyond billable work, source coverage, and the available metadata remain to be defined and validated.

### Manual Mapping Journey

1. A developer or designated operator inspects an ambiguous or unmatched link between a source record and a person, project, topic, or other graph entity.
2. They use the management API, through a developer-only UI proof of concept, to choose or create the intended link or to correct a previously accepted mapping.
3. A subsequent expert-discovery query reflects the corrected connection.

This API is not called by Studio and is not a staff-facing feedback path.
A staff member who notices an irrelevant result would need to report it outside the initial product flow; no reporting mechanism is planned yet.
The precise API operations and first supported link types remain for [pipeline issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16).

## Domain Glossary

| Term                 | Definition                                                                                                                                                                                                                  |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Expertise connection | A sourced association between a colleague and a topic, such as a recorded skill or participation in a related project. A project association does not by itself establish that the colleague has a verified personal skill. |
| Manual mapping       | A human decision that resolves an ambiguous or unmatched link between a source record and a person, project, topic, or other platform entity.                                                                               |
