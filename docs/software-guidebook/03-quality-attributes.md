# Quality Attributes

This chapter records quality goals for the initial expert-discovery demonstration, using the characteristic → subcharacteristic names described by [ISO 25000](https://iso25000.com/en/iso-25000-standards/iso-25010) for [ISO/IEC 25010:2023](https://www.iso.org/standard/78176.html).
The goals are requirements, not measured properties of the current system: this repository has no implementation yet ([Context](01-context.md)).
They address the prioritized, observable scenarios requested in [issue #15](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/15).

## Priority Quality Attributes

The following qualities are most important for the initial demonstration, in priority order:

1. **Compatibility → Interoperability.**
   Studio must be able to use the information exchanged with the HAN service for the expert-discovery demonstration.
2. **Functional suitability → Functional correctness.**
   An inaccurate or unsupported expertise claim could lead staff to contact the wrong colleague or mistake project involvement for a verified personal skill.
3. **Reliability → Fault tolerance.**
   A source refresh failure must not destroy previously valid information or appear to be a successful update.
4. **Security → Confidentiality.**
   Colleague information must not be returned when the request lacks a trustworthy authenticated HAN user identity.
5. **Security → Authenticity.**
   The claimed identity of a HAN user must be verified before the request is treated as coming from that user.

These priorities reflect the decisions for this demonstration.
Marijn Siebel and Sander Maijers have not separately confirmed them.
The [Functional Overview](02-functional-overview.md) defines the initial expert-discovery scope; HR-specific journeys remain uncommitted.

## Compatibility → Interoperability

| Measure | Initial target | Current | Measured at |
| --- | --- | --- | --- |
| Studio exchange | A known expert-discovery request through Studio produces usable colleague results with the expected identity, connection type, reason, source, and insertion time. | Not measured | End-to-end local demonstration through Studio and its MCP client. |

The [Context](01-context.md) identifies Studio as the external chat application and MCP client.
The precise exchange contract remains open under [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).

## Functional Suitability → Functional Correctness

| Measure | Initial target | Current | Measured at |
| --- | --- | --- | --- |
| Source-backed matches | Every displayed match has a traceable source, connection type, reason, and insertion time. | Not measured | Repeatable expert-discovery checks using known source records. |
| Fact age | Every imported fact or connection in the check data has its own insertion timestamp. | Not measured | Inspect facts or connections imported at different times. |
| Incorrect skill claims | No project association or conflicting claim in the check data is presented as a verified personal skill. | Not measured | Checks with project associations and conflicting source claims. |
| Correction outcome | A corrected conflict changes the next result while the original attributed claims remain available for inspection. | Not measured | Management UI correction followed by an expert-discovery check. |

The [glossary](../GLOSSARY.md) distinguishes an expertise connection from a verified personal skill.
The [Functional Overview](02-functional-overview.md) requires source-backed match explanations and a manual-correction journey.
Checks with representative data will be needed before these targets can say anything about match quality beyond the test records.

## Reliability → Fault Tolerance

| Measure | Initial target | Current | Measured at |
| --- | --- | --- | --- |
| Failed refresh | Previously valid data remains unchanged after an injected source-refresh failure. | Not measured | Compare source data before and after a failed refresh. |
| Failure visibility | Every injected refresh failure is visible to an operator; retained facts keep their original insertion times. | Not measured | Operator-facing failure check and inspection of retained facts. |

The acceptable age of retained data and the recovery time after a failure have not been agreed.
[Issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16) covers the later design of refresh and failure handling.

## Security → Confidentiality

| Measure | Initial target | Current | Measured at |
| --- | --- | --- | --- |
| Disclosure without identity | Zero colleague results when a request lacks a trustworthy authenticated HAN user identity. | Not measured | MCP request check with identity absent; a request with accepted identity checks the normal path. |

HR-specific confidentiality targets depend on a later decision to include an HR journey.

## Security → Authenticity

| Measure | Initial target | Current | Measured at |
| --- | --- | --- | --- |
| Claimed user identity | No request with a fabricated or unverifiable HAN user identity is accepted as that user. | Not measured | MCP request checks with valid, altered, and unverifiable identity claims. |

The mechanism for conveying and verifying identity remains a design question under [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).

## Other Qualities

| Quality | Initial target | Current | Measured at |
| --- | --- | --- | --- |
| Maintainability → Modularity | After one optional source is disconnected, another optional source can still refresh and contribute results. | Not measured | Repeatable check with Microsoft Graph and two optional sources. |

Microsoft Graph is the required starting source for identifying HAN colleagues; the independence target applies to the other sources.
Previously imported data from a disconnected source may still support staff-facing results, with its source and insertion time visible.
Disconnecting a source must cause zero automatic deletions of its existing facts or connections, verified by comparing the data before and after disconnection; removal requires a separate operator action.
The initial demonstration does not need a deletion control.

## Trade-offs

| Quality | Tension | Reason and initial response |
| --- | --- | --- |
| Functional correctness | Breadth of results | Withhold unsourced or disputed skill claims even when doing so reduces the number of matches. |
| Fault tolerance | Freshness | Retain the last valid data after a failed refresh and expose its insertion time; an acceptable age limit remains open. |
| Modularity | Freshness after disconnection | Preserve existing data when a source is disconnected, while making its source and insertion time visible. |

The initial targets are repeatable demonstration checks, not production service levels.
Response-time, throughput, availability, and data-age targets need representative data and usage expectations before numeric values can be set.
