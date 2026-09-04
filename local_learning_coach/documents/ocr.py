from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class OCRError(RuntimeError):
    """Kullanıcıya gösterilebilir yerel OCR hatası."""


@dataclass(frozen=True)
class OCRPage:
    page: int
    text: str
    confidence: float | None = None


class OCRAdapter(Protocol):
    name: str

    def available(self) -> bool: ...

    def extract_pdf(self, path: Path) -> list[OCRPage]: ...


class TesseractOCRAdapter:
    """pdftoppm + Tesseract kullanan, yalnızca yerel süreç çalıştıran OCR adapter'ı."""

    name = "Tesseract (yerel)"

    def __init__(self, command: str = "tesseract", language: str = "tur+eng"):
        self.command = command
        self.language = language

    def available(self) -> bool:
        return bool(shutil.which(self.command) and shutil.which("pdftoppm"))

    def extract_pdf(self, path: Path) -> list[OCRPage]:
        if not self.available():
            raise OCRError("OCR için yerel Tesseract ve Poppler pdftoppm kurulmalıdır; bulut OCR kullanılmadı.")
        temp_root = Path(tempfile.mkdtemp(prefix="llc-ocr-"))
        try:
            prefix = temp_root / "page"
            rendered = subprocess.run(
                ["pdftoppm", "-png", "-r", "220", str(path), str(prefix)],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if rendered.returncode != 0:
                raise OCRError("PDF sayfaları yerel OCR için görüntüye dönüştürülemedi.")
            pages: list[OCRPage] = []
            for page_number, image in enumerate(sorted(temp_root.glob("page-*.png")), start=1):
                result = subprocess.run(
                    [self.command, str(image), "stdout", "-l", self.language],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                if result.returncode != 0:
                    raise OCRError(f"Tesseract {page_number}. sayfayı okuyamadı.")
                pages.append(OCRPage(page_number, result.stdout.strip(), None))
            return pages
        except subprocess.TimeoutExpired as exc:
            raise OCRError("Yerel OCR zaman aşımına uğradı.") from exc
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

