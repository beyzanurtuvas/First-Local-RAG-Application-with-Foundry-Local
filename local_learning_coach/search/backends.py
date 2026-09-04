from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from local_learning_coach.config import Settings
from local_learning_coach.retrieval import HybridRetriever
from local_learning_coach.retrieval.hybrid import RetrievalResult


class SearchBackendError(RuntimeError):
    """Kullanıcıya gösterilebilir arama backend hatası."""


def _validate_local_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SearchBackendError("OpenSearch endpoint'i geçerli bir localhost HTTP/HTTPS adresi olmalıdır.")
    host = parsed.hostname.casefold()
    allowed = host == "localhost"
    try:
        allowed = allowed or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if not allowed:
        raise SearchBackendError("OpenSearch yalnızca localhost, 127.0.0.1 veya ::1 üzerinde kullanılabilir.")
    return endpoint.rstrip("/")


class LocalSearchBackend:
    name = "local"

    def __init__(self, settings: Settings):
        self.retriever = HybridRetriever(settings)

    def status(self) -> dict[str, Any]:
        return {"backend": self.name, "available": True, "message": "Dense + BM25 + RRF yerel backend etkin."}

    def search(self, query: str, **filters: Any) -> list[RetrievalResult]:
        return self.retriever.search(query, **filters)


class OpenSearchBackend:
    name = "opensearch"

    def __init__(self, settings: Settings):
        self.endpoint = _validate_local_endpoint(settings.opensearch_endpoint)
        self.index = settings.opensearch_index
        self.timeout = min(settings.web_timeout_seconds, 15)

    def _json_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.endpoint + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise SearchBackendError(
                "Yerel OpenSearch'e ulaşılamadı. Ayarlardan kullanıcı onayıyla yerel backende dönebilirsiniz."
            ) from exc

    def status(self) -> dict[str, Any]:
        try:
            payload = self._json_request("GET", "/")
            return {
                "backend": self.name,
                "available": True,
                "endpoint": self.endpoint,
                "cluster": payload.get("cluster_name", "localhost"),
            }
        except SearchBackendError as exc:
            return {"backend": self.name, "available": False, "endpoint": self.endpoint, "message": str(exc)}

    def search(self, query: str, **filters: Any) -> list[RetrievalResult]:
        top_k = int(filters.get("top_k", 6))
        clauses: list[dict[str, Any]] = []
        document_ids = filters.get("document_ids")
        if document_ids:
            clauses.append({"terms": {"document_id.keyword": list(document_ids)}})
        if filters.get("route"):
            clauses.append({"term": {"route.keyword": filters["route"]}})
        payload = {
            "size": top_k,
            "query": {
                "bool": {
                    "must": [{"multi_match": {"query": query, "fields": ["text^2", "heading^2", "body"]}}],
                    "filter": clauses,
                }
            },
        }
        response = self._json_request("POST", f"/{urllib.parse.quote(self.index)}/_search", payload)
        output: list[RetrievalResult] = []
        for rank, hit in enumerate(response.get("hits", {}).get("hits", []), start=1):
            chunk = hit.get("_source", {})
            if not chunk.get("id") or not chunk.get("document_id"):
                continue
            score = float(hit.get("_score") or 0.0)
            output.append(RetrievalResult(rank, score, score, 0.0, chunk))
        return output

    def index_chunks(self, chunks: list[dict[str, Any]]) -> int:
        if not chunks:
            return 0
        index_path = f"/{urllib.parse.quote(self.index)}"
        try:
            self._json_request(
                "PUT",
                index_path,
                {
                    "mappings": {
                        "properties": {
                            "id": {"type": "keyword"},
                            "document_id": {"type": "keyword"},
                            "route": {"type": "keyword"},
                            "text": {"type": "text"},
                            "heading": {"type": "text"},
                            "body": {"type": "text"},
                        }
                    }
                },
            )
        except SearchBackendError:
            # PUT mevcut bir indekste 400 dönebilir; varlığını açıkça doğrularız.
            self._json_request("GET", index_path)
        lines: list[str] = []
        for item in chunks:
            lines.append(json.dumps({"index": {"_index": self.index, "_id": item["id"]}}))
            lines.append(json.dumps(item, ensure_ascii=False))
        data = ("\n".join(lines) + "\n").encode("utf-8")
        request = urllib.request.Request(
            self.endpoint + "/_bulk", data=data, method="POST",
            headers={"Content-Type": "application/x-ndjson", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise SearchBackendError("Yerel OpenSearch indeksleme işlemi başarısız oldu.") from exc
        if result.get("errors"):
            raise SearchBackendError("OpenSearch bazı chunk kayıtlarını indeksleyemedi.")
        return len(chunks)


class SearchBackendRouter:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.search_backend not in {"local", "opensearch"}:
            raise SearchBackendError("LLC_SEARCH_BACKEND yalnızca local veya opensearch olabilir.")

    @property
    def backend(self) -> LocalSearchBackend | OpenSearchBackend:
        if self.settings.search_backend == "opensearch":
            return OpenSearchBackend(self.settings)
        return LocalSearchBackend(self.settings)

    def status(self) -> dict[str, Any]:
        return self.backend.status()

    def search(self, query: str, *, allow_local_fallback: bool = False, **filters: Any) -> list[RetrievalResult]:
        try:
            return self.backend.search(query, **filters)
        except SearchBackendError:
            if not allow_local_fallback or self.settings.search_backend == "local":
                raise
            return LocalSearchBackend(self.settings).search(query, **filters)
