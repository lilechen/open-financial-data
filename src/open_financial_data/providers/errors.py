"""Stable Provider error taxonomy independent of third-party SDK exceptions."""


class ProviderError(RuntimeError):
    pass


class AuthenticationError(ProviderError):
    pass


class RateLimitError(ProviderError):
    pass


class TemporaryProviderError(ProviderError):
    pass


class InvalidProviderRequestError(ProviderError):
    pass


class ProviderContractChangedError(ProviderError):
    pass
