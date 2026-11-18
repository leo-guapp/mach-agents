"""A2A Gateway inbound per gli altri team."""
from .server import Gateway, TeamConfig, UpstreamAgent, create_app

__all__ = ["Gateway", "TeamConfig", "UpstreamAgent", "create_app"]
