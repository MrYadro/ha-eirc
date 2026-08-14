class EircSpbApiError(Exception):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class EircSpbAuthError(EircSpbApiError):
    pass


class EircSpbConfirmationError(EircSpbApiError):
    pass
