"""Codici di errore JSON-RPC usati da A2A (spec 1.0, sez. 9.5)."""


class A2AError(Exception):
    code = -32603
    default_message = "Internal error"

    def __init__(self, message: str | None = None, data=None):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.data = data

    def to_jsonrpc(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


# errori JSON-RPC standard
class ParseError(A2AError):
    code, default_message = -32700, "Parse error"


class InvalidRequest(A2AError):
    code, default_message = -32600, "Invalid request"


class MethodNotFound(A2AError):
    code, default_message = -32601, "Method not found"


class InvalidParams(A2AError):
    code, default_message = -32602, "Invalid params"


class InternalError(A2AError):
    code, default_message = -32603, "Internal error"


# errori specifici A2A
class TaskNotFound(A2AError):
    code, default_message = -32001, "Task not found"


class TaskNotCancelable(A2AError):
    code, default_message = -32002, "Task not cancelable"


class UnsupportedOperation(A2AError):
    code, default_message = -32004, "Unsupported operation"


class VersionNotSupported(A2AError):
    code, default_message = -32009, "Version not supported"


_BY_CODE = {c.code: c for c in [ParseError, InvalidRequest, MethodNotFound, InvalidParams, InternalError,
                                TaskNotFound, TaskNotCancelable, UnsupportedOperation, VersionNotSupported]}


def from_jsonrpc(err: dict) -> A2AError:
    cls = _BY_CODE.get(err.get("code"), A2AError)
    e = cls(err.get("message"), err.get("data"))
    e.code = err.get("code", e.code)
    return e
