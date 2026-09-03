from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from local_learning_coach.config import ConfigurationError, Settings
from local_learning_coach.database import DatabaseError
from local_learning_coach.foundry import FoundryUnavailable
from local_learning_coach.indexing import EmbeddingError, IndexError
from local_learning_coach.learning import LearningCoachService
from local_learning_coach.logging_utils import configure_logging


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python cli.py",
        description="Local Learning Coach — yerel belge tabanlı öğrenme koçu",
    )
    root.add_argument("--db", help="Varsayılan SQLite yolunu geçersiz kıl")
    commands = root.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="Migration, kaynak kontrolü, indeks ve Foundry durumunu çalıştır")
    setup.add_argument("--no-index", action="store_true", help="İndeks oluşturmadan yalnızca yerel kurulumu hazırla")
    setup.add_argument("--force", action="store_true", help="İndeksi zorla yeniden oluştur")

    profile = commands.add_parser("profile", help="Profil işlemleri")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    create = profile_commands.add_parser("create", help="Etkileşimli profil oluştur")
    create.add_argument("--name")
    create.add_argument("--goal")
    create.add_argument("--route", choices=("data-analysis", "machine-learning", "quantum"))
    create.add_argument("--weekly-days", type=int)
    create.add_argument("--daily-minutes", type=int)
    create.add_argument("--days", help="Virgülle ayrılmış çalışma günleri")
    create.add_argument("--start-date")
    create.add_argument("--target-end-date")
    create.add_argument("--declared-level", choices=("Başlangıç", "Temel", "Orta", "İleri"))
    profile_commands.add_parser("show", help="Son profili göster")
    export = profile_commands.add_parser("export", help="Profili yerel JSON dosyasına aktar")
    export.add_argument("--output", required=True)
    backup = profile_commands.add_parser("backup", help="SQLite veritabanını yerel olarak yedekle")
    backup.add_argument("--output", required=True)

    assess = commands.add_parser("assess", help="Deterministic başlangıç değerlendirmesi")
    assess.add_argument("--route", required=True, choices=("data-analysis", "machine-learning", "quantum"))
    assess.add_argument("--skip", action="store_true", help="Değerlendirmeyi atla ve kullanıcı beyanını kullan")

    plan = commands.add_parser("plan", help="Kişisel plan işlemleri")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    plan_commands.add_parser("generate", help="Belge görevlerinden kapasiteye uygun plan oluştur")

    commands.add_parser("today", help="Bugünün görevlerini listele")
    task = commands.add_parser("task", help="Görev durumunu yönet")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    start = task_commands.add_parser("start", help="Ön koşulları tamamlanan görevi başlat")
    start.add_argument("task_id")
    complete = task_commands.add_parser("complete", help="Görevi gerçek süre ve zorlukla tamamla")
    complete.add_argument("task_id")
    complete.add_argument("--minutes", required=True, type=int)
    complete.add_argument("--difficulty", required=True, type=int, choices=range(1, 6))
    complete.add_argument("--quiz-score", type=float)
    complete.add_argument("--note")
    postpone = task_commands.add_parser("postpone", help="Görevi ve bağımlı açık görevleri yeniden planla")
    postpone.add_argument("task_id")
    review = task_commands.add_parser("review", help="Görevi tekrar için işaretle veya tekrar oturumu oluştur")
    review.add_argument("task_id")

    commands.add_parser("progress", help="Toplam ilerleme ve çalışma süresini göster")
    commands.add_parser("weekly-report", help="Haftalık performans raporu oluştur")
    ask = commands.add_parser("ask", help="Foundry Local ile kaynaklı cevap üret")
    ask.add_argument("question")
    add_filters(ask)
    retrieve = commands.add_parser("retrieve", help="LLM olmadan hibrit retrieval sonuçlarını göster")
    retrieve.add_argument("query")
    add_filters(retrieve)
    retrieve.add_argument("--top-k", type=int, default=6)

    index = commands.add_parser("index", help="RAG indeksini yönet")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    build = index_commands.add_parser("build", help="Üç DOCX için güvenli indeks oluştur")
    build.add_argument("--force", action="store_true")
    index_commands.add_parser("status", help="Manifest ve bayatlık durumunu göster")
    commands.add_parser("interactive", help="Basit etkileşimli menüyü aç")
    commands.add_parser("routes", help="Belgeden çıkarılan üç rotayı karşılaştır")
    return root


def add_filters(command: argparse.ArgumentParser) -> None:
    command.add_argument("--document", help="Tam kaynak DOCX dosya adı")
    command.add_argument("--route", choices=("data-analysis", "machine-learning", "quantum"))
    command.add_argument("--phase")
    command.add_argument("--week")
    command.add_argument("--day")


def prompt(value, message: str, default: str | None = None):
    if value is not None:
        return value
    suffix = f" [{default}]" if default is not None else ""
    answer = input(f"{message}{suffix}: ").strip()
    return answer or default


def create_profile(service: LearningCoachService, args: argparse.Namespace) -> None:
    today = date.today()
    name = prompt(args.name, "Profil adı")
    goal = prompt(args.goal, "Ana öğrenme amacı")
    route = prompt(args.route, "Rota (data-analysis/machine-learning/quantum)", "data-analysis")
    weekly_days = int(prompt(args.weekly_days, "Haftada çalışma günü", "5"))
    daily_minutes = int(prompt(args.daily_minutes, "Günlük dakika", "60"))
    days_raw = prompt(args.days, "Çalışma günleri (virgülle)", "Pazartesi,Salı,Çarşamba,Perşembe,Cuma")
    start_date = prompt(args.start_date, "Başlangıç tarihi (YYYY-MM-DD)", today.isoformat())
    target_end = prompt(args.target_end_date, "Hedef bitiş tarihi (YYYY-MM-DD)", (today + timedelta(days=60)).isoformat())
    declared = prompt(args.declared_level, "Beyan edilen seviye (Başlangıç/Temel/Orta/İleri)", "Başlangıç")
    skills = {
        "python": prompt(None, "Python seviyesi", declared),
        "pandas": prompt(None, "Pandas deneyimi", "Yok"),
        "jupyter": prompt(None, "Jupyter deneyimi", "Yok"),
        "machine_learning": prompt(None, "Makine öğrenmesi bilgisi", "Yok"),
        "pytorch": prompt(None, "PyTorch deneyimi", "Yok"),
        "quantum_computing": prompt(None, "Kuantum hesaplama bilgisi", "Yok"),
        "qsharp": prompt(None, "Q# deneyimi", "Yok"),
    }
    data = {
        "name": name,
        "goal": goal,
        "preferred_route": route,
        "weekly_days": weekly_days,
        "daily_minutes": daily_minutes,
        "preferred_days": [item.strip() for item in days_raw.split(",") if item.strip()],
        "start_date": start_date,
        "target_end_date": target_end,
        "theory_practice_preference": prompt(None, "Teori/uygulama tercihi", "Dengeli"),
        "completed_topics": [item.strip() for item in prompt(None, "Tamamlanan konular (virgülle)", "").split(",") if item.strip()],
        "difficult_topics": [item.strip() for item in prompt(None, "Zorlanılan konular (virgülle)", "").split(",") if item.strip()],
        "review_preference": prompt(None, "Tekrar tercihi", "Haftalık"),
        "declared_level": declared,
    }
    profile_id = service.create_profile(data, skills)
    print(f"Profil oluşturuldu: {profile_id}")


def print_tasks(tasks: list[dict]) -> None:
    if not tasks:
        print("Planlanmış görev yok.")
        return
    for task in tasks:
        estimate = "tahmini" if task.get("estimated_is_system") else "belgeden"
        print(
            f"{task['id']} | {task.get('scheduled_date') or '-'} | {task['status']} | "
            f"{task['estimated_minutes']} dk ({estimate}) | {task['title']}"
        )
        print(f"  Kaynak: {task['source_document']} > {task['source_section']}")


def filters(args: argparse.Namespace) -> dict:
    return {
        key: value
        for key in ("document", "route", "phase", "week", "day")
        if (value := getattr(args, key, None)) is not None
    }


def interactive(service: LearningCoachService) -> None:
    print("Local Learning Coach etkileşimli modu. 'yardım' veya 'çıkış' yazın.")
    while True:
        command = input("koç> ").strip()
        if command in {"çıkış", "cikis", "exit", "quit"}:
            return
        if command in {"yardım", "yardim", "help"}:
            print("Komutlar: bugün, ilerleme, rapor, sor <metin>, bul <metin>, çıkış")
        elif command in {"bugün", "bugun"}:
            print_tasks(service.today())
        elif command == "ilerleme":
            print(json.dumps(service.progress(), ensure_ascii=False, indent=2))
        elif command == "rapor":
            print(json.dumps(service.weekly_report(), ensure_ascii=False, indent=2))
        elif command.startswith("bul "):
            for result in service.retrieve(command[4:]):
                print(f"[{result.rank}] {result.chunk['source']} > {result.chunk['section_path']}")
        elif command.startswith("sor "):
            stream, _ = service.ask(command[4:])
            for piece in stream:
                print(piece, end="", flush=True)
            print()
        else:
            print("Bilinmeyen komut. 'yardım' yazın.")


def run(args: argparse.Namespace) -> int:
    settings = Settings.load()
    if args.db:
        settings = Settings(**{**settings.__dict__, "db_path": Path(args.db).resolve()})
    configure_logging(settings.log_path)
    service = LearningCoachService(settings)
    service.database.migrate()

    if args.command == "setup":
        print(json.dumps(service.setup(build_index=not args.no_index, force_index=args.force), ensure_ascii=False, indent=2))
    elif args.command == "profile" and args.profile_command == "create":
        create_profile(service, args)
    elif args.command == "profile" and args.profile_command == "show":
        print(json.dumps(service.profile(), ensure_ascii=False, indent=2))
    elif args.command == "profile" and args.profile_command == "export":
        profile = service.profile()
        print(service.database.export_profile(profile["id"], Path(args.output).resolve()))
    elif args.command == "profile" and args.profile_command == "backup":
        print(service.database.backup(Path(args.output).resolve()))
    elif args.command == "assess":
        profile = service.profile()
        if args.skip:
            print(json.dumps(service.assessment.skip(profile["id"], args.route, profile["declared_level"]), ensure_ascii=False, indent=2))
        else:
            answers = {}
            for question in service.assessment.questions(args.route):
                print(f"\n{question.question}")
                for index, option in enumerate(question.options, 1):
                    print(f"  {index}. {option}")
                choice = int(input("Yanıt numarası: "))
                if choice < 1 or choice > len(question.options):
                    raise ValueError("Yanıt numarası seçenek aralığında olmalıdır.")
                answers[question.id] = question.options[choice - 1]
            print(json.dumps(service.assessment.grade(profile["id"], args.route, answers), ensure_ascii=False, indent=2))
    elif args.command == "plan":
        print(json.dumps(service.generate_plan(), ensure_ascii=False, indent=2))
    elif args.command == "today":
        print_tasks(service.today())
    elif args.command == "task":
        if args.task_command == "start":
            print(json.dumps(service.start_task(args.task_id), ensure_ascii=False, indent=2))
        elif args.task_command == "complete":
            print(json.dumps(service.complete_task(args.task_id, args.minutes, args.difficulty, quiz_score=args.quiz_score, note=args.note), ensure_ascii=False, indent=2))
        elif args.task_command == "postpone":
            print(json.dumps(service.postpone_task(args.task_id), ensure_ascii=False, indent=2))
        elif args.task_command == "review":
            print(json.dumps(service.review_task(args.task_id), ensure_ascii=False, indent=2))
    elif args.command == "progress":
        print(json.dumps(service.progress(), ensure_ascii=False, indent=2))
    elif args.command == "weekly-report":
        print(json.dumps(service.weekly_report(), ensure_ascii=False, indent=2))
    elif args.command == "retrieve":
        for result in service.retrieve(args.query, top_k=args.top_k, **filters(args)):
            print(f"[{result.rank}] RRF={result.rrf_score:.5f} dense={result.dense_score:.4f} BM25={result.bm25_score:.4f}")
            print(f"  {result.chunk['source']} > {result.chunk['section_path']}")
            print(f"  {result.chunk['body'][:500]}")
    elif args.command == "ask":
        stream, results = service.ask(args.question, **filters(args))
        for piece in stream:
            print(piece, end="", flush=True)
        print("\n\nKaynak parçaları:")
        for result in results:
            print(f"[{result.rank}] {result.chunk['source']} > {result.chunk['section_path']}")
    elif args.command == "index" and args.index_command == "build":
        print(json.dumps(service.build_index(force=args.force), ensure_ascii=False, indent=2))
    elif args.command == "index" and args.index_command == "status":
        print(json.dumps(service.index_status(), ensure_ascii=False, indent=2))
    elif args.command == "interactive":
        interactive(service)
    elif args.command == "routes":
        print(json.dumps(service.route_comparison(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (ConfigurationError, DatabaseError, EmbeddingError, IndexError, FoundryUnavailable, ValueError) as exc:
        logging.exception("Kullanıcı işlemi başarısız")
        print(f"Hata: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nİşlem kullanıcı tarafından durduruldu.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
