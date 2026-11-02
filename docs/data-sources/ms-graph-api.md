# Microsoft Graph data for colleague discovery

This finding supports [MS Graph source research, issue #6](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6).
It inventories every Graph call in the [old Microsoft 365 extractor](https://gitlab.com/han-aim/research/dke/knowledgeplatform/management-app-han/-/blob/af9facd3f6ccaf474dfba351f4e653c339eb06a4/src/knowledgeplatformmanagement_han/data/extract/microsoft365.py), maps relevant data points from [requirements issue #5](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/5), and separates observed responses from documented permissions.
The three old-pipeline endpoints below are the only endpoints considered relevant at this time.
No new live Graph request was made for this note.

## Old pipeline endpoint inventory

The old extractor uses delegated authentication through `InteractiveBrowserCredential` and a configured scope list.
Its source file does not establish which scopes were granted to a particular token.
All three calls use Microsoft Graph `v1.0` through the Python SDK.

| Endpoint and purpose                                                                                                                                                                                                | Selected properties and behavior                                                                                                                                                                                      | Evidence and validation                                               |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| [Get group](https://learn.microsoft.com/en-us/graph/api/group-get?view=graph-rest-1.0), `GET /groups/{id}`: load the configured HAN employee group.                                                                 | Selects `id`; the code also logs the returned group's `displayName` and `mail`.                                                                                                                                       | Old extractor `get_han_employees`; code inspected, not retested here. |
| [List group transitive members](https://learn.microsoft.com/en-us/graph/api/group-list-transitivemembers?view=graph-rest-1.0), `GET /groups/{id}/transitiveMembers/microsoft.graph.user`: enumerate employee users. | Selects `accountEnabled`, `id`, `userType`; filters `accountEnabled eq true and userType eq 'Member'`; requests `$count=true`, up to 999 users per page, and `ConsistencyLevel: eventual`; follows `@odata.nextLink`. | Old extractor `get_han_employees`; code inspected, not retested here. |
| [Get user](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0), `GET /users/{id}`: load each member's profile.                                                                                | Selects `givenName`, `id`, `interests`, `mail`, `responsibilities`, `schools`, `skills`, `surname`.                                                                                                                   | Old extractor `get_user_indepth`; code inspected, not retested here.  |

The old extractor stores a colleague only when the profile has `givenName`, `surname`, and `mail` and at least one of `skills`, `interests`, `responsibilities`, or `schools` is nonempty.
That rule can omit contactable colleagues with empty self-entered profile fields and is not a requirement for the new expert-discovery journey.
The group filter narrows the old pipeline's population to enabled members of the configured employee group; it does not itself prove coverage of every HAN colleague.

## Delegated access and observed requests

The token used for the earlier inspection reportedly had delegated OAuth scopes `email`, `Group.Read.All`, `GroupMember.Read.All`, `Mail.Read`, `openid`, `People.Read`, `profile`, `User.Read`, and `User.ReadBasic.All`.
The project now has delegated `User.Read.All` permission and can retrieve `department` and `jobTitle` for colleagues.
The earlier reported token scopes do not establish the old extractor's configured scope list.
The earlier inspection used the `v1.0` transitive-members and Get user endpoints with broader property selections than the old code:

```http
GET https://graph.microsoft.com/v1.0/groups/{group-id}/transitiveMembers/microsoft.graph.user?$count=true&$select=id,displayName,givenName,surname,mail,companyName,department,jobTitle,mailNickname
ConsistencyLevel: eventual

GET https://graph.microsoft.com/v1.0/users/{id}?$select=id,displayName,givenName,surname,mail,skills,responsibilities,schools,interests,aboutMe,pastProjects,companyName,department,jobTitle,mailNickname
```

## Permissions and extraction constraints

The [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference#userread) says delegated `User.Read` reads the signed-in user's profile.
It does not grant full profiles of other colleagues.
The same reference limits delegated [`User.ReadBasic.All`](https://learn.microsoft.com/en-us/graph/permissions-reference#userreadbasicall) for other work or school users to `displayName`, `givenName`, `id`, `mail`, `photo`, `securityIdentifier`, `surname`, and `userPrincipalName`.
Delegated [`User.Read.All`](https://learn.microsoft.com/en-us/graph/permissions-reference#userreadall) covers other users' full profiles and requires administrator consent.
The project now has this permission and can retrieve the required `department` and `jobTitle` fields.
The earlier `User.ReadBasic.All` token supported basic identity fields but did not grant the selected expertise or work-context fields for other colleagues.
The project access check attributed the earlier observed null values for selected nonbasic fields to insufficient access with that token.

The [Get group permissions table](https://learn.microsoft.com/en-us/graph/api/group-get?view=graph-rest-1.0#permissions) includes delegated `Group.Read.All` and `GroupMember.Read.All` among the permissions for that call.
The [transitive-members permissions table](https://learn.microsoft.com/en-us/graph/api/group-list-transitivemembers?view=graph-rest-1.0#permissions) lists delegated `GroupMember.ReadBasic.All` as least privileged and includes the reported `Group.Read.All` and `GroupMember.Read.All` as higher permissions.
That endpoint can return only an object's type and ID, with other properties null, when the app cannot read the member resource type.
The limited-information rule applies to transitive-member results; the project's access finding about selected Get user properties is recorded separately above.

The old extractor requests the [maximum transitive-members page size of 999](https://learn.microsoft.com/en-us/graph/api/group-list-transitivemembers?view=graph-rest-1.0#optional-query-parameters) and follows `@odata.nextLink` until no page remains.
Its cast and filter use advanced query features with `ConsistencyLevel: eventual` and `$count=true`, so recently changed membership may not appear immediately.
The [Graph paging guidance](https://learn.microsoft.com/en-us/graph/paging) confirms that consumers must follow `@odata.nextLink` for complete collection results.
For a new extraction pipeline, [Graph throttling guidance](https://learn.microsoft.com/en-us/graph/throttling) requires handling HTTP `429` and honoring `Retry-After`; the old extractor does not implement an explicit throttling policy in this file.
One Get user call per enumerated member also makes request volume grow with employee count.

## Available identity and contact fields

The earlier requests selected these fields for the initial [expert-discovery journey](../software-guidebook/02-functional-overview.md):

| Graph field   | Use                                                                                            |
| ------------- | ---------------------------------------------------------------------------------------------- |
| `displayName` | Name shown in a colleague result.                                                              |
| `givenName`   | First name for identification and matching.                                                    |
| `surname`     | Family name for identification and matching.                                                   |
| `mail`        | Work email address shown in a colleague result.                                                |
| `id`          | Internal identifier for joins, deduplication, and provenance. Never expose it to the end user. |

## Selected self-entered fields with sparse reported coverage

The earlier request selected these [Microsoft Graph user properties](https://learn.microsoft.com/en-us/graph/api/resources/user?view=graph-rest-1.0).
The earlier token did not grant full profiles of other colleagues, and the project access check attributed observed nulls to that access limit:

| Graph field        | Possible use for finding expertise                                   |
| ------------------ | -------------------------------------------------------------------- |
| `skills`           | Directly recorded skills; distinguish them from inferred expertise.  |
| `responsibilities` | Work responsibilities that may indicate relevant experience.         |
| `schools`          | Educational background that may help with some searches.             |
| `interests`        | Stated interests that may suggest a topic connection.                |
| `aboutMe`          | Free-text self-description that may mention expertise or work areas. |
| `pastProjects`     | Past project involvement that may support a topic connection.        |

Empty values may mean that a colleague has not supplied usable data; they must not be treated as evidence that the colleague lacks expertise.
The earlier observed nulls reflect that token's access limit, not evidence of an empty colleague profile.
In the inspected response, `skills`, `responsibilities`, `schools`, `interests`, and `pastProjects` were empty, and `aboutMe` was null.
The earlier access assessment supplied the following population counts for **4,712 active accounts**:

| Field              | Accounts with a value | Share of active accounts |
| ------------------ | --------------------: | -----------------------: |
| `interests`        |                    42 |                    0.89% |
| `responsibilities` |                    44 |                    0.93% |
| `schools`          |                    45 |                    0.96% |
| `skills`           |                    74 |                    1.57% |

The four counts sum to 205.
For that earlier population, even if no accounts overlap across fields, at most 205 of 4,712 active accounts (4.35%) had a reported value in at least one of these fields.
Counts for `aboutMe` and `pastProjects` were not supplied.
The earlier reported coverage was too sparse for the four measured fields alone to support colleague discovery across the employee population.
If a populated field is used as an expertise connection, retain its field name as the source.

## Work-context fields

Department and job title are needed for the agreed expert-discovery result and can be retrieved with the project's delegated `User.Read.All` permission.
The earlier requests did not return usable values for these fields because they used a token without that permission.

| Wanted data | Graph user property to investigate | Value for this project                                                                   |
| ----------- | ---------------------------------- | ---------------------------------------------------------------------------------------- |
| Company     | `companyName`                      | Expected to be the same for HAN colleagues, but useful as explicit organization context. |
| Department  | `department`                       | Required context for the expert-discovery result.                                        |
| Job title   | `jobTitle`                         | Required context for the expert-discovery result.                                        |
| Alias       | `mailNickname`                     | Shown as "Alias" on the Microsoft profile card; also used in timetabling.                |

The inspected Get user response had `mailNickname` set to null because the earlier token lacked access, according to the project access check.
[Microsoft describes `mailNickname` as the user's mail alias](https://learn.microsoft.com/en-us/graph/api/resources/user?view=graph-rest-1.0) and says it requires `$select` to retrieve.

## Mapping to the current user needs

The [expert-discovery journey](../software-guidebook/02-functional-overview.md) requires each visible colleague result to have a name, work email address, a reason for the match, and a traceable source.
Department and job title are needed for the agreed expert-discovery result.
The table maps the Graph-relevant candidate data points in [issue #5](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/5) to the three endpoints above.
The issue #5 candidate list was not a final validation of every requirement, so optional context remains provisional.

| Required or candidate data point                                                | Best matching Graph property and endpoint                                                                                                        | Coverage and evidence                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Colleague identity and contact for a result                                     | `id`, `displayName`, `givenName`, `surname`, `mail` from Get user or transitive members.                                                         | **Partial, observed in the reported inspection.** Basic fields are covered by `User.ReadBasic.All`; population-wide completeness of name and work email is unmeasured. Keep `id` internal for joins and provenance.                                                                                                                                                                    |
| Recorded expertise, interests, and experience with a source-backed match reason | `skills`, `responsibilities`, `schools`, `interests` from Get user; `aboutMe` and `pastProjects` were added to the earlier Get user request.     | **Historical coverage was sparse.** Earlier supplied counts found values in at most 4.35% of active accounts for the four measured fields, even with no overlap. The earlier inspected `aboutMe` was null with the limited token. Preserve the property name when a populated value supports an expertise connection. An empty value is not evidence that a colleague lacks expertise. |
| Colleague affiliation and role for match context                                | `department` and `jobTitle` from Get user.                                                                                                       | **Department and job title required and available.** The project has delegated `User.Read.All` and can retrieve both fields. The earlier token lacked this permission.                                                                                                                                                                                                                 |
| Project experience and links to expertise domains or themes                     | `pastProjects` may contain self-entered history in Get user; the three endpoints contain no verified structured project-to-person or theme link. | **Not covered directly.** The inspected `pastProjects` was empty. Source-backed project and topic links need evidence from another source or a documented derivation; a self-entered project name alone does not establish a verified relation.                                                                                                                                        |

Personal project hours, remaining allocation, capacity, and team billability from issue #5 are not mapped to these Graph endpoints because their candidate source is UBW FRIS and those journeys are not in the agreed initial scope.
The current endpoint set provides no evidence that Graph supplies those measures.

Other source gaps are tracked in [source-gap research, issue #7](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/7) and [schema mapping, issue #10](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/10).
