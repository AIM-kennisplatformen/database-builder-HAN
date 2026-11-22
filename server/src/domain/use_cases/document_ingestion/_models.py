"""Private failure types for document candidate selection."""

from domain.exceptions.error import OperationFailure


class DocumentCandidateSelectionFailure(OperationFailure):
    def __init__(self) -> None:
        super().__init__(
            "document_ingest.selection_failed",
            {
                "failure_stage": "document_ingest.selection",
                "reason": "Document candidate selection or persistence failed",
            },
        )
