"""Contracts (Protocols) every slot implementation must satisfy.

A module is a chain of slots. Each slot names a contract here and picks an
implementation with ``type:`` in config. Modules only talk to slots through
these contracts, so swapping GLM-OCR for Azure Document Intelligence is a
config change, not a code change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str
    images: tuple[bytes, ...] = ()  # sent to vision models (Qwen-VL, GLM-OCR)


@dataclass(frozen=True)
class LlmResult:
    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class Document:
    id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    id: str
    doc_id: str
    workspace_id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


@dataclass(frozen=True)
class AccessFilter:
    """What the caller may see. Sent by the Backend with every request."""

    workspace_id: str
    doc_ids: Sequence[str] | None = None  # None = every document in the workspace


def allowed(chunk: Chunk, access: AccessFilter) -> bool:
    if chunk.workspace_id != access.workspace_id:
        return False
    return access.doc_ids is None or chunk.doc_id in set(access.doc_ids)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    issues: Sequence[str] = ()


@runtime_checkable
class Llm(Protocol):
    async def complete(self, messages: Sequence[Message], **options: Any) -> LlmResult: ...


@runtime_checkable
class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class Store(Protocol):
    """Every search must apply ``access``; a chunk outside it must never be returned."""

    async def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None: ...

    async def search_vector(
        self, vector: Sequence[float], access: AccessFilter, top_k: int
    ) -> list[Hit]: ...

    async def search_text(self, query: str, access: AccessFilter, top_k: int) -> list[Hit]: ...


@runtime_checkable
class Cache(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def set(self, key: str, value: Mapping[str, Any], ttl: int) -> None: ...


@runtime_checkable
class Retriever(Protocol):
    async def retrieve(self, query: str, access: AccessFilter, top_k: int) -> list[Hit]: ...


@runtime_checkable
class Reranker(Protocol):
    async def rerank(self, query: str, hits: Sequence[Hit], top_k: int) -> list[Hit]: ...


@runtime_checkable
class Source(Protocol):
    async def fetch(self, **query: Any) -> list[Document]: ...


@runtime_checkable
class Reader(Protocol):
    async def read(self, data: bytes, *, mime: str) -> Document: ...


@runtime_checkable
class Writer(Protocol):
    async def write(self, facts: Mapping[str, float], instructions: str) -> str: ...


@runtime_checkable
class Checker(Protocol):
    async def check(self, item: Any) -> CheckResult: ...


# ---------- data shared by modules A, C-H ----------


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    box: tuple[float, float, float, float] = (0, 0, 0, 0)  # x1, y1, x2, y2 in pixels


@dataclass(frozen=True)
class TagValue:
    value: float
    timestamp: float  # unix seconds from the historian / OPC UA server
    good: bool = True  # OPC UA status code is Good


@dataclass(frozen=True)
class Page:
    url: str
    status: int
    text: str


@runtime_checkable
class TableSource(Protocol):
    """Rows from a named, pre-defined query. Callers never send SQL."""

    async def fetch_table(self, name: str, params: Mapping[str, Any]) -> list[dict[str, Any]]: ...


@runtime_checkable
class FactsBuilder(Protocol):
    async def compute(
        self, rows: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any]
    ) -> dict[str, float]: ...


@runtime_checkable
class ReviewQueue(Protocol):
    """Hands an item to the Backend's Review UI. Returns the review id."""

    async def submit(self, kind: str, payload: Mapping[str, Any]) -> str: ...


@runtime_checkable
class Notifier(Protocol):
    """Progress events to the Backend (seq + event_id)."""

    async def notify(self, event: str, payload: Mapping[str, Any]) -> None: ...


@runtime_checkable
class RecordStore(Protocol):
    async def save(self, collection: str, key: str, data: Mapping[str, Any]) -> None: ...

    async def get(self, collection: str, key: str) -> dict[str, Any] | None: ...

    async def list(self, collection: str, prefix: str = "") -> list[dict[str, Any]]: ...


@runtime_checkable
class Detector(Protocol):
    async def detect(self, image: bytes) -> list[Detection]: ...


@runtime_checkable
class Forecaster(Protocol):
    async def forecast(self, history: Sequence[float], horizon: int) -> list[float]: ...


@runtime_checkable
class ModelRegistry(Protocol):
    async def log(
        self, name: str, metrics: Mapping[str, float], params: Mapping[str, Any]
    ) -> str: ...

    async def latest(self, name: str) -> dict[str, Any] | None: ...


@runtime_checkable
class Allocator(Protocol):
    async def allocate(
        self, demand: Mapping[str, float], supply: float, caps: Mapping[str, float]
    ) -> dict[str, float]: ...


@runtime_checkable
class PlantReader(Protocol):
    """Read-only access to plant signals. There is deliberately no write method."""

    async def read_tags(self, tags: Sequence[str]) -> dict[str, TagValue]: ...


@runtime_checkable
class SoftSensor(Protocol):
    async def predict(self, inputs: Mapping[str, float]) -> float: ...


@runtime_checkable
class Fetcher(Protocol):
    async def fetch(self, url: str) -> Page: ...


# Contract name used in config (``kind``) -> Protocol.
CONTRACTS: dict[str, type] = {
    "cache": Cache,
    "llm": Llm,
    "embedder": Embedder,
    "store": Store,
    "retriever": Retriever,
    "reranker": Reranker,
    "source": Source,
    "reader": Reader,
    "writer": Writer,
    "checker": Checker,
    "table_source": TableSource,
    "facts": FactsBuilder,
    "review": ReviewQueue,
    "notifier": Notifier,
    "records": RecordStore,
    "detector": Detector,
    "forecaster": Forecaster,
    "model_registry": ModelRegistry,
    "allocator": Allocator,
    "plant_reader": PlantReader,
    "soft_sensor": SoftSensor,
    "fetcher": Fetcher,
}
