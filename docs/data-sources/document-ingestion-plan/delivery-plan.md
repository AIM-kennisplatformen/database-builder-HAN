# Delivery plan

Deliver the capability in stages so each boundary can be reviewed before the next one depends on it. The phase order is proposed; exact issue breakdown and numeric budgets are implementation decisions.

## Phase 1: Selection and run contracts

- Define the combined operation request, durable run/file records, reconnect/progress API, and outcome vocabulary.
- Implement shared include/exclude path and extension precedence for metadata and ingestion. Add ingestion-only status selectors and conflict validation.
- Specify canonical path and extension normalization, including case behavior, path boundaries, root, and extensionless files.
- Preserve metadata refresh staging and per-drive publication. Start document work only from the newly published drive snapshot.

**Acceptance scenarios**

- `/A` includes `.pdf` and `.md`, excludes `.xlsx`; `/A/B` includes `.docx`, excludes `.pdf`. Under `/A/B`, `.md` and `.docx` are selected while `.pdf` and `.xlsx` are excluded.
- Equal-specificity opposing directives with an overlapping extension or status fail validation before writes; disjoint directives pass.
- Metadata requests apply the same path/extension decisions and reject status fields.
- A failed drive refresh produces no document work for that drive; another successfully refreshed drive can proceed.
- A metadata-only request always refreshes and never triggers document processing.

## Phase 2: Source retrieval and format processors

- Add a source download port using the existing delegated Graph identity and the repository's InteractiveBrowserCredential-only rule; keep the Graph scopes list empty.
- Add processor selection by detected content type and a common structured representation with source locations.
- Cover the agreed first-release file families. Add lower-priority OCR/vision capability and per-file unsupported/unreadable outcomes.
- Verify source content version before publication and discard work if the content changes mid-run.

**Acceptance scenarios**

- A supported document produces text/structure and a best-available location; a file with incomplete location precision reports it.
- An unsupported or unreadable file receives a file-level outcome while other files continue.
- A source content change between metadata refresh and publication prevents activation of that generation.
- Image/scanned work is selectable and can be deferred by priority or configured budgets.

## Phase 3: Metadata and chunk model stages

- Configure metadata extraction and chunk extraction independently from environment settings.
- Validate typed nullable outputs, evidence locations, chunk ordering, and source-location preservation.
- Record model/prompt versions and usage measurements. Keep extracted suggestions separate from Graph activity fields and subject them to review.
- Handle large documents through bounded requests and deterministic combination of results.

**Acceptance scenarios**

- A document with no authors/themes/keywords can complete with empty or null suggestions.
- A valid suggestion stores its evidence and version; an unsupported citation is dropped and reported.
- An uninterpretable response fails the file, while a valid empty result succeeds.
- Tables retain associated heading/context when available, and all chunks have source locations.

## Phase 4: Embedding, Qdrant generations, and atomic publication

- Add the embedding port and validate finite vectors and consistent dimensions per generation.
- Write complete generations before switching PostgreSQL's active generation; retain old active data on failures.
- Save suggestions and processing versions with the file content version. Record `no_usable_chunks` as an outcome without creating points.
- Add generation cleanup and crash recovery for inactive/unpublished work.

**Acceptance scenarios**

- A failed processor/model/embedding/Qdrant/Postgres publication step leaves the previous active generation and suggestions intact.
- Successful publication exposes only the new complete generation and its matching suggestions.
- A file with metadata suggestions but no chunks saves the suggestions and reports `no_usable_chunks` to the caller.
- A crash during an unpublished rebuild preserves completed files and allows the incomplete file to be retried.

## Phase 5: Durable orchestration, controls, and model cutover

- Enforce one active rebuild per file and share its outcome across requesting runs.
- Implement bounded retries, file/run/time/concurrency/usage caps, priority, and explicit deferral outcomes. Select numeric values from measurements.
- On metadata-stream disconnect, cancel refresh. On document-run disconnect, finish active files and start no more; preserve reconnectable run status.
- Add an explicit embedding-model cutover action and coverage report. Keep post-cutover search restricted to the selected model.
- Implement verified-deletion operator purge and audit behavior.

**Acceptance scenarios**

- Two requests selecting the same file incur one active rebuild and both report its result.
- A caller disconnect allows active work to finish, starts no queued work, and can retrieve the final run state later.
- Transient errors retry within bounds; unstarted files become deferred; exhausted failures become failed.
- Embedding cutover reports complete and incomplete coverage and never mixes incompatible vector spaces in one search.
- A delegated missing observation does not purge shared data; verified purge removes all generations and records an audit outcome.

## Phase 6: Search validation and release

- Validate Graph Search hit identity against drive/item IDs in the HAN tenant. The current known 403 must be resolved before access-first passage search can be enabled.
- Confirm active-generation filtering, outdated status presentation, and unavailable files after embedding cutover.
- Review API docs, operational recovery, privacy/log masking, and storage retention with the released implementation.

**Acceptance scenarios**

- A search result is returned only after a fresh delegated Graph candidate join succeeds.
- Missing Graph authorization or unreliable identity mapping fails closed with an explicit error; it does not return a successful empty result.
- A user without a compatible active vector generation receives metadata/status but no passage.
