class HybridMatchingError(RuntimeError):
    """Raised when hybrid matching cannot build the required local profiles."""


class MatchingApplicationError(RuntimeError):
    """Base class for application-level matching workflow errors."""


class InvalidMatchingInputError(MatchingApplicationError):
    """Raised when required résumé, job, or parameter input is missing or invalid."""


class JobNotFoundError(MatchingApplicationError):
    """Raised when a requested catalog job does not exist."""


class JobCatalogUnavailableError(MatchingApplicationError):
    """Raised when the job catalog cannot be loaded."""
