"""Failures that must stop a run rather than degrade it.

A missing filing degrades the analysis and is reported as an absence. A rule
that cites law the authority pack does not contain is a defect in our own
method, and running on regardless would put a fabricated citation in front of a
client. The first is a finding; the second is an error.
"""


class TFAError(Exception):
    """Base for every error this system raises deliberately."""


class CitationError(TFAError):
    """A rule cites a provision the pinned authority pack cannot resolve."""


class FactMapError(TFAError):
    """The fact dictionary is malformed, or names a source that cannot exist."""


class ClientContextError(TFAError):
    """The declared client context is missing or unusable."""
