# Functional Overview

The initial functional scope centers on a locally run demonstration of colleague and project discovery through Studio.
A functional management API and developer-only UI proof of concept support manual correction of source links.
The [Context chapter](01-context.md) shows the actors and system boundaries.
These are planned capabilities; this repository does not yet implement them.

## User Goals and Validation Status

- **Find colleagues relevant to a topic — initial local-demo journey.**
  Simon reports that a talk with Marijn Siebel confirmed this goal and its candidate data points.
  Source coverage and match quality still require validation.
- **Find HAN projects related to a topic and see what information is available — initial local-demo journey.**
  A HAN staff member uses project results to judge whether a new project in that topic makes sense.
  Simon reports that the talk with Marijn confirmed this goal, journey, and candidate data points.
  Work that HAN employees can bill hours to is a provisional criterion for a project; other criteria remain undefined.
  Available project data and source links still require validation under [source research issues #6](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6), [#7](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/7), and [#9](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9).
- **Correct an ambiguous or wrong source link — operator journey in scope.**
  Simon confirmed a functional management API and developer-only UI proof of concept.
  [Pipeline issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16) must select the first supported link types and correction behavior.
- **Review personal project hours — confirmed goal, uncommitted proof of concept.**
  Real UBW FRIS access is unavailable for this project.
  Simon proposes demonstrating hours spent; the HR scope remains undecided.
  Remaining hours and weekly capacity cannot be assumed from hours spent.
- **Check a colleague's capacity or review billability — confirmed goal, uncommitted journeys.**
  Simon reports that the talk with Marijn confirmed these information goals and candidate data points.
  Whether either belongs in this proof of concept remains undecided; data meaning and access remain unresolved.

## Expert Discovery Journey

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

## Project Discovery Journey

1. An authenticated HAN staff member asks in Studio which projects exist related to a topic.
2. Studio requests graph context through the HAN MCP server and conveys which authenticated staff member made the request.
3. The response lists relevant projects. Each result has a title, a short description, important metadata such as a start date and, when applicable, an end date, a reason for the match, and a traceable source for the connection to the topic.
4. The staff member uses the information to decide whether a new project in that topic makes sense.

Project eligibility beyond billable work, source coverage, and the available metadata remain to be defined and validated.

## Manual Mapping Journey

1. A developer or designated operator inspects an ambiguous or unmatched link between a source record and a person, project, topic, or other graph entity.
2. They use the management API, through a developer-only UI proof of concept, to choose or create the intended link or to correct a previously accepted mapping.
3. A subsequent expert-discovery query reflects the corrected connection.

This API is not called by Studio and is not a staff-facing feedback path.
A staff member who notices an irrelevant result would need to report it outside the initial product flow; no reporting mechanism is planned yet.
The precise API operations and first supported link types remain for [pipeline issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16).

## Confirmed Goals Awaiting Scope Decision

The talk with Marijn confirmed the value of checking a colleague's capacity, reviewing personal project hours and remaining allocation, and reviewing team billability as user goals.
The associated data points are candidates for source validation, and none of these HR-data journeys is committed to the initial demonstration.
A possible narrower demonstration would show hours spent from permitted UBW FRIS demo data.
The project cannot currently claim that UBW FRIS provides a person's weekly working hours or enough information to derive remaining allocation.
The scope of any HR-data demonstration remains to be decided with Marijn Siebel and Sander Maijers.

The project is not defining detailed rules for who may view another person's hours, capacity, or billability from the unavailable real data.
The MCP interaction must carry the requesting user's identity so that later permission or role-based controls can be applied.
Access policy and enforcement remain open in [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).
