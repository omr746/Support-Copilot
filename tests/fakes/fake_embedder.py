"""Meaningful deterministic FakeEmbedder for unit tests and local simulation (Step 2.2)."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Sequence
from typing import ClassVar

import numpy as np

from copilot.rag.embedder.base import BaseEmbedder
from copilot.rag.embedder.vector import normalize_l2
from copilot.rag.fingerprint import EmbedderConfig
from copilot.rag.normalize import normalize_search_text
from copilot.rag.tokens import TokenCounter


class FakeEmbedder(BaseEmbedder):
    """Meaningful, non-random fake embedder based on word and character trigram hashing.

    Guarantees:
    - Generates float32 vectors of dimension d, normalized to Euclidean norm 1.0.
    - Paraphrases and conceptually overlapping sentences score higher than unrelated ones.
    - Cross-language concept anchors connect common Arabic/English support terms.
    - Deterministic: identical input text produces identical vectors across runs.
    - Failing and slow variants via fail_with and delay_seconds parameters.
    """

    # Semantic cross-lingual concept anchors mapping terms to shared concept tokens
    RAW_CONCEPTS: ClassVar[dict[str, list[str]]] = {
        "concept_bill": [
            "فاتورة",
            "فاتوره",
            "فواتير",
            "سداد",
            "دفع",
            "استعلام",
            "تحصيل",
            "مستحقات",
            "مديونية",
            "مديونيه",
            "bill",
            "bills",
            "invoice",
            "invoices",
            "payment",
            "payments",
            "pay",
            "paying",
            "due",
        ],
        "concept_phone": [
            "تليفون",
            "تلفون",
            "تليفونات",
            "تلفونات",
            "هاتف",
            "هواتف",
            "ارضي",
            "أرضي",
            "الارضي",
            "الأرضي",
            "خط",
            "خطوط",
            "موبايل",
            "محمول",
            "phone",
            "phones",
            "landline",
            "landlines",
            "mobile",
            "mobiles",
            "fixed",
        ],
        "concept_package": [
            "باقة",
            "باقه",
            "باقات",
            "خطة",
            "خطه",
            "خطط",
            "عرض",
            "عروض",
            "اشتراك",
            "اشتراكات",
            "package",
            "packages",
            "bundle",
            "bundles",
            "plan",
            "plans",
            "offer",
            "offers",
            "subscription",
        ],
        "concept_internet": [
            "انترنت",
            "إنترنت",
            "نت",
            "وايفاي",
            "واي فاي",
            "راوتر",
            "مودم",
            "سرعة",
            "سرعه",
            "جيجا",
            "جيجابايت",
            "سعة",
            "سعه",
            "كوتا",
            "internet",
            "net",
            "wifi",
            "wi-fi",
            "router",
            "modem",
            "adsl",
            "vdsl",
            "fiber",
            "speed",
            "quota",
            "gigabyte",
            "gb",
        ],
        "concept_support": [
            "دعم",
            "مساعدة",
            "مساعده",
            "خدمة",
            "خدمه",
            "خدمات",
            "عملاء",
            "عميل",
            "شكوى",
            "شكاوى",
            "مشكلة",
            "مشكله",
            "عطل",
            "اعطال",
            "فني",
            "support",
            "help",
            "service",
            "services",
            "customer",
            "care",
            "complaint",
            "issue",
            "technical",
        ],
        "concept_recharge": [
            "شحن",
            "رصيد",
            "كارت",
            "كروت",
            "تحويل",
            "recharge",
            "topup",
            "balance",
            "card",
            "cards",
            "transfer",
        ],
    }

    CONCEPT_SYNONYMS: ClassVar[dict[str, str]] = {}
    for _concept, _terms in RAW_CONCEPTS.items():
        for _term in _terms:
            CONCEPT_SYNONYMS[_term] = _concept
            CONCEPT_SYNONYMS[normalize_search_text(_term)] = _concept

    STOPWORDS: ClassVar[set[str]] = {
        "في",
        "من",
        "على",
        "الى",
        "إلى",
        "عن",
        "مع",
        "هذا",
        "هذه",
        "التي",
        "الذي",
        "طريقة",
        "طريقه",
        "كيفية",
        "كيفيه",
        "عمل",
        "ما",
        "هو",
        "هي",
        "كم",
        "how",
        "to",
        "in",
        "at",
        "the",
        "and",
        "or",
        "of",
        "for",
        "with",
        "is",
        "a",
        "an",
    }

    def __init__(
        self,
        config: EmbedderConfig | None = None,
        tokenizer: TokenCounter | None = None,
        fail_with: Exception | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        cfg = config or EmbedderConfig(
            model_id="mock-multilingual-e5",
            revision="0000000000000000000000000000000000000000",
            dimension=384,
            max_length=512,
            pooling="mean",
            normalize_embeddings=True,
            query_prefix="query: ",
            passage_prefix="passage: ",
            quantization="none",
            runtime="fake",
            embed_text_source="original",
        )
        super().__init__(cfg, tokenizer=tokenizer)
        self.fail_with = fail_with
        self.delay_seconds = delay_seconds

    def _check_fault_injection(self) -> None:
        """Simulate delay or failure if configured."""
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        if self.fail_with is not None:
            raise self.fail_with

    def _compute_meaningful_vector(self, text: str) -> np.ndarray:
        """Compute meaningful float32 vector using character and word trigrams plus concept anchors."""
        dim = self._config.truncate_dim or self._config.dimension
        vec = np.zeros(dim, dtype=np.float32)

        # Standardize for feature hashing
        norm_text = normalize_search_text(text)
        words = [w for w in re.split(r"\s+", norm_text) if w]

        features: list[tuple[str, float]] = []

        # 1. Concept anchors for high cross-lingual/paraphrase recall
        for word in words:
            clean_w = word.strip(".,;:!?()[]{}")
            matched_concept: str | None = None
            for candidate in (
                clean_w,
                clean_w.removeprefix("ال") if len(clean_w) > 3 and clean_w.startswith("ال") else "",
                clean_w.removeprefix("و") if len(clean_w) > 3 and clean_w.startswith("و") else "",
                clean_w.removeprefix("بال")
                if len(clean_w) > 4 and clean_w.startswith("بال")
                else "",
                clean_w.removeprefix("لل") if len(clean_w) > 3 and clean_w.startswith("لل") else "",
            ):
                if candidate and candidate in self.CONCEPT_SYNONYMS:
                    matched_concept = self.CONCEPT_SYNONYMS[candidate]
                    break

            if matched_concept:
                features.append((matched_concept, 6.0))

        # 2. Word unigrams & bigrams (downweighting common procedural/stopwords)
        for i, word in enumerate(words):
            weight = 0.2 if word in self.STOPWORDS else 1.5
            features.append((f"w1_{word}", weight))
            if i + 1 < len(words):
                features.append((f"w2_{word}_{words[i + 1]}", 1.8))

        # 3. Character 3-grams and 4-grams (robust to spelling variants and morphology)
        compact = "".join(words)
        if len(compact) >= 3:
            for k in range(len(compact) - 2):
                features.append((f"c3_{compact[k : k + 3]}", 0.6))
        if len(compact) >= 4:
            for k in range(len(compact) - 3):
                features.append((f"c4_{compact[k : k + 4]}", 0.8))

        if not features:
            features.append(("empty_fallback", 1.0))

        # Feature hashing (MurmurHash-style using md5)
        for feat, weight in features:
            h = hashlib.md5(feat.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "little") % dim
            sign = 1.0 if (h[4] % 2 == 0) else -1.0
            vec[idx] += sign * weight

        # L2-normalize
        return normalize_l2(vec)

    def _raw_embed_batch(self, prefixed_texts: Sequence[str]) -> np.ndarray:
        """Compute meaningful float32 vectors for a batch of prefixed texts."""
        self._check_fault_injection()
        dim = self._config.truncate_dim or self._config.dimension
        out = np.zeros((len(prefixed_texts), dim), dtype=np.float32)
        for i, text in enumerate(prefixed_texts):
            out[i] = self._compute_meaningful_vector(text)
        return out
