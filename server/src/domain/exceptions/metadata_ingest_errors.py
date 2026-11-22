from domain.exceptions.error import DomainError


class MetadataSourceUnavailable(DomainError): ...


class MetadataStoreUnavailable(DomainError): ...


class MetadataRunAlreadyActive(DomainError): ...


class InvalidMetadataSelection(ValueError, DomainError): ...


class MetadataSourceError(DomainError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code: int = status_code
