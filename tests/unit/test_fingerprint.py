"""Unit tests for pipeline fingerprinting, immutability, digests, and comparisons (Step 2.1).

Verifies:
1. Rejection of silent type coercion and unknown keys in from_dict (strict types).
2. Complete EmbedderConfig capturing model_id, pinned revision, prefixes, pooling, quantization.
3. full_digest strictly raises ValueError when embedder_config is None.
4. Chunker size rules: target_tokens + overlap_tokens <= max_tokens; boolean-as-int rejection.
5. Stored digests & schema version verification on deserialization in from_dict().
6. Granular changed_parts(other) reporting cleaner, normalizer, chunker, patterns, embedder.
7. Content-hashed pattern version via compute_pattern_version().
8. Chunker-Embedder cross-validation: chunker.max_tokens <= embedder.max_length.
9. Immutability & hashability (hash(fp), sets, dict keys).
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from copilot.rag.fingerprint import (
    ChunkerConfig,
    EmbedderConfig,
    PipelineFingerprint,
    compute_pattern_version,
)


class TestPipelineFingerprint(unittest.TestCase):
    """Test suite for PipelineFingerprint, ChunkerConfig, and EmbedderConfig."""

    def setUp(self) -> None:
        self.chunker_cfg = ChunkerConfig(
            splitter="heading+recursive-v1",
            max_tokens=500,
            target_tokens=400,
            overlap_tokens=80,  # 400 + 80 = 480 <= 500
            tokenizer_id="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2@rev1",
            separators=("\n\n", "\n", " "),
            section_path_in_embedding=True,
        )
        self.embedder_cfg = EmbedderConfig(
            model_id="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            revision="sha256:abcdef1234567890",
            dimension=384,
            max_length=512,
            pooling="mean",
            normalize_embeddings=True,
            query_prefix="",
            passage_prefix="",
            quantization="none",
        )
        self.pattern_ver = compute_pattern_version("baseline injection regexes v1")
        self.fp = PipelineFingerprint(
            cleaner_version="trafilatura-1.12.2+bs4-4.12.3",
            normalizer_version="ar-normalizer-v1.1",
            chunker_config=self.chunker_cfg,
            injection_pattern_version=self.pattern_ver,
            embedder_config=self.embedder_cfg,
        )

    def test_immutability_and_hashability(self) -> None:
        """Fingerprint and configurations are frozen, immutable, and hashable."""
        h = hash(self.fp)
        self.assertIsInstance(h, int)

        fp_set = {self.fp}
        self.assertIn(self.fp, fp_set)

        fp_dict = {self.fp: "active_pipeline"}
        self.assertEqual(fp_dict[self.fp], "active_pipeline")

        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.fp.cleaner_version = "mutated"  # type: ignore[misc]

        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.chunker_cfg.max_tokens = 600  # type: ignore[misc]

    def test_chunker_config_no_silent_coercion_and_reject_unknown_keys(self) -> None:
        """from_dict rejects silent type coercion and unknown keys."""
        valid_dict = self.chunker_cfg.to_dict()

        # 1. Unknown key must be rejected
        with_unknown = dict(valid_dict)
        with_unknown["extra_typo_key"] = "bogus"
        with self.assertRaises(ValueError) as ctx:
            ChunkerConfig.from_dict(with_unknown)
        self.assertIn("unknown", str(ctx.exception))

        # 2. Missing key must be rejected
        with_missing = dict(valid_dict)
        del with_missing["overlap_tokens"]
        with self.assertRaises(ValueError) as ctx:
            ChunkerConfig.from_dict(with_missing)
        self.assertIn("missing", str(ctx.exception))

        # 3. String as boolean rejected
        with_str_bool = dict(valid_dict)
        with_str_bool["section_path_in_embedding"] = "false"  # not a real bool!
        with self.assertRaises(ValueError):
            ChunkerConfig.from_dict(with_str_bool)

        # 4. Float as int rejected
        with_float = dict(valid_dict)
        with_float["max_tokens"] = 500.0  # float rejected
        with self.assertRaises(ValueError):
            ChunkerConfig.from_dict(with_float)

        # 5. Boolean as int rejected (isinstance(True, int) is True in Python)
        with_bool_int = dict(valid_dict)
        with_bool_int["max_tokens"] = True
        with self.assertRaises(ValueError):
            ChunkerConfig.from_dict(with_bool_int)

    def test_chunker_size_invariants(self) -> None:
        """target_tokens + overlap_tokens must be <= max_tokens."""
        # 450 + 80 = 530 > 500 -> must raise
        with self.assertRaises(ValueError) as ctx:
            ChunkerConfig(
                splitter="heading+recursive-v1",
                max_tokens=500,
                target_tokens=450,
                overlap_tokens=80,
                tokenizer_id="test",
                separators=("\n",),
                section_path_in_embedding=True,
            )
        self.assertIn("must be <= max_tokens", str(ctx.exception))

        # Separators must be non-empty strings
        with self.assertRaises(ValueError):
            ChunkerConfig(
                splitter="heading+recursive-v1",
                max_tokens=500,
                target_tokens=400,
                overlap_tokens=50,
                tokenizer_id="test",
                separators=(123,),  # type: ignore[arg-type]
                section_path_in_embedding=True,
            )

    def test_embedder_config_complete_parameters_and_unpinned_revision(self) -> None:
        """EmbedderConfig captures model, pinned revision, prefixes, pooling, quantization."""
        valid_dict = self.embedder_cfg.to_dict()

        # Unpinned revision rejected
        with self.assertRaises(ValueError) as ctx:
            EmbedderConfig(
                model_id="test",
                revision="",  # empty revision not allowed
                dimension=384,
                max_length=512,
                pooling="mean",
                normalize_embeddings=True,
            )
        self.assertIn("revision must be a non-empty pinned string", str(ctx.exception))

        # Unknown key rejected in from_dict
        bad_keys = dict(valid_dict)
        bad_keys["unknown_param"] = True
        with self.assertRaises(ValueError) as ctx:
            EmbedderConfig.from_dict(bad_keys)
        self.assertIn("unknown", str(ctx.exception))

        # Changing prefix changes embedder digest
        cfg_e5 = dataclasses.replace(
            self.embedder_cfg, query_prefix="query: ", passage_prefix="passage: "
        )
        fp_e5 = dataclasses.replace(self.fp, embedder_config=cfg_e5)
        self.assertNotEqual(self.fp.embedder_digest, fp_e5.embedder_digest)

    def test_cross_check_chunker_and_embedder_limits(self) -> None:
        """Fails loudly if chunker.max_tokens > embedder.max_length."""
        oversize_chunker = dataclasses.replace(
            self.chunker_cfg, max_tokens=600, target_tokens=500, overlap_tokens=80
        )
        # embedder max_length is 512, chunker max_tokens is 600 -> must raise
        with self.assertRaises(ValueError) as ctx:
            PipelineFingerprint(
                cleaner_version="v1",
                normalizer_version="v1",
                chunker_config=oversize_chunker,
                injection_pattern_version="v1",
                embedder_config=self.embedder_cfg,
            )
        self.assertIn("exceeds embedder_config.max_length", str(ctx.exception))

    def test_full_digest_raises_without_embedder(self) -> None:
        """full_digest strictly raises ValueError when embedder_config is None."""
        fp_no_emb = dataclasses.replace(self.fp, embedder_config=None)
        self.assertIsNone(fp_no_emb.embedder_digest)
        self.assertIsInstance(fp_no_emb.chunking_digest, str)

        with self.assertRaises(ValueError) as ctx:
            _ = fp_no_emb.full_digest
        self.assertIn("full_digest requires an embedder_config", str(ctx.exception))

    def test_stored_digests_and_schema_verification(self) -> None:
        """from_dict verifies schema version and recomputed digests match stored values."""
        data = self.fp.to_dict()

        # Valid round trip
        reconstructed = PipelineFingerprint.from_dict(data)
        self.assertEqual(self.fp, reconstructed)

        # 1. Tampered schema raises
        tampered_schema = dict(data)
        tampered_schema["_schema"] = 999
        with self.assertRaises(ValueError) as ctx:
            PipelineFingerprint.from_dict(tampered_schema)
        self.assertIn("schema mismatch", str(ctx.exception))

        # 2. Tampered chunking digest raises
        tampered_chunking = dict(data)
        tampered_chunking["chunking_digest"] = "f" * 64
        with self.assertRaises(ValueError) as ctx:
            PipelineFingerprint.from_dict(tampered_chunking)
        self.assertIn("Stored chunking_digest", str(ctx.exception))

        # 3. Tampered embedder digest raises
        tampered_embedder = dict(data)
        tampered_embedder["embedder_digest"] = "e" * 64
        with self.assertRaises(ValueError) as ctx:
            PipelineFingerprint.from_dict(tampered_embedder)
        self.assertIn("Stored embedder_digest", str(ctx.exception))

    def test_granular_changed_parts(self) -> None:
        """changed_parts returns granular component names: cleaner, normalizer, chunker, patterns, embedder."""
        self.assertEqual(self.fp.changed_parts(self.fp), [])

        # 1. Cleaner
        c_fp = dataclasses.replace(self.fp, cleaner_version="cleaner-v2")
        self.assertEqual(self.fp.changed_parts(c_fp), ["cleaner"])

        # 2. Normalizer
        n_fp = dataclasses.replace(self.fp, normalizer_version="ar-normalizer-v2.0")
        self.assertEqual(self.fp.changed_parts(n_fp), ["normalizer"])

        # 3. Chunker
        new_ch = dataclasses.replace(
            self.chunker_cfg, max_tokens=400, target_tokens=300, overlap_tokens=50
        )
        ch_fp = dataclasses.replace(self.fp, chunker_config=new_ch)
        self.assertEqual(self.fp.changed_parts(ch_fp), ["chunker"])

        # 4. Patterns
        pat_fp = dataclasses.replace(self.fp, injection_pattern_version="new-pattern-hash-99")
        self.assertEqual(self.fp.changed_parts(pat_fp), ["patterns"])

        # 5. Embedder
        new_emb = dataclasses.replace(self.embedder_cfg, model_id="new-embedder-v2")
        emb_fp = dataclasses.replace(self.fp, embedder_config=new_emb)
        self.assertEqual(self.fp.changed_parts(emb_fp), ["embedder"])

        # 6. Multi-component
        multi_fp = dataclasses.replace(
            self.fp, normalizer_version="v2", injection_pattern_version="p2"
        )
        self.assertEqual(sorted(self.fp.changed_parts(multi_fp)), ["normalizer", "patterns"])

    def test_compute_pattern_version_hashing(self) -> None:
        """compute_pattern_version produces deterministic hashes from raw content and files."""
        text = "regex_pattern: ignore all instructions"
        hash1 = compute_pattern_version(text)
        hash2 = compute_pattern_version(text.encode("utf-8"))
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 16)

        # File hashing
        with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as f:
            f.write(text)
            f_path = Path(f.name)

        try:
            file_hash = compute_pattern_version(f_path)
            self.assertEqual(file_hash, hash1)
        finally:
            f_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
