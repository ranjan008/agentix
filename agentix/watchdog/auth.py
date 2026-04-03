"""
JWT authentication + simple in-memory rate limiter.
"""
from __future__ import annotations

import time
from collections import defaultdict

import jwt


class AuthError(Exception):
    pass


class RateLimitError(Exception):
    pass


# ---------------------------------------------------------------------------
# JWT validation
# ---------------------------------------------------------------------------

def validate_jwt(token: str, secret: str, algorithms: list[str] | None = None) -> dict:
    """Decode and validate a JWT bearer token. Returns the claims dict."""
    algorithms = algorithms or ["HS256"]
    try:
        claims = jwt.decode(token, secret, algorithms=algorithms)
    except jwt.ExpiredSignatureError:
        raise AuthError("Token expired")
    except jwt.InvalidTokenError as e:
        raise AuthError(f"Invalid token: {e}")
    return claims


def make_jwt(claims: dict, secret: str, ttl_sec: int = 3600, algorithm: str = "HS256") -> str:
    """Generate a signed JWT (for dev/test use)."""
    payload = {**claims, "exp": int(time.time()) + ttl_sec, "iat": int(time.time())}
    return jwt.encode(payload, secret, algorithm=algorithm)


def extract_bearer(authorization_header: str) -> str:
    """Extract raw token from 'Bearer <token>' header value."""
    if not authorization_header:
        raise AuthError("Missing Authorization header")
    parts = authorization_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError("Authorization header must be 'Bearer <token>'")
    return parts[1]


# ---------------------------------------------------------------------------
# Rate limiter (in-memory sliding window)
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Simple per-identity sliding-window rate limiter.
    Default: 60 requests / 60 seconds per identity.
    """

    def __init__(self, max_requests: int = 60, window_sec: int = 60) -> None:
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._windows: dict[str, list[float]] = defaultdict(list)

    def check(self, identity_id: str) -> None:
        """Raises RateLimitError if the identity has exceeded its quota."""
        now = time.time()
        window_start = now - self.window_sec
        timestamps = self._windows[identity_id]
        # Evict timestamps outside the window
        self._windows[identity_id] = [t for t in timestamps if t > window_start]
        if len(self._windows[identity_id]) >= self.max_requests:
            raise RateLimitError(
                f"Rate limit exceeded for {identity_id}: "
                f"{self.max_requests} requests per {self.window_sec}s"
            )
        self._windows[identity_id].append(now)
