class ServiceError(Exception):
    code = "error"
    status = 400

    def __init__(self, message, *, fields=None, code=None, status=None):
        super().__init__(message)
        self.message = message
        self.fields = fields or {}
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status

    def as_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "status": self.status,
            "fields": self.fields,
        }


class ValidationFailed(ServiceError):
    code = "invalid"
    status = 400


class Forbidden(ServiceError):
    code = "forbidden"
    status = 403


class RateLimited(ServiceError):
    code = "rate_limited"
    status = 429


class NotFound(ServiceError):
    code = "not_found"
    status = 404


class Conflict(ServiceError):
    code = "conflict"
    status = 409


class VersionConflict(Conflict):
    code = "stale_version"


class ClosedConflict(Conflict):
    code = "closed"


class ArchivedConflict(Conflict):
    code = "archived"


class CapacityConflict(Conflict):
    code = "capacity"


class LockUnavailable(ServiceError):
    code = "locked"
    status = 503
