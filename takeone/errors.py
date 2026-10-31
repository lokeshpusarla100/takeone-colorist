from typing import Any


class Problem(Exception):
    def __init__(self, code: str, message: str, action: str, status: int = 400, **details: Any):
        super().__init__(message)
        self.code, self.message, self.action, self.status = code, message, action, status
        self.details = details

    def public(self):
        return {"code": self.code, "message": self.message, "action": self.action, "details": self.details}


def safe_problem(cause: Exception, stage: str) -> Problem:
    if isinstance(cause, Problem):
        cause.details.setdefault("stage", stage)
        return cause
    # Never return model request bodies, authorization headers, or raw provider exceptions.
    return Problem("INTERNAL_ERROR", "The backend operation failed.",
                   "Retry the failed step. Source media and saved answers have been kept.", 500,
                   stage=stage, exception_type=type(cause).__name__)
