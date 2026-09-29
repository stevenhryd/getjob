from .base import ApplyContext, Applier, ApplyResult, Outcome
from .platforms import (
    GlintsApplier,
    GreenhouseApplier,
    JobStreetApplier,
    KalibrrApplier,
    LeverApplier,
    LinkedInApplier,
)

APPLIERS: dict[str, type[Applier]] = {
    "linkedin": LinkedInApplier,
    "jobstreet": JobStreetApplier,
    "glints": GlintsApplier,
    "kalibrr": KalibrrApplier,
    "greenhouse": GreenhouseApplier,
    "lever": LeverApplier,
}

# Platform yang lamarannya bisa diotomatisasi (sisanya berstatus "Perlu manual").
AUTO_PLATFORMS = tuple(APPLIERS)

__all__ = ["APPLIERS", "AUTO_PLATFORMS", "ApplyContext", "Applier", "ApplyResult", "Outcome"]
