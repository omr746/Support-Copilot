"""Pipeline fingerprinting for deterministic versioning and cache invalidation (Step 2.1).

Guarantees:
- End-to-end composite pipeline fingerprint over cleaner, chunker, and embedder.
- Selective invalidation via sub-digests and granular changed_parts() (cleaner, normalizer, chunker, patterns, embedder).
- Strictly typed ChunkerConfig and EmbedderConfig rejecting silent type coercion, unknown keys, and invalid token budgets.
- Cross-validation: chunker.max_tokens <= embedder.max_length.
- Stored digest and schema verification on deserialization in from_dict().
- Hashable and immutable dataclasses with canonical JSON serialization.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

FINGERPRINT_SCHEMA_VERSION: int = 1


def canonical_json(data: dict[str, Any]) -> str:
    """Serialize dictionary to deterministic canonical JSON with sorted keys.

    Enforces allow_nan=False and compact separators to ensure cross-platform stability.
    """
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def compute_pattern_version(source: str | bytes | Path) -> str:
    """Compute a deterministic hash of injection patterns from a file or raw content."""
    if isinstance(source, Path) or (isinstance(source, str) and Path(source).is_file()):
        content = Path(source).read_bytes()
    elif isinstance(source, str):
        content = source.encode("utf-8")
    elif isinstance(source, bytes):
        content = source
    else:
        raise TypeError(f"Expected str, bytes, or Path for pattern source, got {type(source)}")
    return hashlib.sha256(content).hexdigest()[:16]


def compute_embedder_fingerprint(config: EmbedderConfig) -> str:
    """Compute deterministic SHA-256 fingerprint digest of an EmbedderConfig."""
    payload = {
        "_schema": FINGERPRINT_SCHEMA_VERSION,
        "_type": "embedder",
        "embedder_config": config.to_dict(),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class ChunkerConfig:
    """Immutable, typed chunker configuration preventing configuration drift."""

    splitter: str
    max_tokens: int
    target_tokens: int
    overlap_tokens: int
    tokenizer_id: str
    separators: tuple[str, ...]
    section_path_in_embedding: bool

    def __post_init__(self) -> None:
        if not isinstance(self.splitter, str) or not self.splitter:
            raise ValueError(f"splitter must be a non-empty string, got {self.splitter!r}")

        # Strict integer check: reject bool (isinstance(True, int) is True) and floats
        if type(self.max_tokens) is not int or self.max_tokens <= 0:
            raise ValueError(f"max_tokens must be a positive int, got {self.max_tokens!r}")
        if type(self.target_tokens) is not int or self.target_tokens <= 0:
            raise ValueError(f"target_tokens must be a positive int, got {self.target_tokens!r}")
        if type(self.overlap_tokens) is not int or self.overlap_tokens < 0:
            raise ValueError(
                f"overlap_tokens must be a non-negative int, got {self.overlap_tokens!r}"
            )

        # Invariant: target + overlap <= max so overlap prefix never forces chunk over the limit
        if self.target_tokens + self.overlap_tokens > self.max_tokens:
            raise ValueError(
                f"target_tokens ({self.target_tokens}) + overlap_tokens ({self.overlap_tokens}) = "
                f"{self.target_tokens + self.overlap_tokens} must be <= max_tokens ({self.max_tokens})"
            )

        if not isinstance(self.tokenizer_id, str) or not self.tokenizer_id:
            raise ValueError(f"tokenizer_id must be a non-empty string, got {self.tokenizer_id!r}")

        if not isinstance(self.separators, (tuple, list)) or not self.separators:
            raise ValueError("separators must be a non-empty sequence of strings")
        for s in self.separators:
            if not isinstance(s, str) or not s:
                raise ValueError(f"Each separator must be a non-empty str, got {s!r}")
        object.__setattr__(self, "separators", tuple(self.separators))

        if not isinstance(self.section_path_in_embedding, bool):
            raise ValueError(
                f"section_path_in_embedding must be a bool, got {self.section_path_in_embedding!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to canonical dictionary."""
        return {
            "max_tokens": self.max_tokens,
            "overlap_tokens": self.overlap_tokens,
            "section_path_in_embedding": self.section_path_in_embedding,
            "separators": list(self.separators),
            "splitter": self.splitter,
            "target_tokens": self.target_tokens,
            "tokenizer_id": self.tokenizer_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkerConfig:
        """Construct ChunkerConfig from dictionary with strict key and type validation (no silent coercion)."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict for ChunkerConfig, got {type(data)}")

        required = {
            "max_tokens",
            "overlap_tokens",
            "section_path_in_embedding",
            "separators",
            "splitter",
            "target_tokens",
            "tokenizer_id",
        }
        actual = set(data.keys())
        if actual != required:
            missing = required - actual
            unknown = actual - required
            errs: list[str] = []
            if missing:
                errs.append(f"missing {sorted(missing)}")
            if unknown:
                errs.append(f"unknown {sorted(unknown)}")
            raise ValueError(f"Invalid ChunkerConfig keys: {'; '.join(errs)}")

        # Pass values directly without silent coercion (e.g. no int(), bool(), str())
        return cls(
            splitter=data["splitter"],
            max_tokens=data["max_tokens"],
            target_tokens=data["target_tokens"],
            overlap_tokens=data["overlap_tokens"],
            tokenizer_id=data["tokenizer_id"],
            separators=data["separators"],
            section_path_in_embedding=data["section_path_in_embedding"],
        )


@dataclasses.dataclass(frozen=True)
class EmbedderConfig:
    """Immutable embedder configuration capturing all model and inference parameters (Step 2.2)."""

    model_id: str
    revision: str
    dimension: int
    max_length: int
    pooling: str
    normalize_embeddings: bool
    query_prefix: str = ""
    passage_prefix: str = ""
    quantization: str = "none"
    runtime: str = "onnx"  # "onnx" | "torch" | "fake"
    embed_text_source: str = "original"  # "original" | "normalized"
    truncate_dim: int | None = None
    special_tokens: int = 2
    tokenizer_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id:
            raise ValueError(f"model_id must be a non-empty string, got {self.model_id!r}")
        if not isinstance(self.revision, str) or not self.revision:
            raise ValueError(f"revision must be a non-empty pinned string, got {self.revision!r}")
        if type(self.dimension) is not int or self.dimension <= 0:
            raise ValueError(f"dimension must be a positive int, got {self.dimension!r}")
        if type(self.max_length) is not int or self.max_length <= 0:
            raise ValueError(f"max_length must be a positive int, got {self.max_length!r}")
        if not isinstance(self.pooling, str) or not self.pooling:
            raise ValueError(f"pooling must be a non-empty string, got {self.pooling!r}")
        if not isinstance(self.normalize_embeddings, bool):
            raise ValueError(
                f"normalize_embeddings must be a bool, got {self.normalize_embeddings!r}"
            )
        if not isinstance(self.query_prefix, str):
            raise ValueError(f"query_prefix must be a str, got {self.query_prefix!r}")
        if not isinstance(self.passage_prefix, str):
            raise ValueError(f"passage_prefix must be a str, got {self.passage_prefix!r}")
        if not isinstance(self.quantization, str) or not self.quantization:
            raise ValueError(f"quantization must be a non-empty str, got {self.quantization!r}")
        if self.runtime not in ("onnx", "torch", "fake"):
            raise ValueError(
                f"runtime must be one of ('onnx', 'torch', 'fake'), got {self.runtime!r}"
            )
        if self.embed_text_source not in ("original", "normalized"):
            raise ValueError(
                f"embed_text_source must be 'original' or 'normalized', got {self.embed_text_source!r}"
            )
        if self.truncate_dim is not None:
            if type(self.truncate_dim) is not int or not (0 < self.truncate_dim <= self.dimension):
                raise ValueError(
                    f"truncate_dim must be positive int <= dimension ({self.dimension}), got {self.truncate_dim!r}"
                )
        if type(self.special_tokens) is not int or self.special_tokens < 0:
            raise ValueError(
                f"special_tokens must be a non-negative int, got {self.special_tokens!r}"
            )
        if self.tokenizer_id is not None and (
            not isinstance(self.tokenizer_id, str) or not self.tokenizer_id.strip()
        ):
            raise ValueError(
                f"tokenizer_id must be a non-empty string or None, got {self.tokenizer_id!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to canonical dictionary."""
        return {
            "dimension": self.dimension,
            "embed_text_source": self.embed_text_source,
            "max_length": self.max_length,
            "model_id": self.model_id,
            "normalize_embeddings": self.normalize_embeddings,
            "passage_prefix": self.passage_prefix,
            "pooling": self.pooling,
            "quantization": self.quantization,
            "query_prefix": self.query_prefix,
            "revision": self.revision,
            "runtime": self.runtime,
            "special_tokens": self.special_tokens,
            "tokenizer_id": self.tokenizer_id,
            "truncate_dim": self.truncate_dim,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmbedderConfig:
        """Construct EmbedderConfig without silent coercion and with key checking."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict for EmbedderConfig, got {type(data)}")

        core_required = {
            "dimension",
            "max_length",
            "model_id",
            "normalize_embeddings",
            "passage_prefix",
            "pooling",
            "quantization",
            "query_prefix",
            "revision",
        }
        all_allowed = core_required | {
            "runtime",
            "embed_text_source",
            "truncate_dim",
            "special_tokens",
            "tokenizer_id",
        }
        actual = set(data.keys())

        missing = core_required - actual
        unknown = actual - all_allowed
        if missing or unknown:
            errs: list[str] = []
            if missing:
                errs.append(f"missing {sorted(missing)}")
            if unknown:
                errs.append(f"unknown {sorted(unknown)}")
            raise ValueError(f"Invalid EmbedderConfig keys: {'; '.join(errs)}")

        return cls(
            dimension=data["dimension"],
            max_length=data["max_length"],
            model_id=data["model_id"],
            normalize_embeddings=data["normalize_embeddings"],
            passage_prefix=data["passage_prefix"],
            pooling=data["pooling"],
            quantization=data["quantization"],
            query_prefix=data["query_prefix"],
            revision=data["revision"],
            runtime=data.get("runtime", "onnx"),
            embed_text_source=data.get("embed_text_source", "original"),
            truncate_dim=data.get("truncate_dim", None),
            special_tokens=data.get("special_tokens", 2),
            tokenizer_id=data.get("tokenizer_id", None),
        )


@dataclasses.dataclass(frozen=True)
class PipelineFingerprint:
    """Complete, immutable pipeline fingerprint spanning cleaner, chunking, and embedder.

    Guarantees:
    - Truly immutable and hashable (hash(fp) works and fp can be stored in sets/dict keys).
    - Schema versioning and domain separation in all payloads.
    - Full digest for end-to-end pipeline comparison (strictly raises if embedder is unset).
    - Sub-digests for selective invalidation (e.g. re-embed without re-chunking).
    - Granular changed_parts(other) reporting: 'cleaner', 'normalizer', 'chunker', 'patterns', 'embedder'.
    - Stored digest verification on from_dict().
    - Cross-validation: chunker_config.max_tokens <= embedder_config.max_length.
    """

    cleaner_version: str
    normalizer_version: str
    chunker_config: ChunkerConfig
    injection_pattern_version: str
    embedder_config: EmbedderConfig | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.cleaner_version, str) or not self.cleaner_version:
            raise ValueError(
                f"cleaner_version must be a non-empty string, got {self.cleaner_version!r}"
            )
        if not isinstance(self.normalizer_version, str) or not self.normalizer_version:
            raise ValueError(
                f"normalizer_version must be a non-empty string, got {self.normalizer_version!r}"
            )
        if not isinstance(self.chunker_config, ChunkerConfig):
            raise ValueError(
                f"chunker_config must be ChunkerConfig instance, got {type(self.chunker_config)}"
            )
        if (
            not isinstance(self.injection_pattern_version, str)
            or not self.injection_pattern_version
        ):
            raise ValueError(
                f"injection_pattern_version must be a non-empty string, got {self.injection_pattern_version!r}"
            )
        if self.embedder_config is not None and not isinstance(
            self.embedder_config, EmbedderConfig
        ):
            raise ValueError(
                f"embedder_config must be EmbedderConfig or None, got {type(self.embedder_config)}"
            )

        # Cross-check: chunker limit must never exceed embedder sequence length
        if self.embedder_config is not None:
            if self.chunker_config.max_tokens > self.embedder_config.max_length:
                raise ValueError(
                    f"chunker_config.max_tokens ({self.chunker_config.max_tokens}) exceeds "
                    f"embedder_config.max_length ({self.embedder_config.max_length}). "
                    f"Chunks would be silently truncated by the embedding model."
                )

    @property
    def chunking_digest(self) -> str:
        """Deterministic SHA-256 digest over normalization and chunker configurations."""
        payload = {
            "_schema": FINGERPRINT_SCHEMA_VERSION,
            "_type": "chunking",
            "chunker_config": self.chunker_config.to_dict(),
            "injection_pattern_version": self.injection_pattern_version,
            "normalizer_version": self.normalizer_version,
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    @property
    def embedder_digest(self) -> str | None:
        """Deterministic SHA-256 digest over embedder model configuration, or None if not configured."""
        if self.embedder_config is None:
            return None
        return compute_embedder_fingerprint(self.embedder_config)

    @property
    def full_digest(self) -> str:
        """Composite SHA-256 digest over the entire pipeline (cleaner, chunking, embedder).

        Raises ValueError if embedder_config is not set, preventing premature full digest
        creation in Step 2.1 before embeddings are defined. In Step 2.1, use chunking_digest.
        """
        if self.embedder_config is None:
            raise ValueError(
                "full_digest requires an embedder_config. In Step 2.1 before embedder is configured, "
                "use chunking_digest to record chunking state."
            )
        payload = {
            "_schema": FINGERPRINT_SCHEMA_VERSION,
            "_type": "pipeline",
            "chunking_digest": self.chunking_digest,
            "cleaner_version": self.cleaner_version,
            "embedder_digest": self.embedder_digest,
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def changed_parts(self, other: PipelineFingerprint) -> list[str]:
        """Granularly identify which pipeline components differ between self and other.

        Returns items from: 'cleaner', 'normalizer', 'chunker', 'patterns', 'embedder'.
        """
        diffs: list[str] = []
        if self.cleaner_version != other.cleaner_version:
            diffs.append("cleaner")
        if self.normalizer_version != other.normalizer_version:
            diffs.append("normalizer")
        if self.chunker_config != other.chunker_config:
            diffs.append("chunker")
        if self.injection_pattern_version != other.injection_pattern_version:
            diffs.append("patterns")
        if self.embedder_config != other.embedder_config:
            diffs.append("embedder")
        return diffs

    def to_dict(self) -> dict[str, Any]:
        """Serialize complete fingerprint to dictionary representation for database/manifest storage."""
        full_dig = self.full_digest if self.embedder_config is not None else None
        return {
            "_schema": FINGERPRINT_SCHEMA_VERSION,
            "chunker_config": self.chunker_config.to_dict(),
            "chunking_digest": self.chunking_digest,
            "cleaner_version": self.cleaner_version,
            "embedder_config": self.embedder_config.to_dict() if self.embedder_config else None,
            "embedder_digest": self.embedder_digest,
            "full_digest": full_dig,
            "injection_pattern_version": self.injection_pattern_version,
            "normalizer_version": self.normalizer_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineFingerprint:
        """Deserialize from dictionary, verifying schema version and recomputed digests."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict for PipelineFingerprint, got {type(data)}")

        stored_schema = data.get("_schema")
        if stored_schema != FINGERPRINT_SCHEMA_VERSION:
            raise ValueError(
                f"Fingerprint schema mismatch: expected schema {FINGERPRINT_SCHEMA_VERSION}, "
                f"got {stored_schema!r}"
            )

        embedder_cfg = None
        if data.get("embedder_config") is not None:
            embedder_cfg = EmbedderConfig.from_dict(data["embedder_config"])

        fp = cls(
            cleaner_version=data["cleaner_version"],
            normalizer_version=data["normalizer_version"],
            chunker_config=ChunkerConfig.from_dict(data["chunker_config"]),
            injection_pattern_version=data["injection_pattern_version"],
            embedder_config=embedder_cfg,
        )

        # Integrity check: verify stored digests match recomputed values
        if "chunking_digest" in data and data["chunking_digest"] is not None:
            if data["chunking_digest"] != fp.chunking_digest:
                raise ValueError(
                    f"Stored chunking_digest ({data['chunking_digest']}) does not match "
                    f"recomputed digest ({fp.chunking_digest})"
                )

        if "embedder_digest" in data and data["embedder_digest"] is not None:
            if data["embedder_digest"] != fp.embedder_digest:
                raise ValueError(
                    f"Stored embedder_digest ({data['embedder_digest']}) does not match "
                    f"recomputed digest ({fp.embedder_digest})"
                )

        if (
            "full_digest" in data
            and data["full_digest"] is not None
            and fp.embedder_config is not None
        ):
            if data["full_digest"] != fp.full_digest:
                raise ValueError(
                    f"Stored full_digest ({data['full_digest']}) does not match "
                    f"recomputed digest ({fp.full_digest})"
                )

        return fp
