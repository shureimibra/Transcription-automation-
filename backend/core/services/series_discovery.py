"""
Backward-compatible entry point for multi-page/series media discovery.

The actual strategy implementations moved to core.services.discovery (a
pluggable list of DiscoveryStrategy subclasses -- see discovery/engine.py).
This module is kept so existing imports (`from core.services import
series_discovery; series_discovery.discover(url)`) continue to work
unchanged; nothing about the public API changed, only the internals grew
from two hardcoded techniques into an extensible strategy list.
"""
try:
    from backend.core.services.discovery import discover
except ModuleNotFoundError:
    from core.services.discovery import discover

__all__ = ["discover"]
