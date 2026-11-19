from domain.exceptions.error import DomainError


class SourceUnavailable(DomainError): ...


class StoreUnavailable(DomainError): ...


class MetadataRunAlreadyActive(DomainError): ...


class MetadataSourceError(DomainError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code: int = status_code
