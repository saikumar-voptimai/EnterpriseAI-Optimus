"""Qdrant similarity index for knowledge chunks, used when VECTOR_BACKEND=qdrant.

PostgreSQL remains the system of record: chunk text, revisions and vectors stay
there, so the collection can be rebuilt with `python -m app.knowledge_cli qdrant-sync`.
Qdrant only proposes candidate chunk IDs. Callers re-check every candidate through
the SQL audience predicate, so a stale or over-broad payload can lose recall but
never expose a chunk the caller may not read.
"""

import logging
from typing import Any
import httpx
from app.config import get_settings
from app.services.errors import ProviderError

log = logging.getLogger(__name__)
KEYWORD_FIELDS = ("audience", "document_id", "revision_id", "project_id", "embedding_model")


def audience_key(workspace_id: str | None, owner_id: str | None) -> str:
    return f"workspace:{workspace_id}" if workspace_id else f"user:{owner_id}"


def _match(key: str, value: str) -> dict:
    return {"key": key, "match": {"value": str(value)}}


class QdrantIndex:
    def __init__(self, settings: Any = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        self.transport = transport
        self.base = self.settings.qdrant_url.rstrip("/")
        # One collection per dimension, so changing the embedding profile never
        # writes vectors of a new size into an incompatible collection.
        self.collection = f"{self.settings.qdrant_collection}_{self.settings.embedding_dimensions}"

    def _headers(self) -> dict:
        key = self.settings.qdrant_api_key
        return {"api-key": key} if key else {}

    def _client(self, sync: bool = False):
        options = dict(
            base_url=self.base,
            headers=self._headers(),
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )
        return httpx.Client(**options) if sync else httpx.AsyncClient(**options)

    @staticmethod
    def _check(response: httpx.Response, action: str, allow: tuple[int, ...] = ()) -> dict:
        if response.status_code in allow:
            return {}
        if response.status_code >= 300:
            raise ProviderError(
                f"Qdrant {action} failed with HTTP {response.status_code}.",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Qdrant {action} returned an invalid response.") from exc

    async def _call(self, method: str, path: str, action: str, **kwargs) -> dict:
        try:
            async with self._client() as client:
                response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Qdrant {action} could not connect.", retryable=True) from exc
        return self._check(response, action)

    async def ensure_collection(self) -> None:
        path = f"/collections/{self.collection}"
        try:
            async with self._client() as client:
                response = await client.get(path)
                if response.status_code == 200:
                    return
                self._check(response, "collection lookup", allow=(404,))
                created = await client.put(
                    path,
                    json={
                        "vectors": {
                            "size": self.settings.embedding_dimensions,
                            "distance": "Cosine",
                        }
                    },
                )
                # A concurrent worker may have created it first.
                self._check(created, "collection creation", allow=(409,))
                for field in KEYWORD_FIELDS:
                    indexed = await client.put(
                        f"{path}/index",
                        params={"wait": "true"},
                        json={"field_name": field, "field_schema": "keyword"},
                    )
                    self._check(indexed, "payload index creation")
        except httpx.HTTPError as exc:
            raise ProviderError("Qdrant could not be reached.", retryable=True) from exc

    async def upsert_revision(
        self,
        *,
        document_id: str,
        revision_id: str,
        audience: str,
        project_id: str | None,
        points: list[tuple[str, list[float]]],
    ) -> None:
        """Write one batch of a revision and drop vectors of the document's older revisions."""
        if not points:
            return
        await self.ensure_collection()
        payload = {
            "audience": audience,
            "document_id": str(document_id),
            "revision_id": str(revision_id),
            "embedding_model": self.settings.embedding_model,
        }
        if project_id:
            payload["project_id"] = str(project_id)
        await self._call(
            "PUT",
            f"/collections/{self.collection}/points",
            "upsert",
            params={"wait": "true"},
            json={
                "points": [
                    {"id": str(chunk_id), "vector": vector, "payload": payload}
                    for chunk_id, vector in points
                ]
            },
        )
        await self._call(
            "POST",
            f"/collections/{self.collection}/points/delete",
            "stale revision cleanup",
            params={"wait": "true"},
            json={
                "filter": {
                    "must": [_match("document_id", document_id)],
                    "must_not": [_match("revision_id", revision_id)],
                }
            },
        )

    async def search(
        self, vector: list[float], *, audience: str, project_id: str | None, limit: int
    ) -> list[str]:
        """Return candidate chunk IDs, nearest first. Callers must re-authorize them."""
        must = [
            _match("audience", audience),
            _match("embedding_model", self.settings.embedding_model),
        ]
        if project_id:
            must.append(_match("project_id", project_id))
        try:
            async with self._client() as client:
                response = await client.post(
                    f"/collections/{self.collection}/points/query",
                    json={
                        "query": vector,
                        "filter": {"must": must},
                        "limit": max(1, min(int(limit), 200)),
                        "with_payload": False,
                        "with_vector": False,
                    },
                )
        except httpx.HTTPError as exc:
            raise ProviderError("Qdrant search could not connect.", retryable=True) from exc
        if response.status_code == 404:
            return []  # Nothing has been indexed for this profile yet.
        data = self._check(response, "search")
        try:
            return [str(point["id"]) for point in data["result"]["points"]]
        except (KeyError, TypeError) as exc:
            raise ProviderError("Qdrant search returned an invalid response.") from exc

    def delete_document(self, document_id: str) -> None:
        """Best-effort removal after a document is deleted; SQL already hides orphans."""
        try:
            with self._client(sync=True) as client:
                response = client.post(
                    f"/collections/{self.collection}/points/delete",
                    params={"wait": "true"},
                    json={"filter": {"must": [_match("document_id", document_id)]}},
                )
            self._check(response, "document cleanup", allow=(404,))
        except (httpx.HTTPError, ProviderError) as exc:
            log.warning("Qdrant cleanup skipped: error_type=%s", type(exc).__name__)
