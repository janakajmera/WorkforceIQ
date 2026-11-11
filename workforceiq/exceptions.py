"""Exception hierarchy. UI code catches WorkforceIQError and shows ``str(exc)`` to the user."""


class WorkforceIQError(Exception):
    """Base class for all application errors (messages are safe to show to end users)."""


class ConfigurationError(WorkforceIQError):
    """Missing or invalid configuration (e.g. credentials not provided)."""


class DatabaseError(WorkforceIQError):
    """Unexpected database failure."""


class DatabaseConnectionError(DatabaseError):
    """The MySQL server could not be reached or authentication failed."""


class ValidationError(WorkforceIQError):
    """Input violates a business rule or database constraint."""


class DuplicateError(ValidationError):
    """A unique constraint was violated."""


class NotFoundError(WorkforceIQError):
    """The requested record does not exist."""


class EtlError(WorkforceIQError):
    """An ETL stored procedure failed."""
