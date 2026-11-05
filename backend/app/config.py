"""Resource and deployment controls. Cryptographic profile is intentionally fixed."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    rate_limit_requests: int = 60
    rate_window_seconds: float = 60.0
    max_concurrent_jobs: int = 2
    max_body_bytes: int = 24 * 1024 * 1024
    max_upload_bytes: int = 10 * 1024 * 1024
    body_timeout_seconds: float = 30.0
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    allowed_origins: tuple[str, ...] = ("http://localhost:8000", "http://127.0.0.1:8000")

    def __post_init__(self):
        if min(self.rate_limit_requests, self.rate_window_seconds, self.max_concurrent_jobs,
               self.max_body_bytes, self.max_upload_bytes, self.body_timeout_seconds) <= 0:
            raise ValueError("Resource limits must be positive")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("Explicit allowed hosts are required")

    @classmethod
    def from_env(cls):
        settings = cls()
        return cls(
            allowed_hosts=tuple(x.strip() for x in os.getenv("STEG_ALLOWED_HOSTS", ",".join(settings.allowed_hosts)).split(",") if x.strip()),
            allowed_origins=tuple(x.strip() for x in os.getenv("STEG_ALLOWED_ORIGINS", ",".join(settings.allowed_origins)).split(",") if x.strip()),
        )
