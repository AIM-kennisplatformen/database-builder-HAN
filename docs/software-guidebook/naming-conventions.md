# Naming conventions

These conventions make names predictable and let readers infer a type's role or a value's meaning without tracing every use. Apply them to new and refactored code. Existing names will be refactored after these conventions have been documented. When a name is part of a serialized contract or public import path, account for compatibility as part of that refactor.

Examples in this document illustrate the proposed conventions and are based on the suggestions supplied for this discussion; they are not a review of current source names.

## Classes

### C1 — Order class names from domain to responsibility to implementation

Use:

```text
Domain + Subject + Responsibility + Implementation
```

The **domain** is the broader business area or feature, such as `Employee`, `Document`, or `Metadata`. The **subject** is the specific entity or concept within that domain, such as `Drive`, `Group`, or `Snapshot`. Domain and subject are distinct parts of the name. Follow them with the class responsibility and, when needed, the implementation technology. Omit a part when it does not apply; do not repeat the domain as the subject.

Examples:

| Earlier name suggestion | Proposed name |
| --- | --- |
| `PostgresIbWriter` | `IbWriterPostgres` |
| `PostgresRhaWriter` | `RhaWriterPostgres` |
| `PostgresDocumentStore` | `DocumentStorePostgres` |
| `EmployeePostgresWriter` | `EmployeeWriterPostgres` |
| `MsGraphEmployeeSource` | `EmployeeSourceMsGraph` |
| `SharePointDocumentMetadataSource` | `DocumentMetadataSourceSharePoint` |

Where a domain and subject are both present, preserve both in that order. `Metadata` is the domain and `Drive` is the subject, so the order is `MetadataDrive`, not `DriveMetadata`. Related classes should group conceptually by domain and subject rather than relying on individual exceptions.

### C2 — Use a consistent specificity hierarchy

Within the domain and subject, use a predictable order for additional qualifications:

```text
Broad domain → specific subject → operation or state → role
```

For the `Metadata` domain and `Drive` subject, prefer `MetadataDriveIngestRequest` and `MetadataDriveIngestProgress`. The domain must precede the subject, even if an earlier draft example placed them in the opposite order. Also prefer `DocumentFileIngestSelection` over `DocumentIngestFileSelection`, and `EmployeeSyncProgress` / `EmployeeSyncResult` over plural `EmployeesSyncProgress` / `EmployeesSyncResult`.

Place the same kind of qualifier in the same position across related types. Do not put a subject before the operation in one class and after it in another.

### C3 — Name classes for what they represent

A class name describes a noun, role, state, or result. A function name describes an action. Prefer `EmployeeWriteOutcome` over `WriteEmployeeOutcome` when the class represents the outcome of an employee write.

Names such as `GroupDrives` may be appropriate if the class represents a collection; `GroupDriveCollection` makes that meaning explicit. `CombinedOperationStoreMixin` is appropriate only if the type is actually used as a mixin. Choose the name from the architectural responsibility rather than adding a suffix for consistency alone.

### C4 — Distinguish domain models, API models, ports, adapters, and exceptions

Use consistent role endings and keep the technology at the end of adapter names:

| Type | Convention | Example |
| --- | --- | --- |
| Domain entity | `Subject` | `Employee` |
| Request | `SubjectActionRequest` | `EmployeeSyncRequest` |
| Result | `SubjectActionResult` | `EmployeeSyncResult` |
| Progress | `SubjectActionProgress` | `EmployeeSyncProgress` |
| API request | `SubjectActionRequestBody` | `EmployeeSyncRequestBody` |
| API response | `SubjectActionResponse` | `EmployeeSyncResponse` |
| Port | `DomainSubjectRole` | `EmployeeWriter` |
| Adapter | `DomainSubjectRoleTechnology` | `EmployeeWriterPostgres` |
| Exception | `Domain[Subject]RoleFailureReason` | `EmployeeSourceUnavailable`, `UbwfrisStoreUnavailable` |

Use the domain qualifier when it disambiguates the subject or role. An exception that crosses module boundaries should identify its domain, as described in C5.

### C5 — Prefer domain-qualified exception names

An exception named `SourceUnavailable` or `StoreUnavailable` can be ambiguous when imported or logged outside its defining module. Qualify it with the affected domain, for example `EmployeeSourceUnavailable`, `MetadataSourceUnavailable`, or `UbwfrisStoreUnavailable`.

### C6 — Use singular nouns for domain subjects

Use a singular subject such as `EmployeeSyncRequest`, even when an operation may process many employees. Keep a plural name only when the class explicitly represents a collection of distinct entities.

## Functions

### F1 — Start ordinary function names with an active verb

Use:

```text
Verb + Object + Qualifier
```

Name what the function does, rather than only what it returns. For example, prefer `build_selector_rules` to `selector_rules`, `select_drive_ids` to `selected_drive_ids`, and `build_failure_details` to `failure_details`. Use `_extract_employee_values`, `_count_members`, `_build_document` (when constructing one), and `_resolve_folder_id` (when looking up or deriving an ID) for corresponding helpers.

This rule applies to ordinary functions and methods. Preserve required protocol/interface method names across their ports and implementations. Python special methods, properties, and boolean predicate forms follow their language or predicate conventions rather than being forced into an ordinary verb-object name.

### F2 — Give verbs distinct, predictable meanings

Use verbs to communicate the behavior:

| Verb | Meaning |
| --- | --- |
| `get_` | Obtain a specific object; may perform I/O |
| `list_` | Return a materialized collection |
| `stream_` | Yield items incrementally |
| `fetch_` | Retrieve data from an external service |
| `find_` | Search for a matching item that may be absent |
| `select_` | Choose items according to criteria |
| `filter_` | Remove items that do not match criteria |
| `build_` | Construct an object without persisting it |
| `parse_` | Convert external or serialized input into a structured form |
| `normalize_` | Convert a representation into canonical form |
| `validate_` | Check a condition and raise on invalid input |
| `is_`, `has_`, `can_` | Return a boolean |
| `begin_` | Start an operation |
| `stage_` | Write provisional data |
| `publish_` | Make staged data visible |
| `record_` | Persist an observation or outcome |
| `delete_` | Remove data |
| `refresh_` | Update data from an authoritative source |
| `sync_` | Reconcile a destination with a source |

For example, use `list_user_groups` when a function collects streamed groups into a tuple. Do not use `get_` as a universal verb for calculations, transformations, or side effects.

### F3 — Put verbs first and order objects from general to specific

Use:

```text
Verb + Domain + Subject + Qualifier
```

Prefer `list_user_group_drives` to `get_user_group_drives`, `select_drive_ids` to `selected_drive_ids`, `_refresh_drive_metadata_independently` to `_refresh_one_drive_independently`, and `_record_drive_refresh_failure` to `_record_failed_drive`. Use `_record_drive_refresh_skip`, `_publish_drive_metadata_refresh`, and `_flush_workbook_row_batch` to make the object and action clear.

Keep related names in the same order. Avoid patterns such as `refresh_group_metadata` alongside `metadata_group_refresh` for the same kind of operation.

### F4 — Distinguish calculations, transformations, and side effects

A function name must not hide important behavior. Use names such as `build_document` for construction, `save_document` for persistence, `fetch_document` for external retrieval, `select_document` for criterion-based choice, `refresh_document_metadata` for updates, and `publish_document_metadata` for making staged metadata visible.

### F5 — Put behavioral qualifiers at the end

Keep the core action and object stable, then add a qualifier for behavior, scope, or execution strategy. Names such as `refresh_drive_metadata_atomically`, `refresh_group_metadata_atomically`, and `refresh_group_metadata_per_drive` follow this pattern. Rename `_run_atomic_group_metadata_refresh` to `_refresh_group_metadata_atomically` only when it performs that same refresh operation.

Do not repeat context that is already clear from the containing module unless it distinguishes behavior.

### F6 — Make boolean functions read as questions

Boolean functions should clearly read as predicates. Use `is_`, `has_`, `can_`, or an active predicate verb such as `matches_`. For example, `_matches_path` and `_matches_extension` are clear predicates; `_requires_candidate_path`, `_is_candidate_drive_included`, and `_do_metadata_rules_overlap` are also explicit. Keep one predictable form for related predicates rather than mechanically adding `is_` to every one.

### F7 — Preserve protocol and interface method names across implementations

Ports and their adapters share the same method names. Methods such as `begin_sync`, `stage_employee`, `publish_sync`, and `discard_sync` must be changed in the abstraction and every implementation together. A naming change to an interface method is one coordinated change across that contract.

## Variables

### V1 — Name variables for the value they hold

Functions name actions; variables name data. Prefer a subject, property, and useful qualifier, such as `sync_timestamp`, `current_employee_record`, `timestamp_row`, `self_reported_details`, `rules_by_drive_id`, `locations_by_drive_id`, and `groups_by_id`. A reader should be able to understand the value without inspecting its assignment.

### V2 — Use a consistent specificity order

For most variables, order information as:

```text
Subject → property → state or time → unit
```

Examples include `employee_sync_started_at`, `employee_sync_completed_at`, `document_processing_last_attempt_at`, `document_file_size_bytes`, and `workbook_row_count`.

### V3 — Encode meaningful collection lookup keys

Use `_by_` when a mapping's key is important to understanding or using the value. For example, use `groups_by_id`, `locations_by_drive_id`, and `rules_by_drive_id` for dictionaries with those keys. Keep semantic collection names such as `normalized_rules` and `invalid_row_numbers`. Do not add `_list`, `_tuple`, or `_dict` mechanically.

### V4 — Name boolean variables for their state or meaning

Use prefixes with distinct meanings:

| Prefix | Meaning | Example |
| --- | --- | --- |
| `is_` | Current state | `is_published` |
| `has_` | Presence | `has_metadata` |
| `can_` | Capability | `can_scan_drive` |
| `should_` | Decision | `should_publish_snapshot` |

Apply this convention to model fields when appropriate; for example, `deferred: bool` could be `is_deferred`. Do not rename externally serialized fields without accounting for compatibility.

### V5 — Use `_count` for integer counters when ambiguity is possible

Prefer `total_row_count`, `processed_row_count`, `created_row_count`, and `invalid_row_count` for integer counters. Reserve names ending in `_rows` for collections of rows. For example, `invalid_rows` is a collection, while `invalid_row_count` is an integer.

### V6 — Include units in measured values

Include units whenever a value measures time, size, or another unit-bearing quantity. Use names such as `timeout_seconds`, `retry_delay_ms`, `file_size_bytes`, `elapsed_seconds`, and `parttime_percentage`. Avoid ambiguous names such as `timeout`, `duration`, or `size` where the unit matters.

### V7 — Keep abbreviations consistent and domain-approved

Choose one canonical spelling for each established abbreviation and use it across classes, functions, variables, and modules. Established examples include `Ubwfris`, `Ib`, `Rha`, `MsGraph`, `id`, and `url`. Do not alternate among `ID`, `Id`, `identifier`, and `uid` for the same concept unless the names represent different meanings.

### V8 — Prefix the distinguishing state when comparing versions

When comparing versions or states of the same property, put the distinguishing state first so related values group together. For example, use `active_content_version` and `last_attempt_content_version`. This rule takes precedence over V2 when the names distinguish versions or states of one property.

## Modules and packages

### M1 — Extract cohesive shared prefixes into a directory

When two or more sibling files share a meaningful prefix and belong to one cohesive feature, extract the shared concept into a directory and name the files for their responsibilities:

```text
shared_concept/file_responsibility.py
```

This is mandatory when those conditions hold. The shared directory supplies context, so the child filenames need not repeat it. For example:

```text
msgraph/document_metadata/source.py
msgraph/document_metadata/scan.py
msgraph/document_metadata/models.py
```

The prefix alone does not establish cohesion. Group files that belong to the same feature; do not combine separate lifecycles solely to satisfy a naming pattern.

### M2 — Let directories represent concepts and files represent responsibilities

Organize names from broad architecture toward specific work:

```text
Layer → Domain or integration → Feature → Responsibility
```

Examples include `infrastructure/msgraph/document_metadata/source.py`, `infrastructure/msgraph/document_metadata/scan.py`, and `domain/use_cases/metadata_refresh/drive/refresh.py` or `staging.py`.

### M3 — Do not repeat the parent directory name in child files

When a directory already supplies the concept, name the child for its responsibility:

| Avoid | Prefer |
| --- | --- |
| `employees/employee_source.py` | `employees/source.py` |
| `employees/employee_store.py` | `employees/store.py` |
| `documents/document_store.py` | `documents/store.py` |
| `document_metadata/document_metadata_scan.py` | `document_metadata/scan.py` |
| `metadata_refresh/metadata_refresh.py` | `metadata_refresh/refresh.py` |

Keep a repeated prefix only when removing it would make the filename ambiguous or when a public import path needs to be preserved for compatibility.

### M4 — Prefer feature directories when functionality changes together

Group files by the feature that owns their behavior when they are tightly coupled. For example, feature-specific exceptions may live under `domain/employee_sync/errors.py`, `domain/ubwfris_ingest/errors.py`, or `domain/metadata_ingest/errors.py` rather than all being placed in `domain/exceptions/`. Shared exceptions may remain in `domain/exceptions/`; this rule does not require moving every exception.

### M5 — Use singular, lowercase `snake_case` for concept names

Prefer directory and file names such as `employee_sync/`, `document_ingestion/`, `metadata_refresh/`, and `document_metadata/`. Plural directories are appropriate when they represent a collection of distinct entities, such as `documents/`, `employees/`, or `routes/`. Choose one convention per architectural role.

### M6 — Use generic filenames only when the directory makes their meaning clear

Names such as `models.py`, `source.py`, `store.py`, and `refresh.py` are clear inside a directory that supplies the feature context, such as `document_metadata/models.py` or `metadata_refresh/group/_selectors.py`. Avoid unqualified generic paths such as `domain/models/models.py`, `infrastructure/helpers.py`, `domain/utils.py`, and `services/common.py`.

### M7 — Create directories only when they establish useful structure

A directory should group multiple files, separate substantial responsibilities, or establish a meaningful package boundary. Avoid a directory containing only `service.py` or `implementation.py` when it adds no architectural meaning. The two-file threshold in M1 is the minimum for extracting a shared prefix when the files are cohesive; a directory may also be justified by the other purposes in this rule.
