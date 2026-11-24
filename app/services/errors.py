"""One exception type for every expected, user-facing service failure."""


class ServiceError(Exception):
    def __init__(self, code, message, status=400, *, fields=None, retry_after=None,
                 session_id=None, device_id=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.fields = fields
        self.retry_after = retry_after
        self.session_id = session_id      # context for the security-event log
        self.device_id = device_id
