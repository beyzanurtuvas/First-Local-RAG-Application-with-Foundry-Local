from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from local_learning_coach.config import Settings


class FoundryUnavailable(RuntimeError):
    """Foundry Local kurulumu, servisi veya modeli kullanılamıyor."""


class FoundryAdapter:
    """Yalnızca localhost üzerindeki OpenAI-uyumlu Foundry Local endpoint'ine bağlanır."""

    def __init__(self, settings: Settings):
        self.settings = settings
        parsed = urllib.parse.urlparse(settings.foundry_endpoint)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Güvenlik nedeniyle Foundry endpoint'i yalnızca localhost olabilir.")

    @staticmethod
    def cli_status() -> dict[str, Any]:
        executable = shutil.which("foundry")
        if not executable:
            return {
                "installed": False,
                "message": "Foundry Local CLI bulunamadı. Windows için resmi komut: winget install Microsoft.FoundryLocal",
            }
        result: dict[str, Any] = {"installed": True, "path": executable}
        try:
            version = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, timeout=10, check=False
            )
            result["version"] = (version.stdout or version.stderr).strip()
            models = subprocess.run(
                [executable, "model", "list", "--cached"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            result["cached_models"] = models.stdout.strip()
            result["model_list_exit_code"] = models.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["message"] = f"CLI bulundu ancak sorgulanamadı: {exc}"
        return result

    def health(self) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.settings.foundry_endpoint}/models",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            models = [item.get("id", "") for item in payload.get("data", [])]
            selected = self.settings.foundry_model or (models[0] if models else "")
            return {
                "available": bool(models),
                "endpoint": self.settings.foundry_endpoint,
                "models": models,
                "selected_model": selected,
                "method": "OpenAI uyumlu yerel HTTP endpoint'i",
            }
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return {
                "available": False,
                "endpoint": self.settings.foundry_endpoint,
                "models": [],
                "selected_model": self.settings.foundry_model,
                "message": (
                    "Foundry Local servisine bağlanılamadı. CLI/SDK ile yerel web servisini başlatın ve "
                    f"LLC_FOUNDRY_ENDPOINT ayarını doğrulayın: {exc}"
                ),
            }

    def stream_chat(self, messages: list[dict[str, str]], *, max_tokens: int = 700) -> Iterator[str]:
        health = self.health()
        if not health["available"]:
            raise FoundryUnavailable(health.get("message", "Foundry Local modeli kullanılamıyor."))
        model = self.settings.foundry_model or health["selected_model"]
        if model not in health["models"]:
            raise FoundryUnavailable(
                f"Yapılandırılan Foundry modeli yüklü değil: {model}. Kullanılabilir modeller: {', '.join(health['models'])}"
            )
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": self.settings.foundry_temperature,
                "max_tokens": max_tokens,
                "stream": True,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.settings.foundry_endpoint}/chat/completions",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.foundry_timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    delta = event.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield delta
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FoundryUnavailable(f"Foundry Local yanıt üretimi başarısız: {exc}") from exc

    def structured_json(
        self,
        messages: list[dict[str, str]],
        *,
        required_keys: set[str],
        max_tokens: int = 1400,
    ) -> dict[str, Any]:
        """Yerel model JSON çıktısını ayrıştırır; doğrulanmayan çıktı çağırana dönmez."""
        content = "".join(self.stream_chat(messages, max_tokens=max_tokens)).strip()
        if content.startswith("```"):
            content = content.removeprefix("```json").removeprefix("```")
            if content.endswith("```"):
                content = content[:-3]
        try:
            payload = json.loads(content.strip())
        except json.JSONDecodeError as exc:
            raise FoundryUnavailable("Foundry Local geçerli JSON üretmedi; deterministik sonuç korunuyor.") from exc
        if not isinstance(payload, dict) or not required_keys.issubset(payload):
            raise FoundryUnavailable(
                "Foundry Local JSON çıktısı zorunlu şema alanlarını taşımıyor; veritabanına yazılmadı."
            )
        return payload
