"""Text embeddings (ARCHITECTURE §3.4, §4): one interface, interchangeable implementations.

Used by the ingestion (documents) and, from phase 3, by the API (questions): both
sides must use the same model, otherwise their vectors are not comparable.
"""

import logging
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    @property
    def max_input_tokens(self) -> int:
        """Longer inputs are truncated by the model."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """One unit-length vector per text (cosine similarity = dot product)."""
        ...

    def embed_query(self, text: str) -> list[float]: ...

    def count_tokens(self, text: str) -> int: ...


def is_in_hf_cache(model_name: str) -> bool:
    """True if ``model_name`` (e.g. "BAAI/bge-m3") is already in the Hugging Face cache."""
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    hub = Path(os.environ.get("HF_HUB_CACHE", hf_home / "hub"))
    snapshots = hub / f"models--{model_name.replace('/', '--')}" / "snapshots"
    return snapshots.is_dir() and any(snapshots.iterdir())


def _to_lists(rows: Iterable[Any]) -> list[list[float]]:
    return [[float(x) for x in row] for row in rows]


class SentenceTransformerEmbedder:
    """Local model through sentence-transformers (default: BAAI/bge-m3, 1024 dimensions).

    The model is downloaded once into the Hugging Face cache, then runs locally
    (Apple GPU, CUDA or CPU, detected automatically unless ``device`` is given).
    bge-m3 needs no query instruction prefix.
    """

    def __init__(self, model_name: str, *, device: str | None = None, batch_size: int = 16):
        # torch logs a harmless "Redirects are currently not supported in MacOs" at import.
        logging.getLogger("torch.distributed.elastic.multiprocessing.redirects").setLevel(
            logging.ERROR
        )
        cached = is_in_hf_cache(model_name)
        if cached:
            # Already downloaded: no network call to the Hugging Face Hub (faster start,
            # works offline, no "unauthenticated requests" warning). Must be set before
            # huggingface_hub is imported. Process-wide: models used alongside this one
            # (e.g. the reranker, phase 4) must be in the cache too.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        # Heavy import (torch): only when a real model is needed. Not installed in CI.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, device=device, local_files_only=cached)
        self._model_name = model_name
        self._batch_size = batch_size
        dimension = self._model.get_embedding_dimension()
        if dimension is None:
            raise ValueError(f"{model_name} does not report its embedding dimension")
        self._dimension = int(dimension)
        max_tokens = self._model.max_seq_length
        if max_tokens is None:
            raise ValueError(f"{model_name} does not report its maximum input length")
        self._max_input_tokens = int(max_tokens)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_input_tokens(self) -> int:
        return self._max_input_tokens

    @property
    def device(self) -> str:
        return str(self._model.device)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode_document(
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return _to_lists(vectors)

    def embed_query(self, text: str) -> list[float]:
        vectors = self._model.encode_query(
            [text], normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        return _to_lists(vectors)[0]

    def count_tokens(self, text: str) -> int:
        return len(self._model.tokenizer(text, add_special_tokens=True)["input_ids"])
