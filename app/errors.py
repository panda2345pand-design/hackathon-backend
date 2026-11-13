"""アプリのエラー。どの層からでも投げられ、main.py のハンドラーが {"detail": {"code", "message"}} の JSON にする。"""


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class Unauthorized(AppError):
    status, code = 401, "unauthorized"


class Forbidden(AppError):
    status, code = 403, "forbidden"


class NotFound(AppError):
    status, code = 404, "not_found"


class Conflict(AppError):
    status, code = 409, "conflict"


class Unprocessable(AppError):
    status, code = 422, "unprocessable"
