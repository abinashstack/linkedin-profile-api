"""Exception hierarchy for everything that can go wrong talking to LinkedIn."""


class LinkedInAPIError(Exception):
    """Base class for errors raised while talking to LinkedIn."""


class InvalidProfileURLError(LinkedInAPIError):
    """The supplied URL is not a linkedin.com/in/<id> profile URL."""


class AuthenticationError(LinkedInAPIError):
    """No usable LinkedIn session could be obtained (bad credentials, nothing configured)."""


class ChallengeRequiredError(LinkedInAPIError):
    """LinkedIn returned a CAPTCHA / security-checkpoint instead of a session or a profile."""


class SessionExpiredError(LinkedInAPIError):
    """A previously working session cookie was rejected (401/403)."""


class ProfileNotFoundError(LinkedInAPIError):
    """LinkedIn returned 404, or the profile is private/unreachable with this session."""


class RateLimitedError(LinkedInAPIError):
    """LinkedIn returned 429."""


class UpstreamError(LinkedInAPIError):
    """Any other unexpected response from LinkedIn."""
