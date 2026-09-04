from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from local_learning_coach.learning import LearningCoachService


def main() -> int:
    parser = argparse.ArgumentParser(description="Üç eğitim DOCX'i için güvenli yerel indeks oluştur")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(LearningCoachService().build_index(force=args.force), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
