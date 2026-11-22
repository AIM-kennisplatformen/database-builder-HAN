# Processing and storage

## Per-file pipeline

The processing pipeline is format-independent at its boundary. A processor registry routes by detected content type, using the Graph extension as a hint, and records the processor and version used. The first release targets common office documents, PDFs, plain text, Markdown, HTML, CSV, and image files; the registry can grow to cover additional formats. Unsupported or unreadable files receive a per-file outcome and reason rather than aborting the drive.

Processors produce a common structured representation containing text, tables, document structure, and the best available source locations. Locations may identify a page, slide, sheet, heading, or text offset. When a processor can provide only document-level location, the result records that precision limitation. Scanned pages and image-only content are in scope at lower priority through OCR or vision-capable processing.

Each file uses three separately configured model stages:

1. **Metadata extraction** proposes content authors, themes, keywords, and later approved fields. Values may be empty or null because a document may not contain them. `createdBy` and `lastModifiedBy` remain Graph activity fields and never imply content authorship.
2. **Chunk extraction** receives the structured representation and returns coherent chunks with source locations. It preserves tables with their headings/context where possible and respects the selected embedding model's limits. Large inputs may be split across multiple requests; results are combined with provenance intact.
3. **Embedding** converts each accepted chunk to a finite vector. Batches and multiple requests may be used within configured service and cost limits.

The three stages may call an approved local service or an environment-configured OpenAI-compatible service such as the user-approved Nebius route through OpenRouter. Exact providers, model IDs, prompts, and processors remain implementation choices. Requests must not accept arbitrary service endpoints.

Each nonempty metadata suggestion retains evidence location, extraction stage/model/prompt version, and review status in PostgreSQL. A suggestion whose cited evidence cannot be found in the extracted representation is dropped and reported. A response that cannot be interpreted is a real stage failure. Empty extraction is a valid result. Candidate suggestions are not authoritative document metadata until a separate review/promotion workflow approves them.

Downloaded files and intermediate extracted content exist only for the active processing attempt. Published passage text and vectors live in Qdrant; source metadata, versioned suggestions, and processing history live in PostgreSQL.

## File publication and outcomes

Each file is isolated from other files in the run. A failure in one file does not discard completed work for other files. Within a file, publish the new result only after all required stages have succeeded and source content is confirmed unchanged.

For a successful file with chunks:

1. Create a new Qdrant generation with chunk points.
2. In one PostgreSQL publication step, make the generation and its versioned metadata suggestions active and record processor, policy, prompt, and model versions.
3. Remove prior Qdrant generations after the Postgres switch.

If the operation fails before publication, keep the previous successful generation and metadata active; mark the attempt failed and clean up its inactive Qdrant points. Crash recovery discards unpublished work, leaves completed files and run history intact, and makes failed work selectable again.

If processing finds no usable chunks, record `no_usable_chunks` and return that outcome to the caller. Save valid metadata suggestions for the processed content version, create no new Qdrant generation, and leave any old generation marked outdated. Empty metadata suggestions do not make an otherwise valid file attempt fail.

## Qdrant and PostgreSQL responsibilities

PostgreSQL remains authoritative for the Graph document identity `(drive_id, item_id)`, catalog metadata, source content version, observations, run/file outcomes, versioned content metadata suggestions, and active generation reference. Each suggestion set is tied to the content version and extraction policies that produced it. Results for an older passage use suggestions from that passage's version; the latest suggestions may also be displayed as current document metadata.

Each Qdrant point stores passage text, its embedding, location, Graph drive/item identity, PostgreSQL metadata ID, vector generation, and chunk identity. Extracted author/theme/keyword values remain in Postgres unless a future search requirement explicitly needs a Qdrant filter over reviewed fields. Qdrant is not exposed directly to Studio or API callers.

A changed `cTag` makes content-derived results outdated. If `cTag` is unavailable, the source adapter establishes whether content changed; a metadata-only `eTag` change does not by itself force content processing. If content changes between refresh/download and publication, discard the attempt and mark it outdated. A processor, chunk prompt/model, metadata prompt/model, or other relevant processing-policy change also marks affected results outdated. Metadata refresh alone does not rebuild vectors.

The previous successful generation remains searchable while content or non-embedding policy is outdated. A changed embedding model is different: vectors from different models do not share a similarity space. Keep using the old query embedding model and its generations until an operator performs an explicit corpus cutover. After cutover, use only the new query model and compatible vectors. Incomplete coverage is allowed and clearly reported; files without a compatible generation cannot provide passage results. Old points may be retained for rollback or cleanup but are not searched after cutover.

## Access and deletion

Document search follows ADR-007: get fresh permission-trimmed Microsoft Search candidates for the requesting user's delegated Graph token, join exact `(drive_id, item_id)` identities, then restrict Postgres/Qdrant search to those candidates. Never use ingestion status or stored permissions as a substitute for the live access check. The exact Graph Search identity join remains an implementation gate in the target tenant.

A delta deleted marker or missing item under a delegated principal may mean that principal lost visibility. Record that observation without deleting the shared document, suggestions, or Qdrant points. A separate operator purge action removes shared content only after deletion is verified. Qdrant cleanup must account for all generations, and Postgres history must retain the purge audit outcome.
