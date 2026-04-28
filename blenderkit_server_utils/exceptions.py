"""Custom exceptions for BlenderKit server utilities."""


class ProcessingError(Exception):
    """Exception raised for errors during processing of assets."""


class BlenderKitError(Exception):
    """Base class for all BlenderKit exceptions."""


class AssetNotFoundError(BlenderKitError):
    """Exception raised when an asset is not found."""


class FatalWorkerError(Exception):
    """Exception raised to abort a multi-asset run on a fatal worker error.

    The original cause is preserved via ``__cause__`` (``raise ... from exc``).
    """


class AssetUploadError(BlenderKitError):
    """Exception raised when an asset fails to upload."""


class SearchResultLimitError(BlenderKitError):
    """Exception raised when search results exceed the maximum limit."""


class SearchRequestRepeatError(BlenderKitError):
    """Exception raised when repeated search request limit is reached."""
