from .base import HttpClient, JobPosting, JobSource, SourceContext, SourceError, apply_method_for_url
from .glints import GlintsSource
from .jobstreet import JobStreetSource
from .kalibrr import KalibrrSource
from .linkedin import LinkedInSource
from .remote import GreenhouseSource, LeverSource, RemoteOKSource, RemotiveSource

SOURCE_CLASSES: dict[str, type[JobSource]] = {
    "linkedin": LinkedInSource,
    "jobstreet": JobStreetSource,
    "glints": GlintsSource,
    "kalibrr": KalibrrSource,
    "remoteok": RemoteOKSource,
    "remotive": RemotiveSource,
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
}

__all__ = [
    "SOURCE_CLASSES",
    "HttpClient",
    "JobPosting",
    "JobSource",
    "SourceContext",
    "SourceError",
    "apply_method_for_url",
]
