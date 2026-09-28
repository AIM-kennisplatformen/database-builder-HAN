# Data

This chapter lists the data points required by the confirmed user needs in the [Functional Overview](02-functional-overview.md). It describes requirements, not an implemented schema.

## Data Stores Overview

No data store has been selected or implemented in this repository. There are currently no database schemas, migrations, storage configurations, or persisted datasets.

## Data Sources Overview

The following sources have been identified by the requirements research. Their exact fields, coverage, and joining keys have not been verified.

| Source                                | Relevant data                                                                                     | Evidence and status                                                   |
| ------------------------------------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Microsoft Graph                       | Person identity, work contact, organizational affiliation, role, team, and management information | Candidate in [issue #5][issue-5]; investigated in [issue #6][issue-6] |
| UBW FRIS                              | Project participation, capacity, allocated and recorded hours, remaining hours, and billability   | Candidate in [issue #5][issue-5]; investigated in [issue #9][issue-9] |
| HAN publication data                  | Publications and other evidence supporting expertise and experience                               | A specific publication source has not been selected                   |
| Domain or theme classification source | Expertise domains and societal themes used to relate people and projects                          | A specific vocabulary or source has not been selected                 |

[issue-5]: https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/5
[issue-6]: https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6
[issue-9]: https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9

## Database Model

No database model has been implemented. The entities and relations below group the confirmed data requirements without prescribing how they must be represented in a future schema.

### Key Entities

#### Person

Represents a colleague whom users may find through expertise, project, capacity, or organizational information.

- **Key data points**: stable person identifier, display name, work contact information, active status.
- **Key relations**: organizational affiliation, organizational role, team membership, management, project participation, expertise, capacity, project hours, and billability.
- **Lifecycle**: not documented.

#### Organizational Unit

Represents a school, research department, educational programme, or other organizational grouping.

- **Key data points**: stable organizational-unit identifier, name, unit type.
- **Key relations**: affiliated people, roles, teams, and projects.
- **Lifecycle**: not documented.

#### Team

Represents a group of employees for which billability may be reviewed.

- **Key data points**: stable team identifier, name.
- **Key relations**: team members, manager, and organizational unit.
- **Lifecycle**: not documented.

#### Project

Represents a project through which people, experience, capacity, and hours are connected.

- **Key data points**: stable project identifier, name, description, status, start date, end date.
- **Key relations**: participants, participant roles, organizational unit, topics, allocated hours, and recorded hours.
- **Lifecycle**: not documented.

#### Topic

Represents a named skill, expertise domain, subject, or societal theme used to find people and projects. Examples include “Python,” “circular economy,” and “energy transition.”

- **Key data points**: stable topic identifier, label, topic type, source.
- **Key relations**: people with relevant expertise, supporting evidence, and classified projects.
- **Lifecycle**: not documented.

#### Evidence Item

Represents information supporting a person's expertise or experience, such as a publication or project contribution.

- **Key data points**: stable evidence identifier, evidence type, title, summary or description, source reference or URL, publication or occurrence date.
- **Key relations**: people, their roles, projects, and topics.
- **Lifecycle**: follows the source, but source-specific behavior has not been documented.

#### Capacity Record

Represents the capacity reported for a person during a particular period.

- **Key data points**: person identifier, period start, period end, period granularity, capacity amount, unit, observation date, actual or forecast indicator.
- **Key relations**: person and applicable period.
- **Lifecycle**: not documented.

#### Project Hours Record

Represents hours associated with one person and project during a particular period.

- **Key data points**: person identifier, project identifier, period start, period end, hour category, amount, unit, observation date.
- **Hour categories required by the user needs**: allocated or budgeted hours, booked or recorded hours, committed or projected hours where supplied, and remaining hours.
- **Key relations**: person, project, and applicable period.
- **Lifecycle**: not documented.

#### Billability Record

Represents billable hours or billability reported for a person or team during a particular period.

- **Key data points**: person or team identifier, period start, period end, billable hours or other stated billability measure, percentage where supplied, aggregation level, observation date.
- **Key relations**: person or team and applicable period.
- **Lifecycle**: not documented.

#### Source Record

Represents the provenance needed to trace an imported data point to its origin.

- **Key data points**: source system, source record identifier, source modification time, retrieval time, and transformation identifier where applicable.
- **Key relations**: imported entity, relation, or value.
- **Lifecycle**: determined by the source; no local policy is documented.

### Key Relations

| Relation                   | Participants                              | Required relation data                                             |
| -------------------------- | ----------------------------------------- | ------------------------------------------------------------------ |
| Organizational affiliation | Person and Organizational Unit            | Organizational role, start date, end date where available          |
| Team membership            | Person and Team                           | Membership role, start date, end date where available              |
| Management                 | Manager and Person or Team                | Start date, end date, source                                       |
| Project participation      | Person and Project                        | Participant role, participation start date, participation end date |
| Person expertise           | Person and Topic                          | Source, explicit or inferred indicator                             |
| Expertise support          | Evidence Item, Person, and Topic          | Person's role in the evidence, source                              |
| Project classification     | Project and Topic                         | Source, explicit or inferred indicator                             |
| Capacity for period        | Person and Capacity Record                | Period and unit                                                    |
| Project hours for period   | Person, Project, and Project Hours Record | Period and hour category                                           |
| Billability for period     | Person or Team and Billability Record     | Period and aggregation level                                       |
| Provenance                 | Source Record and imported data           | Source identifier and relevant timestamps                          |

Stable identifiers are needed for people, organizational units, teams, projects, topics, and evidence. The available identifiers and joining keys have not yet been established. Names, titles, and labels alone do not provide stable identity.

## Data Flows

No ingestion or retrieval flow has been implemented.

The project requirements call for data from different sources to remain traceable to its origin and for changed source records to be identifiable. No source-to-model mappings or transformations currently exist in this repository.

## Data Sensitivity

| Data type                                                     | Classification or known restriction                                                            |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Person identity, work contact, and organizational affiliation | Personal organizational data                                                                   |
| Expertise and supporting evidence                             | Visibility depends on the source; access is limited to authenticated HAN staff                 |
| Capacity                                                      | HR-adjacent data; exact access rules are not documented                                        |
| Personal project hours and allocations                        | Confidential HR and project data; staff may view their own information                         |
| Employee and team billability                                 | Confidential management data; managers may view information for employees or teams they manage |
| Management relationships                                      | Personal organizational data used to determine management scope                                |
| Raw UBW FRIS records                                          | Must not be exposed verbatim to unauthorized users                                             |

Production extracts, personal data, secrets, and confidential source records must not be committed to this repository.

## Seed Data

No seed data or seed-loading process exists.

## Test Data

No fixtures, factories, or test datasets exist.

## Migrations

No schema or migration strategy exists.

## Backup and Recovery

There is no application data store for which backup or recovery procedures can currently be documented.
