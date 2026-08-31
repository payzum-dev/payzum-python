"""The seam between the SDK and the network.

Exists so the client can be tested without sockets and so a host application
can supply its own HTTP stack (requests, httpx, aiohttp behind a sync shim)
without this package taking a dependency on any of them. A payment SDK with
zero runtime dependencies is a smaller supply-chain surface, which matters
more here than the convenience of a fluent HTTP library.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Mapping, Optional, Protocol, Union

from .errors import TransportError


class Response:
    """A raw HTTP response. The body stays a string so signatures and decimals survive."""

    def __init__(self, status: int, body: str, headers: Optional[Mapping[str, str]] = None) -> None:
        self.status = status
        self.body = body
        #: Lower-cased names.
        self.headers: dict[str, str] = {k.lower(): v for k, v in (headers or {}).items()}

    def header(self, name: str) -> Optional[str]:
        return self.headers.get(name.lower())

    def retry_after_seconds(self) -> Optional[int]:
        """Seconds the server asked us to wait, when it said so."""
        value = self.header("retry-after")
        return int(value) if value is not None and value.isdigit() else None

    def is_idempotent_replay(self) -> bool:
        """Set only when the API replayed an earlier idempotent request."""
        return self.header("x-payzum-idempotent-replay") == "true"


class Transport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        query: Mapping[str, Union[str, int]],
        body: Optional[str],
        timeout_seconds: int,
    ) -> Response: ...


class UrllibTransport:
    """Default transport on the standard library. No sockets touched in tests."""

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        query: Mapping[str, Union[str, int]],
        body: Optional[str],
        timeout_seconds: int,
    ) -> Response:
        if query:
            url = url + "?" + urllib.parse.urlencode({k: str(v) for k, v in query.items()})

        request = urllib.request.Request(
            url,
            data=body.encode("utf-8") if body is not None else None,
            headers=dict(headers),
            method=method,
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as raw:
                return Response(raw.status, raw.read().decode("utf-8"), dict(raw.headers.items()))
        except urllib.error.HTTPError as exc:
            # An HTTP error IS a response — the client maps it to a typed ApiError.
            return Response(exc.code, exc.read().decode("utf-8"), dict(exc.headers.items()))
        except urllib.error.URLError as exc:
            raise TransportError(f"Request to {url} failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TransportError(f"Request to {url} timed out after {timeout_seconds}s") from exc
