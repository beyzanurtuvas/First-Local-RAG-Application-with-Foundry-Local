from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

from local_learning_coach.config import Settings
from local_learning_coach.database import Database
from local_learning_coach.database.db import utc_now
from local_learning_coach.indexing import IndexManager


class WebSourceError(ValueError):
    """Kullanıcıya gösterilebilir web kaynak güvenliği veya erişim hatası."""


@dataclass(frozen=True)
class DiscoveredSource:
    url: str
    title: str
    description: str = ""
    published_at: str | None = None


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int = 8) -> list[DiscoveredSource]: ...


def normalize_public_url(url: str, *, resolver=socket.getaddrinfo) -> str:
    value = url.strip()
    if not value:
        raise WebSourceError("URL boş olamaz.")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise WebSourceError("Yalnızca http:// ve https:// adresleri kabul edilir.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise WebSourceError("URL geçerli bir genel alan adı içermelidir; kullanıcı bilgisi kullanılamaz.")
    if parsed.port and parsed.port not in {80, 443}:
        raise WebSourceError("Web kaynağında yalnızca standart HTTP/HTTPS portları kullanılabilir.")
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise WebSourceError("Localhost veya yerel ağ adresleri web kaynağı olarak kullanılamaz.")
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
        addresses = [literal]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(item[4][0]) for item in resolver(hostname, parsed.port or 443)]
        except (OSError, ValueError) as exc:
            raise WebSourceError("Alan adı çözümlenemedi; ağ bağlantısını ve URL'yi kontrol edin.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise WebSourceError("Özel, loopback, link-local veya ayrılmış IP adreslerine erişim engellendi.")
    normalized_path = urllib.parse.quote(urllib.parse.unquote(parsed.path or "/"), safe="/%:@")
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)))
    host = hostname.encode("idna").decode("ascii")
    netloc = host + (f":{parsed.port}" if parsed.port else "")
    return urllib.parse.urlunsplit((parsed.scheme.casefold(), netloc, normalized_path, query, ""))


def classify_source(url: str) -> tuple[str, str]:
    host = (urllib.parse.urlsplit(url).hostname or "").casefold()
    path = urllib.parse.urlsplit(url).path.casefold()
    if host.endswith(".gov") or ".gov." in host or host.endswith(".bel.tr"):
        return "Resmî dokümantasyon", "yüksek — kamu/resmî alan adı"
    if host.endswith(".edu") or ".edu." in host or host.endswith(".ac.uk"):
        return "Üniversite veya eğitim kurumu", "yüksek — eğitim kurumu alan adı"
    if any(value in host for value in ("arxiv.org", "doi.org", "pubmed.ncbi.nlm.nih.gov")):
        return "Akademik yayın", "yüksek — akademik yayın altyapısı"
    if any(value in host for value in ("iso.org", "ietf.org", "w3.org", "rfc-editor.org")):
        return "Teknik standart", "yüksek — standart kuruluşu"
    if "docs." in host or "/docs" in path or "developer." in host:
        return "Resmî dokümantasyon", "orta-yüksek — dokümantasyon URL örüntüsü"
    if any(value in host for value in ("stackoverflow.com", "stackexchange.com", "reddit.com", "github.com")):
        return "Topluluk veya forum", "orta — topluluk kaynağı; doğrulama gerekir"
    if any(value in host for value in ("medium.com", "substack.com", "blogspot.com", "wordpress.com")):
        return "Kişisel blog", "düşük-orta — kişisel yayın; çapraz doğrulama gerekir"
    return "Güvenilir eğitim kaynağı", "orta — alan adı kuralı kesinlik sağlamaz"


class JsonSearchProvider:
    """Yapılandırılmış JSON arama sağlayıcısı; sağlayıcıya özel alanlar bu sınırda kalır."""

    name = "Yapılandırılmış JSON sağlayıcısı"

    def __init__(self, endpoint: str, api_key: str = "", timeout: int = 12):
        if not endpoint:
            raise WebSourceError("Web arama sağlayıcısı yapılandırılmamış; doğrudan URL ekleyin.")
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, limit: int = 8) -> list[DiscoveredSource]:
        if not query.strip():
            raise WebSourceError("Arama sorgusu boş olamaz.")
        separator = "&" if "?" in self.endpoint else "?"
        url = f"{self.endpoint}{separator}{urllib.parse.urlencode({'q': query, 'limit': min(max(limit, 1), 20)})}"
        headers = {"Accept": "application/json", "User-Agent": "LocalLearningCoach/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=self.timeout) as response:
                payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise WebSourceError("Web arama sağlayıcısına ulaşılamadı; doğrudan URL ekleyebilirsiniz.") from exc
        rows = payload.get("results", payload if isinstance(payload, list) else [])
        if not isinstance(rows, list):
            raise WebSourceError("Arama sağlayıcısı beklenen JSON sonuç listesini döndürmedi.")
        output: list[DiscoveredSource] = []
        for row in rows[:limit]:
            if not isinstance(row, dict) or not row.get("url"):
                continue
            output.append(
                DiscoveredSource(
                    url=str(row["url"]),
                    title=str(row.get("title") or row["url"]),
                    description=str(row.get("description") or row.get("snippet") or ""),
                    published_at=str(row["published_at"]) if row.get("published_at") else None,
                )
            )
        return output


class _ReadableHTML(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "aside", "form", "noscript", "svg"}
    BLOCK = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "code", "tr", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.current_tag = ""
        self.current: list[str] = []
        self.blocks: list[tuple[str, str]] = []
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK or tag == "title":
            self._flush()
            self.current_tag = tag
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.current.append(f" [URL: {href}] ")
        if tag == "br":
            self.current.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if not self.skip_depth and (tag in self.BLOCK or tag == "title"):
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.current.append(data)

    def _flush(self) -> None:
        value = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.current)).strip()
        value = re.sub(r"\n{3,}", "\n\n", value)
        if value:
            if self.current_tag == "title" and not self.title:
                self.title = value
            elif self.current_tag:
                self.blocks.append((self.current_tag, value))
        self.current = []
        self.current_tag = ""

    def cleaned_html(self) -> str:
        self._flush()
        lines = ["<!doctype html><html><head><meta charset=\"utf-8\">"]
        lines.append(f"<title>{html.escape(self.title)}</title></head><body>")
        seen: set[tuple[str, str]] = set()
        for tag, value in self.blocks:
            key = (tag, value.casefold())
            if key in seen:
                continue
            seen.add(key)
            safe_tag = tag if tag in self.BLOCK else "p"
            lines.append(f"<{safe_tag}>{html.escape(value)}</{safe_tag}>")
        lines.append("</body></html>")
        return "\n".join(lines)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, max_redirects: int):
        self.max_redirects = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        count = int(req.headers.get("X-LLC-Redirect-Count", "0")) + 1
        if count > self.max_redirects:
            raise WebSourceError("Web kaynağı yönlendirme sınırını aştı.")
        safe_url = normalize_public_url(urllib.parse.urljoin(req.full_url, newurl))
        request = super().redirect_request(req, fp, code, msg, headers, safe_url)
        if request:
            request.add_header("X-LLC-Redirect-Count", str(count))
        return request


class SafeWebFetcher:
    ALLOWED_TYPES = {"text/html", "application/xhtml+xml", "text/plain", "application/pdf"}

    def __init__(self, settings: Settings, *, opener: Any | None = None):
        self.settings = settings
        self.opener = opener or urllib.request.build_opener(_SafeRedirectHandler(settings.web_max_redirects))

    def _open(self, url: str):
        request = urllib.request.Request(
            normalize_public_url(url),
            headers={"User-Agent": "LocalLearningCoach/1.0 (+local educational source import)", "Accept": "text/html,text/plain,application/pdf"},
        )
        return self.opener.open(request, timeout=self.settings.web_timeout_seconds)

    def _robots_allowed(self, url: str) -> bool:
        parsed = urllib.parse.urlsplit(url)
        robots_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        try:
            with self._open(robots_url) as response:
                raw = response.read(min(self.settings.web_max_bytes, 512 * 1024)).decode("utf-8", errors="replace")
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(raw.splitlines())
            return parser.can_fetch("LocalLearningCoach", url)
        except WebSourceError:
            raise
        except Exception:
            return True

    def fetch(self, url: str, *, check_robots: bool = True) -> dict[str, Any]:
        normalized = normalize_public_url(url)
        if check_robots and not self._robots_allowed(normalized):
            raise WebSourceError("Site robots.txt kuralları bu sayfanın alınmasına izin vermiyor.")
        try:
            with self._open(normalized) as response:
                final_url = normalize_public_url(response.geturl())
                content_type = response.headers.get_content_type().casefold()
                if content_type not in self.ALLOWED_TYPES:
                    raise WebSourceError(f"Desteklenmeyen web içerik türü: {content_type}")
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > self.settings.web_max_bytes:
                    raise WebSourceError("Web içeriği güvenli indirme boyutu sınırını aşıyor.")
                content = response.read(self.settings.web_max_bytes + 1)
        except WebSourceError:
            raise
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise WebSourceError("Web kaynağına ulaşılamadı; ağ bağlantısını veya URL'yi kontrol edin.") from exc
        if len(content) > self.settings.web_max_bytes:
            raise WebSourceError("Web içeriği güvenli indirme boyutu sınırını aşıyor.")
        if content_type == "application/pdf":
            if not content.startswith(b"%PDF-"):
                raise WebSourceError("Sunucu PDF bildirdi ancak içerik imzası geçersiz.")
            title = Path(urllib.parse.urlsplit(final_url).path).stem or urllib.parse.urlsplit(final_url).hostname or "Web PDF"
            cleaned = content
            suffix = ".pdf"
        else:
            charset = response.headers.get_content_charset() if "response" in locals() else None
            text = content.decode(charset or "utf-8", errors="replace")
            if content_type in {"text/html", "application/xhtml+xml"}:
                parser = _ReadableHTML()
                parser.feed(text)
                cleaned = parser.cleaned_html().encode("utf-8")
                title = parser.title or urllib.parse.urlsplit(final_url).hostname or "Web kaynağı"
                suffix = ".html"
            else:
                cleaned = text.encode("utf-8")
                title = next((line.strip() for line in text.splitlines() if line.strip()), "Web metni")[:180]
                suffix = ".txt"
        if len(cleaned.strip()) < 40:
            raise WebSourceError("Web sayfasından yeterli okunabilir içerik çıkarılamadı.")
        return {
            "url": final_url,
            "content": cleaned,
            "content_type": content_type,
            "title": title[:180],
            "suffix": suffix,
            "sha256": hashlib.sha256(cleaned).hexdigest(),
            "accessed_at": datetime.now(timezone.utc).isoformat(),
        }


class WebSourceDiscoveryAdapter:
    def __init__(self, settings: Settings, database: Database, provider: SearchProvider | None = None):
        self.settings = settings
        self.database = database
        self.provider = provider

    @property
    def provider_status(self) -> dict[str, Any]:
        return {
            "configured": self.provider is not None,
            "provider": self.provider.name if self.provider else None,
            "manual_url_fallback": True,
        }

    def add_url(self, url: str, *, title: str = "", description: str = "") -> dict[str, Any]:
        normalized = normalize_public_url(url)
        source_type, reliability = classify_source(normalized)
        domain = urllib.parse.urlsplit(normalized).hostname or ""
        source_id = "web-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
        return self.database.save_web_source(
            {
                "id": source_id,
                "url": url,
                "normalized_url": normalized,
                "title": title.strip() or domain,
                "domain": domain,
                "description": description.strip(),
                "source_type": source_type,
                "reliability_class": reliability,
            }
        )

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if self.provider is None:
            raise WebSourceError("Arama sağlayıcısı yapılandırılmamış. Doğrudan URL ekleme kullanılabilir.")
        saved: list[dict[str, Any]] = []
        for item in self.provider.search(query, limit):
            saved.append(self.add_url(item.url, title=item.title, description=item.description))
        return saved

    def approve(self, source_id: str, *, fetcher: SafeWebFetcher | None = None, rebuild_index: bool = True) -> dict[str, Any]:
        source = self.database.web_source(source_id)
        if not source or source["status"] == "archived":
            raise WebSourceError("Onaylanacak etkin web kaynağı bulunamadı.")
        fetched = (fetcher or SafeWebFetcher(self.settings)).fetch(source["normalized_url"])
        duplicate = self.database.source_document_by_sha256(fetched["sha256"])
        snapshots = self.settings.data_dir / "web_snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        document_id = duplicate["id"] if duplicate else "webdoc-" + fetched["sha256"][:20]
        snapshot_path = (snapshots / f"{source_id}-{fetched['sha256'][:12]}{fetched['suffix']}").resolve()
        if snapshot_path.parent != snapshots.resolve():
            raise WebSourceError("Web snapshot için güvenli yerel hedef oluşturulamadı.")
        if not snapshot_path.exists():
            snapshot_path.write_bytes(fetched["content"])
        if not duplicate:
            self.database.register_uploaded_document(
                {
                    "id": document_id,
                    "filename": snapshot_path.name,
                    "original_filename": fetched["title"] + fetched["suffix"],
                    "path": str(snapshot_path),
                    "title": fetched["title"],
                    "route": "web-" + fetched["sha256"][:16],
                    "sha256": fetched["sha256"],
                    "mime_type": fetched["content_type"],
                    "source_kind": "web",
                    "source_url": fetched["url"],
                    "accessed_at": fetched["accessed_at"],
                }
            )
        result = self.database.approve_web_source(
            source_id,
            document_id=document_id,
            content_sha256=fetched["sha256"],
            snapshot_path=str(snapshot_path),
            mime_type=fetched["content_type"],
            accessed_at=fetched["accessed_at"],
        )
        if rebuild_index:
            IndexManager(self.settings, self.database).update_document(document_id)
        return result


def configured_web_adapter(settings: Settings, database: Database) -> WebSourceDiscoveryAdapter:
    provider = None
    if settings.web_search_endpoint:
        provider = JsonSearchProvider(
            settings.web_search_endpoint,
            settings.web_search_api_key,
            settings.web_timeout_seconds,
        )
    return WebSourceDiscoveryAdapter(settings, database, provider)
