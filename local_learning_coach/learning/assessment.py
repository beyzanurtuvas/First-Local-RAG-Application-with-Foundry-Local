from __future__ import annotations

from dataclasses import dataclass

from local_learning_coach.database import Database


@dataclass(frozen=True)
class Question:
    id: str
    route: str
    question: str
    options: tuple[str, ...]
    correct_answer: str
    source_hint: str


QUESTIONS: tuple[Question, ...] = (
    Question("da-1", "data-analysis", "Pandas'ta CSV dosyası hangi işlevle okunur?", ("pd.read_csv", "pd.open", "csv.load", "df.read"), "pd.read_csv", "Day 5"),
    Question("da-2", "data-analysis", "Bir DataFrame'de koşula göre satır seçme işlemi nedir?", ("Filtreleme", "Derleme", "Serileştirme", "Örnekleme"), "Filtreleme", "Day 6"),
    Question("da-3", "data-analysis", "Gruplama ve toplulaştırma hangi günde ele alınır?", ("Day 8", "Day 2", "Day 16", "Day 20"), "Day 8", "Day 8"),
    Question("da-4", "data-analysis", "Titanic yolculuk ve aile analizi hangi gündedir?", ("Day 12", "Day 5", "Day 9", "Day 19"), "Day 12", "Day 12"),
    Question("da-5", "data-analysis", "Belgede geçen temel görselleştirme araçları hangileridir?", ("Matplotlib ve Seaborn", "TensorFlow ve Keras", "Spark ve Hadoop", "Q# ve QDK"), "Matplotlib ve Seaborn", "Day 9"),
    Question("ml-1", "machine-learning", "Sayısal bir değeri tahmin eden temel yaklaşım hangisidir?", ("Regresyon", "Kümeleme", "Sınıflandırma", "Sıralama"), "Regresyon", "Week 1"),
    Question("ml-2", "machine-learning", "MSE ve RMSE belgede ne için kullanılır?", ("Model hatasını değerlendirmek", "CSV okumak", "Qubit ölçmek", "Sunum oluşturmak"), "Model hatasını değerlendirmek", "Week 2 / Week 5"),
    Question("ml-3", "machine-learning", "PyTorch temelleri hangi hafta başlar?", ("Week 3", "Week 1", "Week 5", "Week 6"), "Week 3", "Week 3"),
    Question("ml-4", "machine-learning", "Sliding window hangi veri türünü modele hazırlamak için kullanılır?", ("Zaman serisi", "Görüntü etiketi", "Metin özeti", "Kuantum durumu"), "Zaman serisi", "Week 4"),
    Question("ml-5", "machine-learning", "Projede karşılaştırılan iki yinelenen ağ hangileridir?", ("LSTM ve GRU", "CNN ve GAN", "KNN ve SVM", "BERT ve GPT"), "LSTM ve GRU", "Week 5–6"),
    Question("q-1", "quantum", "Bir qubit'i süperpozisyona almak için belgede örneklenen kapı hangisidir?", ("H", "RESET", "PRINT", "SORT"), "H", "Week 1"),
    Question("q-2", "quantum", "İlk Q# programları nerede çalıştırılır?", ("Yerel simülatör", "Gerçek kuantum donanımı", "Bulut LLM", "SQL sunucusu"), "Yerel simülatör", "Week 1"),
    Question("q-3", "quantum", "Proje için ayrıntılı örnek algoritma hangisidir?", ("Grover araması", "QuickSort", "PageRank", "Lineer regresyon"), "Grover araması", "Week 3"),
    Question("q-4", "quantum", "Sistematik simülatör testi hangi haftadadır?", ("Week 5", "Week 1", "Week 3", "Week 6"), "Week 5", "Week 5"),
    Question("q-5", "quantum", "Dokümantasyon ve kapanış hangi haftadadır?", ("Week 6", "Week 2", "Week 4", "Week 5"), "Week 6", "Week 6"),
)


class AssessmentEngine:
    def __init__(self, database: Database):
        self.database = database

    def seed_questions(self) -> None:
        import json

        with self.database.connection() as conn:
            conn.executemany(
                """INSERT INTO assessment_questions(id, route, question, options_json, correct_answer, points, source_hint)
                   VALUES (?, ?, ?, ?, ?, 20, ?)
                   ON CONFLICT(id) DO UPDATE SET question=excluded.question, options_json=excluded.options_json,
                       correct_answer=excluded.correct_answer, source_hint=excluded.source_hint""",
                [
                    (q.id, q.route, q.question, json.dumps(q.options, ensure_ascii=False), q.correct_answer, q.source_hint)
                    for q in QUESTIONS
                ],
            )

    def questions(self, route: str) -> list[Question]:
        return [question for question in QUESTIONS if question.route == route]

    @staticmethod
    def level_for_score(score: float) -> str:
        if score < 25:
            return "Başlangıç"
        if score < 50:
            return "Temel"
        if score < 80:
            return "Orta"
        return "İleri"

    def grade(self, profile_id: int, route: str, answers: dict[str, str]) -> dict:
        questions = self.questions(route)
        if not questions:
            raise ValueError(f"Bilinmeyen rota: {route}")
        correct = sum(1 for question in questions if answers.get(question.id) == question.correct_answer)
        score = correct / len(questions) * 100
        level = self.level_for_score(score)
        self.database.save_assessment(profile_id, route, answers, score, level, False)
        return {
            "score": score,
            "level": level,
            "correct": correct,
            "total": len(questions),
            "message": f"{level} düzeyinde bir başlangıç noktası belirlendi. Bu sonuç yalnızca verdiğiniz yanıtlara dayanır.",
        }

    def skip(self, profile_id: int, route: str, declared_level: str) -> dict:
        self.database.save_assessment(profile_id, route, {}, None, declared_level, True)
        return {
            "score": None,
            "level": declared_level,
            "skipped": True,
            "message": "Değerlendirme atlandı; düzey kullanıcı beyanı olarak ayrı kaydedildi.",
        }
