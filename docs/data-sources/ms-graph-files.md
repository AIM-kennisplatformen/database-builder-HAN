# Retrieve Teams and SharePoint files with Microsoft Graph

This describes the Microsoft Graph `v1.0` request sequence for finding a user's Teams document libraries and downloading a file.
The sequence takes a trusted Graph user ID and currently uses delegated access.
Every Graph request uses `https://graph.microsoft.com/v1.0` as its base URL and sends `Authorization: Bearer {access-token}`.
The token needs permission to read the relevant groups and files, and delegated access remains limited by the signed-in user's SharePoint permissions.
Consult the permission tables for [direct memberships](https://learn.microsoft.com/en-us/graph/api/user-list-memberof?view=graph-rest-1.0), [groups](https://learn.microsoft.com/en-us/graph/api/group-get?view=graph-rest-1.0), and [drives](https://learn.microsoft.com/en-us/graph/api/drive-list?view=graph-rest-1.0) when configuring the application.

## Application authentication boundary

The application's authentication endpoint uses Graph `GET /me` under the local delegated token to establish the signed-in user's Graph ID.
It may then issue the project JWT described in the [integration design](sharepoint-document-integration.md#local-identity-and-token-handling).
Authentication middleware protects the application's file-retrieval endpoint and supplies the verified user ID to the retrieval use case.
The endpoint must not accept an arbitrary caller-supplied user ID in place of that verified identity.
In delegated mode, the Graph token used for retrieval must belong to the same user; naming another user in a Graph URL does not impersonate them.
`GET /me` is not part of the file-retrieval request sequence below.

## Request sequence

1. **Find the user's groups:** `GET /users/{user-id}/memberOf` returns direct memberships for the middleware's verified user ID.
   Keep entries whose `@odata.type` is `#microsoft.graph.group` and record each `id` as a candidate group ID.
   This step can be skipped when the Team's group ID is already known.
   [Direct membership API](https://learn.microsoft.com/en-us/graph/api/user-list-memberof?view=graph-rest-1.0)
2. **Identify Teams:** For each candidate, call `GET /groups/{group-id}?$select=id,displayName,resourceProvisioningOptions`.
   Keep groups whose `resourceProvisioningOptions` includes `Team`.
   Choose the target group ID from these results.
   [Get group API](https://learn.microsoft.com/en-us/graph/api/group-get?view=graph-rest-1.0)
3. **Find document libraries:** Call `GET /groups/{group-id}/drives`.
   Each returned drive represents a document library; retain its `id` as the drive ID.
   [List drives API](https://learn.microsoft.com/en-us/graph/api/drive-list?view=graph-rest-1.0)
4. **List files and folders:** For each drive, call `GET /drives/{drive-id}/root/children`.
   For any returned item with a `folder` facet that you want to traverse, call `GET /drives/{drive-id}/items/{item-id}/children` and repeat for its subfolders.
   Keep the drive ID and item `id` for each file of interest; the `file` facet distinguishes files from folders.
   [List children API](https://learn.microsoft.com/en-us/graph/api/driveitem-list-children?view=graph-rest-1.0)
5. **Download a file:** Call `GET /drives/{drive-id}/items/{item-id}/content` for a file item.
   Graph responds with `302 Found`; follow the `Location` header to the temporary, preauthenticated download URL.
   Do not send the Graph bearer token to that URL.
   [Download content API](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0)

For every collection in steps 1, 3, and 4, follow `@odata.nextLink` until the response has no next link; otherwise some groups, libraries, or items may be missed.
[Microsoft Graph paging guidance](https://learn.microsoft.com/en-us/graph/paging)

## Token permissions and future application access

Graph documents different permission requirements for reading the signed-in user's memberships and another user's memberships.
For `/users/{user-id}/memberOf`, confirm that the selected delegated scopes cover the request; the [membership API](https://learn.microsoft.com/en-us/graph/api/user-list-memberof?view=graph-rest-1.0) lists `User.Read.All` for another user and `Directory.Read.All` for application access.
The user-ID endpoint allows an eventual application token to perform group discovery without changing the request path.
An application token has its own access rather than the named user's access, so it cannot by itself prove that the user may see a listed drive or file.
The application's access-control policy must still enforce user-specific file access before returning results.
[Microsoft Graph permission types](https://learn.microsoft.com/en-us/graph/permissions-overview)

## Coverage

This sequence starts from the Team group's drives, which contain standard channel files.
Private and shared channels have separate SharePoint sites, so their files require finding and listing those sites' drives separately.
[Teams and SharePoint integration](https://learn.microsoft.com/en-us/sharepoint/teams-connected-sites)
`/users/{user-id}/memberOf` returns direct memberships and does not discover a host Team accessible only through a shared channel.
Membership-based discovery also does not enumerate every file shared directly with the user.
