"""Provider capability contracts and built-in registry metadata."""

from .models import Capability, DataRequest, FetchRequest, ProviderAdapter, ProviderDescriptor, RawBatch
from .registry import ProviderRegistry, builtin_registry
from .loader import AdapterLoadError, load_adapter, registry_for_routes
from .errors import (
    AuthenticationError,
    InvalidProviderRequestError,
    ProviderContractChangedError,
    ProviderError,
    RateLimitError,
    TemporaryProviderError,
)

__all__ = [
    "Capability",
    "AdapterLoadError",
    "AuthenticationError",
    "DataRequest",
    "FetchRequest",
    "ProviderAdapter",
    "ProviderContractChangedError",
    "ProviderDescriptor",
    "ProviderError",
    "RawBatch",
    "RateLimitError",
    "TemporaryProviderError",
    "InvalidProviderRequestError",
    "ProviderRegistry",
    "builtin_registry",
    "load_adapter",
    "registry_for_routes",
]
