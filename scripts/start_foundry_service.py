from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Foundry Local SDK ile modeli yükle ve OpenAI-uyumlu localhost servisini başlat"
    )
    parser.add_argument("--model", default="qwen2.5-0.5b", help="Foundry Local model alias'ı")
    args = parser.parse_args()
    try:
        from foundry_local_sdk import Configuration, FoundryLocalManager
    except ImportError:
        print(
            "Foundry Local Windows SDK kurulu değil. Çalıştırın: "
            ".venv\\Scripts\\python -m pip install foundry-local-sdk-winml",
            file=sys.stderr,
        )
        return 2

    config = Configuration(app_name="local_learning_coach")
    FoundryLocalManager.initialize(config)
    manager = FoundryLocalManager.instance
    model = None
    try:
        print("Donanım yürütme sağlayıcıları doğrulanıyor...")
        manager.download_and_register_eps(
            progress_callback=lambda name, percent: print(f"\r{name}: %{percent:5.1f}", end="", flush=True)
        )
        print()
        model = manager.catalog.get_model(args.model)
        print(f"Model indiriliyor/doğrulanıyor: {args.model}")
        model.download(lambda percent: print(f"\rModel: %{percent:5.1f}", end="", flush=True))
        print()
        model.load()
        manager.start_web_service()
        base_url = f"{manager.urls[0].rstrip('/')}/v1"
        print(f"Foundry Local hazır: {base_url}")
        print(f"LLC_FOUNDRY_MODEL için gerçek model kimliği: {model.id}")
        print("Servisi durdurmak için Ctrl+C kullanın.")
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nFoundry Local servisi durduruluyor...")
    finally:
        try:
            manager.stop_web_service()
        except Exception:
            pass
        if model is not None:
            try:
                model.unload()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
