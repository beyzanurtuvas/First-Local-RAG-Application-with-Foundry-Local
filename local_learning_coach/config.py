from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SOURCE_DIR = Path(
    r"C:\Users\MSI-CYBORG\Desktop\Building Your First Local RAG Application with Foundry Local"
)

DOCUMENT_DEFINITIONS = (
    (
        "machine-learning",
        "Makine Öğrenmesi ve PyTorch",
        "One-Month Machine Learning Plan.docx",
    ),
    (
        "data-analysis",
        "Python ve Veri Analizi",
        "Python Pandas Titanic Project Plan.docx",
    ),
    (
        "quantum",
        "Kuantum Programlama ve Q#",
        "Quantum Programming Month Plan.docx",
    ),
)


class ConfigurationError(RuntimeError):
    """Kullanıcıya gösterilebilir yapılandırma hatası."""


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    db_path: Path
    index_dir: Path
    log_path: Path
    source_paths: tuple[Path, ...]
    embedding_backend: str
    embedding_model: str
    chunk_max_chars: int
    chunk_overlap_paragraphs: int
    foundry_endpoint: str
    foundry_model: str
    foundry_temperature: float
    foundry_timeout_seconds: int
    max_context_chars: int
    search_backend: str = "local"
    opensearch_endpoint: str = "http://127.0.0.1:9200"
    opensearch_index: str = "local-learning-coach"
    web_search_endpoint: str = ""
    web_search_api_key: str = ""
    web_timeout_seconds: int = 12
    web_max_bytes: int = 5 * 1024 * 1024
    web_max_redirects: int = 4
    ocr_enabled: bool = False
    ocr_command: str = "tesseract"
    code_runner_enabled: bool = False
    code_runner_timeout_seconds: int = 5
    code_runner_max_output_bytes: int = 64 * 1024
    code_runner_max_source_bytes: int = 128 * 1024
    cache_max_entries: int = 10_000

    @classmethod
    def load(cls, *, project_root: Path | None = None) -> "Settings":
        root = (project_root or PROJECT_ROOT).resolve()
        _load_dotenv(root / ".env")
        data_dir = Path(os.getenv("LLC_DATA_DIR", str(root / "data"))).resolve()
        source_dir = Path(os.getenv("LLC_SOURCE_DIR", str(DEFAULT_SOURCE_DIR)))
        configured = os.getenv("LLC_SOURCE_DOCS")
        if configured:
            source_paths = tuple(Path(item.strip()) for item in configured.split(";") if item.strip())
        else:
            source_paths = tuple(source_dir / filename for _, _, filename in DOCUMENT_DEFINITIONS)
        return cls(
            project_root=root,
            data_dir=data_dir,
            db_path=Path(os.getenv("LLC_DB_PATH", str(data_dir / "app.db"))).resolve(),
            index_dir=Path(os.getenv("LLC_INDEX_DIR", str(data_dir / "index"))).resolve(),
            log_path=Path(os.getenv("LLC_LOG_PATH", str(root / "logs" / "app.log"))).resolve(),
            source_paths=source_paths,
            embedding_backend=os.getenv("LLC_EMBEDDING_BACKEND", "fastembed").strip().lower(),
            embedding_model=os.getenv(
                "LLC_EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ).strip(),
            chunk_max_chars=int(os.getenv("LLC_CHUNK_MAX_CHARS", "2400")),
            chunk_overlap_paragraphs=int(os.getenv("LLC_CHUNK_OVERLAP_PARAGRAPHS", "1")),
            foundry_endpoint=os.getenv("LLC_FOUNDRY_ENDPOINT", "http://127.0.0.1:5272/v1").rstrip("/"),
            foundry_model=os.getenv("LLC_FOUNDRY_MODEL", "").strip(),
            foundry_temperature=float(os.getenv("LLC_FOUNDRY_TEMPERATURE", "0.0")),
            foundry_timeout_seconds=int(os.getenv("LLC_FOUNDRY_TIMEOUT_SECONDS", "30")),
            max_context_chars=int(os.getenv("LLC_MAX_CONTEXT_CHARS", "12000")),
            search_backend=os.getenv("LLC_SEARCH_BACKEND", "local").strip().lower(),
            opensearch_endpoint=os.getenv("LLC_OPENSEARCH_ENDPOINT", "http://127.0.0.1:9200").rstrip("/"),
            opensearch_index=os.getenv("LLC_OPENSEARCH_INDEX", "local-learning-coach").strip(),
            web_search_endpoint=os.getenv("LLC_WEB_SEARCH_ENDPOINT", "").strip(),
            web_search_api_key=os.getenv("LLC_WEB_SEARCH_API_KEY", "").strip(),
            web_timeout_seconds=int(os.getenv("LLC_WEB_TIMEOUT_SECONDS", "12")),
            web_max_bytes=int(os.getenv("LLC_WEB_MAX_BYTES", str(5 * 1024 * 1024))),
            web_max_redirects=int(os.getenv("LLC_WEB_MAX_REDIRECTS", "4")),
            ocr_enabled=os.getenv("LLC_OCR_ENABLED", "0").strip().lower() in {"1", "true", "yes"},
            ocr_command=os.getenv("LLC_OCR_COMMAND", "tesseract").strip(),
            code_runner_enabled=os.getenv("LLC_CODE_RUNNER_ENABLED", "0").strip().lower() in {"1", "true", "yes"},
            code_runner_timeout_seconds=int(os.getenv("LLC_CODE_RUNNER_TIMEOUT_SECONDS", "5")),
            code_runner_max_output_bytes=int(os.getenv("LLC_CODE_RUNNER_MAX_OUTPUT_BYTES", str(64 * 1024))),
            code_runner_max_source_bytes=int(os.getenv("LLC_CODE_RUNNER_MAX_SOURCE_BYTES", str(128 * 1024))),
            cache_max_entries=int(os.getenv("LLC_CACHE_MAX_ENTRIES", "10000")),
        )

    def ensure_runtime_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def validate_sources(self) -> None:
        allowed_names = {item[2] for item in DOCUMENT_DEFINITIONS}
        configured_names = {path.name for path in self.source_paths}
        extras = configured_names - allowed_names
        if extras:
            raise ConfigurationError(
                "Varsayılan bilgi tabanına yalnızca izin verilen üç DOCX eklenebilir: "
                + ", ".join(sorted(extras))
            )
        missing = [str(path) for path in self.source_paths if not path.is_file()]
        if missing:
            raise ConfigurationError(
                "Kaynak eğitim belgesi bulunamadı. LLC_SOURCE_DIR veya LLC_SOURCE_DOCS ayarını kontrol edin:\n- "
                + "\n- ".join(missing)
            )
        if len(self.source_paths) != 3 or configured_names != allowed_names:
            raise ConfigurationError("Bilgi tabanı tam olarak tanımlı üç eğitim belgesini içermelidir.")


def route_for_filename(filename: str) -> tuple[str, str]:
    for slug, title, expected in DOCUMENT_DEFINITIONS:
        if filename == expected:
            return slug, title
    raise ConfigurationError(f"İzin verilmeyen kaynak belge: {filename}")
