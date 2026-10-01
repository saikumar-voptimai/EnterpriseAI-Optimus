class ServiceError(Exception):
    """An intentionally safe, user-visible service failure."""

    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class ProviderError(ServiceError):
    def __init__(self, detail: str, *, retryable: bool = False, request_id: str | None = None):
        super().__init__(detail, 502)
        self.retryable = retryable
        self.request_id = request_id
