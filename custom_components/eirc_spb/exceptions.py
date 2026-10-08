def _message(data, default: str = "API request failed") -> str:
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return default


def _code(data) -> str | None:
    if isinstance(data, dict) and data.get("code") is not None:
        return str(data["code"])
    return None


class EircSpbApiError(Exception):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class EircSpbAuthError(EircSpbApiError):
    pass


class EircSpbConfirmationError(EircSpbApiError):
    pass
