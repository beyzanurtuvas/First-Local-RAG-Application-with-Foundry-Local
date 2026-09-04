from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any

from local_learning_coach.config import Settings
from local_learning_coach.database import Database
from local_learning_coach.database.db import utc_now
from local_learning_coach.foundry import FoundryAdapter
from local_learning_coach.interview.runner import CodeRunner, configured_code_runner
from local_learning_coach.interview.taxonomy import TAXONOMY_VERSION, TRACKS, competencies_for_track


QUESTION_BANK_VERSION = "interview-bank-v1"
SCORING_VERSION = "interview-scoring-v1"
PLANNER_VERSION = "interview-planner-v1"
RUBRIC_VERSION = "interview-rubric-v1"
FOUNDRY_PROMPT_VERSION = "interview-foundry-prompt-v1"


def _stable_id(prefix: str, *parts: object, length: int = 20) -> str:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:length]
    return f"{prefix}-{digest}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def _question_templates(track_id: str) -> list[dict[str, Any]]:
    prefix = "bp" if track_id == "backend-python" else "se"

    def comp(slug: str, fallback: str = "programming") -> str:
        if track_id == "backend-python":
            return f"bp-{slug}"
        core = {item["id"].removeprefix("se-") for item in competencies_for_track(track_id)}
        return f"se-{slug if slug in core else fallback}"

    rows = [
        ("technical", "data-structures", "Bir hash tablosunda anahtar aramanın ortalama zaman karmaşıklığı nedir?",
         ["O(1)", "O(log n)", "O(n)", "O(n²)"], "O(1)", ["hash", "ortalama"], 90),
        ("technical", "algorithms", "Sıralı bir dizide binary search zaman karmaşıklığı nedir?",
         ["O(log n)", "O(1)", "O(n)", "O(n log n)"], "O(log n)", ["binary", "log"], 90),
        ("technical", "oop", "Nesne davranışlarını kalıtım yerine bir araya getirmeyi hangi yaklaşım anlatır?",
         ["Bileşim", "Global durum", "Kopyalama", "Polling"], "Bileşim", ["bileşim"], 90),
        ("technical", "sql", "İki tablodaki eşleşen kayıtları birleştiren temel SQL işlemi hangisidir?",
         ["JOIN", "DROP", "VACUUM", "GRANT"], "JOIN", ["join"], 90),
        ("technical", "http-rest", "Başarılı bir kaynak oluşturma isteği için yaygın HTTP durum kodu hangisidir?",
         ["201", "204", "301", "404"], "201", ["http", "201"], 90),
        ("technical", "testing", "Bir fonksiyonu dış bağımlılıklardan izole eden test türü hangisidir?",
         ["Birim testi", "Yük testi", "Kabul testi", "Canary dağıtımı"], "Birim testi", ["birim", "izole"], 90),
        ("technical", "git", "Yeni bir geliştirmeyi ana daldan ayırmak için hangi Git kavramı kullanılır?",
         ["Branch", "Tag silme", "Stash drop", "Rebase --abort"], "Branch", ["branch"], 90),
        ("technical", "debugging", "Tekrarlanabilir bir hatayı çözmenin ilk güvenilir adımı nedir?",
         ["Hatayı kontrollü biçimde yeniden üretmek", "Rastgele kod silmek", "Logları kapatmak", "Bağımlılıkları gizlemek"],
         "Hatayı kontrollü biçimde yeniden üretmek", ["yeniden", "üret"], 100),
        ("technical", "security", "Bir API’de kullanıcının belirli kaynağa erişip erişemeyeceğini hangi kontrol belirler?",
         ["Authorization", "Serialization", "Caching", "Compression"], "Authorization", ["authorization", "yetki"], 100),
        ("technical", "python", "Python sözlüklerinde anahtarla erişimin ortalama karmaşıklığı nedir?",
         ["O(1)", "O(log n)", "O(n)", "O(n²)"], "O(1)", ["dict", "hash"], 90),
        ("algorithm", "algorithms", "Bir listedeki tekrar eden ilk değeri verimli biçimde nasıl bulursunuz? Yaklaşımı ve karmaşıklığı açıklayın.",
         [], None, ["set", "hash", "o(n)", "karmaşıklık"], 480),
        ("algorithm", "data-structures", "Parantezlerin dengeli olup olmadığını kontrol eden yaklaşımı ve kullanılacak veri yapısını açıklayın.",
         [], None, ["stack", "yığın", "push", "pop"], 480),
        ("coding", "python", "Python ile add(a, b) fonksiyonunu yazın. Fonksiyon iki sayının toplamını döndürmelidir.",
         [], None, ["def", "return"], 420),
        ("debugging", "debugging", "Python'da `def add_item(x, items=[]): items.append(x); return items` kodundaki problemi ve düzeltmeyi açıklayın.",
         [], None, ["mutable", "varsayılan", "none"], 300),
        ("sql", "sql", "users(id,name) ve orders(id,user_id,total) tablolarında her kullanıcının toplam sipariş tutarını döndüren sorguyu açıklayın.",
         [], None, ["join", "group by", "sum"], 420),
        ("system-design", "backend-design" if track_id == "backend-python" else "architecture", "Junior seviyede bir URL kısaltma servisinin API, veri modeli, cache, hata ve güvenlik bileşenlerini tasarlayın.",
         [], None, ["api", "veri", "cache", "hata", "güven"], 900),
        ("behavioral", "behavioral", "Bir ekip çalışmasında karşılaştığınız teknik anlaşmazlığı STAR yapısıyla anlatın.",
         [], None, ["situation", "task", "action", "result"], 420),
        ("behavioral", "behavioral", "Bir hatadan ne öğrendiğinizi somut bir örnek ve sonuçla STAR yapısında anlatın.",
         [], None, ["situation", "task", "action", "result"], 420),
        ("technical", "sql", "Bir transaction'ın ya tamamen uygulanması ya da hiç uygulanmaması hangi ACID özelliğidir?",
         ["Atomicity", "Consistency", "Isolation", "Durability"], "Atomicity", ["atomicity"], 90),
        ("technical", "networks", "Bir alan adını IP adresine çözümleyen temel servis hangisidir?",
         ["DNS", "HTTP", "TLS", "SSH"], "DNS", ["dns"], 90),
        ("code-review", "clean-code", "Uzun ve birden fazla sorumluluğu olan bir fonksiyonu nasıl inceler ve iyileştirirsiniz?",
         [], None, ["sorumluluk", "test", "refactor"], 360),
        ("short-technical", "testing", "Birim testi ile entegrasyon testinin temel farkını kısa biçimde açıklayın.",
         [], None, ["izole", "bağımlılık", "birlikte"], 180),
    ]
    output = []
    for index, (kind, slug, prompt, options, answer, keywords, seconds) in enumerate(rows, start=1):
        output.append({
            "id": f"{prefix}-q{index:02d}",
            "track_id": track_id,
            "competency_id": comp(slug),
            "target_level": "intern-junior",
            "question_type": kind,
            "prompt": prompt,
            "options": options,
            "correct_answer": answer,
            "rubric": {"expected_keywords": keywords, "rubric_version": RUBRIC_VERSION},
            "estimated_seconds": seconds,
            "difficulty": "temel" if index <= 10 else "orta",
            "prerequisite_ids": [],
            "source_id": f"system:{QUESTION_BANK_VERSION}",
            "source_chunk_id": None,
            "source_locator": "Yerel ve sürümlü teknik mülakat soru bankası",
            "generation_method": "sistem tarafından oluşturuldu",
            "bank_version": QUESTION_BANK_VERSION,
        })
    return output


class InterviewCoach:
    def __init__(self, database: Database, settings: Settings, runner: CodeRunner | None = None):
        self.database = database
        self.settings = settings
        self.runner = runner or configured_code_runner(settings)

    def seed(self) -> dict[str, int]:
        self.database.migrate()
        now = utc_now()
        track_count = 0
        competency_count = 0
        question_count = 0
        with self.database.connection() as conn:
            for track in TRACKS:
                conn.execute(
                    """INSERT INTO interview_tracks(id,title,description,support_level,taxonomy_version,updated_at)
                       VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                       description=excluded.description,support_level=excluded.support_level,
                       taxonomy_version=excluded.taxonomy_version,updated_at=excluded.updated_at""",
                    (track["id"], track["title"], track["description"], track["support_level"], TAXONOMY_VERSION, now),
                )
                track_count += 1
                for competency in competencies_for_track(track["id"]):
                    conn.execute(
                        """INSERT INTO interview_competencies(id,track_id,name,description,level,role_weight,
                               measurement_methods_json,taxonomy_version) VALUES (?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description,
                           level=excluded.level,role_weight=excluded.role_weight,
                           measurement_methods_json=excluded.measurement_methods_json,
                           taxonomy_version=excluded.taxonomy_version""",
                        (competency["id"], competency["track_id"], competency["name"], competency["description"],
                         competency["level"], competency["role_weight"], _json(competency["measurement_methods"]),
                         competency["taxonomy_version"]),
                    )
                    competency_count += 1
                    conn.execute("DELETE FROM competency_prerequisites WHERE competency_id=?", (competency["id"],))
                    conn.executemany(
                        "INSERT INTO competency_prerequisites(competency_id,prerequisite_id) VALUES (?,?)",
                        [(competency["id"], dependency) for dependency in competency["prerequisites"]],
                    )
            for track_id in ("software-engineering-general", "backend-python"):
                for question in _question_templates(track_id):
                    conn.execute(
                        """INSERT INTO interview_questions(id,track_id,competency_id,target_level,question_type,prompt,
                               options_json,correct_answer,rubric_json,estimated_seconds,difficulty,
                               prerequisite_ids_json,source_id,source_chunk_id,source_locator,generation_method,
                               bank_version,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')
                           ON CONFLICT(id) DO UPDATE SET prompt=excluded.prompt,options_json=excluded.options_json,
                           correct_answer=excluded.correct_answer,rubric_json=excluded.rubric_json,
                           estimated_seconds=excluded.estimated_seconds,difficulty=excluded.difficulty,
                           source_id=excluded.source_id,source_chunk_id=excluded.source_chunk_id,
                           source_locator=excluded.source_locator,generation_method=excluded.generation_method,
                           bank_version=excluded.bank_version""",
                        (question["id"], question["track_id"], question["competency_id"], question["target_level"],
                         question["question_type"], question["prompt"], _json(question["options"]),
                         question["correct_answer"], _json(question["rubric"]), question["estimated_seconds"],
                         question["difficulty"], _json(question["prerequisite_ids"]), question["source_id"],
                         question["source_chunk_id"], question["source_locator"], question["generation_method"],
                         question["bank_version"]),
                    )
                    question_count += 1
        return {"tracks": track_count, "competencies": competency_count, "questions": question_count}

    def tracks(self) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM interview_tracks ORDER BY CASE support_level WHEN 'full' THEN 0 ELSE 1 END, title"
            )]

    def competencies(self, track_id: str, profile_id: int | None = None) -> list[dict[str, Any]]:
        profile_filter = " AND pc.profile_id=?" if profile_id is not None else " AND 1=0"
        with self.database.connection() as conn:
            rows = conn.execute(
                """SELECT c.*, COALESCE(pc.proficiency_score,0) proficiency_score,
                          COALESCE(pc.confidence_score,0) confidence_score, pc.evidence_json
                   FROM interview_competencies c LEFT JOIN profile_competencies pc
                     ON pc.competency_id=c.id""" + profile_filter +
                " WHERE c.track_id=? ORDER BY c.role_weight DESC,c.name",
                ((profile_id, track_id) if profile_id is not None else (track_id,)),
            ).fetchall()
        result = [dict(row) for row in rows]
        with self.database.connection() as conn:
            prerequisite_rows = conn.execute(
                """SELECT p.competency_id,p.prerequisite_id FROM competency_prerequisites p
                   JOIN interview_competencies c ON c.id=p.competency_id WHERE c.track_id=?""", (track_id,),
            ).fetchall()
        prerequisite_map: dict[str, list[str]] = {}
        for row in prerequisite_rows:
            prerequisite_map.setdefault(row["competency_id"], []).append(row["prerequisite_id"])
        for item in result:
            item["measurement_methods"] = json.loads(item.pop("measurement_methods_json"))
            item["evidence"] = json.loads(item["evidence_json"]) if item.get("evidence_json") else []
            item["prerequisites"] = prerequisite_map.get(item["id"], [])
        return result

    def save_career_profile(self, profile_id: int, data: dict[str, Any]) -> dict[str, Any]:
        track_ids = {item["id"] for item in TRACKS}
        if data.get("target_track") not in track_ids:
            raise ValueError("Geçersiz teknik mülakat track'i.")
        if data.get("target_level") not in {"intern", "junior", "mid", "senior"}:
            raise ValueError("Geçersiz hedef seviye.")
        target_date = date.fromisoformat(str(data["target_interview_date"]))
        if target_date < date.today():
            raise ValueError("Hedef mülakat tarihi geçmişte olamaz.")
        now = utc_now()
        with self.database.connection() as conn:
            if not conn.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone():
                raise ValueError("Kariyer profiline bağlanacak genel profil bulunamadı.")
            conn.execute(
                """INSERT INTO career_profiles(profile_id,department,education_type,education_status,experience,
                       target_track,target_role,target_level,preferred_language,other_technologies_json,company_type,
                       interview_language,target_interview_date,weekly_days,daily_minutes,preferred_days_json,
                       declared_strengths_json,declared_weaknesses_json,job_url,job_text,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(profile_id) DO UPDATE SET department=excluded.department,
                       education_type=excluded.education_type,education_status=excluded.education_status,
                       experience=excluded.experience,target_track=excluded.target_track,target_role=excluded.target_role,
                       target_level=excluded.target_level,preferred_language=excluded.preferred_language,
                       other_technologies_json=excluded.other_technologies_json,company_type=excluded.company_type,
                       interview_language=excluded.interview_language,target_interview_date=excluded.target_interview_date,
                       weekly_days=excluded.weekly_days,daily_minutes=excluded.daily_minutes,
                       preferred_days_json=excluded.preferred_days_json,
                       declared_strengths_json=excluded.declared_strengths_json,
                       declared_weaknesses_json=excluded.declared_weaknesses_json,job_url=excluded.job_url,
                       job_text=excluded.job_text,updated_at=excluded.updated_at""",
                (profile_id, str(data["department"]).strip(), str(data.get("education_type", "")).strip() or None,
                 str(data["education_status"]), str(data["experience"]), str(data["target_track"]),
                 str(data["target_role"]).strip(), str(data["target_level"]), str(data["preferred_language"]),
                 _json(_text_list(data.get("other_technologies"))), str(data["company_type"]),
                 str(data["interview_language"]), target_date.isoformat(), int(data["weekly_days"]),
                 int(data["daily_minutes"]), _json(_text_list(data.get("preferred_days"))),
                 _json(_text_list(data.get("declared_strengths"))), _json(_text_list(data.get("declared_weaknesses"))),
                 str(data.get("job_url", "")).strip() or None, str(data.get("job_text", "")).strip() or None, now, now),
            )
        profile = self.career_profile(profile_id)
        assert profile is not None
        return profile

    def career_profile(self, profile_id: int) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            row = conn.execute("SELECT * FROM career_profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for field in ("other_technologies_json", "preferred_days_json", "declared_strengths_json", "declared_weaknesses_json"):
            result[field.removesuffix("_json")] = json.loads(result.pop(field))
        return result

    def _questions(self, track_id: str, question_ids: list[str] | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM interview_questions WHERE track_id=? AND status='active'"
        params: list[Any] = [track_id]
        if question_ids:
            query += f" AND id IN ({','.join('?' for _ in question_ids)})"
            params.extend(question_ids)
        with self.database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        output = [dict(row) for row in rows]
        order = {value: index for index, value in enumerate(question_ids or [])}
        output.sort(key=lambda item: order.get(item["id"], 10_000))
        for item in output:
            item["options"] = json.loads(item.pop("options_json"))
            item["rubric"] = json.loads(item.pop("rubric_json"))
            item["prerequisite_ids"] = json.loads(item.pop("prerequisite_ids_json"))
        return output

    def start_assessment(self, profile_id: int, *, seed: int | None = None) -> dict[str, Any]:
        profile = self.career_profile(profile_id)
        if not profile:
            raise ValueError("Önce teknik mülakat kariyer profilini oluşturun.")
        if profile["target_level"] not in {"intern", "junior"}:
            raise ValueError("İlk sürümün tam değerlendirmesi yalnız stajyer ve junior seviyelerini destekler.")
        questions = self._questions(profile["target_track"])
        if not questions:
            raise ValueError("Bu beta track için henüz tam değerlendirme soru bankası bulunmuyor.")
        used_seed = int(seed if seed is not None else profile_id * 10_007 + date.today().toordinal())
        rng = random.Random(used_seed)
        technical = [item for item in questions if item["question_type"] == "technical"]
        rng.shuffle(technical)
        selected = technical[:10]
        for kind, limit in (("algorithm", 2), ("coding", 1), ("debugging", 1), ("sql", 1),
                            ("system-design", 1), ("behavioral", 2)):
            values = [item for item in questions if item["question_type"] == kind]
            rng.shuffle(values)
            selected.extend(values[:limit])
        session_id = _stable_id("assessment", profile_id, profile["target_track"], used_seed, utc_now())
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO interview_assessment_sessions(id,profile_id,track_id,target_level,seed,
                       question_ids_json,status,resumed_at,started_at) VALUES (?,?,?,?,?,?,'active',?,?)""",
                (session_id, profile_id, profile["target_track"], profile["target_level"], used_seed,
                 _json([item["id"] for item in selected]), utc_now(), utc_now()),
            )
        return self.assessment_session(session_id)

    def assessment_session(self, session_id: str) -> dict[str, Any]:
        with self.database.connection() as conn:
            row = conn.execute("SELECT * FROM interview_assessment_sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise ValueError("Teknik değerlendirme oturumu bulunamadı.")
            responses = [dict(item) for item in conn.execute(
                "SELECT * FROM interview_assessment_responses WHERE session_id=? ORDER BY id", (session_id,)
            )]
        result = dict(row)
        result["question_ids"] = json.loads(result.pop("question_ids_json"))
        result["responses"] = responses
        result["elapsed_seconds"] = self._elapsed(result)
        return result

    @staticmethod
    def _elapsed(session: dict[str, Any]) -> int:
        elapsed = max(0, int(session.get("accumulated_seconds") or 0))
        if session.get("status") == "active" and session.get("resumed_at"):
            elapsed += max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(session["resumed_at"])).total_seconds()))
        return elapsed

    def assessment_action(self, session_id: str, action: str) -> dict[str, Any]:
        if action not in {"pause", "resume", "abandon"}:
            raise ValueError("Geçersiz değerlendirme oturumu işlemi.")
        session = self.assessment_session(session_id)
        now = utc_now()
        with self.database.connection() as conn:
            if action == "pause" and session["status"] == "active":
                conn.execute(
                    "UPDATE interview_assessment_sessions SET status='paused',accumulated_seconds=?,resumed_at=NULL WHERE id=?",
                    (session["elapsed_seconds"], session_id),
                )
            elif action == "resume" and session["status"] == "paused":
                conn.execute("UPDATE interview_assessment_sessions SET status='active',resumed_at=? WHERE id=?", (now, session_id))
            elif action == "abandon" and session["status"] in {"active", "paused"}:
                conn.execute(
                    "UPDATE interview_assessment_sessions SET status='abandoned',accumulated_seconds=?,resumed_at=NULL,completed_at=? WHERE id=?",
                    (session["elapsed_seconds"], now, session_id),
                )
            else:
                raise ValueError("Oturum mevcut durumunda bu işlemi kabul etmiyor.")
        return self.assessment_session(session_id)

    def current_assessment_question(self, session_id: str) -> dict[str, Any] | None:
        session = self.assessment_session(session_id)
        answered = {item["question_id"] for item in session["responses"]}
        return next((item for item in self._questions(session["track_id"], session["question_ids"]) if item["id"] not in answered), None)

    def _evaluate_response(
        self,
        question: dict[str, Any],
        response: str,
        elapsed_seconds: int,
        *,
        skipped: bool = False,
        runner_approved: bool = False,
    ) -> tuple[dict[str, Any], Any | None]:
        if skipped:
            return {key: None for key in ("correctness_score", "code_test_score", "reasoning_score", "complexity_score",
                                           "communication_score", "time_score")} | {
                "evidence": {"measured": False, "reason": "Kullanıcı soruyu atladı."}
            }, None
        text = response.strip()
        if not text:
            raise ValueError("Yanıt boş olamaz; yanıtlamak istemiyorsanız soruyu atlayın.")
        normalized = text.casefold()
        keywords = [str(item).casefold() for item in question["rubric"].get("expected_keywords", [])]
        matched = [item for item in keywords if item in normalized]
        coverage = len(matched) / max(1, len(keywords))
        kind = question["question_type"]
        run_result = None
        correctness: float | None
        code_score: float | None = None
        if kind == "technical":
            correctness = 100.0 if text == question["correct_answer"] else 0.0
        elif kind == "coding":
            trusted_tests = "assert add(2, 3) == 5\n---TEST---\nassert add(-1, 1) == 0"
            run_result = self.runner.run(text, trusted_tests, approved=runner_approved)
            code_score = round(run_result.passed_tests / max(1, run_result.total_tests) * 100, 1)
            correctness = code_score
        else:
            correctness = round(coverage * 100, 1)
        reasoning = round(min(100.0, coverage * 80 + (20 if len(text) >= 100 else 0)), 1) if kind != "technical" else None
        complexity = round(min(100.0, coverage * 100), 1) if kind in {"algorithm", "coding"} else None
        communication = round(min(100.0, 30 + len(text.split()) * 2), 1) if kind != "technical" else None
        time_score = round(max(0.0, min(100.0, 100 - max(0, elapsed_seconds - question["estimated_seconds"]) /
                                                   max(1, question["estimated_seconds"]) * 100)), 1)
        evidence: dict[str, Any] = {
            "measured": True,
            "question_id": question["id"],
            "competency_id": question["competency_id"],
            "source_id": question["source_id"],
            "source_chunk_id": question["source_chunk_id"],
            "source_locator": question["source_locator"],
            "matched_rubric_terms": matched,
            "rubric_version": RUBRIC_VERSION,
        }
        if kind == "system-design":
            evidence["rubric_scores"] = self.system_design_rubric(text)
            evidence["missing_dimensions"] = [
                name for name, value in evidence["rubric_scores"].items() if value == 0
            ]
        if kind == "behavioral":
            evidence["star"] = self.star_analysis(text)
        if run_result:
            evidence["code_run"] = run_result.__dict__
        return {
            "correctness_score": correctness,
            "code_test_score": code_score,
            "reasoning_score": reasoning,
            "complexity_score": complexity,
            "communication_score": communication,
            "time_score": time_score,
            "evidence": evidence,
        }, run_result

    def submit_assessment_response(
        self,
        session_id: str,
        question_id: str,
        response: str,
        *,
        elapsed_seconds: int = 0,
        hint_count: int = 0,
        skipped: bool = False,
        runner_approved: bool = False,
        use_foundry: bool = False,
    ) -> dict[str, Any]:
        session = self.assessment_session(session_id)
        if session["status"] != "active":
            raise ValueError("Yalnız aktif değerlendirme oturumuna yanıt gönderilebilir.")
        current = self.current_assessment_question(session_id)
        if not current or current["id"] != question_id:
            raise ValueError("Yanıt beklenen sıradaki soruya ait değil.")
        scores, run_result = self._evaluate_response(
            current, response, elapsed_seconds, skipped=skipped, runner_approved=runner_approved,
        )
        if use_foundry and not skipped and current["question_type"] != "technical":
            scores["evidence"]["foundry_review"] = self.foundry_rubric_review(
                current, response, scores, profile_id=session["profile_id"],
            )
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO interview_assessment_responses(session_id,question_id,response_text,skipped,
                       correctness_score,code_test_score,reasoning_score,complexity_score,communication_score,
                       time_score,elapsed_seconds,hint_count,evidence_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (session_id, question_id, "" if skipped else response, int(skipped), scores["correctness_score"],
                 scores["code_test_score"], scores["reasoning_score"], scores["complexity_score"],
                 scores["communication_score"], scores["time_score"], max(0, int(elapsed_seconds)),
                 max(0, int(hint_count)), _json(scores["evidence"]), utc_now()),
            )
            if run_result:
                conn.execute(
                    """INSERT INTO coding_attempts(session_id,question_id,code_sha256,runner,status,passed_tests,
                           total_tests,elapsed_ms,output_excerpt,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (session_id, question_id, run_result.code_sha256, run_result.runner, run_result.status,
                     run_result.passed_tests, run_result.total_tests, run_result.elapsed_ms,
                     run_result.output[:2000], utc_now()),
                )
        if self.current_assessment_question(session_id) is None:
            self.complete_assessment(session_id)
        return scores

    def complete_assessment(self, session_id: str) -> dict[str, Any]:
        session = self.assessment_session(session_id)
        if self.current_assessment_question(session_id) is not None:
            raise ValueError("Değerlendirme tamamlanmadan önce tüm sorular yanıtlanmalı veya atlanmalıdır.")
        if session["status"] != "completed":
            with self.database.connection() as conn:
                conn.execute(
                    "UPDATE interview_assessment_sessions SET status='completed',accumulated_seconds=?,resumed_at=NULL,completed_at=? WHERE id=?",
                    (session["elapsed_seconds"], utc_now(), session_id),
                )
        return self.generate_gap_report(session_id)

    def _cache_get(self, namespace: str, cache_key: str, version: str) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM analysis_cache WHERE namespace=? AND cache_key=? AND version=?",
                (namespace, cache_key, version),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE analysis_cache SET hit_count=hit_count+1,last_accessed_at=? WHERE namespace=? AND cache_key=?",
                (utc_now(), namespace, cache_key),
            )
        return json.loads(row[0])

    def _cache_put(self, namespace: str, cache_key: str, version: str, payload: dict[str, Any]) -> None:
        encoded = _json(payload)
        now = utc_now()
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO analysis_cache(namespace,cache_key,payload_json,version,size_bytes,created_at,last_accessed_at)
                   VALUES (?,?,?,?,?,?,?) ON CONFLICT(namespace,cache_key) DO UPDATE SET payload_json=excluded.payload_json,
                   version=excluded.version,size_bytes=excluded.size_bytes,last_accessed_at=excluded.last_accessed_at""",
                (namespace, cache_key, encoded, version, len(encoded.encode("utf-8")), now, now),
            )
            excess = conn.execute("SELECT MAX(0,COUNT(*)-?) FROM analysis_cache", (self.settings.cache_max_entries,)).fetchone()[0]
            if excess:
                conn.execute(
                    "DELETE FROM analysis_cache WHERE rowid IN (SELECT rowid FROM analysis_cache ORDER BY last_accessed_at LIMIT ?)",
                    (int(excess),),
                )

    def generate_gap_report(self, session_id: str) -> dict[str, Any]:
        session = self.assessment_session(session_id)
        questions = {item["id"]: item for item in self._questions(session["track_id"], session["question_ids"])}
        evidence_fingerprint = [
            {"q": item["question_id"], "scores": [item[key] for key in (
                "correctness_score", "code_test_score", "reasoning_score", "complexity_score",
                "communication_score", "time_score")], "skipped": item["skipped"]}
            for item in session["responses"]
        ]
        cache_key = hashlib.sha256(_json({
            "profile_input_hash": hashlib.sha256(_json(self.career_profile(session["profile_id"])).encode("utf-8")).hexdigest(),
            "track": session["track_id"], "taxonomy": TAXONOMY_VERSION,
            "source_set_hash": hashlib.sha256(_json(sorted({q["source_id"] for q in questions.values()})).encode("utf-8")).hexdigest(),
            "scoring": SCORING_VERSION, "evidence": evidence_fingerprint,
        }).encode("utf-8")).hexdigest()
        cached = self._cache_get(f"gap:{session['profile_id']}", cache_key, SCORING_VERSION)
        if cached:
            return {**cached, "cache_hit": True}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for response in session["responses"]:
            if response["skipped"]:
                continue
            grouped.setdefault(questions[response["question_id"]]["competency_id"], []).append(response)
        competencies = self.competencies(session["track_id"])
        items: list[dict[str, Any]] = []
        weighted_total = 0.0
        weight_sum = 0.0
        confidence_values = []
        for competency in competencies:
            rows = grouped.get(competency["id"], [])
            attempts = []
            types = set()
            evidence = []
            for row in rows:
                applicable = {
                    "correctness_score": 35, "code_test_score": 25, "reasoning_score": 15,
                    "complexity_score": 10, "communication_score": 10, "time_score": 5,
                }
                values = [(float(row[key]), weight) for key, weight in applicable.items() if row[key] is not None]
                score = sum(value * weight for value, weight in values) / max(1, sum(weight for _, weight in values))
                attempts.append(score)
                question = questions[row["question_id"]]
                types.add(question["question_type"])
                evidence.append({
                    "question_id": row["question_id"], "question_type": question["question_type"],
                    "score": round(score, 1), "source_id": question["source_id"],
                    "source_chunk_id": question["source_chunk_id"], "source_locator": question["source_locator"],
                    "correct": score >= 70,
                })
            proficiency = round(sum(attempts) / len(attempts), 1) if attempts else 0.0
            consistency = max(0.0, 20.0 - (statistics.pstdev(attempts) / 5 if len(attempts) > 1 else 10.0))
            latest_at = max((datetime.fromisoformat(row["created_at"]) for row in rows), default=None)
            recency_days = max(0, (datetime.now(timezone.utc) - latest_at).days) if latest_at else None
            recency_score = max(0.0, 10.0 - (recency_days or 0) / 18) if latest_at else 0.0
            confidence = round(min(100.0, min(40, len(attempts) * 15) + min(30, len(types) * 10) + consistency + recency_score), 1) if attempts else 0.0
            gap = round(100 - proficiency, 1)
            status = "ölçülmedi" if not attempts else "öncelikli" if proficiency < 50 else "geliştirilmeli" if proficiency < 70 else "iyi"
            wrong = [entry for entry in evidence if not entry["correct"]]
            error_types = sorted({entry["question_type"] for entry in wrong})
            sources = sorted(
                {(entry["source_id"], entry.get("source_chunk_id"), entry.get("source_locator")) for entry in evidence},
                key=lambda value: tuple(str(part or "") for part in value),
            )
            item = {
                "competency_id": competency["id"], "name": competency["name"],
                "proficiency_score": proficiency, "confidence_score": confidence,
                "role_weight": competency["role_weight"], "gap": gap, "priority": 0.0,
                "status": status, "evidence": evidence,
                "correct_examples": [entry for entry in evidence if entry["correct"]][:3],
                "incorrect_examples": wrong[:3], "repeated_error_types": error_types,
                "recommended_study_type": (
                    "kaynak bağlı tekrar ve süreli uygulama" if attempts else "ön koşul taraması ve temel teknik soru"
                ),
                "sources": [{"source_id": source_id, "source_chunk_id": chunk_id, "source_locator": locator}
                            for source_id, chunk_id, locator in sources],
                "prerequisites": competency["prerequisites"], "recency_days": recency_days,
                "explanation": (
                    "Bu alan henüz ölçülmedi; düşük confidence nedeniyle kesin seviye yorumu yapılmadı."
                    if not attempts else f"{len(attempts)} ölçüm ve {len(types)} farklı soru türüne dayanır."
                ),
            }
            items.append(item)
            if attempts:
                weighted_total += proficiency * float(competency["role_weight"])
                weight_sum += float(competency["role_weight"])
                confidence_values.append(confidence)
        item_by_id = {item["competency_id"]: item for item in items}
        for item in items:
            prerequisite_scores = [item_by_id[value]["proficiency_score"] for value in item["prerequisites"] if value in item_by_id]
            prerequisite_readiness = min(prerequisite_scores) / 100 if prerequisite_scores else 1.0
            confidence_factor = 0.5 + item["confidence_score"] / 200
            recency_factor = max(0.8, 1.0 - min(item["recency_days"] or 0, 365) / 1825) if item["recency_days"] is not None else 0.8
            item["confidence_factor"] = round(confidence_factor, 4)
            item["recency_factor"] = round(recency_factor, 4)
            item["prerequisite_readiness"] = round(prerequisite_readiness, 4)
            item["priority"] = round(
                float(item["role_weight"]) * (item["gap"] / 100) * confidence_factor * recency_factor *
                max(0.25, prerequisite_readiness), 4,
            )
        overall = round(weighted_total / weight_sum, 1) if weight_sum else 0.0
        overall_confidence = round(sum(confidence_values) / len(confidence_values), 1) if confidence_values else 0.0
        items.sort(key=lambda item: (-item["priority"], item["name"]))
        report_id = _stable_id("gap", session_id, cache_key)
        report = {
            "id": report_id, "profile_id": session["profile_id"], "track_id": session["track_id"],
            "session_id": session_id, "overall_score": overall, "confidence_score": overall_confidence,
            "items": items, "scoring_version": SCORING_VERSION,
            "disclaimer": "Puanlar yalnız kayıtlı teknik kanıtlara dayanır; işe alınma veya kişilik tahmini değildir.",
        }
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO interview_gap_reports(id,profile_id,track_id,session_id,overall_score,
                       confidence_score,report_json,scoring_version,created_at) VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET overall_score=excluded.overall_score,
                       confidence_score=excluded.confidence_score,report_json=excluded.report_json""",
                (report_id, session["profile_id"], session["track_id"], session_id, overall,
                 overall_confidence, _json(report), SCORING_VERSION, utc_now()),
            )
            for item in items:
                conn.execute(
                    """INSERT INTO profile_competencies(profile_id,competency_id,proficiency_score,confidence_score,
                           evidence_json,scoring_version,updated_at) VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(profile_id,competency_id) DO UPDATE SET proficiency_score=excluded.proficiency_score,
                       confidence_score=excluded.confidence_score,evidence_json=excluded.evidence_json,
                       scoring_version=excluded.scoring_version,updated_at=excluded.updated_at""",
                    (session["profile_id"], item["competency_id"], item["proficiency_score"],
                     item["confidence_score"], _json(item["evidence"]), SCORING_VERSION, utc_now()),
                )
        self._cache_put(f"gap:{session['profile_id']}", cache_key, SCORING_VERSION, report)
        return {**report, "cache_hit": False}

    def latest_gap_report(self, profile_id: int) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT report_json FROM interview_gap_reports WHERE profile_id=? ORDER BY created_at DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def create_plan(self, profile_id: int, gap_report_id: str | None = None) -> dict[str, Any]:
        career = self.career_profile(profile_id)
        if not career:
            raise ValueError("Teknik mülakat kariyer profili bulunamadı.")
        with self.database.connection() as conn:
            if gap_report_id:
                row = conn.execute("SELECT * FROM interview_gap_reports WHERE id=? AND profile_id=?", (gap_report_id, profile_id)).fetchone()
            else:
                row = conn.execute("SELECT * FROM interview_gap_reports WHERE profile_id=? ORDER BY created_at DESC LIMIT 1", (profile_id,)).fetchone()
        if not row:
            raise ValueError("Plan için önce başlangıç değerlendirmesi ve eksiklik raporu oluşturun.")
        gap = json.loads(row["report_json"])
        cache_key = hashlib.sha256(_json({
            "gap": row["id"], "gap_hash": hashlib.sha256(row["report_json"].encode("utf-8")).hexdigest(),
            "role": career["target_role"], "level": career["target_level"],
            "date": career["target_interview_date"], "weekly": career["weekly_days"],
            "daily": career["daily_minutes"], "planner": PLANNER_VERSION,
        }).encode("utf-8")).hexdigest()
        cached = self._cache_get(f"plan:{profile_id}", cache_key, PLANNER_VERSION)
        if cached:
            return {**cached, "cache_hit": True}
        selected = [item for item in gap["items"] if item["status"] != "iyi"][:8]
        if not selected:
            selected = gap["items"][:4]
        gap_by_id = {item["competency_id"]: item for item in gap["items"]}
        selected_ids = {item["competency_id"] for item in selected}

        def include_prerequisites(competency_id: str) -> None:
            for dependency in gap_by_id.get(competency_id, {}).get("prerequisites", []):
                if dependency in gap_by_id and gap_by_id[dependency]["proficiency_score"] < 70:
                    if dependency not in selected_ids:
                        selected_ids.add(dependency)
                        include_prerequisites(dependency)

        for competency_id in tuple(selected_ids):
            include_prerequisites(competency_id)
        ordered: list[dict[str, Any]] = []
        visited: set[str] = set()

        def append_after_prerequisites(competency_id: str) -> None:
            if competency_id in visited:
                return
            for dependency in gap_by_id[competency_id].get("prerequisites", []):
                if dependency in selected_ids:
                    append_after_prerequisites(dependency)
            visited.add(competency_id)
            ordered.append(gap_by_id[competency_id])

        for item in selected:
            append_after_prerequisites(item["competency_id"])
        selected = ordered
        plan_id = _stable_id("interview-plan", profile_id, row["id"], cache_key)
        with self.database.connection() as conn:
            existing = conn.execute("SELECT 1 FROM interview_plans WHERE id=?", (plan_id,)).fetchone()
        if existing:
            result = self.plan(profile_id)
            assert result is not None
            return {**result, "cache_hit": True}
        preferred_days = career["preferred_days"] or ["Pazartesi", "Çarşamba", "Cuma"]
        day_map = {"Pazartesi": 0, "Salı": 1, "Çarşamba": 2, "Perşembe": 3, "Cuma": 4, "Cumartesi": 5, "Pazar": 6}
        allowed = {day_map[item] for item in preferred_days if item in day_map}
        cursor = date.today()

        def next_date() -> str:
            nonlocal cursor
            while allowed and cursor.weekday() not in allowed:
                cursor += timedelta(days=1)
            value = cursor.isoformat()
            cursor += timedelta(days=1)
            return value

        tasks = []
        previous_id: str | None = None
        for item in selected:
            name = item["name"].casefold()
            task_type = "algoritma" if "algorit" in name or "veri yap" in name else "sql" if "sql" in name else \
                "sistem tasarımı" if "tasarım" in name or "mimari" in name else \
                "davranışsal" if "davranış" in name else "kodlama" if "python" in name or "programlama" in name else "teknik soru"
            evidence = item.get("evidence") or []
            source = evidence[0] if evidence else {
                "source_id": f"system:{QUESTION_BANK_VERSION}", "source_chunk_id": None,
                "source_locator": "Yerel teknik mülakat yetkinlik taksonomisi",
            }
            for activity, minutes in (("Konu çalışması", min(60, career["daily_minutes"])),
                                      ("Süreli uygulama", min(75, career["daily_minutes"]))):
                task_id = _stable_id("interview-task", plan_id, item["competency_id"], activity)
                tasks.append({
                    "id": task_id, "plan_id": plan_id, "competency_id": item["competency_id"],
                    "title": f"{activity}: {item['name']}",
                    "description": f"{item['explanation']} Bu çalışma {item['name']} eksikliğini hedefler.",
                    "task_type": task_type if activity == "Süreli uygulama" else "konu okuma",
                    "estimated_minutes": max(10, minutes), "position": len(tasks) + 1,
                    "prerequisite_task_ids": [previous_id] if previous_id else [],
                    "difficulty": "temel" if item["confidence_score"] < 40 else "orta",
                    "source_id": source["source_id"], "source_chunk_id": source.get("source_chunk_id"),
                    "source_locator": source.get("source_locator"), "generation_method": "gap raporundan deterministik",
                    "reason": (
                        f"priority={item['role_weight']}×{item['gap']/100:.3f}×{item['confidence_factor']}×"
                        f"{item['recency_factor']}×{max(0.25, item['prerequisite_readiness'])}={item['priority']}; "
                        f"yeterlik {item['proficiency_score']}, güven {item['confidence_score']}."
                    ),
                    "included": True, "status": "pending", "scheduled_date": next_date(),
                })
                previous_id = task_id
        now = utc_now()
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO interview_plans(id,profile_id,gap_report_id,status,target_interview_date,
                       weekly_days,daily_minutes,planner_version,cache_key,created_at,updated_at)
                   VALUES (?,?,?,'draft',?,?,?,?,?,?,?)""",
                (plan_id, profile_id, row["id"], career["target_interview_date"], career["weekly_days"],
                 career["daily_minutes"], PLANNER_VERSION, cache_key, now, now),
            )
            conn.executemany(
                """INSERT INTO interview_plan_tasks(id,plan_id,competency_id,title,description,task_type,
                       estimated_minutes,position,prerequisite_task_ids_json,difficulty,source_id,source_chunk_id,
                       source_locator,generation_method,reason,included,status,scheduled_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(item["id"], plan_id, item["competency_id"], item["title"], item["description"], item["task_type"],
                  item["estimated_minutes"], item["position"], _json(item["prerequisite_task_ids"]), item["difficulty"],
                  item["source_id"], item["source_chunk_id"], item["source_locator"], item["generation_method"],
                  item["reason"], int(item["included"]), item["status"], item["scheduled_date"]) for item in tasks],
            )
        result = {"id": plan_id, "profile_id": profile_id, "gap_report_id": row["id"], "status": "draft", "tasks": tasks}
        self._cache_put(f"plan:{profile_id}", cache_key, PLANNER_VERSION, result)
        return {**result, "cache_hit": False}

    def plan(self, profile_id: int, *, include_archived: bool = False) -> dict[str, Any] | None:
        query = "SELECT * FROM interview_plans WHERE profile_id=?"
        if not include_archived:
            query += " AND status<>'archived'"
        query += " ORDER BY updated_at DESC LIMIT 1"
        with self.database.connection() as conn:
            row = conn.execute(query, (profile_id,)).fetchone()
            if not row:
                return None
            tasks = [dict(item) for item in conn.execute(
                "SELECT * FROM interview_plan_tasks WHERE plan_id=? ORDER BY position", (row["id"],)
            )]
        for task in tasks:
            task["prerequisite_task_ids"] = json.loads(task.pop("prerequisite_task_ids_json"))
            task["included"] = bool(task["included"])
        return {**dict(row), "tasks": tasks}

    def update_plan_tasks(self, plan_id: str, edits: list[dict[str, Any]]) -> int:
        with self.database.connection() as conn:
            plan = conn.execute("SELECT * FROM interview_plans WHERE id=?", (plan_id,)).fetchone()
            current = [dict(item) for item in conn.execute(
                "SELECT * FROM interview_plan_tasks WHERE plan_id=? ORDER BY position", (plan_id,)
            )]
        if not plan or plan["status"] != "draft":
            raise ValueError("Yalnız onaylanmamış mülakat planı düzenlenebilir.")
        ids = {item["id"] for item in current}
        if {str(item["id"]) for item in edits} != ids:
            raise ValueError("Plan görev kimlikleri değiştirilemez.")
        ordered = sorted(edits, key=lambda item: (int(item["position"]), str(item["id"])))
        with self.database.connection() as conn:
            conn.execute("UPDATE interview_plan_tasks SET position=-position WHERE plan_id=?", (plan_id,))
            for position, item in enumerate(ordered, start=1):
                title = str(item["title"]).strip()
                minutes = int(item["estimated_minutes"])
                if not title or not 10 <= minutes <= 720:
                    raise ValueError("Görev adı ve 10-720 dakika süre sınırı geçerli olmalıdır.")
                conn.execute(
                    "UPDATE interview_plan_tasks SET title=?,estimated_minutes=?,position=?,included=? WHERE id=? AND plan_id=?",
                    (title, minutes, position, int(bool(item.get("included", True))), item["id"], plan_id),
                )
            conn.execute("UPDATE interview_plans SET updated_at=? WHERE id=?", (utc_now(), plan_id))
        return len(edits)

    def add_plan_task(
        self, plan_id: str, competency_id: str, title: str, task_type: str, estimated_minutes: int,
    ) -> dict[str, Any]:
        title = title.strip()
        if not title or not 10 <= int(estimated_minutes) <= 720:
            raise ValueError("Görev adı ve 10-720 dakika süre sınırı geçerli olmalıdır.")
        with self.database.connection() as conn:
            plan = conn.execute(
                """SELECT p.*,c.target_track,c.preferred_days_json FROM interview_plans p
                   JOIN career_profiles c ON c.profile_id=p.profile_id WHERE p.id=?""", (plan_id,),
            ).fetchone()
            competency = conn.execute(
                "SELECT * FROM interview_competencies WHERE id=?", (competency_id,),
            ).fetchone()
            if not plan or plan["status"] != "draft":
                raise ValueError("Görev yalnız onaylanmamış plan taslağına eklenebilir.")
            if not competency or competency["track_id"] != plan["target_track"]:
                raise ValueError("Görev yetkinliği plan track'iyle eşleşmiyor.")
            position = int(conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM interview_plan_tasks WHERE plan_id=?", (plan_id,),
            ).fetchone()[0])
            task_id = _stable_id("interview-task-user", plan_id, competency_id, title, position, utc_now())
            last_date = conn.execute(
                "SELECT MAX(scheduled_date) FROM interview_plan_tasks WHERE plan_id=?", (plan_id,),
            ).fetchone()[0]
            cursor = date.fromisoformat(last_date) + timedelta(days=1) if last_date else date.today()
            day_map = {"Pazartesi": 0, "Salı": 1, "Çarşamba": 2, "Perşembe": 3, "Cuma": 4, "Cumartesi": 5, "Pazar": 6}
            allowed = {day_map[item] for item in json.loads(plan["preferred_days_json"]) if item in day_map}
            while allowed and cursor.weekday() not in allowed:
                cursor += timedelta(days=1)
            scheduled_date = min(cursor, date.fromisoformat(plan["target_interview_date"])).isoformat()
            conn.execute(
                """INSERT INTO interview_plan_tasks(id,plan_id,competency_id,title,description,task_type,
                       estimated_minutes,position,prerequisite_task_ids_json,difficulty,source_id,source_chunk_id,
                       source_locator,generation_method,reason,included,status,scheduled_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, plan_id, competency_id, title, f"Kullanıcının eklediği {competency['name']} çalışması.",
                 task_type, int(estimated_minutes), position, "[]", "kullanıcı seçimi",
                 f"system:{QUESTION_BANK_VERSION}", None, "Yerel teknik mülakat yetkinlik taksonomisi",
                 "kullanıcı tarafından plana eklendi", "Kullanıcı plan taslağına açıkça ekledi.", 1, "pending", scheduled_date),
            )
        with self.database.connection() as conn:
            row = conn.execute("SELECT * FROM interview_plan_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row)

    def reschedule_plan(self, plan_id: str, profile_id: int, target_interview_date: str) -> dict[str, Any]:
        target = date.fromisoformat(target_interview_date)
        if target < date.today():
            raise ValueError("Hedef mülakat tarihi geçmişte olamaz.")
        career = self.career_profile(profile_id)
        if not career:
            raise ValueError("Kariyer profili bulunamadı.")
        with self.database.connection() as conn:
            plan = conn.execute("SELECT * FROM interview_plans WHERE id=? AND profile_id=?", (plan_id, profile_id)).fetchone()
            tasks = [dict(row) for row in conn.execute(
                "SELECT * FROM interview_plan_tasks WHERE plan_id=? ORDER BY position", (plan_id,),
            )]
        if not plan:
            raise ValueError("Yeniden takvimlenecek plan bulunamadı.")
        day_map = {"Pazartesi": 0, "Salı": 1, "Çarşamba": 2, "Perşembe": 3, "Cuma": 4, "Cumartesi": 5, "Pazar": 6}
        allowed = {day_map[item] for item in career["preferred_days"] if item in day_map}
        cursor = date.today()
        updates: list[tuple[str, str]] = []
        for task in tasks:
            if not task["included"] or task["status"] == "completed":
                continue
            while allowed and cursor.weekday() not in allowed:
                cursor += timedelta(days=1)
            updates.append((min(cursor, target).isoformat(), task["id"]))
            cursor += timedelta(days=1)
        new_key = hashlib.sha256(_json({
            "previous_cache_key": plan["cache_key"], "target_interview_date": target.isoformat(),
            "capacity": [career["weekly_days"], career["daily_minutes"]], "planner": PLANNER_VERSION,
        }).encode("utf-8")).hexdigest()
        with self.database.connection() as conn:
            conn.executemany("UPDATE interview_plan_tasks SET scheduled_date=? WHERE id=?", updates)
            conn.execute(
                "UPDATE interview_plans SET target_interview_date=?,cache_key=?,updated_at=? WHERE id=?",
                (target.isoformat(), new_key, utc_now(), plan_id),
            )
            conn.execute(
                "UPDATE career_profiles SET target_interview_date=?,updated_at=? WHERE profile_id=?",
                (target.isoformat(), utc_now(), profile_id),
            )
            conn.execute(
                """INSERT INTO interview_adaptive_decisions(profile_id,decision_type,reason,payload_json,applied,created_at)
                   VALUES (?,?,?,?,1,?)""",
                (profile_id, "calendar_reschedule", "Kullanıcı hedef mülakat tarihini değiştirdi; görev içeriği ve tamamlanan görevler korundu.",
                 _json({"plan_id": plan_id, "target_interview_date": target.isoformat(), "updated_tasks": len(updates)}), utc_now()),
            )
        result = self.plan(profile_id)
        assert result is not None
        return result

    def confirm_plan(self, plan_id: str, profile_id: int) -> dict[str, Any]:
        with self.database.connection() as conn:
            plan = conn.execute("SELECT status FROM interview_plans WHERE id=? AND profile_id=?", (plan_id, profile_id)).fetchone()
            if not plan or plan["status"] != "draft":
                raise ValueError("Onaylanacak mülakat planı taslağı bulunamadı.")
            conn.execute("UPDATE interview_plans SET status='confirmed',updated_at=? WHERE id=?", (utc_now(), plan_id))
        result = self.plan(profile_id)
        assert result is not None
        return result

    def archive_plan(self, plan_id: str, profile_id: int, confirmation: str) -> bool:
        expected = f"PLANI ARŞİVLE {plan_id}"
        if confirmation != expected:
            raise ValueError(f"Arşivleme için tam olarak '{expected}' yazılmalıdır.")
        with self.database.connection() as conn:
            changed = conn.execute(
                "UPDATE interview_plans SET status='archived',updated_at=? WHERE id=? AND profile_id=?",
                (utc_now(), plan_id, profile_id),
            ).rowcount
        if not changed:
            raise ValueError("Arşivlenecek mülakat planı bulunamadı.")
        return True

    def update_plan_task_status(self, task_id: str, status: str) -> dict[str, Any]:
        transitions = {
            "pending": {"in_progress", "skipped"},
            "in_progress": {"completed", "needs_review", "skipped"},
            "completed": {"needs_review"},
            "needs_review": {"in_progress", "completed", "skipped"},
            "skipped": {"pending"},
        }
        with self.database.connection() as conn:
            row = conn.execute(
                """SELECT t.*,p.status plan_status FROM interview_plan_tasks t
                   JOIN interview_plans p ON p.id=t.plan_id WHERE t.id=?""", (task_id,),
            ).fetchone()
            if not row or row["plan_status"] != "confirmed":
                raise ValueError("Yalnız onaylı mülakat planındaki görev güncellenebilir.")
            if status not in transitions.get(row["status"], set()):
                raise ValueError(f"Geçersiz görev geçişi: {row['status']} → {status}")
            completed_at = utc_now() if status == "completed" else None
            conn.execute(
                "UPDATE interview_plan_tasks SET status=?,completed_at=? WHERE id=?",
                (status, completed_at, task_id),
            )
        with self.database.connection() as conn:
            updated = conn.execute("SELECT * FROM interview_plan_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(updated)

    @staticmethod
    def system_design_rubric(response: str) -> dict[str, int]:
        text = response.casefold()
        dimensions = {
            "gereksinimler": ("gereksinim", "trafik", "kısıt"),
            "bileşenler": ("servis", "bileşen", "istemci"),
            "veri_modeli": ("veri", "tablo", "şema"),
            "api": ("api", "endpoint", "http"),
            "veri_akışı": ("akış", "istek", "yanıt"),
            "hata_yönetimi": ("hata", "retry", "timeout"),
            "caching": ("cache", "ttl", "invalidation"),
            "ölçeklenebilirlik": ("ölçek", "load balancer", "kuyruk"),
            "güvenlik": ("güven", "auth", "rate limit"),
            "gözlemlenebilirlik": ("log", "metrik", "trace"),
            "gerekçelendirme": ("çünkü", "trade-off", "tercih"),
        }
        return {name: min(4, sum(term in text for term in terms) + (1 if len(response.split()) >= 80 else 0))
                for name, terms in dimensions.items()}

    @staticmethod
    def star_analysis(response: str) -> dict[str, Any]:
        text = response.casefold()
        markers = {
            "situation": ("situation", "durum", "karşılaşt"),
            "task": ("task", "görev", "sorumlulu"),
            "action": ("action", "aksiyon", "yaptım", "uyguladım"),
            "result": ("result", "sonuç", "sonunda", "%"),
        }
        present = {name: any(term in text for term in terms) for name, terms in markers.items()}
        words = response.split()
        structure = {
            "question_relevance": bool(words),
            "concrete_example": any(term in text for term in ("örnek", "proje", "ekip", "hata", "sorun")),
            "own_contribution": any(term in text for term in ("ben ", "yaptım", "uyguladım", "tasarladım", "çözdüm")),
            "measurable_result": any(char.isdigit() for char in response) or "%" in response,
            "clarity_and_length": 20 <= len(words) <= 300,
        }
        return {
            "components": present,
            "completion_percentage": round(sum(present.values()) / 4 * 100, 1),
            "missing": [name for name, value in present.items() if not value],
            "communication_structure": structure,
            "communication_percentage": round(sum(structure.values()) / len(structure) * 100, 1),
            "response_sha256": hashlib.sha256(response.encode("utf-8")).hexdigest(),
            "notice": "Bu yalnız STAR yapısı ve iletişim açıklığı analizidir; kişilik değerlendirmesi değildir.",
        }

    def start_mock_interview(self, profile_id: int, interview_type: str) -> dict[str, Any]:
        career = self.career_profile(profile_id)
        if not career:
            raise ValueError("Önce kariyer profilini oluşturun.")
        type_map = {
            "Hızlı teknik tarama": {"technical"}, "Algoritma mülakatı": {"algorithm", "coding"},
            "Python backend mülakatı": {"technical", "coding", "debugging", "sql", "system-design"},
            "Sistem tasarımı mülakatı": {"system-design"}, "Davranışsal mülakat": {"behavioral"},
            "Tam karma mülakat": {"technical", "algorithm", "coding", "sql", "system-design", "behavioral"},
        }
        if interview_type not in type_map:
            raise ValueError("Geçersiz deneme mülakatı türü.")
        available = [item for item in self._questions(career["target_track"]) if item["question_type"] in type_map[interview_type]]
        quotas = {
            "Hızlı teknik tarama": {"technical": 6},
            "Algoritma mülakatı": {"algorithm": 2, "coding": 1},
            "Python backend mülakatı": {"technical": 3, "coding": 1, "debugging": 1, "sql": 1, "system-design": 1},
            "Sistem tasarımı mülakatı": {"system-design": 1},
            "Davranışsal mülakat": {"behavioral": 2},
            "Tam karma mülakat": {"technical": 2, "algorithm": 1, "coding": 1, "sql": 1, "system-design": 1, "behavioral": 2},
        }
        questions = []
        for kind, limit in quotas[interview_type].items():
            questions.extend([item for item in available if item["question_type"] == kind][:limit])
        mock_id = _stable_id("mock", profile_id, interview_type, utc_now())
        now = utc_now()
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO mock_interviews(id,profile_id,track_id,interview_type,target_level,
                       question_ids_json,status,resumed_at,started_at) VALUES (?,?,?,?,?,?,'active',?,?)""",
                (mock_id, profile_id, career["target_track"], interview_type, career["target_level"],
                 _json([item["id"] for item in questions]), now, now),
            )
        return self.mock_interview(mock_id)

    def mock_interview(self, mock_id: str) -> dict[str, Any]:
        with self.database.connection() as conn:
            row = conn.execute("SELECT * FROM mock_interviews WHERE id=?", (mock_id,)).fetchone()
        if not row:
            raise ValueError("Deneme mülakatı bulunamadı.")
        result = dict(row)
        for source, target in (("question_ids_json", "question_ids"), ("responses_json", "responses"),
                               ("rubric_scores_json", "rubric_scores"), ("report_json", "report")):
            result[target] = json.loads(result.pop(source))
        result["elapsed_seconds"] = self._elapsed(result)
        questions = self._questions(result["track_id"], result["question_ids"])
        result["current_question"] = next((item for item in questions if item["id"] not in result["responses"]), None)
        return result

    def mock_action(self, mock_id: str, action: str) -> dict[str, Any]:
        if action not in {"pause", "resume", "finish"}:
            raise ValueError("Geçersiz deneme mülakatı işlemi.")
        mock = self.mock_interview(mock_id)
        now = utc_now()
        completed_now = False
        with self.database.connection() as conn:
            if action == "pause" and mock["status"] == "active":
                conn.execute("UPDATE mock_interviews SET status='paused',accumulated_seconds=?,resumed_at=NULL WHERE id=?",
                             (mock["elapsed_seconds"], mock_id))
            elif action == "resume" and mock["status"] == "paused":
                conn.execute("UPDATE mock_interviews SET status='active',resumed_at=? WHERE id=?", (now, mock_id))
            elif action == "finish" and mock["status"] in {"active", "paused"}:
                if mock["current_question"] is not None:
                    raise ValueError("Tüm mülakat soruları yanıtlanmadan oturum bitirilemez.")
                scores = [float(item["overall_score"]) for item in mock["rubric_scores"].values()]
                report = {
                    "overall_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
                    "answered_questions": len(mock["responses"]),
                    "strengths": [key for key, item in mock["rubric_scores"].items() if item["overall_score"] >= 70],
                    "improvements": [key for key, item in mock["rubric_scores"].items() if item["overall_score"] < 60],
                    "disclaimer": "Sonuç yalnız bu oturumdaki teknik kanıtlara dayanır ve işe alınma garantisi değildir.",
                    "scoring_version": SCORING_VERSION,
                }
                conn.execute(
                    "UPDATE mock_interviews SET status='completed',accumulated_seconds=?,resumed_at=NULL,completed_at=?,report_json=? WHERE id=?",
                    (mock["elapsed_seconds"], now, _json(report), mock_id),
                )
                completed_now = True
            else:
                raise ValueError("Deneme mülakatı mevcut durumunda bu işlemi kabul etmiyor.")
        if completed_now:
            self._apply_mock_results(mock_id)
        return self.mock_interview(mock_id)

    def submit_mock_response(
        self, mock_id: str, response: str, *, elapsed_seconds: int = 0, runner_approved: bool = False,
        use_foundry: bool = False, hint_count: int = 0, skipped: bool = False,
    ) -> dict[str, Any]:
        mock = self.mock_interview(mock_id)
        if mock["status"] != "active" or not mock["current_question"]:
            raise ValueError("Yanıt bekleyen aktif deneme mülakatı bulunamadı.")
        question = mock["current_question"]
        scores, _ = self._evaluate_response(
            question, response, elapsed_seconds, runner_approved=runner_approved, skipped=skipped,
        )
        if use_foundry and not skipped and question["question_type"] != "technical":
            scores["evidence"]["foundry_review"] = self.foundry_rubric_review(
                question, response, scores, profile_id=mock["profile_id"],
            )
        numeric = [value for key, value in scores.items() if key.endswith("_score") and value is not None]
        overall = round(sum(numeric) / len(numeric), 1) if numeric else 0.0
        follow_up = None
        if overall < 60:
            follow_up = "Bu yaklaşımın temel eksikliğini ve farklı bir çözümün trade-off'unu açıklar mısınız?"
        mock["responses"][question["id"]] = "" if skipped else response
        mock["rubric_scores"][question["id"]] = {
            **scores, "overall_score": overall, "follow_up": follow_up,
            "hint_count": max(0, int(hint_count)), "scoring_version": SCORING_VERSION,
        }
        with self.database.connection() as conn:
            conn.execute(
                "UPDATE mock_interviews SET responses_json=?,rubric_scores_json=? WHERE id=?",
                (_json(mock["responses"]), _json(mock["rubric_scores"]), mock_id),
            )
            if overall < 60:
                conn.execute(
                    """INSERT INTO interview_adaptive_decisions(profile_id,competency_id,decision_type,reason,
                           payload_json,applied,created_at) VALUES (?,?,?,?,?,0,?)""",
                    (mock["profile_id"], question["competency_id"], "review_suggestion",
                     "Deneme mülakatı puanı 60'ın altında; kullanıcı onayıyla tekrar planlanabilir.",
                     _json({"mock_id": mock_id, "question_id": question["id"], "score": overall}), utc_now()),
                )
            conditions: list[tuple[str, str]] = []
            if scores.get("code_test_score") is not None and scores["code_test_score"] < 60:
                conditions.append(("low_code_tests", "Kod birim test başarısı %60 altında."))
            if elapsed_seconds > question["estimated_seconds"]:
                conditions.append(("time_overrun", "Yanıt hedef süreyi aştı."))
            if hint_count >= 2:
                conditions.append(("high_hint_use", "Aynı soruda en az iki ipucu kullanıldı."))
            design = scores.get("evidence", {}).get("rubric_scores", {})
            if design and sum(design.values()) / len(design) < 2:
                conditions.append(("low_system_design_rubric", "Sistem tasarımı rubric ortalaması 2/4 altında."))
            star = scores.get("evidence", {}).get("star", {})
            if star.get("missing"):
                conditions.append(("missing_star_component", "STAR cevabında eksik bileşen var: " + ", ".join(star["missing"])))
            if overall >= 85:
                conditions.append(("acceleration_suggestion", "Yüksek başarı nedeniyle yalnız açık görevlerde hızlandırma önerilebilir."))
            for decision_type, reason in conditions:
                conn.execute(
                    """INSERT INTO interview_adaptive_decisions(profile_id,competency_id,decision_type,reason,
                           payload_json,applied,created_at) VALUES (?,?,?,?,?,0,?)""",
                    (mock["profile_id"], question["competency_id"], decision_type, reason,
                     _json({"mock_id": mock_id, "question_id": question["id"]}), utc_now()),
                )
        return {"question_id": question["id"], "overall_score": overall, "scores": scores, "follow_up": follow_up}

    def _apply_mock_results(self, mock_id: str) -> None:
        mock = self.mock_interview(mock_id)
        questions = {item["id"]: item for item in self._questions(mock["track_id"], mock["question_ids"])}
        grouped: dict[str, list[tuple[str, float]]] = {}
        for question_id, score in mock["rubric_scores"].items():
            grouped.setdefault(questions[question_id]["competency_id"], []).append((question_id, float(score["overall_score"])))
        now = utc_now()
        with self.database.connection() as conn:
            for competency_id, attempts in grouped.items():
                row = conn.execute(
                    "SELECT * FROM profile_competencies WHERE profile_id=? AND competency_id=?",
                    (mock["profile_id"], competency_id),
                ).fetchone()
                score = sum(value for _, value in attempts) / len(attempts)
                previous_score = float(row["proficiency_score"]) if row else score
                previous_confidence = float(row["confidence_score"]) if row else 0.0
                proficiency = round(previous_score * 0.7 + score * 0.3, 1) if row else round(score, 1)
                confidence = round(min(100.0, previous_confidence + min(20, 8 * len(attempts))), 1)
                evidence = json.loads(row["evidence_json"]) if row else []
                evidence.extend({
                    "mock_id": mock_id, "question_id": question_id, "question_type": questions[question_id]["question_type"],
                    "score": value, "source_id": questions[question_id]["source_id"],
                    "source_chunk_id": questions[question_id]["source_chunk_id"],
                    "source_locator": questions[question_id]["source_locator"],
                } for question_id, value in attempts)
                conn.execute(
                    """INSERT INTO profile_competencies(profile_id,competency_id,proficiency_score,confidence_score,
                           evidence_json,scoring_version,updated_at) VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(profile_id,competency_id) DO UPDATE SET proficiency_score=excluded.proficiency_score,
                           confidence_score=excluded.confidence_score,evidence_json=excluded.evidence_json,
                           scoring_version=excluded.scoring_version,updated_at=excluded.updated_at""",
                    (mock["profile_id"], competency_id, proficiency, confidence, _json(evidence), SCORING_VERSION, now),
                )
            latest = conn.execute(
                "SELECT * FROM interview_gap_reports WHERE profile_id=? ORDER BY created_at DESC LIMIT 1",
                (mock["profile_id"],),
            ).fetchone()
            if latest:
                report = json.loads(latest["report_json"])
                current_rows = {row["competency_id"]: row for row in conn.execute(
                    "SELECT * FROM profile_competencies WHERE profile_id=?", (mock["profile_id"],),
                )}
                for item in report["items"]:
                    if item["competency_id"] in current_rows:
                        item["proficiency_score"] = current_rows[item["competency_id"]]["proficiency_score"]
                        item["confidence_score"] = current_rows[item["competency_id"]]["confidence_score"]
                measured = [item for item in report["items"] if item["confidence_score"] > 0]
                weight_sum = sum(float(item["role_weight"]) for item in measured)
                report["overall_score"] = round(sum(float(item["role_weight"]) * item["proficiency_score"] for item in measured) / max(1, weight_sum), 1)
                report["confidence_score"] = round(sum(item["confidence_score"] for item in measured) / max(1, len(measured)), 1)
                report["id"] = _stable_id("gap-mock", latest["id"], mock_id)
                report["session_id"] = None
                report["mock_id"] = mock_id
                conn.execute(
                    """INSERT OR IGNORE INTO interview_gap_reports(id,profile_id,track_id,session_id,overall_score,
                           confidence_score,report_json,scoring_version,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (report["id"], mock["profile_id"], mock["track_id"], None, report["overall_score"],
                     report["confidence_score"], _json(report), SCORING_VERSION, now),
                )
            plan = conn.execute(
                "SELECT id FROM interview_plans WHERE profile_id=? AND status='confirmed' ORDER BY updated_at DESC LIMIT 1",
                (mock["profile_id"],),
            ).fetchone()
            if plan:
                position = int(conn.execute(
                    "SELECT COALESCE(MAX(position),0) FROM interview_plan_tasks WHERE plan_id=?", (plan["id"],),
                ).fetchone()[0])
                for competency_id, attempts in grouped.items():
                    score = sum(value for _, value in attempts) / len(attempts)
                    if score >= 60:
                        continue
                    question_id = attempts[0][0]
                    question = questions[question_id]
                    task_id = _stable_id("mock-review", plan["id"], mock_id, competency_id)
                    position += 1
                    conn.execute(
                        """INSERT OR IGNORE INTO interview_plan_tasks(id,plan_id,competency_id,title,description,
                               task_type,estimated_minutes,position,prerequisite_task_ids_json,difficulty,source_id,
                               source_chunk_id,source_locator,generation_method,reason,included,status,scheduled_date)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (task_id, plan["id"], competency_id, f"Deneme mülakatı tekrarı: {question['question_type']}",
                         f"{score:.1f} puanlı deneme kanıtı için kaynak/rubric bağlı tekrar.", "tekrar", 30, position,
                         "[]", "orta", question["source_id"], question["source_chunk_id"], question["source_locator"],
                         "deneme mülakatı sonucundan deterministik", f"Mock {mock_id} puanı 60 altı: {score:.1f}",
                         1, "pending", date.today().isoformat()),
                    )
                    conn.execute(
                        """INSERT INTO interview_adaptive_decisions(profile_id,competency_id,decision_type,reason,
                               payload_json,applied,created_at) VALUES (?,?,?,?,?,1,?)""",
                        (mock["profile_id"], competency_id, "review_task_added",
                         "Düşük deneme mülakatı kanıtı için açık plana tekrar görevi eklendi; tamamlanmış görevler korunuyor.",
                         _json({"mock_id": mock_id, "task_id": task_id, "score": score}), now),
                    )

    def extract_job_requirements(self, source_ids: list[str]) -> dict[str, Any]:
        if not source_ids:
            raise ValueError("En az bir onaylanmış web kaynağı seçin.")
        with self.database.connection() as conn:
            placeholders = ",".join("?" for _ in source_ids)
            rows = conn.execute(
                f"""SELECT id,title,source_url,accessed_at,path AS snapshot_path
                    FROM source_documents WHERE id IN ({placeholders}) AND status='active' AND source_kind='web'""",
                source_ids,
            ).fetchall()
        if {row["id"] for row in rows} != set(source_ids):
            raise ValueError("İş ilanı analizi yalnız kullanıcı tarafından onaylanmış web snapshot'larını kullanabilir.")
        chunks = self.database.source_chunks(source_ids)
        terms = {
            "Python": ("programming_languages", ("python",)),
            "Java": ("programming_languages", ("java",)),
            "C#": ("programming_languages", ("c#", ".net")),
            "SQL": ("databases", ("sql", "postgres", "mysql")),
            "MongoDB": ("databases", ("mongodb",)),
            "REST API": ("frameworks", ("rest", "api", "http")),
            "FastAPI": ("frameworks", ("fastapi",)),
            "Django": ("frameworks", ("django",)),
            "Git": ("preferred_skills", ("git",)),
            "Docker": ("cloud_devops", ("docker", "container")),
            "Kubernetes": ("cloud_devops", ("kubernetes",)),
            "Cloud": ("cloud_devops", ("aws", "azure", "gcp", "cloud")),
            "CI/CD": ("cloud_devops", ("ci/cd", "pipeline")),
            "Testing": ("mandatory_skills", ("pytest", "unit test", "testing", "test")),
            "Caching": ("technical_responsibilities", ("cache", "redis")),
            "Async": ("technical_responsibilities", ("async", "asynchronous")),
        }
        found = []
        for skill, (category, needles) in terms.items():
            evidence = []
            for chunk in chunks:
                text = f"{chunk['heading']} {chunk['body']}".casefold()
                if any(term in text for term in needles):
                    evidence.append({"source_id": chunk["document_id"], "chunk_id": chunk["id"],
                                     "url": chunk.get("source_url"), "section": chunk["section_path"],
                                     "requirement_kind": (
                                         "preferred" if any(term in text for term in ("preferred", "nice to have", "tercih", "artı"))
                                         else "mandatory" if any(term in text for term in ("required", "must", "zorunlu", "aranan"))
                                         else "mentioned"
                                     )})
            if evidence:
                found.append({"skill": skill, "source_count": len({item['source_id'] for item in evidence}),
                              "mention_chunks": len(evidence), "category": category, "evidence": evidence})
        found.sort(key=lambda item: (-item["source_count"], -item["mention_chunks"], item["skill"]))
        all_text = " ".join(f"{chunk['heading']} {chunk['body']}" for chunk in chunks).casefold()
        level = next((value for value in ("intern", "junior", "mid", "senior", "stajyer") if value in all_text), "belirtilmedi")
        grouped = {category: [item["skill"] for item in found if item["category"] == category]
                   for category in ("mandatory_skills", "preferred_skills", "programming_languages", "frameworks",
                                    "databases", "cloud_devops", "technical_responsibilities")}
        return {
            "sources": [dict(row) for row in rows], "position_title": " / ".join(sorted({row["title"] for row in rows})),
            "level": level, "requirements": grouped, "skills": found,
            "notice": "Bu sonuç yalnız seçilen ilan snapshot'larındaki terim sıklığıdır; sektörün tamamını temsil etmez.",
        }

    def foundry_rubric_review(
        self, question: dict[str, Any], response: str, deterministic: dict[str, Any], *, profile_id: int | None = None,
    ) -> dict[str, Any]:
        cache_key = hashlib.sha256(_json({
            "question": question["id"], "response": response, "rubric": question["rubric"],
            "deterministic": deterministic, "model": self.settings.foundry_model,
            "prompt_version": FOUNDRY_PROMPT_VERSION,
            "source_set_hash": hashlib.sha256(_json([question["source_id"], question["source_chunk_id"]]).encode("utf-8")).hexdigest(),
        }).encode("utf-8")).hexdigest()
        namespace = f"foundry-rubric:{profile_id if profile_id is not None else 'anonymous'}"
        cached = self._cache_get(namespace, cache_key, RUBRIC_VERSION)
        if cached:
            return {**cached, "cache_hit": True}
        result = FoundryAdapter(self.settings).structured_json(
            [{"role": "system", "content": (
                "Yalnız kullanıcı yanıtı ve verilen rubric üzerinden değerlendir. JSON dışında yanıt verme. "
                "scores, explanation, evidence, question_id ve source_id anahtarlarını üret; kimlikleri aynen kopyala. "
                "evidence yalnız kullanıcı yanıtından kısa ve birebir alıntılar içersin."
            )},
             {"role": "user", "content": _json({"question": question["prompt"], "response": response,
                                                   "rubric": question["rubric"], "deterministic": deterministic,
                                                   "source_id": question["source_id"],
                                                   "source_chunk_id": question["source_chunk_id"]})}],
            required_keys={"scores", "explanation", "evidence", "question_id", "source_id"}, max_tokens=1000,
        )
        if result.get("question_id") != question["id"] or result.get("source_id") != question["source_id"]:
            raise ValueError("Foundry değerlendirmesi soru/kaynak kimliğiyle eşleşmiyor; sonuç kaydedilmedi.")
        scores = result.get("scores")
        if not isinstance(scores, dict) or any(not isinstance(value, (int, float)) or not 0 <= value <= 100 for value in scores.values()):
            raise ValueError("Foundry rubric puanları 0-100 şemasına uymuyor; sonuç kaydedilmedi.")
        evidence = result.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("Foundry değerlendirmesi kullanıcı yanıtından kanıt taşımıyor; sonuç kaydedilmedi.")
        normalized_response = response.casefold()
        if any(not isinstance(item, str) or item.casefold() not in normalized_response for item in evidence):
            raise ValueError("Foundry kanıtı kullanıcı yanıtında doğrulanamadı; sonuç kaydedilmedi.")
        self._cache_put(namespace, cache_key, RUBRIC_VERSION, result)
        return {**result, "cache_hit": False}

    def cache_status(self) -> dict[str, Any]:
        with self.database.connection() as conn:
            analysis = conn.execute("SELECT COUNT(*),COALESCE(SUM(size_bytes),0),COALESCE(SUM(hit_count),0) FROM analysis_cache").fetchone()
            embeddings = conn.execute("SELECT COUNT(*),COALESCE(SUM(LENGTH(vector_blob)),0),COALESCE(SUM(hit_count),0) FROM embedding_cache").fetchone()
        return {
            "analysis_entries": analysis[0], "analysis_bytes": analysis[1], "analysis_hits": analysis[2],
            "analysis_misses": analysis[0],
            "embedding_entries": embeddings[0], "embedding_bytes": embeddings[1], "embedding_hits": embeddings[2],
            "embedding_misses": embeddings[0],
        }

    def clear_cache(self, confirmation: str) -> dict[str, int]:
        if confirmation != "YEREL ÖNBELLEĞİ TEMİZLE":
            raise ValueError("Önbellek temizlemek için tam olarak 'YEREL ÖNBELLEĞİ TEMİZLE' yazılmalıdır.")
        with self.database.connection() as conn:
            analysis = conn.execute("DELETE FROM analysis_cache").rowcount
            embeddings = conn.execute("DELETE FROM embedding_cache").rowcount
        return {"analysis_removed": analysis, "embedding_removed": embeddings}

    def dashboard(self, profile_id: int) -> dict[str, Any]:
        career = self.career_profile(profile_id)
        if not career:
            return {"career_profile": None, "empty": True}
        gap = self.latest_gap_report(profile_id)
        plan = self.plan(profile_id)
        with self.database.connection() as conn:
            latest_mock = conn.execute(
                "SELECT * FROM mock_interviews WHERE profile_id=? ORDER BY started_at DESC LIMIT 1", (profile_id,)
            ).fetchone()
            latest_code = conn.execute(
                """SELECT ca.* FROM coding_attempts ca JOIN interview_assessment_sessions s ON s.id=ca.session_id
                   WHERE s.profile_id=? ORDER BY ca.created_at DESC LIMIT 1""", (profile_id,),
            ).fetchone()
            adaptive = [dict(row) for row in conn.execute(
                "SELECT * FROM interview_adaptive_decisions WHERE profile_id=? ORDER BY created_at DESC LIMIT 10", (profile_id,)
            )]
            today_tasks = [dict(row) for row in conn.execute(
                """SELECT t.* FROM interview_plan_tasks t JOIN interview_plans p ON p.id=t.plan_id
                   WHERE p.profile_id=? AND p.status='confirmed' AND t.included=1
                     AND t.status IN ('pending','in_progress','needs_review') AND t.scheduled_date<=?
                   ORDER BY t.position""", (profile_id, date.today().isoformat()),
            )]
            algorithm_row = conn.execute(
                """SELECT AVG(r.correctness_score) FROM interview_assessment_responses r
                   JOIN interview_assessment_sessions s ON s.id=r.session_id
                   JOIN interview_questions q ON q.id=r.question_id
                   WHERE s.profile_id=? AND q.question_type='algorithm' AND r.skipped=0""", (profile_id,),
            ).fetchone()
        target = date.fromisoformat(career["target_interview_date"])
        tasks = plan["tasks"] if plan else []
        open_tasks = [item for item in tasks if item["included"] and item["status"] not in {"completed", "skipped"}]
        remaining_minutes = sum(item["estimated_minutes"] for item in open_tasks)
        remaining_days = (target - date.today()).days
        capacity = max(1, career["daily_minutes"] * career["weekly_days"] * max(0, remaining_days) / 7)
        risk = "Hedef tarih geçti" if remaining_days < 0 else "Risk altında" if remaining_minutes > capacity else "Programla uyumlu"
        items = gap["items"] if gap else []
        latest_mock_data = dict(latest_mock) if latest_mock else None
        system_scores: list[float] = []
        star_scores: list[float] = []
        if latest_mock_data:
            rubric_scores = json.loads(latest_mock_data["rubric_scores_json"])
            questions = {item["id"]: item for item in self._questions(latest_mock_data["track_id"], json.loads(latest_mock_data["question_ids_json"]))}
            for question_id, score in rubric_scores.items():
                if questions[question_id]["question_type"] == "system-design":
                    dimensions = score.get("evidence", {}).get("rubric_scores", {})
                    if dimensions:
                        system_scores.append(sum(dimensions.values()) / len(dimensions) * 25)
                if questions[question_id]["question_type"] == "behavioral":
                    star_scores.append(float(score.get("evidence", {}).get("star", {}).get("completion_percentage", 0)))
        sources = []
        seen_sources: set[tuple[Any, Any]] = set()
        for item in items:
            for source in item.get("sources", []):
                identity = (source.get("source_id"), source.get("source_chunk_id"))
                if identity not in seen_sources:
                    seen_sources.add(identity)
                    sources.append(source)
        return {
            "career_profile": career, "gap": gap, "plan": plan,
            "target_role": career["target_role"], "target_level": career["target_level"],
            "overall_score": gap["overall_score"] if gap else None,
            "confidence_score": gap["confidence_score"] if gap else None,
            "remaining_days": remaining_days, "remaining_minutes": remaining_minutes, "risk": risk,
            "top_gaps": items[:3], "strongest": sorted(items, key=lambda item: -item["proficiency_score"])[:3],
            "weakest": sorted(items, key=lambda item: item["proficiency_score"])[:3],
            "today_tasks": today_tasks, "upcoming_mock": latest_mock_data if latest_mock_data and latest_mock_data["status"] in {"active", "paused"} else None,
            "latest_mock": latest_mock_data,
            "latest_code": dict(latest_code) if latest_code else None,
            "algorithm_success_rate": round(float(algorithm_row[0]), 1) if algorithm_row and algorithm_row[0] is not None else None,
            "system_design_score": round(sum(system_scores) / len(system_scores), 1) if system_scores else None,
            "star_completion_rate": round(sum(star_scores) / len(star_scores), 1) if star_scores else None,
            "sources": sources, "adaptive_decisions": adaptive, "cache": self.cache_status(), "empty": False,
        }
