# Data

This chapter follows the scope and validation status in the [Functional Overview](02-functional-overview.md).
Expert discovery and manual correction of source links are in the initial proof of concept.
The data needed for those journeys still depends on source research; project hours, capacity, and billability remain candidate journeys awaiting a scope decision.
The entities below are a conceptual inventory, not an implemented or approved schema.
The earlier candidate needs are recorded in [issue #5](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/5).

## Data Stores Overview

TypeDB is the planned knowledge-graph store.
No store has been implemented in this repository, and there are currently no database schemas, migrations, storage configurations, or persisted datasets.

## Data Sources Overview

The following sources are available or under consideration for the initial journey and candidate journeys.
Their exact fields, coverage, and joining keys have not been verified.

| Source | Possible contribution | Access and validation status |
| --- | --- | --- |
| Microsoft Graph | Colleague identity and work contact; potential expertise, affiliation, and role | Production access is available, but the current token does not provide all selected expertise and work-context fields. See the [access assessment][graph-assessment] for [issue #6][issue-6]. |
| UBW FRIS | Project associations for expert discovery; hours, capacity, or billability only if a later scope decision includes them | Demo data only; usable fields remain under [issue #9][issue-9]. |
| Enrichment source | Source-backed connections between people and topics, with possible supporting evidence | A trusted API, dataset, or approved manual import remains to be selected under [issue #7][issue-7]. |
| HAN publication data | Publications and other possible evidence supporting expertise and experience | A specific publication source has not been selected; its use depends on suitable enrichment data. |
| Domain or theme classification source | Expertise domains and societal themes that may connect people and projects | A specific vocabulary or source has not been selected. |

[issue-6]: https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6
[issue-7]: https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/7
[issue-9]: https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9
[graph-assessment]: ../data-sources/ms-graph-api.md

## Database Model

No database model has been implemented.
The entities and relations below describe possible data for the initial journeys and earlier candidate needs without prescribing a TypeDB schema.
Capacity, project hours, and billability are not initial data requirements.
The fields listed for those candidate entities require validation if their journeys are approved.

### Key Entities

#### Person

Represents a colleague whom users may find through a sourced expertise connection.

- **Initial data points**: a stable identifier for linking records, display name, and work email address; department and function where available.
- **Initial relations**: sourced expertise connections and any available project or organizational associations that explain a match.
- **Candidate relations**: team membership, management, capacity, project hours, and billability if a later journey requires them.
- **Lifecycle**: not documented.

#### Organizational Unit

Represents a school, research department, educational programme, or other organizational grouping.

- **Key data points**: stable organizational-unit identifier, name, unit type.
- **Key relations**: affiliated people, roles, teams, and projects.
- **Lifecycle**: not documented.

#### Team

Candidate for a later team-billability journey; its inclusion in the initial graph is uncommitted.

- **Key data points**: stable team identifier, name.
- **Key relations**: team members, manager, and organizational unit.
- **Lifecycle**: not documented.

#### Project

Represents a project that may support a person's connection to a topic.
Capacity and hours are candidate extensions only.

- **Key data points**: stable project identifier, name, description, status, start date, end date.
- **Initial relations where supported by source data**: participants, participant roles, organizational unit, and topics.
- **Candidate relations**: allocated and recorded hours if an HR-data journey is approved.
- **Lifecycle**: not documented.

#### Topic

Represents a named skill, expertise domain, subject, or societal theme used to find people.
A separate project-exploration journey remains uncommitted.
Examples include “Python,” “circular economy,” and “energy transition.”

- **Key data points**: stable topic identifier, label, topic type, source.
- **Key relations**: typed, sourced connections to people; supporting evidence and classified projects where available.
- **Lifecycle**: not documented.

#### Evidence Item

Represents possible richer support for a person's expertise connection, such as a publication or project contribution.
The initial result requires a traceable source and match reason, but a separate evidence item depends on available data.

- **Key data points**: stable evidence identifier, evidence type, title, summary or description, source reference or URL, publication or occurrence date.
- **Key relations**: people, their roles, projects, and topics.
- **Lifecycle**: follows the source, but source-specific behavior has not been documented.

#### Capacity Record

Candidate for a later capacity journey; it is not an initial requirement.

- **Key data points**: person identifier, period start, period end, period granularity, capacity amount, unit, observation date, actual or forecast indicator.
- **Key relations**: person and applicable period.
- **Lifecycle**: not documented.

#### Project Hours Record

Candidate for a later project-hours journey; it is not an initial requirement.

- **Key data points**: person identifier, project identifier, period start, period end, hour category, amount, unit, observation date.
- **Hour categories to validate if this journey is approved**: allocated or budgeted hours, booked or recorded hours, committed or projected hours where supplied, and remaining hours.
  Hours spent alone do not establish remaining allocation.
- **Key relations**: person, project, and applicable period.
- **Lifecycle**: not documented.

#### Billability Record

Candidate for a later billability journey; it is not an initial requirement.

- **Key data points**: person or team identifier, period start, period end, billable hours or other stated billability measure, percentage where supplied, aggregation level, observation date.
- **Key relations**: person or team and applicable period.
- **Lifecycle**: not documented.

#### Source Record

Represents the provenance needed to trace an imported data point to its origin.

- **Key data points**: source system, source record identifier, source modification time, retrieval time, and transformation identifier where applicable.
- **Key relations**: imported entity, relation, or value.
- **Lifecycle**: determined by the source; no local policy is documented.

### Key Relations

| Relation | Participants | Initial status |
| --- | --- | --- |
| Organizational affiliation | Person and Organizational Unit | Useful when available for colleague context. |
| Project participation | Person and Project | Possible source-backed reason for an expertise connection; source link and person's role need validation. |
| Expertise connection | Person and Topic | Initial journey needs a connection type, match reason, and traceable source. |
| Expertise support | Evidence Item, Person, and Topic | Richer evidence is desirable where suitable data is available. |
| Project classification | Project and Topic | Possible support for an expertise connection; classification and source need validation. |
| Provenance | Source Record and imported data | Initial journey needs every match to remain traceable to a source. |

Team membership and management, capacity for a period, project hours for a period, and billability for a period are candidate relations for uncommitted journeys.

Stable identifiers are needed for whichever people, organizations, projects, topics, and evidence sources are used in the initial journey.
Candidate entities would need their own identifiers if brought into scope.
The available identifiers and joining keys have not yet been established; names, titles, and labels alone do not provide stable identity.

## Data Flows

No ingestion or retrieval flow has been implemented.

The initial expert-discovery result must explain each colleague match and trace it to a source.
The management journey must allow a correction to an ambiguous, unmatched, or previously accepted link to appear in a later query.
No source-to-model mappings or transformations currently exist in this repository.

## Data Sensitivity

| Data type | Classification or known restriction |
| --- | --- |
| Person identity, work contact, and organizational affiliation | Personal organizational data; the initial expert-discovery journey is for authenticated HAN staff. |
| Expertise connections and supporting evidence | Visibility depends on the source; source access and MCP authorization need validation. |
| Capacity, project hours, and billability | Candidate HR-related data; no detailed viewing rules or initial data requirement have been agreed. |
| Management relationships | Candidate personal organizational data if a later journey needs a management scope. |
| Raw UBW FRIS records | Demo data only for this project; exposure and authorization rules need design before any HR-data journey. |

Production extracts, personal data, secrets, and confidential source records must not be committed to this repository.

## Seed Data

No seed data or seed-loading process exists.

## Test Data

No fixtures, factories, or test datasets exist.

## Migrations

No schema or migration strategy exists.

## Backup and Recovery

No application data store has been implemented, so backup and recovery procedures cannot yet be documented.
