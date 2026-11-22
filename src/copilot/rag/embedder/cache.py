"""Query embedding cache with bounded LRU memory, TTL, single-flight coalescing, and HMAC privacy (Step 2.2)."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import numpy as np

from copilot.core.errors import InvalidInputError
from copilot.rag.embedder.base import Embedder
from copilot.rag.embedder.inputs import detect_pii, prepare_query_text
from copilot.rag.fingerprint import EmbedderConfig
from copilot.rag.tokens import TokenCounter


def compute_query_cache_key(
    prepared_query: str,
    fingerprint: str,
    secret: str | bytes | None = None,
) -> str:
    """Compute deterministic cache key for query embedding with optional HMAC secret.

    Security & Privacy:
    - Default (secret=None): SHA-256(prepared_query + ":" + fingerprint).
    - Hardened (secret provided): HMAC-SHA256(secret, prepared_query + ":" + fingerprint).
      Prevents dictionary and rainbow table recovery of common queries if cache keys or
      stores are exposed to untrusted observers.
    """
    key_input = f"{prepared_query}:{fingerprint}".encode()
    if secret is not None:
        if isinstance(secret, str):
            secret_bytes = secret.encode("utf-8")
        elif isinstance(secret, bytes):
            secret_bytes = secret
        else:
            raise InvalidInputError("secret must be str or bytes", "embedder")
        if not secret_bytes:
            raise InvalidInputError("secret must not be empty", "embedder")
        return hmac.new(secret_bytes, key_input, hashlib.sha256).hexdigest()

    return hashlib.sha256(key_input).hexdigest()


class QueryCacheBackend(Protocol):
    """Protocol for query embedding cache storage (Step 2.2)."""

    def get(self, key: str) -> np.ndarray | None: ...

    def set(self, key: str, value: np.ndarray, ttl_seconds: int = 3600) -> None: ...

    def clear(self) -> None: ...


class InMemoryQueryCache:
    """Thread-safe in-memory cache for query embeddings with TTL and LRU bounded memory."""

    @dataclasses.dataclass
    class _CacheEntry:
        vector: np.ndarray
        expires_at: float

    def __init__(
        self,
        max_size: int = 10_000,
        clock: Any = None,
    ) -> None:
        if not isinstance(max_size, int) or isinstance(max_size, bool) or max_size <= 0:
            raise InvalidInputError("max_size must be a positive integer", "embedder")

        self._max_size = max_size
        self._store: OrderedDict[str, InMemoryQueryCache._CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._clock = clock if clock is not None else time.monotonic
        self._eviction_count = 0

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def eviction_count(self) -> int:
        with self._lock:
            return self._eviction_count

    def __bool__(self) -> bool:
        return True

    def get(self, key: str) -> np.ndarray | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if self._clock() > entry.expires_at:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return entry.vector.copy()

    def set(self, key: str, value: np.ndarray, ttl_seconds: int = 3600) -> None:
        if (
            not isinstance(ttl_seconds, (int, float))
            or isinstance(ttl_seconds, bool)
            or ttl_seconds <= 0
        ):
            raise InvalidInputError("ttl_seconds must be a positive number", "embedder")

        with self._lock:
            now = self._clock()
            expires = now + ttl_seconds

            # If key already in cache, update and mark most recently used
            if key in self._store:
                self._store[key] = self._CacheEntry(vector=value.copy(), expires_at=expires)
                self._store.move_to_end(key)
                return

            # If capacity reached, first purge expired entries
            if len(self._store) >= self._max_size:
                expired_keys = [k for k, v in self._store.items() if now > v.expires_at]
                for k in expired_keys:
                    del self._store[k]

            # If still at or over capacity, evict least recently used (LRU)
            while len(self._store) >= self._max_size:
                self._store.popitem(last=False)
                self._eviction_count += 1

            self._store[key] = self._CacheEntry(vector=value.copy(), expires_at=expires)

    def cleanup_expired(self) -> int:
        """Purge all expired entries from memory. Returns number of purged entries."""
        with self._lock:
            now = self._clock()
            expired_keys = [k for k, v in self._store.items() if now > v.expires_at]
            for k in expired_keys:
                del self._store[k]
            return len(expired_keys)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


class SingleFlight:
    """Coalesces concurrent duplicate in-flight operations by key so only one executes."""

    class _FlightCall:
        def __init__(self) -> None:
            self.event = threading.Event()
            self.result: np.ndarray | None = None
            self.exception: BaseException | None = None

    def __init__(self) -> None:
        self._calls: dict[str, SingleFlight._FlightCall] = {}
        self._lock = threading.Lock()

    def do(self, key: str, fn: Callable[[], np.ndarray]) -> tuple[np.ndarray, bool]:
        """Execute fn for key, or wait for an existing in-flight execution.

        Returns (result, is_shared) where is_shared is True if the call was coalesced.
        """
        with self._lock:
            if key in self._calls:
                call = self._calls[key]
                is_leader = False
            else:
                call = self._FlightCall()
                self._calls[key] = call
                is_leader = True

        if not is_leader:
            call.event.wait()
            if call.exception is not None:
                raise call.exception
            if call.result is None:
                raise RuntimeError("SingleFlight leader finished with no result")
            return call.result.copy(), True

        try:
            result = fn()
            call.result = result
            return result, False
        except BaseException as exc:
            call.exception = exc
            raise
        finally:
            with self._lock:
                if self._calls.get(key) is call:
                    del self._calls[key]
            call.event.set()


class CachedQueryEmbedder:
    """Wrapper providing query-embedding caching over an underlying Embedder.

    Rules:
    - Key is SHA-256 (or HMAC-SHA256 if cache_key_secret is configured) of prepared_query:fingerprint.
    - Never stores raw query text.
    - Automatically skips cache for queries flagged with has_pii=True or containing detected PII.
    - Single-flight mechanism coalesces concurrent identical in-flight queries.
    - Hit, miss, coalesced, and PII skip counters tracked.
    - Transparently delegates document embedding directly to underlying embedder.
    """

    def __init__(
        self,
        embedder: Embedder,
        cache: QueryCacheBackend | None = None,
        ttl_seconds: int = 3600,
        cache_key_secret: str | bytes | None = None,
    ) -> None:
        if (
            not isinstance(ttl_seconds, (int, float))
            or isinstance(ttl_seconds, bool)
            or ttl_seconds <= 0
        ):
            raise InvalidInputError("ttl_seconds must be a positive number", "embedder")

        if cache_key_secret is not None:
            if not isinstance(cache_key_secret, (str, bytes)):
                raise InvalidInputError("cache_key_secret must be str or bytes", "embedder")
            if not cache_key_secret:
                raise InvalidInputError("cache_key_secret must not be empty", "embedder")

        self._embedder = embedder
        self._cache = cache if cache is not None else InMemoryQueryCache()
        self._ttl_seconds = int(ttl_seconds)
        self._cache_key_secret = cache_key_secret
        self._single_flight = SingleFlight()
        self._lock = threading.Lock()

        self.cache_hits = 0
        self.cache_misses = 0
        self.coalesced_requests = 0
        self.pii_skipped = 0

    @property
    def config(self) -> EmbedderConfig:
        return self._embedder.config

    @property
    def fingerprint(self) -> str:
        return self._embedder.fingerprint

    @property
    def tokenizer(self) -> TokenCounter:
        return self._embedder.tokenizer

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        """Delegate document embedding directly (document embeddings are cached at chunk level)."""
        return self._embedder.embed_documents(texts)

    def embed_query(self, text: str, *, has_pii: bool = False) -> np.ndarray:
        """Embed query with caching, checking PII exemptions, single-flight coalescing, and secure hashing."""
        if has_pii or detect_pii(text):
            with self._lock:
                self.pii_skipped += 1
            return self._embedder.embed_query(text)

        # Hash key incorporates transformed text and embedder fingerprint
        clean_text = prepare_query_text(text, self._embedder.config.embed_text_source)
        cache_key = compute_query_cache_key(
            clean_text, self._embedder.fingerprint, self._cache_key_secret
        )

        # Fast path: cache hit
        cached_vec = self._cache.get(cache_key)
        if cached_vec is not None:
            with self._lock:
                self.cache_hits += 1
            return cached_vec

        # Slow path: single-flight deduplicated inference
        def _compute_and_cache() -> np.ndarray:
            # Double-check cache in case a preceding flight just finished
            existing = self._cache.get(cache_key)
            if existing is not None:
                return existing

            with self._lock:
                self.cache_misses += 1

            vec = self._embedder.embed_query(text)
            self._cache.set(cache_key, vec, ttl_seconds=self._ttl_seconds)
            return vec

        vec, is_shared = self._single_flight.do(cache_key, _compute_and_cache)
        if is_shared:
            with self._lock:
                self.coalesced_requests += 1

        return vec
