from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class DatabaseError(RuntimeError):
    """Kullanıcıya gösterilebilir yerel veritabanı hatası."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            goal TEXT NOT NULL,
            preferred_route TEXT NOT NULL,
            weekly_days INTEGER NOT NULL CHECK(weekly_days BETWEEN 1 AND 7),
            daily_minutes INTEGER NOT NULL CHECK(daily_minutes BETWEEN 10 AND 720),
            preferred_days TEXT NOT NULL,
            start_date TEXT NOT NULL,
            target_end_date TEXT NOT NULL,
            theory_practice_preference TEXT NOT NULL,
            completed_topics TEXT NOT NULL DEFAULT '[]',
            difficult_topics TEXT NOT NULL DEFAULT '[]',
            review_preference TEXT NOT NULL,
            declared_level TEXT NOT NULL DEFAULT 'Başlangıç',
            measured_level TEXT,
            assessment_status TEXT NOT NULL DEFAULT 'user_declared',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS profile_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            skill_name TEXT NOT NULL,
            declared_level TEXT NOT NULL,
            UNIQUE(profile_id, skill_name)
        );

        CREATE TABLE IF NOT EXISTS assessment_questions (
            id TEXT PRIMARY KEY,
            route TEXT NOT NULL,
            question TEXT NOT NULL,
            options_json TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 1,
            source_hint TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS assessment_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            route TEXT NOT NULL,
            answers_json TEXT NOT NULL,
            score REAL,
            level TEXT NOT NULL,
            skipped INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_documents (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            route TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
            section_path TEXT NOT NULL,
            heading TEXT NOT NULL,
            phase TEXT,
            week TEXT,
            day TEXT,
            body TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            position INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS learning_routes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_document TEXT NOT NULL,
            description TEXT NOT NULL,
            task_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS learning_tasks (
            id TEXT PRIMARY KEY,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            source_document TEXT NOT NULL,
            source_section TEXT NOT NULL,
            source_chunk_id TEXT NOT NULL,
            route TEXT NOT NULL,
            phase TEXT,
            week TEXT,
            day TEXT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            task_type TEXT NOT NULL,
            estimated_minutes INTEGER NOT NULL CHECK(estimated_minutes > 0),
            estimated_is_system INTEGER NOT NULL DEFAULT 1,
            position INTEGER NOT NULL,
            prerequisite_task_ids TEXT NOT NULL DEFAULT '[]',
            parent_task_id TEXT,
            is_critical INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_progress','completed','postponed','skipped','needs_review')),
            scheduled_date TEXT,
            started_at TEXT,
            completed_at TEXT,
            actual_minutes INTEGER CHECK(actual_minutes IS NULL OR actual_minutes >= 0),
            difficulty_rating INTEGER CHECK(difficulty_rating IS NULL OR difficulty_rating BETWEEN 1 AND 5),
            quiz_score REAL CHECK(quiz_score IS NULL OR (quiz_score >= 0 AND quiz_score <= 100)),
            user_note TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            needs_review INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(
                (status = 'completed' AND completed_at IS NOT NULL AND actual_minutes IS NOT NULL)
                OR (status <> 'completed' AND completed_at IS NULL)
            )
        );

        CREATE TABLE IF NOT EXISTS task_dependencies (
            task_id TEXT NOT NULL REFERENCES learning_tasks(id) ON DELETE CASCADE,
            prerequisite_task_id TEXT NOT NULL REFERENCES learning_tasks(id) ON DELETE CASCADE,
            PRIMARY KEY(task_id, prerequisite_task_id),
            CHECK(task_id <> prerequisite_task_id)
        );

        CREATE TABLE IF NOT EXISTS task_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL REFERENCES learning_tasks(id) ON DELETE CASCADE,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            from_status TEXT,
            to_status TEXT NOT NULL,
            actual_minutes INTEGER,
            difficulty_rating INTEGER,
            quiz_score REAL,
            note TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            session_date TEXT NOT NULL,
            planned_minutes INTEGER NOT NULL DEFAULT 0,
            actual_minutes INTEGER NOT NULL DEFAULT 0,
            completed_tasks INTEGER NOT NULL DEFAULT 0,
            UNIQUE(profile_id, session_date)
        );

        CREATE TABLE IF NOT EXISTS weekly_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            week_start TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(profile_id, week_start)
        );

        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            content TEXT NOT NULL,
            citations_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS application_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_profile_date ON learning_tasks(profile_id, scheduled_date);
        CREATE INDEX IF NOT EXISTS idx_tasks_profile_status ON learning_tasks(profile_id, status);
        CREATE INDEX IF NOT EXISTS idx_chunks_document_position ON source_chunks(document_id, position);
        """,
    ),
    (
        2,
        """
        ALTER TABLE source_documents ADD COLUMN original_filename TEXT;
        ALTER TABLE source_documents ADD COLUMN mime_type TEXT;
        ALTER TABLE source_documents ADD COLUMN origin TEXT NOT NULL DEFAULT 'builtin'
            CHECK(origin IN ('builtin','uploaded'));
        ALTER TABLE source_documents ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active','archived'));
        ALTER TABLE source_documents ADD COLUMN uploaded_at TEXT;
        ALTER TABLE source_documents ADD COLUMN archived_at TEXT;
        ALTER TABLE source_chunks ADD COLUMN page INTEGER CHECK(page IS NULL OR page > 0);
        ALTER TABLE learning_tasks ADD COLUMN source_page INTEGER CHECK(source_page IS NULL OR source_page > 0);
        ALTER TABLE learning_tasks ADD COLUMN provenance TEXT NOT NULL DEFAULT 'belgeden çıkarıldı'
            CHECK(provenance IN ('belgeden çıkarıldı','sistem tarafından oluşturulan alıştırma','belge dışı ön koşul önerisi'));

        CREATE UNIQUE INDEX IF NOT EXISTS idx_source_documents_sha256 ON source_documents(sha256);
        CREATE INDEX IF NOT EXISTS idx_source_documents_origin_status ON source_documents(origin, status);

        CREATE TABLE IF NOT EXISTS custom_routes (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
            profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            goal TEXT NOT NULL,
            declared_level TEXT NOT NULL,
            weekly_days INTEGER NOT NULL CHECK(weekly_days BETWEEN 1 AND 7),
            daily_minutes INTEGER NOT NULL CHECK(daily_minutes BETWEEN 10 AND 720),
            preferred_days_json TEXT NOT NULL,
            start_date TEXT NOT NULL,
            target_end_date TEXT NOT NULL,
            theory_practice_preference TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','confirmed','archived')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS custom_route_tasks (
            id TEXT PRIMARY KEY,
            route_id TEXT NOT NULL REFERENCES custom_routes(id) ON DELETE CASCADE,
            source_document TEXT NOT NULL,
            source_section TEXT NOT NULL,
            source_chunk_id TEXT NOT NULL,
            source_page INTEGER CHECK(source_page IS NULL OR source_page > 0),
            phase TEXT,
            week TEXT,
            day TEXT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            task_type TEXT NOT NULL,
            estimated_minutes INTEGER NOT NULL CHECK(estimated_minutes > 0),
            provenance TEXT NOT NULL CHECK(provenance IN ('belgeden çıkarıldı','sistem tarafından oluşturulan alıştırma','belge dışı ön koşul önerisi')),
            position INTEGER NOT NULL,
            included INTEGER NOT NULL DEFAULT 1 CHECK(included IN (0,1)),
            prerequisite_task_ids TEXT NOT NULL DEFAULT '[]',
            UNIQUE(route_id, position)
        );

        CREATE INDEX IF NOT EXISTS idx_custom_routes_document ON custom_routes(document_id, status);
        CREATE INDEX IF NOT EXISTS idx_custom_route_tasks_route ON custom_route_tasks(route_id, position);
        """,
    ),
    (
        3,
        """
        ALTER TABLE profiles ADD COLUMN goal_description TEXT NOT NULL DEFAULT '';
        ALTER TABLE profiles ADD COLUMN source_method TEXT NOT NULL DEFAULT 'Hazır rotalardan seç';
        ALTER TABLE source_documents ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'document';
        ALTER TABLE source_documents ADD COLUMN source_url TEXT;
        ALTER TABLE source_documents ADD COLUMN accessed_at TEXT;
        ALTER TABLE source_chunks ADD COLUMN extraction_method TEXT NOT NULL DEFAULT 'native';
        ALTER TABLE custom_routes ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE custom_routes ADD COLUMN sufficiency_approved INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE custom_routes ADD COLUMN source_set_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE custom_route_tasks ADD COLUMN module TEXT;
        ALTER TABLE custom_route_tasks ADD COLUMN confidence_explanation TEXT NOT NULL DEFAULT '';
        ALTER TABLE custom_route_tasks ADD COLUMN generation_type TEXT NOT NULL DEFAULT 'belgeden çıkarıldı';
        ALTER TABLE learning_tasks ADD COLUMN module TEXT;
        ALTER TABLE learning_tasks ADD COLUMN confidence_explanation TEXT NOT NULL DEFAULT '';
        ALTER TABLE learning_tasks ADD COLUMN generation_type TEXT NOT NULL DEFAULT 'belgeden çıkarıldı';

        CREATE TABLE IF NOT EXISTS route_sources (
            route_id TEXT NOT NULL,
            source_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
            source_type TEXT NOT NULL,
            source_order INTEGER NOT NULL,
            source_role TEXT NOT NULL DEFAULT 'auxiliary' CHECK(source_role IN ('primary','auxiliary')),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
            approved_at TEXT,
            PRIMARY KEY(route_id, source_id)
        );

        CREATE TABLE IF NOT EXISTS web_sources (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            normalized_url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            domain TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL,
            reliability_class TEXT NOT NULL,
            published_at TEXT,
            accessed_at TEXT,
            status TEXT NOT NULL DEFAULT 'discovered' CHECK(status IN ('discovered','approved','archived','failed')),
            document_id TEXT REFERENCES source_documents(id) ON DELETE SET NULL,
            content_sha256 TEXT,
            snapshot_path TEXT,
            mime_type TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sufficiency_reports (
            id TEXT PRIMARY KEY,
            route_id TEXT NOT NULL,
            profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
            goal TEXT NOT NULL,
            report_json TEXT NOT NULL,
            coverage_score REAL NOT NULL CHECK(coverage_score BETWEEN 0 AND 100),
            can_generate INTEGER NOT NULL CHECK(can_generate IN (0,1)),
            approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)),
            created_at TEXT NOT NULL,
            approved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS task_sources (
            task_id TEXT NOT NULL,
            task_scope TEXT NOT NULL CHECK(task_scope IN ('draft','plan','quiz')),
            source_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
            chunk_id TEXT NOT NULL,
            section_path TEXT,
            page INTEGER,
            url TEXT,
            PRIMARY KEY(task_id, task_scope, source_id, chunk_id)
        );

        CREATE TABLE IF NOT EXISTS source_conflicts (
            id TEXT PRIMARY KEY,
            route_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            source_ids_json TEXT NOT NULL,
            explanation TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS custom_route_versions (
            id TEXT PRIMARY KEY,
            route_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(route_id, version)
        );

        CREATE TABLE IF NOT EXISTS custom_quiz_questions (
            id TEXT PRIMARY KEY,
            route_id TEXT NOT NULL,
            question TEXT NOT NULL,
            options_json TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            explanation TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            topic TEXT NOT NULL,
            source_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
            chunk_id TEXT NOT NULL,
            source_locator TEXT NOT NULL,
            generation_method TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS custom_quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            route_id TEXT NOT NULL,
            answers_json TEXT NOT NULL,
            score REAL,
            skipped INTEGER NOT NULL DEFAULT 0 CHECK(skipped IN (0,1)),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS focus_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            task_id TEXT REFERENCES learning_tasks(id) ON DELETE SET NULL,
            status TEXT NOT NULL CHECK(status IN ('running','paused','finished','reset')),
            started_at TEXT NOT NULL,
            resumed_at TEXT,
            paused_at TEXT,
            finished_at TEXT,
            accumulated_seconds INTEGER NOT NULL DEFAULT 0 CHECK(accumulated_seconds >= 0),
            updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_focus_one_active
            ON focus_sessions(profile_id) WHERE status IN ('running','paused');

        CREATE TABLE IF NOT EXISTS adaptive_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            task_id TEXT REFERENCES learning_tasks(id) ON DELETE SET NULL,
            decision_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            applied INTEGER NOT NULL DEFAULT 0 CHECK(applied IN (0,1)),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS document_index_status (
            document_id TEXT PRIMARY KEY REFERENCES source_documents(id) ON DELETE CASCADE,
            content_sha256 TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            vector_start INTEGER NOT NULL,
            vector_end INTEGER NOT NULL,
            backend TEXT NOT NULL,
            status TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_route_sources_route ON route_sources(route_id, source_order);
        CREATE INDEX IF NOT EXISTS idx_web_sources_status ON web_sources(status, domain);
        CREATE INDEX IF NOT EXISTS idx_reports_route ON sufficiency_reports(route_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_task_sources_task ON task_sources(task_id, task_scope);
        CREATE INDEX IF NOT EXISTS idx_quiz_route ON custom_quiz_questions(route_id);
        CREATE INDEX IF NOT EXISTS idx_adaptive_profile ON adaptive_decisions(profile_id, created_at);
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS career_profiles (
            profile_id INTEGER PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
            department TEXT NOT NULL,
            education_type TEXT,
            education_status TEXT NOT NULL,
            experience TEXT NOT NULL,
            target_track TEXT NOT NULL,
            target_role TEXT NOT NULL,
            target_level TEXT NOT NULL CHECK(target_level IN ('intern','junior','mid','senior')),
            preferred_language TEXT NOT NULL,
            other_technologies_json TEXT NOT NULL DEFAULT '[]',
            company_type TEXT NOT NULL,
            interview_language TEXT NOT NULL,
            target_interview_date TEXT NOT NULL,
            weekly_days INTEGER NOT NULL CHECK(weekly_days BETWEEN 1 AND 7),
            daily_minutes INTEGER NOT NULL CHECK(daily_minutes BETWEEN 10 AND 720),
            preferred_days_json TEXT NOT NULL DEFAULT '[]',
            declared_strengths_json TEXT NOT NULL DEFAULT '[]',
            declared_weaknesses_json TEXT NOT NULL DEFAULT '[]',
            job_url TEXT,
            job_text TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS interview_tracks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            support_level TEXT NOT NULL,
            taxonomy_version TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS interview_competencies (
            id TEXT PRIMARY KEY,
            track_id TEXT NOT NULL REFERENCES interview_tracks(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            level TEXT NOT NULL,
            role_weight REAL NOT NULL CHECK(role_weight > 0 AND role_weight <= 1),
            measurement_methods_json TEXT NOT NULL,
            taxonomy_version TEXT NOT NULL,
            UNIQUE(track_id, name)
        );

        CREATE TABLE IF NOT EXISTS competency_prerequisites (
            competency_id TEXT NOT NULL REFERENCES interview_competencies(id) ON DELETE CASCADE,
            prerequisite_id TEXT NOT NULL REFERENCES interview_competencies(id) ON DELETE CASCADE,
            PRIMARY KEY(competency_id, prerequisite_id),
            CHECK(competency_id <> prerequisite_id)
        );

        CREATE TABLE IF NOT EXISTS interview_questions (
            id TEXT PRIMARY KEY,
            track_id TEXT NOT NULL REFERENCES interview_tracks(id),
            competency_id TEXT NOT NULL REFERENCES interview_competencies(id),
            target_level TEXT NOT NULL,
            question_type TEXT NOT NULL,
            prompt TEXT NOT NULL,
            options_json TEXT NOT NULL DEFAULT '[]',
            correct_answer TEXT,
            rubric_json TEXT NOT NULL DEFAULT '{}',
            estimated_seconds INTEGER NOT NULL CHECK(estimated_seconds BETWEEN 30 AND 7200),
            difficulty TEXT NOT NULL,
            prerequisite_ids_json TEXT NOT NULL DEFAULT '[]',
            source_id TEXT NOT NULL,
            source_chunk_id TEXT,
            source_locator TEXT,
            generation_method TEXT NOT NULL,
            bank_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived'))
        );

        CREATE TABLE IF NOT EXISTS interview_assessment_sessions (
            id TEXT PRIMARY KEY,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            track_id TEXT NOT NULL REFERENCES interview_tracks(id),
            target_level TEXT NOT NULL,
            seed INTEGER NOT NULL,
            question_ids_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','paused','completed','abandoned')),
            accumulated_seconds INTEGER NOT NULL DEFAULT 0 CHECK(accumulated_seconds >= 0),
            resumed_at TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS interview_assessment_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES interview_assessment_sessions(id) ON DELETE CASCADE,
            question_id TEXT NOT NULL REFERENCES interview_questions(id),
            response_text TEXT NOT NULL,
            skipped INTEGER NOT NULL DEFAULT 0 CHECK(skipped IN (0,1)),
            correctness_score REAL,
            code_test_score REAL,
            reasoning_score REAL,
            complexity_score REAL,
            communication_score REAL,
            time_score REAL,
            elapsed_seconds INTEGER NOT NULL DEFAULT 0 CHECK(elapsed_seconds >= 0),
            hint_count INTEGER NOT NULL DEFAULT 0 CHECK(hint_count >= 0),
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(session_id, question_id)
        );

        CREATE TABLE IF NOT EXISTS coding_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT REFERENCES interview_assessment_sessions(id) ON DELETE CASCADE,
            question_id TEXT NOT NULL REFERENCES interview_questions(id),
            code_sha256 TEXT NOT NULL,
            runner TEXT NOT NULL,
            status TEXT NOT NULL,
            passed_tests INTEGER NOT NULL DEFAULT 0,
            total_tests INTEGER NOT NULL DEFAULT 0,
            elapsed_ms INTEGER NOT NULL DEFAULT 0,
            output_excerpt TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS profile_competencies (
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            competency_id TEXT NOT NULL REFERENCES interview_competencies(id) ON DELETE CASCADE,
            proficiency_score REAL NOT NULL CHECK(proficiency_score BETWEEN 0 AND 100),
            confidence_score REAL NOT NULL CHECK(confidence_score BETWEEN 0 AND 100),
            evidence_json TEXT NOT NULL,
            scoring_version TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(profile_id, competency_id)
        );

        CREATE TABLE IF NOT EXISTS interview_gap_reports (
            id TEXT PRIMARY KEY,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            track_id TEXT NOT NULL REFERENCES interview_tracks(id),
            session_id TEXT REFERENCES interview_assessment_sessions(id),
            overall_score REAL NOT NULL CHECK(overall_score BETWEEN 0 AND 100),
            confidence_score REAL NOT NULL CHECK(confidence_score BETWEEN 0 AND 100),
            report_json TEXT NOT NULL,
            scoring_version TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS interview_plans (
            id TEXT PRIMARY KEY,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            gap_report_id TEXT NOT NULL REFERENCES interview_gap_reports(id),
            status TEXT NOT NULL CHECK(status IN ('draft','confirmed','archived')),
            target_interview_date TEXT NOT NULL,
            weekly_days INTEGER NOT NULL,
            daily_minutes INTEGER NOT NULL,
            planner_version TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS interview_plan_tasks (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES interview_plans(id) ON DELETE CASCADE,
            competency_id TEXT NOT NULL REFERENCES interview_competencies(id),
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            task_type TEXT NOT NULL,
            estimated_minutes INTEGER NOT NULL CHECK(estimated_minutes BETWEEN 10 AND 720),
            position INTEGER NOT NULL,
            prerequisite_task_ids_json TEXT NOT NULL DEFAULT '[]',
            difficulty TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_chunk_id TEXT,
            source_locator TEXT,
            generation_method TEXT NOT NULL,
            reason TEXT NOT NULL,
            included INTEGER NOT NULL DEFAULT 1 CHECK(included IN (0,1)),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_progress','completed','skipped','needs_review')),
            scheduled_date TEXT,
            completed_at TEXT,
            UNIQUE(plan_id, position)
        );

        CREATE TABLE IF NOT EXISTS mock_interviews (
            id TEXT PRIMARY KEY,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            track_id TEXT NOT NULL REFERENCES interview_tracks(id),
            interview_type TEXT NOT NULL,
            target_level TEXT NOT NULL,
            question_ids_json TEXT NOT NULL,
            responses_json TEXT NOT NULL DEFAULT '{}',
            rubric_scores_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL CHECK(status IN ('active','paused','completed','abandoned')),
            accumulated_seconds INTEGER NOT NULL DEFAULT 0 CHECK(accumulated_seconds >= 0),
            resumed_at TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            report_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS interview_adaptive_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            competency_id TEXT REFERENCES interview_competencies(id),
            decision_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            applied INTEGER NOT NULL DEFAULT 0 CHECK(applied IN (0,1)),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS analysis_cache (
            namespace TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            version TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_accessed_at TEXT NOT NULL,
            PRIMARY KEY(namespace, cache_key)
        );

        CREATE TABLE IF NOT EXISTS embedding_cache (
            cache_key TEXT PRIMARY KEY,
            content_sha256 TEXT NOT NULL,
            backend TEXT NOT NULL,
            model TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            normalization_version TEXT NOT NULL,
            vector_blob BLOB NOT NULL,
            dimension INTEGER NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_accessed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_career_track ON career_profiles(target_track, target_level);
        CREATE INDEX IF NOT EXISTS idx_competencies_track ON interview_competencies(track_id, role_weight);
        CREATE INDEX IF NOT EXISTS idx_questions_track ON interview_questions(track_id, target_level, question_type, status);
        CREATE INDEX IF NOT EXISTS idx_assessment_profile ON interview_assessment_sessions(profile_id, started_at);
        CREATE INDEX IF NOT EXISTS idx_responses_session ON interview_assessment_responses(session_id, question_id);
        CREATE INDEX IF NOT EXISTS idx_profile_competencies_score ON profile_competencies(profile_id, proficiency_score);
        CREATE INDEX IF NOT EXISTS idx_gap_profile ON interview_gap_reports(profile_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_plan_profile ON interview_plans(profile_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_mock_profile ON mock_interviews(profile_id, started_at);
        CREATE INDEX IF NOT EXISTS idx_analysis_cache_lru ON analysis_cache(last_accessed_at);
        CREATE INDEX IF NOT EXISTS idx_embedding_cache_lru ON embedding_cache(last_accessed_at);
        """,
    ),
)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            return conn
        except sqlite3.DatabaseError as exc:
            raise DatabaseError(
                f"SQLite veritabanı açılamadı veya bozuk görünüyor: {self.path}. Yedekten geri yüklemeyi deneyin."
            ) from exc

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            raise DatabaseError(f"SQLite işlemi başarısız oldu: {exc}") from exc
        finally:
            conn.close()

    def migrate(self) -> list[int]:
        applied: list[int] = []
        with self.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            existing = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for version, sql in MIGRATIONS:
                if version in existing:
                    continue
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, utc_now()),
                )
                applied.append(version)
            check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise DatabaseError(f"SQLite bütünlük kontrolü başarısız: {check}")
        return applied

    def table_names(self) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return [row[0] for row in rows]

    def create_profile(self, data: dict[str, Any], skills: dict[str, str]) -> int:
        now = utc_now()
        fields = (
            "name", "goal", "preferred_route", "weekly_days", "daily_minutes",
            "preferred_days", "start_date", "target_end_date", "theory_practice_preference",
            "completed_topics", "difficult_topics", "review_preference", "declared_level",
        )
        values = []
        for field in fields:
            value = data[field]
            if field in {"preferred_days", "completed_topics", "difficult_topics"}:
                value = json.dumps(value, ensure_ascii=False)
            values.append(value)
        with self.connection() as conn:
            cur = conn.execute(
                f"INSERT INTO profiles ({','.join(fields)}, created_at, updated_at) VALUES ({','.join('?' for _ in fields)}, ?, ?)",
                (*values, now, now),
            )
            profile_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE profiles SET goal_description=?, source_method=? WHERE id=?",
                (str(data.get("goal_description", "")), str(data.get("source_method", "Hazır rotalardan seç")), profile_id),
            )
            conn.executemany(
                "INSERT INTO profile_skills(profile_id, skill_name, declared_level) VALUES (?, ?, ?)",
                [(profile_id, name, level) for name, level in skills.items()],
            )
        return profile_id

    def get_profile(self, profile_id: int | None = None) -> dict[str, Any] | None:
        with self.connection() as conn:
            if profile_id is None:
                row = conn.execute("SELECT * FROM profiles ORDER BY id DESC LIMIT 1").fetchone()
            else:
                row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            for field in ("preferred_days", "completed_topics", "difficult_topics"):
                result[field] = json.loads(result[field])
            skills = conn.execute(
                "SELECT skill_name, declared_level FROM profile_skills WHERE profile_id = ? ORDER BY skill_name",
                (result["id"],),
            ).fetchall()
            result["skills"] = {item[0]: item[1] for item in skills}
            return result

    def list_profiles(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM profiles ORDER BY id")]

    def delete_profile(self, profile_id: int, confirmation: str) -> None:
        expected = f"PROFİLİ SİL {profile_id}"
        if confirmation != expected:
            raise DatabaseError(f"Silme iptal edildi. Açık onay metni tam olarak '{expected}' olmalıdır.")
        with self.connection() as conn:
            cur = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
            if cur.rowcount == 0:
                raise DatabaseError(f"Profil bulunamadı: {profile_id}")

    def export_profile(self, profile_id: int, output_path: Path) -> Path:
        profile = self.get_profile(profile_id)
        if not profile:
            raise DatabaseError(f"Profil bulunamadı: {profile_id}")
        tables = {
            "tasks": ("SELECT * FROM learning_tasks WHERE profile_id = ? ORDER BY position", (profile_id,)),
            "progress": ("SELECT * FROM task_progress WHERE profile_id = ? ORDER BY id", (profile_id,)),
            "assessments": ("SELECT * FROM assessment_attempts WHERE profile_id = ? ORDER BY id", (profile_id,)),
            "sessions": ("SELECT * FROM daily_sessions WHERE profile_id = ? ORDER BY session_date", (profile_id,)),
            "reviews": ("SELECT * FROM weekly_reviews WHERE profile_id = ? ORDER BY week_start", (profile_id,)),
            "chat": ("SELECT * FROM chat_history WHERE profile_id = ? ORDER BY id", (profile_id,)),
            "career_profile": ("SELECT * FROM career_profiles WHERE profile_id = ?", (profile_id,)),
            "interview_assessments": ("SELECT * FROM interview_assessment_sessions WHERE profile_id = ? ORDER BY started_at", (profile_id,)),
            "interview_responses": (
                """SELECT r.* FROM interview_assessment_responses r JOIN interview_assessment_sessions s
                   ON s.id=r.session_id WHERE s.profile_id=? ORDER BY r.id""", (profile_id,),
            ),
            "interview_gap_reports": ("SELECT * FROM interview_gap_reports WHERE profile_id = ? ORDER BY created_at", (profile_id,)),
            "interview_plans": ("SELECT * FROM interview_plans WHERE profile_id = ? ORDER BY created_at", (profile_id,)),
            "interview_plan_tasks": (
                """SELECT t.* FROM interview_plan_tasks t JOIN interview_plans p ON p.id=t.plan_id
                   WHERE p.profile_id=? ORDER BY p.created_at,t.position""", (profile_id,),
            ),
            "mock_interviews": ("SELECT * FROM mock_interviews WHERE profile_id = ? ORDER BY started_at", (profile_id,)),
            "interview_adaptive_decisions": (
                "SELECT * FROM interview_adaptive_decisions WHERE profile_id = ? ORDER BY created_at", (profile_id,),
            ),
        }
        payload: dict[str, Any] = {"exported_at": utc_now(), "profile": profile}
        with self.connection() as conn:
            for key, (query, params) in tables.items():
                payload[key] = [dict(row) for row in conn.execute(query, params)]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    def backup(self, output_path: Path) -> Path:
        if not self.path.exists():
            raise DatabaseError("Yedeklenecek veritabanı henüz oluşturulmamış.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source, sqlite3.connect(output_path) as target:
            source.backup(target)
        return output_path

    def upsert_source_data(self, documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> None:
        now = utc_now()
        with self.connection() as conn:
            for doc in documents:
                conn.execute(
                    """INSERT INTO source_documents(id, filename, path, title, route, sha256, indexed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET path=excluded.path, title=excluded.title,
                           route=excluded.route, sha256=excluded.sha256, indexed_at=excluded.indexed_at""",
                    (doc["id"], doc["filename"], doc["path"], doc["title"], doc["route"], doc["sha256"], now),
                )
                conn.execute("DELETE FROM source_chunks WHERE document_id = ?", (doc["id"],))
            conn.executemany(
                """INSERT INTO source_chunks(id, document_id, section_path, heading, phase, week, day, page,
                                              body, content_hash, position, extraction_method)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"], item["document_id"], item["section_path"], item["heading"],
                        item.get("phase"), item.get("week"), item.get("day"), item.get("page"), item["body"],
                        item["content_hash"], item["order"], item.get("extraction_method", "native"),
                    )
                    for item in chunks
                ],
            )

    def source_document(self, document_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM source_documents WHERE id = ?", (document_id,)).fetchone()
        return dict(row) if row else None

    def source_document_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM source_documents WHERE sha256 = ?", (sha256,)).fetchone()
        return dict(row) if row else None

    def list_source_documents(
        self,
        *,
        origin: str | None = None,
        source_kind: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM source_documents WHERE 1=1"
        params: list[Any] = []
        if origin is not None:
            query += " AND origin = ?"
            params.append(origin)
        if source_kind is not None:
            query += " AND source_kind = ?"
            params.append(source_kind)
        if not include_archived:
            query += " AND status = 'active'"
        query += " ORDER BY COALESCE(uploaded_at, indexed_at), id"
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(query, params)]

    def register_uploaded_document(self, metadata: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO source_documents(
                       id, filename, original_filename, path, title, route, sha256, mime_type,
                       origin, status, uploaded_at, indexed_at, source_kind, source_url, accessed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', 'active', ?, ?, ?, ?, ?)""",
                (
                    metadata["id"], metadata["filename"], metadata["original_filename"],
                    metadata["path"], metadata["title"], metadata["route"], metadata["sha256"],
                    metadata["mime_type"], now, now, metadata.get("source_kind", "document"),
                    metadata.get("source_url"), metadata.get("accessed_at"),
                ),
            )
        result = self.source_document(metadata["id"])
        assert result is not None
        return result

    def remove_uploaded_document_record(self, document_id: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "DELETE FROM source_documents WHERE id = ? AND origin = 'uploaded'",
                (document_id,),
            )

    def archive_uploaded_document(self, document_id: str, confirmation: str) -> dict[str, Any]:
        expected = f"BELGEYİ ARŞİVLE {document_id}"
        if confirmation != expected:
            raise DatabaseError(f"Arşivleme iptal edildi. Onay metni tam olarak '{expected}' olmalıdır.")
        document = self.source_document(document_id)
        if not document:
            raise DatabaseError(f"Belge bulunamadı: {document_id}")
        if document.get("origin") != "uploaded":
            raise DatabaseError("Hazır üç kaynak belge arşivlenemez veya silinemez.")
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                "UPDATE source_documents SET status='archived', archived_at=? WHERE id=?",
                (now, document_id),
            )
            conn.execute(
                "UPDATE custom_routes SET status='archived', updated_at=? WHERE document_id=?",
                (now, document_id),
            )
            conn.execute(
                "UPDATE route_sources SET status='archived' WHERE source_id=?",
                (document_id,),
            )
        result = self.source_document(document_id)
        assert result is not None
        return result

    def save_custom_route(self, route: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
        now = utc_now()
        if not tasks:
            raise DatabaseError("Rota taslağı için en az bir görev gereklidir.")
        with self.connection() as conn:
            existing = conn.execute("SELECT status FROM custom_routes WHERE id=?", (route["id"],)).fetchone()
            if existing and existing[0] == "confirmed":
                raise DatabaseError("Onaylanmış rota otomatik olarak üzerine yazılamaz.")
            conn.execute(
                """INSERT INTO custom_routes(
                       id, document_id, profile_id, title, goal, declared_level, weekly_days,
                       daily_minutes, preferred_days_json, start_date, target_end_date,
                       theory_practice_preference, status, created_at, updated_at,
                       sufficiency_approved, source_set_hash, version
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET profile_id=excluded.profile_id, title=excluded.title,
                       goal=excluded.goal, declared_level=excluded.declared_level,
                       weekly_days=excluded.weekly_days, daily_minutes=excluded.daily_minutes,
                       preferred_days_json=excluded.preferred_days_json, start_date=excluded.start_date,
                       target_end_date=excluded.target_end_date,
                       theory_practice_preference=excluded.theory_practice_preference,
                       sufficiency_approved=excluded.sufficiency_approved,
                       source_set_hash=excluded.source_set_hash, version=excluded.version,
                       status='draft', updated_at=excluded.updated_at""",
                (
                    route["id"], route["document_id"], route.get("profile_id"), route["title"],
                    route["goal"], route["declared_level"], route["weekly_days"], route["daily_minutes"],
                    json.dumps(route["preferred_days"], ensure_ascii=False), route["start_date"],
                    route["target_end_date"], route["theory_practice_preference"], now, now,
                    int(bool(route.get("sufficiency_approved", False))), route.get("source_set_hash", ""),
                    int(route.get("version", 1)),
                ),
            )
            conn.execute("DELETE FROM custom_route_tasks WHERE route_id=?", (route["id"],))
            conn.executemany(
                """INSERT INTO custom_route_tasks(
                       id, route_id, source_document, source_section, source_chunk_id, source_page,
                       phase, week, day, title, description, task_type, estimated_minutes,
                       provenance, position, included, prerequisite_task_ids, module,
                       confidence_explanation, generation_type
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"], route["id"], item["source_document"], item["source_section"],
                        item["source_chunk_id"], item.get("source_page"), item.get("phase"),
                        item.get("week"), item.get("day"), item["title"], item["description"],
                        item["task_type"], int(item["estimated_minutes"]), item["provenance"],
                        int(item["position"]), int(item.get("included", True)),
                        json.dumps(item.get("prerequisite_task_ids", [])),
                        item.get("module"), item.get("confidence_explanation", ""),
                        item.get("generation_type", item["provenance"]),
                    )
                    for item in tasks
                ],
            )
        return route["id"]

    def custom_route(self, route_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT r.*, COALESCE(d.original_filename, d.filename) AS original_filename, d.filename AS stored_filename,
                          d.path AS document_path, d.status AS document_status
                   FROM custom_routes r JOIN source_documents d ON d.id=r.document_id
                   WHERE r.id=?""",
                (route_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["preferred_days"] = json.loads(result.pop("preferred_days_json"))
        return result

    def list_custom_routes(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        query = """SELECT r.*, d.original_filename FROM custom_routes r
                   JOIN source_documents d ON d.id=r.document_id"""
        if not include_archived:
            query += " WHERE r.status <> 'archived' AND d.status = 'active'"
        query += " ORDER BY r.updated_at DESC"
        with self.connection() as conn:
            rows = conn.execute(query).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["preferred_days"] = json.loads(item.pop("preferred_days_json"))
        return result

    def custom_route_tasks(self, route_id: str, *, included_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM custom_route_tasks WHERE route_id=?"
        if included_only:
            query += " AND included=1"
        query += " ORDER BY position, id"
        with self.connection() as conn:
            rows = conn.execute(query, (route_id,)).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["prerequisite_task_ids"] = json.loads(item["prerequisite_task_ids"])
        return result

    def update_custom_route_tasks(self, route_id: str, edits: list[dict[str, Any]]) -> int:
        route = self.custom_route(route_id)
        if not route or route["status"] != "draft":
            raise DatabaseError("Yalnızca onaylanmamış rota taslağı düzenlenebilir.")
        existing = {item["id"] for item in self.custom_route_tasks(route_id)}
        supplied = {str(item["id"]) for item in edits}
        if supplied != existing:
            raise DatabaseError("Rota görevi listesi eksik veya bu rotaya ait olmayan kimlik içeriyor.")
        ordered = sorted(edits, key=lambda item: (int(item["position"]), str(item["id"])))
        dependencies: dict[str, list[str]] = {}
        for index, item in enumerate(ordered):
            task_id = str(item["id"])
            requested = item.get("prerequisite_task_ids")
            if requested is None:
                requested = [str(ordered[index - 1]["id"])] if index and item.get("included", True) else []
            values = [str(value) for value in requested]
            if task_id in values or any(value not in existing for value in values):
                raise DatabaseError("Geçersiz görev ön koşulu seçildi.")
            dependencies[task_id] = values
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise DatabaseError("Ön koşul grafiğinde döngüye izin verilmez.")
            if node in visited:
                return
            visiting.add(node)
            for dependency in dependencies.get(node, []):
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in dependencies:
            visit(node)
        with self.connection() as conn:
            conn.execute("UPDATE custom_route_tasks SET position = -position WHERE route_id=?", (route_id,))
            for position, item in enumerate(ordered, start=1):
                title = str(item["title"]).strip()
                description = str(item.get("description", "")).strip()
                minutes = int(item["estimated_minutes"])
                if not title or minutes < 10 or minutes > 720:
                    raise DatabaseError("Görev adı boş olamaz; süre 10-720 dakika arasında olmalıdır.")
                conn.execute(
                    """UPDATE custom_route_tasks SET title=?, description=?, estimated_minutes=?, position=?, included=?,
                           prerequisite_task_ids=?, module=?, task_type=?
                       WHERE id=? AND route_id=?""",
                    (
                        title, description or title, minutes, position, int(bool(item.get("included", True))),
                        json.dumps(dependencies[str(item["id"])]), item.get("module"),
                        str(item.get("task_type") or "teori"),
                        item["id"], route_id,
                    ),
                )
            conn.execute("UPDATE custom_routes SET updated_at=? WHERE id=?", (utc_now(), route_id))
        return len(edits)

    def duplicate_custom_route_task(
        self,
        route_id: str,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        import hashlib

        route = self.custom_route(route_id)
        if not route or route["status"] != "draft":
            raise DatabaseError("Yalnızca rota taslağında görev çoğaltılabilir.")
        tasks = self.custom_route_tasks(route_id)
        source = next((item for item in tasks if item["id"] == task_id), None)
        if not source:
            raise DatabaseError("Çoğaltılacak görev bulunamadı.")
        requested_title = str(title or f"{source['title']} (kopya)").strip()
        requested_description = str(description or source["description"]).strip()
        if not requested_title:
            raise DatabaseError("Yeni görev adı boş olamaz.")
        new_id = "draft-" + hashlib.sha256(f"{task_id}|copy|{len(tasks)}".encode()).hexdigest()[:20]
        with self.connection() as conn:
            conn.execute("UPDATE custom_route_tasks SET position=-position WHERE route_id=? AND position>?", (route_id, source["position"]))
            conn.execute("UPDATE custom_route_tasks SET position=(-position)+1 WHERE route_id=? AND position<0", (route_id,))
            conn.execute(
                """INSERT INTO custom_route_tasks(
                       id, route_id, source_document, source_section, source_chunk_id, source_page,
                       phase, week, day, title, description, task_type, estimated_minutes, provenance,
                       position, included, prerequisite_task_ids, module, confidence_explanation, generation_type)
                   SELECT ?, route_id, source_document, source_section, source_chunk_id, source_page,
                          phase, week, day, ?, ?, task_type, estimated_minutes,
                          'sistem tarafından oluşturulan alıştırma', ?, 1, prerequisite_task_ids, module,
                          'Kullanıcı tarafından kaynak bağlı görevden çoğaltıldı.',
                          'sistem tarafından oluşturulan alıştırma'
                   FROM custom_route_tasks WHERE id=? AND route_id=?""",
                (
                    new_id, requested_title, requested_description,
                    int(source["position"]) + 1, task_id, route_id,
                ),
            )
        references = self.task_sources(task_id, "draft")
        if references:
            self.save_task_sources(new_id, "draft", references)
        return next(item for item in self.custom_route_tasks(route_id) if item["id"] == new_id)

    def create_custom_route_version(self, route_id: str) -> str:
        import hashlib

        route = self.custom_route(route_id)
        if not route or route["status"] != "confirmed":
            raise DatabaseError("Yalnızca onaylanmış rotadan yeni düzenleme sürümü oluşturulabilir.")
        tasks = self.custom_route_tasks(route_id)
        version = int(route.get("version") or 1) + 1
        new_route_id = f"{route_id}-v{version}"
        id_map = {
            item["id"]: "draft-" + hashlib.sha256(f"{new_route_id}|{item['id']}".encode()).hexdigest()[:20]
            for item in tasks
        }
        configuration = {
            "id": new_route_id,
            "document_id": route["document_id"],
            "profile_id": route.get("profile_id"),
            "title": route["title"],
            "goal": route["goal"],
            "declared_level": route["declared_level"],
            "weekly_days": route["weekly_days"],
            "daily_minutes": route["daily_minutes"],
            "preferred_days": route["preferred_days"],
            "start_date": route["start_date"],
            "target_end_date": route["target_end_date"],
            "theory_practice_preference": route["theory_practice_preference"],
            "sufficiency_approved": bool(route.get("sufficiency_approved")),
            "source_set_hash": route.get("source_set_hash", ""),
            "version": version,
        }
        cloned: list[dict[str, Any]] = []
        for item in tasks:
            cloned.append({
                **item,
                "id": id_map[item["id"]],
                "prerequisite_task_ids": [id_map[value] for value in item["prerequisite_task_ids"] if value in id_map],
            })
        snapshot_id = f"{route_id}-v{route.get('version', 1)}"
        with self.connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO custom_route_versions(id, route_id, version, snapshot_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (snapshot_id, route_id, int(route.get("version") or 1), json.dumps({"route": route, "tasks": tasks}, ensure_ascii=False), utc_now()),
            )
        self.save_custom_route(configuration, cloned)
        sources = self.route_sources(route_id, include_archived=True)
        self.set_route_sources(new_route_id, [
            {"source_id": item["source_id"], "source_type": item["source_type"], "source_role": item["source_role"],
             "approved_at": item.get("approved_at")}
            for item in sources if item["document_status"] == "active"
        ])
        for old_id, new_id in id_map.items():
            references = self.task_sources(old_id, "draft")
            if references:
                self.save_task_sources(new_id, "draft", references)
        return new_route_id

    def confirm_custom_route(self, route_id: str, profile_id: int) -> None:
        with self.connection() as conn:
            route = conn.execute("SELECT * FROM custom_routes WHERE id=?", (route_id,)).fetchone()
            if route is None or route["status"] != "draft":
                raise DatabaseError("Onaylanabilir rota taslağı bulunamadı.")
            changed = conn.execute(
                "UPDATE custom_routes SET status='confirmed', profile_id=?, updated_at=? WHERE id=? AND status='draft'",
                (profile_id, utc_now(), route_id),
            )
            conn.execute(
                """UPDATE profiles SET preferred_route=?, weekly_days=?, daily_minutes=?, preferred_days=?,
                       start_date=?, target_end_date=?, theory_practice_preference=?, declared_level=?,
                       measured_level=NULL, assessment_status='user_declared', updated_at=? WHERE id=?""",
                (
                    route_id, route["weekly_days"], route["daily_minutes"], route["preferred_days_json"],
                    route["start_date"], route["target_end_date"], route["theory_practice_preference"],
                    route["declared_level"], utc_now(), profile_id,
                ),
            )

    def save_assessment(
        self,
        profile_id: int,
        route: str,
        answers: dict[str, str],
        score: float | None,
        level: str,
        skipped: bool,
    ) -> int:
        with self.connection() as conn:
            cur = conn.execute(
                """INSERT INTO assessment_attempts(profile_id, route, answers_json, score, level, skipped, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (profile_id, route, json.dumps(answers, ensure_ascii=False), score, level, int(skipped), utc_now()),
            )
            conn.execute(
                "UPDATE profiles SET measured_level = ?, assessment_status = ?, updated_at = ? WHERE id = ?",
                (None if skipped else level, "user_declared" if skipped else "measured", utc_now(), profile_id),
            )
            return int(cur.lastrowid)

    def replace_profile_plan(self, profile_id: int, tasks: list[dict[str, Any]]) -> int:
        with self.connection() as conn:
            existing_progress = conn.execute(
                "SELECT COUNT(*) FROM learning_tasks WHERE profile_id = ? AND status <> 'pending'", (profile_id,)
            ).fetchone()[0]
            if existing_progress:
                raise DatabaseError(
                    "İlerleme geçmişi bulunan plan otomatik olarak silinmez. Mevcut görevleri yeniden planlayın."
                )
            conn.execute("DELETE FROM learning_tasks WHERE profile_id = ?", (profile_id,))
            for task in tasks:
                conn.execute(
                    """INSERT INTO learning_tasks(
                        id, profile_id, source_document, source_section, source_chunk_id, route,
                        phase, week, day, source_page, provenance, title, description, task_type, estimated_minutes,
                        estimated_is_system, position, prerequisite_task_ids, parent_task_id,
                        is_critical, status, scheduled_date, created_at, updated_at, module,
                        confidence_explanation, generation_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
                    (
                        task["id"], profile_id, task["source_document"], task["source_section"],
                        task["source_chunk_id"], task["route"], task.get("phase"), task.get("week"),
                        task.get("day"), task.get("source_page"), task.get("provenance", "belgeden çıkarıldı"),
                        task["title"], task["description"], task["task_type"],
                        task["estimated_minutes"], int(task.get("estimated_is_system", True)),
                        task["position"], json.dumps(task.get("prerequisite_task_ids", [])),
                        task.get("parent_task_id"), int(task.get("is_critical", False)),
                        task.get("scheduled_date"), utc_now(), utc_now(), task.get("module"),
                        task.get("confidence_explanation", ""),
                        task.get("generation_type", task.get("provenance", "belgeden çıkarıldı")),
                    ),
                )
            for task in tasks:
                for prerequisite in task.get("prerequisite_task_ids", []):
                    conn.execute(
                        "INSERT INTO task_dependencies(task_id, prerequisite_task_id) VALUES (?, ?)",
                        (task["id"], prerequisite),
                    )
        return len(tasks)

    def tasks(self, profile_id: int, *, scheduled_date: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM learning_tasks WHERE profile_id = ?"
        params: list[Any] = [profile_id]
        if scheduled_date is not None:
            query += " AND scheduled_date = ?"
            params.append(scheduled_date)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY scheduled_date, position"
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["prerequisite_task_ids"] = json.loads(item["prerequisite_task_ids"])
        return result

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM learning_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["prerequisite_task_ids"] = json.loads(result["prerequisite_task_ids"])
        return result

    def transition_task(
        self,
        task_id: str,
        new_status: str,
        *,
        actual_minutes: int | None = None,
        difficulty_rating: int | None = None,
        quiz_score: float | None = None,
        note: str | None = None,
        skip_confirmed: bool = False,
    ) -> dict[str, Any]:
        allowed = {
            "pending": {"in_progress", "completed", "postponed", "skipped", "needs_review"},
            "in_progress": {"completed", "postponed", "needs_review"},
            "postponed": {"pending", "in_progress", "needs_review"},
            "needs_review": {"in_progress", "completed", "postponed"},
            "skipped": {"pending"},
            "completed": {"needs_review"},
        }
        now = utc_now()
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM learning_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise DatabaseError(f"Görev bulunamadı: {task_id}")
            task = dict(row)
            old = task["status"]
            if new_status not in allowed.get(old, set()):
                raise DatabaseError(f"Geçersiz görev durumu geçişi: {old} → {new_status}")
            if new_status in {"in_progress", "completed"}:
                missing = conn.execute(
                    """SELECT d.prerequisite_task_id FROM task_dependencies d
                       JOIN learning_tasks p ON p.id = d.prerequisite_task_id
                       WHERE d.task_id = ? AND p.status <> 'completed'""",
                    (task_id,),
                ).fetchall()
                if missing:
                    raise DatabaseError(
                        "Ön koşullar tamamlanmadan görev başlatılamaz: " + ", ".join(item[0] for item in missing)
                    )
            if new_status == "completed" and (actual_minutes is None or actual_minutes < 0):
                raise DatabaseError("Görevi tamamlamak için gerçek çalışma süresi zorunludur.")
            if new_status == "skipped" and task["is_critical"] and not skip_confirmed:
                raise DatabaseError("Kritik görev kullanıcı onayı olmadan atlanamaz.")
            started_at = task["started_at"]
            completed_at = None
            if new_status == "in_progress" and not started_at:
                started_at = now
            if new_status == "completed":
                started_at = started_at or now
                completed_at = now
            review_flag = int(new_status == "needs_review" or (quiz_score is not None and quiz_score < 60))
            retry_count = task["retry_count"] + (1 if new_status == "needs_review" else 0)
            conn.execute(
                """UPDATE learning_tasks SET status=?, started_at=?, completed_at=?, actual_minutes=?,
                   difficulty_rating=?, quiz_score=?, user_note=?, needs_review=?, retry_count=?, updated_at=? WHERE id=?""",
                (
                    new_status, started_at, completed_at,
                    actual_minutes if actual_minutes is not None else task["actual_minutes"],
                    difficulty_rating if difficulty_rating is not None else task["difficulty_rating"],
                    quiz_score if quiz_score is not None else task["quiz_score"],
                    note if note is not None else task["user_note"], review_flag, retry_count, now, task_id,
                ),
            )
            conn.execute(
                """INSERT INTO task_progress(task_id, profile_id, from_status, to_status,
                   actual_minutes, difficulty_rating, quiz_score, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, task["profile_id"], old, new_status, actual_minutes, difficulty_rating, quiz_score, note, now),
            )
            if new_status == "completed":
                session_date = date.today().isoformat()
                conn.execute(
                    """INSERT INTO daily_sessions(profile_id, session_date, planned_minutes, actual_minutes, completed_tasks)
                       VALUES (?, ?, 0, ?, 1)
                       ON CONFLICT(profile_id, session_date) DO UPDATE SET
                           actual_minutes=actual_minutes+excluded.actual_minutes,
                           completed_tasks=completed_tasks+1""",
                    (task["profile_id"], session_date, actual_minutes),
                )
        result = self.get_task(task_id)
        assert result is not None
        return result

    def reschedule(self, task_id: str, new_date: str) -> None:
        with self.connection() as conn:
            row = conn.execute("SELECT status FROM learning_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise DatabaseError(f"Görev bulunamadı: {task_id}")
            if row[0] == "completed":
                raise DatabaseError("Tamamlanmış görev yeniden planlanamaz.")
            conn.execute(
                "UPDATE learning_tasks SET scheduled_date=?, status='pending', updated_at=? WHERE id=?",
                (new_date, utc_now(), task_id),
            )

    def insert_review_task(self, original: dict[str, Any], scheduled_date: str) -> dict[str, Any]:
        import hashlib

        retry_number = int(original.get("retry_count") or 0) + 1
        task_id = "review-" + hashlib.sha256(
            f"{original['id']}|{retry_number}".encode("utf-8")
        ).hexdigest()[:18]
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO learning_tasks(
                    id, profile_id, source_document, source_section, source_chunk_id, route,
                    phase, week, day, source_page, provenance, title, description, task_type, estimated_minutes,
                    estimated_is_system, position, prerequisite_task_ids, parent_task_id,
                    is_critical, status, scheduled_date, retry_count, needs_review, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sistem tarafından oluşturulan alıştırma', ?, ?, 'tekrar', ?, 1, ?, '[]', ?, 0,
                          'needs_review', ?, ?, 1, ?, ?)""",
                (
                    task_id, original["profile_id"], original["source_document"], original["source_section"],
                    original["source_chunk_id"], original["route"], original.get("phase"), original.get("week"),
                    original.get("day"), original.get("source_page"), f"Tekrar: {original['title']}",
                    "Düşük sınav puanı veya kullanıcı işareti nedeniyle kaynak görevden türetilmiş tekrar oturumu.\n\n"
                    + original["description"],
                    min(original["estimated_minutes"], 45), original["position"] + 1_000_000 + retry_number,
                    original["id"], scheduled_date, retry_number, now, now,
                ),
            )
        task = self.get_task(task_id)
        assert task is not None
        return task

    def progress_summary(self, profile_id: int) -> dict[str, Any]:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) completed,
                          COALESCE(SUM(actual_minutes),0) actual_minutes,
                          SUM(CASE WHEN needs_review=1 THEN 1 ELSE 0 END) review_count
                   FROM learning_tasks WHERE profile_id=?""",
                (profile_id,),
            ).fetchone()
            latest = conn.execute(
                "SELECT level, score, skipped, created_at FROM assessment_attempts WHERE profile_id=? ORDER BY id DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
        total = row["total"] or 0
        completed = row["completed"] or 0
        return {
            "total": total,
            "completed": completed,
            "percentage": round((completed / total * 100) if total else 0.0, 1),
            "actual_minutes": row["actual_minutes"],
            "review_count": row["review_count"] or 0,
            "latest_assessment": dict(latest) if latest else None,
        }

    def source_chunks(self, document_ids: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        with self.connection() as conn:
            rows = conn.execute(
                f"""SELECT c.*, d.original_filename, d.filename, d.source_kind, d.source_url,
                            d.title AS document_title, d.status AS document_status
                     FROM source_chunks c JOIN source_documents d ON d.id=c.document_id
                     WHERE c.document_id IN ({placeholders}) AND d.status='active'
                     ORDER BY c.document_id, c.position""",
                list(document_ids),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_route_sources(self, route_id: str, sources: list[dict[str, Any]]) -> None:
        if not sources:
            raise DatabaseError("Rota için en az bir kaynak seçilmelidir.")
        ids = [str(item["source_id"]) for item in sources]
        if len(ids) != len(set(ids)):
            raise DatabaseError("Aynı kaynak rotaya birden fazla kez eklenemez.")
        with self.connection() as conn:
            placeholders = ",".join("?" for _ in ids)
            active = {
                row[0]
                for row in conn.execute(
                    f"SELECT id FROM source_documents WHERE id IN ({placeholders}) AND status='active'", ids
                )
            }
            if active != set(ids):
                raise DatabaseError("Seçilen kaynaklardan biri bulunamadı veya arşivlenmiş.")
            conn.execute("DELETE FROM route_sources WHERE route_id=?", (route_id,))
            conn.executemany(
                """INSERT INTO route_sources(route_id, source_id, source_type, source_order, source_role,
                                               status, approved_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?)""",
                [
                    (
                        route_id,
                        item["source_id"],
                        item.get("source_type", "document"),
                        index,
                        item.get("source_role", "primary" if index == 1 else "auxiliary"),
                        item.get("approved_at") or utc_now(),
                    )
                    for index, item in enumerate(sources, start=1)
                ],
            )

    def route_sources(self, route_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        query = """SELECT rs.*, d.title, d.original_filename, d.filename, d.path, d.sha256,
                          d.source_kind, d.source_url, d.mime_type, d.status AS document_status
                   FROM route_sources rs JOIN source_documents d ON d.id=rs.source_id
                   WHERE rs.route_id=?"""
        if not include_archived:
            query += " AND rs.status='active' AND d.status='active'"
        query += " ORDER BY rs.source_order, rs.source_id"
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(query, (route_id,))]

    def save_web_source(self, item: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO web_sources(id, url, normalized_url, title, domain, description,
                       source_type, reliability_class, published_at, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?)
                   ON CONFLICT(normalized_url) DO UPDATE SET title=excluded.title,
                       description=excluded.description, source_type=excluded.source_type,
                       reliability_class=excluded.reliability_class, published_at=excluded.published_at,
                       updated_at=excluded.updated_at""",
                (
                    item["id"], item["url"], item["normalized_url"], item["title"], item["domain"],
                    item.get("description", ""), item["source_type"], item["reliability_class"],
                    item.get("published_at"), now, now,
                ),
            )
            row = conn.execute("SELECT * FROM web_sources WHERE normalized_url=?", (item["normalized_url"],)).fetchone()
        assert row is not None
        return dict(row)

    def web_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM web_sources WHERE id=?", (source_id,)).fetchone()
        return dict(row) if row else None

    def list_web_sources(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM web_sources"
        if not include_archived:
            query += " WHERE status <> 'archived'"
        query += " ORDER BY updated_at DESC"
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(query)]

    def approve_web_source(
        self,
        source_id: str,
        *,
        document_id: str,
        content_sha256: str,
        snapshot_path: str,
        mime_type: str,
        accessed_at: str,
    ) -> dict[str, Any]:
        with self.connection() as conn:
            changed = conn.execute(
                """UPDATE web_sources SET status='approved', document_id=?, content_sha256=?,
                       snapshot_path=?, mime_type=?, accessed_at=?, last_error=NULL, updated_at=?
                   WHERE id=? AND status IN ('discovered','failed','approved')""",
                (document_id, content_sha256, snapshot_path, mime_type, accessed_at, utc_now(), source_id),
            )
            if changed.rowcount == 0:
                raise DatabaseError("Onaylanacak web kaynağı bulunamadı.")
        result = self.web_source(source_id)
        assert result is not None
        return result

    def archive_web_source(self, source_id: str, confirmation: str) -> dict[str, Any]:
        expected = f"WEB KAYNAĞINI ARŞİVLE {source_id}"
        if confirmation != expected:
            raise DatabaseError(f"Arşivleme için tam olarak '{expected}' yazılmalıdır.")
        with self.connection() as conn:
            row = conn.execute("SELECT document_id FROM web_sources WHERE id=?", (source_id,)).fetchone()
            if row is None:
                raise DatabaseError("Web kaynağı bulunamadı.")
            conn.execute("UPDATE web_sources SET status='archived', updated_at=? WHERE id=?", (utc_now(), source_id))
            if row[0]:
                conn.execute("UPDATE source_documents SET status='archived', archived_at=? WHERE id=?", (utc_now(), row[0]))
                conn.execute("UPDATE route_sources SET status='archived' WHERE source_id=?", (row[0],))
        result = self.web_source(source_id)
        assert result is not None
        return result

    def save_sufficiency_report(self, report: dict[str, Any]) -> str:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO sufficiency_reports(id, route_id, profile_id, goal, report_json,
                       coverage_score, can_generate, approved, created_at, approved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET report_json=excluded.report_json,
                       coverage_score=excluded.coverage_score, can_generate=excluded.can_generate,
                       approved=0, approved_at=NULL, created_at=excluded.created_at""",
                (
                    report["id"], report["route_id"], report.get("profile_id"), report["goal"],
                    json.dumps(report, ensure_ascii=False), float(report["coverage_score"]),
                    int(bool(report["can_generate"])), int(bool(report.get("approved", False))),
                    utc_now(), utc_now() if report.get("approved") else None,
                ),
            )
        return str(report["id"])

    def replace_source_conflicts(self, route_id: str, conflicts: list[dict[str, Any]]) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM source_conflicts WHERE route_id=?", (route_id,))
            conn.executemany(
                """INSERT INTO source_conflicts(id, route_id, topic, source_ids_json, explanation, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"], route_id, item["topic"],
                        json.dumps(item["source_ids"], ensure_ascii=False), item["explanation"], utc_now(),
                    )
                    for item in conflicts
                ],
            )

    def sufficiency_report(self, report_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM sufficiency_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["report"] = json.loads(result["report_json"])
        return result

    def approve_sufficiency_report(self, report_id: str) -> None:
        with self.connection() as conn:
            row = conn.execute("SELECT route_id FROM sufficiency_reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                raise DatabaseError("Kaynak yeterlilik raporu bulunamadı.")
            conn.execute(
                "UPDATE sufficiency_reports SET approved=1, approved_at=? WHERE id=?", (utc_now(), report_id)
            )
            conn.execute("UPDATE custom_routes SET sufficiency_approved=1, updated_at=? WHERE id=?", (utc_now(), row[0]))

    def save_task_sources(self, task_id: str, task_scope: str, sources: list[dict[str, Any]]) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM task_sources WHERE task_id=? AND task_scope=?", (task_id, task_scope))
            conn.executemany(
                """INSERT INTO task_sources(task_id, task_scope, source_id, chunk_id, section_path, page, url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        task_id, task_scope, source["source_id"], source["chunk_id"],
                        source.get("section_path"), source.get("page"), source.get("url"),
                    )
                    for source in sources
                ],
            )

    def task_sources(self, task_id: str, task_scope: str = "draft") -> list[dict[str, Any]]:
        with self.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM task_sources WHERE task_id=? AND task_scope=? ORDER BY source_id, chunk_id",
                    (task_id, task_scope),
                )
            ]

    def save_custom_quiz(self, route_id: str, questions: list[dict[str, Any]]) -> int:
        with self.connection() as conn:
            conn.execute("DELETE FROM custom_quiz_questions WHERE route_id=?", (route_id,))
            conn.executemany(
                """INSERT INTO custom_quiz_questions(id, route_id, question, options_json,
                       correct_answer, explanation, difficulty, topic, source_id, chunk_id,
                       source_locator, generation_method, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"], route_id, item["question"], json.dumps(item["options"], ensure_ascii=False),
                        item["correct_answer"], item["explanation"], item["difficulty"], item["topic"],
                        item["source_id"], item["chunk_id"], item["source_locator"],
                        item["generation_method"], utc_now(),
                    )
                    for item in questions
                ],
            )
        return len(questions)

    def custom_quiz(self, route_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM custom_quiz_questions WHERE route_id=? ORDER BY id", (route_id,)).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["options"] = json.loads(item.pop("options_json"))
        return result

    def save_custom_quiz_attempt(
        self, profile_id: int, route_id: str, answers: dict[str, str], score: float | None, skipped: bool
    ) -> int:
        with self.connection() as conn:
            cur = conn.execute(
                """INSERT INTO custom_quiz_attempts(profile_id, route_id, answers_json, score, skipped, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (profile_id, route_id, json.dumps(answers, ensure_ascii=False), score, int(skipped), utc_now()),
            )
        return int(cur.lastrowid)

    def _focus_elapsed(self, row: dict[str, Any], now: datetime | None = None) -> int:
        elapsed = max(0, int(row["accumulated_seconds"] or 0))
        if row["status"] == "running" and row.get("resumed_at"):
            current = now or datetime.now(timezone.utc)
            resumed = datetime.fromisoformat(row["resumed_at"])
            elapsed += max(0, int((current - resumed).total_seconds()))
        return elapsed

    def focus_session(self, profile_id: int) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM focus_sessions WHERE profile_id=? AND status IN ('running','paused') ORDER BY id DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["elapsed_seconds"] = self._focus_elapsed(result)
        return result

    def focus_action(self, profile_id: int, action: str, task_id: str | None = None) -> dict[str, Any] | None:
        allowed = {"start", "pause", "resume", "finish", "reset"}
        if action not in allowed:
            raise DatabaseError("Geçersiz odak sayacı işlemi.")
        now = utc_now()
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM focus_sessions WHERE profile_id=? AND status IN ('running','paused') ORDER BY id DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
            current = dict(row) if row else None
            if action == "start":
                if current:
                    raise DatabaseError("Bu profil için zaten etkin bir odak sayacı var.")
                if task_id and not conn.execute(
                    "SELECT 1 FROM learning_tasks WHERE id=? AND profile_id=?", (task_id, profile_id)
                ).fetchone():
                    raise DatabaseError("Odak sayacı görevi bu profile ait değil.")
                cur = conn.execute(
                    """INSERT INTO focus_sessions(profile_id, task_id, status, started_at, resumed_at,
                           accumulated_seconds, updated_at) VALUES (?, ?, 'running', ?, ?, 0, ?)""",
                    (profile_id, task_id, now, now, now),
                )
                session_id = int(cur.lastrowid)
            else:
                if not current:
                    raise DatabaseError("Etkin odak sayacı bulunamadı.")
                session_id = int(current["id"])
                elapsed = self._focus_elapsed(current, datetime.fromisoformat(now))
                if action == "pause":
                    if current["status"] != "running":
                        raise DatabaseError("Yalnızca çalışan sayaç duraklatılabilir.")
                    conn.execute(
                        "UPDATE focus_sessions SET status='paused', paused_at=?, accumulated_seconds=?, updated_at=? WHERE id=?",
                        (now, elapsed, now, session_id),
                    )
                elif action == "resume":
                    if current["status"] != "paused":
                        raise DatabaseError("Yalnızca duraklatılmış sayaç devam ettirilebilir.")
                    conn.execute(
                        "UPDATE focus_sessions SET status='running', resumed_at=?, paused_at=NULL, updated_at=? WHERE id=?",
                        (now, now, session_id),
                    )
                elif action in {"finish", "reset"}:
                    status = "finished" if action == "finish" else "reset"
                    conn.execute(
                        "UPDATE focus_sessions SET status=?, finished_at=?, accumulated_seconds=?, updated_at=? WHERE id=?",
                        (status, now, 0 if action == "reset" else elapsed, now, session_id),
                    )
        if action in {"finish", "reset"}:
            with self.connection() as conn:
                result = conn.execute("SELECT * FROM focus_sessions WHERE id=?", (session_id,)).fetchone()
            output = dict(result) if result else None
            if output:
                output["elapsed_seconds"] = int(output["accumulated_seconds"])
            return output
        return self.focus_session(profile_id)

    def apply_focus_duration(self, task_id: str, seconds: int) -> dict[str, Any]:
        minutes = max(0, int(round(seconds / 60)))
        with self.connection() as conn:
            row = conn.execute("SELECT status FROM learning_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise DatabaseError("Süre aktarılacak görev bulunamadı.")
            if row[0] == "completed":
                raise DatabaseError("Tamamlanmış görevin gerçek süresi odak sayacından sessizce değiştirilemez.")
            conn.execute(
                "UPDATE learning_tasks SET actual_minutes=?, updated_at=? WHERE id=?",
                (minutes, utc_now(), task_id),
            )
        result = self.get_task(task_id)
        assert result is not None
        return result

    def save_adaptive_decision(
        self, profile_id: int, task_id: str | None, decision_type: str, reason: str,
        payload: dict[str, Any], *, applied: bool = False,
    ) -> int:
        with self.connection() as conn:
            cur = conn.execute(
                """INSERT INTO adaptive_decisions(profile_id, task_id, decision_type, reason,
                       payload_json, applied, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (profile_id, task_id, decision_type, reason, json.dumps(payload, ensure_ascii=False), int(applied), utc_now()),
            )
        return int(cur.lastrowid)

    def adaptive_decisions(self, profile_id: int) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM adaptive_decisions WHERE profile_id=? ORDER BY id DESC", (profile_id,)
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["payload"] = json.loads(item.pop("payload_json"))
        return result

    def analytics(self, profile_id: int) -> dict[str, list[dict[str, Any]]]:
        with self.connection() as conn:
            sessions = [dict(row) for row in conn.execute(
                "SELECT * FROM daily_sessions WHERE profile_id=? ORDER BY session_date", (profile_id,)
            )]
            tasks = [dict(row) for row in conn.execute(
                """SELECT id, title, module, task_type, status, estimated_minutes, actual_minutes,
                          difficulty_rating, quiz_score, scheduled_date, completed_at, source_document
                   FROM learning_tasks WHERE profile_id=? ORDER BY position""", (profile_id,)
            )]
            reports = [dict(row) for row in conn.execute(
                "SELECT coverage_score, created_at FROM sufficiency_reports WHERE profile_id=? ORDER BY created_at",
                (profile_id,),
            )]
        return {"sessions": sessions, "tasks": tasks, "sufficiency": reports}

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM application_settings WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO application_settings(key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, utc_now()),
            )

    def save_chat(self, profile_id: int | None, role: str, content: str, citations: list[dict[str, Any]]) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO chat_history(profile_id, role, content, citations_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (profile_id, role, content, json.dumps(citations, ensure_ascii=False), utc_now()),
            )

    def chat_history(self, profile_id: int | None, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as conn:
            if profile_id is None:
                rows = conn.execute(
                    "SELECT * FROM chat_history WHERE profile_id IS NULL ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM chat_history WHERE profile_id=? ORDER BY id DESC LIMIT ?", (profile_id, limit)
                ).fetchall()
        return list(reversed([dict(row) for row in rows]))
