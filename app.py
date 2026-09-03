from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from local_learning_coach.config import DOCUMENT_DEFINITIONS, Settings
from local_learning_coach.foundry import FoundryAdapter
from local_learning_coach.interview.ui import render_interview_app
from local_learning_coach.learning import LearningCoachService
from local_learning_coach.logging_utils import configure_logging


st.set_page_config(page_title="Local Learning Coach", page_icon="📚", layout="wide")
settings = Settings.load()
configure_logging(settings.log_path)


@st.cache_resource
def service() -> LearningCoachService:
    instance = LearningCoachService(settings)
    instance.database.migrate()
    instance._restore_runtime_settings()
    instance.assessment.seed_questions()
    instance.interview.seed()
    return instance


def safe(action, success: str | None = None):
    try:
        result = action()
        if success:
            st.success(success)
        return result
    except Exception as exc:
        logging.exception("Web işlemi başarısız")
        st.error(f"İşlem tamamlanamadı: {exc}")
        return None


coach = service()
profile = coach.database.get_profile()

app_mode = st.sidebar.selectbox(
    "Çalışma modu", ("Genel Öğrenme Koçu", "Teknik Mülakat Koçu"), key="app-mode",
)
if app_mode == "Teknik Mülakat Koçu":
    render_interview_app(coach, profile)
    st.stop()

PAGES = (
    "1. Onboarding ve profil",
    "2. Başlangıç değerlendirmesi",
    "3. Ana gösterge paneli",
    "4. Bugünün görevleri",
    "5. Haftalık plan",
    "6. Görev ayrıntısı",
    "7. Belgelere soru sor",
    "8. Rota karşılaştırma",
    "9. Haftalık rapor",
    "10. Ayarlar",
    "11. Belge ve rota yönetimi",
    "12. Hazır rotalar kataloğu",
    "13. Açık internet kaynakları",
    "14. Grafikler ve odak sayacı",
)
page = st.sidebar.radio("Ekran", PAGES, key="general-page")
st.sidebar.caption("Veriler yalnızca bu bilgisayardaki SQLite ve indeks dosyalarında tutulur.")


def require_profile():
    if not profile:
        st.info("Önce Onboarding ve profil ekranından bir profil oluşturun.")
        st.stop()


def task_card(task):
    with st.container(border=True):
        st.subheader(task["title"])
        st.write(task["description"][:700])
        st.caption(
            f"{task['source_document']} · Faz {task.get('phase') or '-'} · Hafta {task.get('week') or '-'} · "
            f"Gün {task.get('day') or '-'} · {task['estimated_minutes']} dk (tahmini) · {task['task_type']}"
        )
        st.write(f"Durum: `{task['status']}`")
        a, b, c, d = st.columns(4)
        if a.button("Başlat", key=f"start-{task['id']}", disabled=task["status"] not in {"pending", "postponed", "needs_review"}):
            safe(lambda: coach.start_task(task["id"]), "Görev başlatıldı.")
            st.rerun()
        if b.button("Ertele", key=f"post-{task['id']}", disabled=task["status"] == "completed"):
            safe(lambda: coach.postpone_task(task["id"]), "Görev ve açık bağımlıları yeniden planlandı.")
            st.rerun()
        if c.button("Tekrar gerekli", key=f"review-{task['id']}"):
            safe(lambda: coach.review_task(task["id"]), "Tekrar kaydı oluşturuldu.")
            st.rerun()
        if d.button("Ayrıntıya git", key=f"detail-{task['id']}"):
            st.session_state["selected_task"] = task["id"]
            st.info("Sol menüden Görev ayrıntısı ekranını açın.")


if page == PAGES[0]:
    st.title("Ne öğrenmek istiyorsunuz?")
    if profile:
        st.success(f"Aktif profil: {profile['name']} · {profile['preferred_route']}")
        with st.expander("Kayıtlı profil bilgileri"):
            st.json(profile)
    st.write("Önce hedefinizi ve çalışma kapasitenizi belirleyin; hazır üç rota başlangıçta otomatik seçilmez.")
    with st.form("profile-form"):
        name = st.text_input("Ad veya profil adı")
        goal = st.text_input("Öğrenme hedefi")
        goal_description = st.text_area("Hedefin açıklaması")
        declared_level = st.selectbox("Mevcut seviye", ["Yok", "Başlangıç", "Temel", "Orta", "İleri"], index=1)
        source_methods = [
            "Kendi belgelerimi yükle",
            "Birden fazla belge kullan",
            "Hazır rotalardan seç",
            "Açık internet kaynakları bul",
            "Belgeler ve internet kaynaklarını birlikte kullan",
            "Henüz belgem yok",
        ]
        source_method = st.selectbox("Kaynak yöntemi", source_methods, index=None, placeholder="Bir kaynak yöntemi seçin")
        route_labels = {title: slug for slug, title, _ in DOCUMENT_DEFINITIONS}
        route_title = st.selectbox(
            "Hazır rota",
            list(route_labels),
            index=None,
            disabled=source_method != "Hazır rotalardan seç",
            placeholder="Hazır rota kataloğundan seçim yapın",
        )
        weekly_days = st.slider("Haftada çalışılabilecek gün", 1, 7, 5)
        daily_minutes = st.number_input("Günlük dakika", min_value=10, max_value=720, value=60, step=5)
        weekdays = st.multiselect(
            "Tercih edilen çalışma günleri",
            ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"],
            default=["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma"],
        )
        start_date = st.date_input("Başlangıç tarihi", value=date.today())
        target_date = st.date_input("Hedef bitirme tarihi", value=date.today() + timedelta(days=60))
        preference = st.select_slider("Teori/uygulama tercihi", ["Teori ağırlıklı", "Dengeli", "Uygulama ağırlıklı"], value="Dengeli")
        completed = st.text_area("Daha önce tamamlanan konular (satır başına bir konu)")
        difficult = st.text_area("Zorlanılan konular (satır başına bir konu)")
        review = st.selectbox("Tekrar tercihi", ["Her oturum sonrası", "Haftalık", "Düşük sınav puanında"])
        submitted = st.form_submit_button("Profili oluştur")
    if submitted:
        if not source_method:
            st.warning("Profil oluşturmadan önce kaynak yöntemini seçin.")
            st.stop()
        if source_method == "Hazır rotalardan seç" and not route_title:
            st.warning("Hazır rotalardan birini seçin.")
            st.stop()
        data = {
            "name": name,
            "goal": goal,
            "goal_description": goal_description,
            "source_method": source_method,
            "preferred_route": route_labels[route_title] if route_title else "unassigned",
            "weekly_days": weekly_days,
            "daily_minutes": int(daily_minutes),
            "preferred_days": weekdays,
            "start_date": start_date.isoformat(),
            "target_end_date": target_date.isoformat(),
            "theory_practice_preference": preference,
            "completed_topics": [item.strip() for item in completed.splitlines() if item.strip()],
            "difficult_topics": [item.strip() for item in difficult.splitlines() if item.strip()],
            "review_preference": review,
            "declared_level": declared_level if declared_level != "Yok" else "Başlangıç",
        }
        skills = {"general": declared_level}
        created = safe(lambda: coach.create_profile(data, skills), "Profil oluşturuldu.")
        if created:
            st.rerun()
    if source_method == "Henüz belgem yok":
        st.info("Sistem kaynak olmadan rota uydurmaz. Hedefiniz için giriş kaynağı, uygulama örnekleri ve değerlendirme içeren güvenilir belgeler ekleyin veya açık kaynak araştırmasına geçin.")
    elif source_method in {"Kendi belgelerimi yükle", "Birden fazla belge kullan", "Belgeler ve internet kaynaklarını birlikte kullan"}:
        st.info("Profil oluşturduktan sonra ‘Belge ve rota yönetimi’ ekranından kaynaklarınızı ekleyin.")
    elif source_method == "Açık internet kaynakları bul":
        st.info("Profil oluşturduktan sonra ‘Açık internet kaynakları’ ekranında bilgilendirme ve açık onay akışını izleyin.")

elif page == PAGES[1]:
    require_profile()
    st.title("Başlangıç değerlendirmesi")
    questions = coach.assessment.questions(profile["preferred_route"])
    if not questions:
        custom_questions = coach.custom_quiz(profile["preferred_route"])
        if not custom_questions:
            st.info("Bu rota için kaynak bağlı sınav henüz üretilmedi. Rota yönetiminden oluşturabilir veya sınavı atlayabilirsiniz.")
            if st.button("Kaynak bağlı sınavı atla"):
                safe(lambda: coach.skip_custom_quiz(profile["id"], profile["preferred_route"]), "Sınav atlandı; ölçülmüş seviye kaydedilmedi.")
        else:
            with st.form("custom-assessment"):
                answers = {
                    item["id"]: st.radio(
                        item["question"], item["options"], index=None,
                        key=f"custom-{item['id']}", help=f"Kaynak: {item['source_locator']}",
                    )
                    for item in custom_questions
                }
                submit_custom = st.form_submit_button("Kaynak bağlı sınavı puanla")
            if submit_custom:
                result = safe(lambda: coach.grade_custom_quiz(profile["id"], profile["preferred_route"], answers))
                if result:
                    st.success(f"Puan: %{result['score']} · {result['correct']}/{result['total']} doğru")
            if st.button("Sınavı atla"):
                safe(lambda: coach.skip_custom_quiz(profile["id"], profile["preferred_route"]), "Sınav atlandı; ölçülmüş seviye kaydedilmedi.")
    else:
        with st.form("assessment"):
            answers = {q.id: st.radio(q.question, q.options, index=None, key=q.id) for q in questions}
            submit = st.form_submit_button("Puanla")
        if submit:
            if any(value is None for value in answers.values()):
                st.warning("Tüm soruları yanıtlayın veya değerlendirmeyi atlayın.")
            else:
                result = safe(lambda: coach.assessment.grade(profile["id"], profile["preferred_route"], answers))
                if result:
                    st.success(f"Puan: {result['score']:.0f}/100 · Düzey: {result['level']}")
                    st.write(result["message"])
        if st.button("Değerlendirmeyi atla"):
            result = safe(lambda: coach.assessment.skip(profile["id"], profile["preferred_route"], profile["declared_level"]))
            if result:
                st.info(result["message"])

elif page == PAGES[2]:
    require_profile()
    st.title("Ana gösterge paneli")
    dashboard = coach.dashboard(profile["id"])
    summary = dashboard["progress"]
    tasks = coach.database.tasks(profile["id"])
    today_tasks = dashboard["today"]
    open_tasks = [task for task in tasks if task["status"] != "completed"]
    current = open_tasks[0] if open_tasks else None
    cols = st.columns(5)
    cols[0].metric("Öğrenme hedefi", profile["goal"][:32])
    cols[1].metric("İlerleme", f"%{summary['percentage']}")
    cols[2].metric("Toplam çalışma", f"{summary['actual_minutes']} dk")
    cols[3].metric("Yerel / web kaynak", f"{dashboard['source_counts']['local']} / {dashboard['source_counts']['web']}")
    cols[4].metric("Açık / tamamlanan", f"{summary['total'] - summary['completed']} / {summary['completed']}")
    countdown = dashboard["countdown"]
    st.subheader("Hedef sayacı")
    timer_cols = st.columns(5)
    timer_cols[0].metric("Hedef tarihi", countdown["target_date"])
    timer_cols[1].metric("Kalan gün", countdown["remaining_days"])
    timer_cols[2].metric("Kalan çalışma günü", countdown["remaining_study_days"])
    timer_cols[3].metric("Tahmini bitiş", countdown["estimated_completion_date"])
    timer_cols[4].metric("Durum", countdown["status"])
    if countdown["status"] in {"Risk altında", "Hedef tarih geçti"}:
        st.error("Hedef mevcut kapasiteyle risk altında. Kapasite kullanıcı onayı olmadan artırılmaz.")
    else:
        st.success(countdown["status"])
    if current:
        st.info(f"Mevcut konum: Faz {current.get('phase') or '-'} / Hafta {current.get('week') or '-'} / Gün {current.get('day') or '-'}")
    st.caption(f"Aktif rota: {profile['preferred_route']} · Kaynak yeterlilik: {'onaylandı' if dashboard['sources'] else 'kaynak seçimi bekleniyor'}")
    st.subheader("Bugünün görevleri")
    for task in today_tasks:
        task_card(task)
    st.subheader("Yaklaşan görevler")
    for task in dashboard["upcoming"]:
        st.write(f"- {task.get('scheduled_date')} · {task['title']}")
    if dashboard["review_topics"]:
        st.subheader("Tekrar bekleyen konular")
        for task in dashboard["review_topics"]:
            st.write(f"- {task['title']}")
    st.subheader("Son değerlendirme")
    st.write(summary["latest_assessment"] or "Henüz değerlendirme yok.")

elif page == PAGES[3]:
    require_profile()
    st.title("Bugünün görevleri")
    for task in coach.today(profile["id"]):
        task_card(task)
    if not coach.today(profile["id"]):
        st.info("Bugüne planlanmış görev yok.")

elif page == PAGES[4]:
    require_profile()
    st.title("Haftalık plan")
    week_start = st.date_input("Hafta başlangıcı", date.today() - timedelta(days=date.today().weekday()))
    tasks = coach.weekly_plan(profile["id"], week_start)
    for day_value in range(7):
        current_day = week_start + timedelta(days=day_value)
        st.subheader(current_day.strftime("%d.%m.%Y"))
        day_tasks = [task for task in tasks if task["scheduled_date"] == current_day.isoformat()]
        for task in day_tasks:
            st.write(f"- `{task['status']}` {task['title']} · {task['estimated_minutes']} dk (tahmini)")
        if not day_tasks:
            st.caption("Görev yok")

elif page == PAGES[5]:
    require_profile()
    st.title("Görev ayrıntısı")
    tasks = coach.database.tasks(profile["id"])
    if not tasks:
        st.info("Önce Ayarlar ekranından veya CLI'dan kişisel plan oluşturun.")
        st.stop()
    default_id = st.session_state.get("selected_task", tasks[0]["id"])
    ids = [task["id"] for task in tasks]
    selected = st.selectbox("Görev", ids, index=ids.index(default_id) if default_id in ids else 0)
    task = next(item for item in tasks if item["id"] == selected)
    st.subheader(task["title"])
    st.write(task["description"])
    st.json({key: task[key] for key in task if key not in {"description"}})
    with st.form("complete-task"):
        minutes = st.number_input("Gerçek süre (dakika)", min_value=0, value=task["estimated_minutes"])
        difficulty = st.slider("Zorluk puanı", 1, 5, 3)
        quiz = st.number_input("Mini sınav puanı (opsiyonel; -1 boş)", min_value=-1, max_value=100, value=-1)
        note = st.text_area("Kullanıcı notu")
        complete = st.form_submit_button("Tamamla")
    if complete:
        result = safe(lambda: coach.complete_task(selected, int(minutes), difficulty, quiz_score=None if quiz < 0 else quiz, note=note), "Görev tamamlandı.")
        if result:
            st.rerun()

elif page == PAGES[6]:
    st.title("Belgelere soru sor")
    custom_routes = coach.list_custom_routes()
    route_labels = {
        "Tümü": None,
        "Python ve Veri Analizi": "data-analysis",
        "Makine Öğrenmesi ve PyTorch": "machine-learning",
        "Kuantum Programlama ve Q#": "quantum",
        **{f"Özel · {item['title']}": item["id"] for item in custom_routes},
    }
    uploaded_docs = coach.uploaded_documents()
    document_labels = {"Tümü": None}
    document_labels.update({item[2]: {"document": item[2]} for item in DOCUMENT_DEFINITIONS})
    document_labels.update(
        {
            f"Yüklenen · {item.get('original_filename') or item['filename']} · {item['id'][-8:]}":
                {"document_id": item["id"]}
            for item in uploaded_docs
        }
    )
    route_label = st.selectbox("Rota filtresi", list(route_labels))
    document_label = st.selectbox("Belge filtresi", list(document_labels))
    question = st.chat_input("Yerel eğitim belgeleri hakkında sorun")
    if profile:
        for message in coach.database.chat_history(profile["id"]):
            with st.chat_message(message["role"]):
                st.write(message["content"])
    if question:
        kwargs = {}
        if route_labels[route_label]:
            kwargs["route"] = route_labels[route_label]
        if document_labels[document_label]:
            kwargs.update(document_labels[document_label])
        with st.chat_message("user"):
            st.write(question)
        try:
            stream, results = coach.ask(question, profile["id"] if profile else None, **kwargs)
            with st.chat_message("assistant"):
                st.write_stream(stream)
            with st.expander("Kaynak parçaları ve retrieval ayrıntıları"):
                for result in results:
                    st.markdown(f"**[{result.rank}] {result.chunk['source']}**")
                    st.caption(result.chunk["section_path"])
                    st.code(f"RRF={result.rrf_score:.5f} dense={result.dense_score:.4f} BM25={result.bm25_score:.4f}")
                    st.write(result.chunk["body"])
        except Exception as exc:
            logging.exception("Soru-cevap başarısız")
            st.error(str(exc))

elif page == PAGES[7]:
    st.title("Rota karşılaştırma")
    comparison = safe(coach.route_comparison)
    if comparison:
        for route in comparison:
            with st.container(border=True):
                st.subheader(route["title"])
                st.write(f"Kaynak: {route['source_document']}")
                st.write(f"Belge görevi: {route['source_task_count']} · Chunk: {route['chunk_count']}")
                st.write(f"Fazlar: {route['phases']} · Haftalar: {route['weeks']} · Günler: {route['days']}")

elif page == PAGES[8]:
    require_profile()
    st.title("Haftalık rapor")
    if st.button("Raporu güncelle"):
        report = safe(lambda: coach.weekly_report(profile["id"]))
        if report:
            st.json(report)
    else:
        st.caption("Rapor, yalnızca görev durumları, gerçek süreler, mini sınav ve zorluk kayıtlarına dayanır.")

elif page == PAGES[9]:
    st.title("Ayarlar")
    st.subheader("İndeks")
    st.json(coach.index_status())
    if st.button("İndeksi oluştur / güncelle"):
        result = safe(lambda: coach.build_index(force=False), "İndeks hazır.")
        if result:
            st.json(result)
    st.subheader("Foundry Local")
    st.json(FoundryAdapter(coach.settings).health())
    st.json(FoundryAdapter.cli_status())
    with st.form("foundry-settings"):
        foundry_endpoint = st.text_input("Yerel endpoint", value=coach.settings.foundry_endpoint)
        foundry_model = st.text_input("Model kimliği", value=coach.settings.foundry_model)
        test_foundry = st.form_submit_button("Kaydet ve bağlantıyı test et")
    if test_foundry:
        result = safe(lambda: coach.configure_foundry(foundry_endpoint, foundry_model), "Foundry Local ayarı yerel olarak kaydedildi.")
        if result:
            st.json(result)
    st.subheader("Arama backend'i")
    st.json(coach.search_backend_status())
    backend_choice = st.radio("Backend", ["local", "opensearch"], index=0 if coach.settings.search_backend == "local" else 1, horizontal=True)
    fallback_confirmation = st.checkbox("OpenSearch'ten yerel backende dönüşü onaylıyorum")
    opensearch_index_confirmation = st.checkbox(
        "Onaylanmış yerel kaynaklarımı yalnız localhost OpenSearch'e indekslemeyi onaylıyorum"
    )
    if st.button(
        "Arama backend'ini uygula",
        disabled=(backend_choice == coach.settings.search_backend)
        or (backend_choice == "opensearch" and not opensearch_index_confirmation),
    ):
        result = safe(
            lambda: coach.select_search_backend(
                backend_choice,
                confirmed_fallback=fallback_confirmation,
                approved_local_indexing=opensearch_index_confirmation,
            )
        )
        if result:
            st.json(result)
            st.rerun()
    if coach.settings.search_backend == "opensearch" and st.button(
        "OpenSearch indeksini yeniden eşitle", disabled=not opensearch_index_confirmation,
    ):
        result = safe(lambda: coach.sync_opensearch(approved_local_indexing=opensearch_index_confirmation))
        if result:
            st.json(result)
    st.caption("OpenSearch yalnızca isteğe bağlı localhost indeks backend'idir; internetten kaynak bulmaz. Varsayılan local backend dense + BM25 + RRF'dir.")
    st.subheader("Yerel OCR")
    st.write("Etkin" if coach.settings.ocr_enabled else "Kapalı")
    st.caption("Taranmış PDF'ler için LLC_OCR_ENABLED=1 ayarı ve yerel Tesseract + pdftoppm gerekir. Metin katmanı varsa OCR çalıştırılmaz.")
    if profile:
        st.subheader("Plan ve yerel veri")
        if st.button("Kişisel plan oluştur"):
            result = safe(lambda: coach.generate_plan(profile["id"]))
            if result:
                st.json(result)
        export_path = settings.data_dir / f"profile-{profile['id']}.json"
        if st.button("Profili JSON olarak dışa aktar"):
            safe(lambda: coach.database.export_profile(profile["id"], export_path), f"Dosya yazıldı: {export_path}")
        backup_path = settings.data_dir / f"backup-{date.today().isoformat()}.db"
        if st.button("SQLite yedeği oluştur"):
            safe(lambda: coach.database.backup(backup_path), f"Yedek yazıldı: {backup_path}")

elif page == PAGES[10]:
    st.title("Belge ve rota yönetimi")
    st.caption("DOCX ve PDF belgeleri yalnızca bu bilgisayarda saklanır. Hazır üç kaynak belge değiştirilemez.")

    st.subheader("1. Referans belge yükle")
    uploaded_file = st.file_uploader("DOCX veya PDF seçin", type=["docx", "pdf"], max_upload_size=25)
    if st.button("Belgeyi yükle ve indeksle", disabled=uploaded_file is None):
        result = safe(
            lambda: coach.upload_document(uploaded_file.name, uploaded_file.getvalue()),
            "Belge güvenli yerel depoya kaydedildi ve indekslendi.",
        )
        if result:
            if result.get("duplicate"):
                st.info("Bu belgenin aynı içerikteki kopyası zaten kayıtlı; yeni kopya oluşturulmadı.")
            st.session_state["custom_document_id"] = result["id"]
            st.rerun()

    all_uploaded = coach.uploaded_documents(include_archived=True)
    if all_uploaded:
        st.subheader("Yüklenen belgeler")
        for document_row in all_uploaded:
            st.write(
                f"- `{document_row['status']}` · {document_row.get('original_filename') or document_row['filename']} "
                f"· `{document_row['id']}`"
            )
    active_documents = [item for item in all_uploaded if item["status"] == "active" and item.get("source_kind") != "web"]

    st.subheader("2. Kaynakları seç ve yeterliliği ölç")
    if not profile:
        st.info("Rotayı kişisel plana bağlamak için önce Onboarding ve profil ekranından profil oluşturun.")
    else:
        source_rows = coach.all_sources()
        source_options = {
            f"{'Web' if item.get('source_kind') == 'web' else ('Hazır' if item.get('origin') == 'builtin' else 'Belge')} · "
            f"{item.get('original_filename') or item['filename']} · {item['id'][-8:]}": item["id"]
            for item in source_rows
        }
        with st.form("advanced-route-form"):
            selected_source_labels = st.multiselect("Bir veya daha fazla onaylı kaynak", list(source_options))
            route_title = st.text_input("Rota adı")
            route_goal = st.text_area("Öğrenme hedefi")
            declared_level = st.selectbox("Mevcut seviye", ["Başlangıç", "Temel", "Orta", "İleri"])
            weekly_days = st.slider("Haftalık çalışma günü", 1, 7, int(profile["weekly_days"]))
            daily_minutes = st.number_input(
                "Günlük çalışma süresi (dakika)", min_value=10, max_value=720,
                value=int(profile["daily_minutes"]), step=5,
            )
            preferred_days = st.multiselect(
                "Çalışma günleri",
                ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"],
                default=profile["preferred_days"],
            )
            route_start = st.date_input("Başlangıç tarihi", value=date.today(), key="custom-start")
            route_target = st.date_input(
                "Hedef bitiş tarihi", value=date.today() + timedelta(days=60), key="custom-target"
            )
            route_preference = st.select_slider(
                "Teori/uygulama tercihi",
                ["Teori ağırlıklı", "Dengeli", "Uygulama ağırlıklı"],
                value="Dengeli",
            )
            analyze_submit = st.form_submit_button("Kaynak yeterlilik raporu oluştur")
        if analyze_submit:
            selected_source_ids = [source_options[label] for label in selected_source_labels]
            configuration = {
                "title": route_title,
                "goal": route_goal,
                "declared_level": declared_level,
                "weekly_days": weekly_days,
                "daily_minutes": int(daily_minutes),
                "preferred_days": preferred_days,
                "start_date": route_start.isoformat(),
                "target_end_date": route_target.isoformat(),
                "theory_practice_preference": route_preference,
            }
            result = safe(lambda: coach.analyze_sources(profile["id"], selected_source_ids, configuration))
            if result:
                st.session_state["sufficiency_report"] = result
                st.session_state["advanced_route_configuration"] = configuration
                st.session_state["advanced_route_source_ids"] = selected_source_ids
                st.rerun()
        report = st.session_state.get("sufficiency_report")
        if report:
            st.metric("Kapsam puanı", f"%{report['coverage_score']}")
            st.write(report["confidence_explanation"])
            st.caption(report["score_method"])
            if report["missing_topics"]:
                st.warning("Eksik konular (belge dışı öneri): " + ", ".join(report["missing_topics"]))
            if report.get("warning"):
                st.error(report["warning"])
            with st.expander("Kaynak yeterlilik raporunun tamamı"):
                st.json(report)
            report_confirm = st.checkbox("Raporu inceledim; seçili kaynaklarla rota taslağı oluşturulmasını onaylıyorum")
            if st.button("Raporu onayla ve rota taslağını oluştur", disabled=not report_confirm or not report["can_generate"]):
                coach.approve_sufficiency(report["id"])
                result = safe(
                    lambda: coach.create_multi_source_route_draft(
                        st.session_state["advanced_route_source_ids"], profile["id"],
                        st.session_state["advanced_route_configuration"], report["id"],
                    ),
                    "Düzenlenebilir çoklu kaynak rota taslağı oluşturuldu.",
                )
                if result:
                    st.session_state["custom_route_id"] = result["route"]["id"]
                    st.session_state.pop("sufficiency_report", None)
                    st.rerun()

    routes = coach.list_custom_routes()
    if routes:
        st.subheader("3. Taslağı düzenle ve onayla")
        route_options = {f"{item['title']} · {item['status']}": item["id"] for item in routes}
        selected_route_label = st.selectbox("Özel rota", list(route_options))
        selected_route_id = route_options[selected_route_label]
        draft_tasks = coach.custom_route_tasks(selected_route_id)
        editable_rows = [
            {
                "id": item["id"],
                "position": item["position"],
                "included": bool(item["included"]),
                "title": item["title"],
                "description": item["description"],
                "module": item.get("module"),
                "task_type": item["task_type"],
                "estimated_minutes": item["estimated_minutes"],
                "generation_type": item.get("generation_type", item["provenance"]),
                "source_section": item["source_section"],
                "source_page": item.get("source_page"),
                "prerequisite_task_ids": item.get("prerequisite_task_ids", []),
            }
            for item in draft_tasks
        ]
        edited_rows = st.data_editor(
            editable_rows,
            hide_index=True,
            width="stretch",
            disabled=["id", "generation_type", "source_section", "source_page", "prerequisite_task_ids"],
            column_config={
                "position": st.column_config.NumberColumn("Sıra", min_value=1, step=1),
                "included": st.column_config.CheckboxColumn("Dahil"),
                "title": st.column_config.TextColumn("Görev adı"),
                "description": st.column_config.TextColumn("Açıklama"),
                "module": st.column_config.TextColumn("Modül"),
                "task_type": st.column_config.SelectboxColumn("Görev türü", options=["teori", "uygulama", "tekrar", "değerlendirme"]),
                "estimated_minutes": st.column_config.NumberColumn("Dakika", min_value=10, max_value=720, step=5),
                "generation_type": "Üretim türü",
                "source_section": "Kaynak bölüm",
                "source_page": "Sayfa",
            },
            key=f"route-editor-{selected_route_id}",
        )
        selected_route = next(item for item in routes if item["id"] == selected_route_id)
        route_source_rows = coach.route_sources(selected_route_id)
        with st.expander("Rota kaynakları ve görev kaynak kimlikleri"):
            st.json(route_source_rows)
            for task_item in draft_tasks:
                st.write(task_item["title"])
                st.json(coach.database.task_sources(task_item["id"], "draft"))
        if st.button("Taslak değişikliklerini kaydet", disabled=selected_route["status"] != "draft"):
            edits = edited_rows.to_dict("records") if hasattr(edited_rows, "to_dict") else list(edited_rows)
            saved = safe(lambda: coach.update_custom_route_tasks(selected_route_id, edits), "Taslak kaydedildi.")
            if saved:
                st.rerun()
        prerequisite_target = st.selectbox(
            "Ön koşulu düzenlenecek görev",
            [item["id"] for item in draft_tasks],
            format_func=lambda task_id: next(item["title"] for item in draft_tasks if item["id"] == task_id),
            disabled=selected_route["status"] != "draft",
        )
        prerequisite_task = next(item for item in draft_tasks if item["id"] == prerequisite_target)
        prerequisite_choices = [item["id"] for item in draft_tasks if item["id"] != prerequisite_target]
        prerequisite_ids = st.multiselect(
            "Bu görevden önce tamamlanması gereken görevler",
            prerequisite_choices,
            default=[item for item in prerequisite_task.get("prerequisite_task_ids", []) if item in prerequisite_choices],
            format_func=lambda task_id: next(item["title"] for item in draft_tasks if item["id"] == task_id),
            disabled=selected_route["status"] != "draft",
        )
        if st.button("Ön koşulları kaydet", disabled=selected_route["status"] != "draft"):
            prerequisite_edits = [dict(item) for item in draft_tasks]
            for item in prerequisite_edits:
                if item["id"] == prerequisite_target:
                    item["prerequisite_task_ids"] = prerequisite_ids
            if safe(
                lambda: coach.update_custom_route_tasks(selected_route_id, prerequisite_edits),
                "Ön koşullar kaydedildi.",
            ):
                st.rerun()
        duplicate_id = st.selectbox("Çoğaltılacak görev", [item["id"] for item in draft_tasks], disabled=selected_route["status"] != "draft")
        if st.button("Seçili görevi çoğalt", disabled=selected_route["status"] != "draft"):
            if safe(lambda: coach.duplicate_custom_route_task(selected_route_id, duplicate_id), "Görev kaynak bağı korunarak çoğaltıldı."):
                st.rerun()
        with st.form(f"new-route-task-{selected_route_id}"):
            new_task_title = st.text_input("Yeni görev adı")
            new_task_description = st.text_area("Yeni görev açıklaması")
            new_task_source = st.selectbox(
                "Kaynak bağı için örnek görev",
                [item["id"] for item in draft_tasks],
                format_func=lambda task_id: next(item["title"] for item in draft_tasks if item["id"] == task_id),
            )
            add_new_task = st.form_submit_button(
                "Kaynak bağlı yeni görev ekle",
                disabled=selected_route["status"] != "draft" or not new_task_title.strip(),
            )
        if add_new_task:
            if safe(
                lambda: coach.duplicate_custom_route_task(
                    selected_route_id,
                    new_task_source,
                    title=new_task_title,
                    description=new_task_description or new_task_title,
                ),
                "Yeni görev kaynak bağı korunarak eklendi.",
            ):
                st.rerun()
        if selected_route["status"] == "confirmed" and st.button("Düzenlemek için yeni rota sürümü oluştur"):
            new_version = safe(lambda: coach.create_route_version(selected_route_id), "Onaylı rota korunarak yeni taslak sürüm oluşturuldu.")
            if new_version:
                st.session_state["custom_route_id"] = new_version
                st.rerun()
        if st.button("Foundry Local ile taslağı kaynaklara bağlı iyileştir", disabled=selected_route["status"] != "draft"):
            improved = safe(
                lambda: coach.improve_custom_route_with_foundry(selected_route_id),
                "Foundry Local çıktısı doğrulandı ve rota taslağı güncellendi.",
            )
            if improved:
                st.rerun()
        if st.button("Kaynak bağlı başlangıç sınavı oluştur", disabled=selected_route["status"] == "archived"):
            quiz = safe(lambda: coach.create_custom_quiz(selected_route_id, [item["source_id"] for item in route_source_rows]))
            if quiz:
                st.success(f"{len(quiz)} kaynak bağlı soru oluşturuldu.")
                st.json(quiz)
        if st.button("Foundry Local ile kaynak bağlı sınav oluştur", disabled=selected_route["status"] == "archived"):
            quiz = safe(
                lambda: coach.create_custom_quiz_with_foundry(
                    selected_route_id, [item["source_id"] for item in route_source_rows]
                )
            )
            if quiz:
                st.success(f"{len(quiz)} Foundry Local sorusu kaynak kanıtıyla doğrulandı.")
                st.json(quiz)
        if st.button(
            "Rotayı onayla ve kişisel plana aktar",
            disabled=selected_route["status"] != "draft" or not profile,
        ):
            installed = safe(
                lambda: coach.install_custom_route(selected_route_id, profile["id"]),
                "Özel rota kişisel plana aktarıldı.",
            )
            if installed:
                st.json(installed)
                st.rerun()

    if active_documents:
        with st.expander("Belgeyi arşivle"):
            archive_options = {
                f"{item.get('original_filename') or item['filename']} · {item['id'][-8:]}": item["id"]
                for item in active_documents
            }
            archive_label = st.selectbox("Arşivlenecek belge", list(archive_options))
            archive_id = archive_options[archive_label]
            st.warning("Belge dosyası ve geçmiş görevler korunur; belge yeni arama ve rota üretiminden çıkarılır.")
            confirmation = st.text_input(f"Onay için yazın: BELGEYİ ARŞİVLE {archive_id}")
            if st.button("Belgeyi arşivle"):
                archived = safe(
                    lambda: coach.archive_uploaded_document(archive_id, confirmation),
                    "Belge arşivlendi; hazır üç kaynak belge korunmaya devam ediyor.",
                )
                if archived:
                    st.rerun()

elif page == PAGES[11]:
    st.title("Hazır rotalar kataloğu")
    st.caption("Bu üç rota isteğe bağlıdır; başlangıçta otomatik seçilmez ve kaynak belgeleri salt okunur korunur.")
    for route in coach.route_comparison():
        with st.container(border=True):
            st.subheader(route["title"])
            st.write(f"Kaynak belge: {route['source_document']}")
            st.write(f"Ana konular: {', '.join(route['topics'])}")
            st.write(f"Kaynak görevi: {route['source_task_count']} · Chunk: {route['chunk_count']}")
            st.write(f"Faz: {len(route['phases'])} · Hafta: {len(route['weeks'])} · Gün: {len(route['days'])}")
            st.write(f"Tahmini toplam süre: {route['source_task_count'] * 60} dakika")
            if st.button("Bu rotayı kullan", key=f"ready-{route['route']}", disabled=not profile):
                result = safe(lambda route_id=route["route"]: coach.select_ready_route(profile["id"], route_id), "Hazır rota mevcut planlama davranışıyla etkinleştirildi.")
                if result:
                    st.json(result)
                    st.rerun()
    if not profile:
        st.info("Bir hazır rotayı kullanmak için önce kaynak bağımsız onboarding profilini oluşturun.")

elif page == PAGES[12]:
    st.title("Açık internet kaynakları")
    st.warning(
        "Arama sorgusu veya onayladığınız URL internete gönderilir. Bulunan içerik onayınız olmadan indirilmez, "
        "rotaya eklenmez ya da indekslenmez. Bulut LLM kullanılmaz; onaylanan sayfanın yerel snapshot'ı saklanır."
    )
    web_consent = st.checkbox("Bu bilgilendirmeyi okudum ve web kaynak keşfini açıkça başlatıyorum")
    st.json(coach.web_sources.provider_status)
    direct_url = st.text_input("Doğrudan http/https URL")
    direct_title = st.text_input("Kaynak başlığı (opsiyonel)")
    if st.button("URL'yi keşif listesine ekle", disabled=not web_consent or not direct_url):
        if safe(lambda: coach.add_web_url(direct_url, title=direct_title), "URL yalnızca keşif listesine eklendi; içerik henüz indirilmedi."):
            st.rerun()
    query = st.text_input("Yapılandırılmış sağlayıcıyla arama sorgusu")
    if st.button("Açık kaynak ara", disabled=not web_consent or not query or not coach.web_sources.provider_status["configured"]):
        if safe(lambda: coach.discover_web_sources(query), "Arama sonuçları keşif listesine eklendi; henüz indekslenmedi."):
            st.rerun()
    if not coach.web_sources.provider_status["configured"]:
        st.info("Arama sağlayıcısı yapılandırılmamış. Manuel URL ekleme güvenli fallback olarak kullanılabilir.")
    web_rows = coach.list_web_sources()
    for item in web_rows:
        with st.container(border=True):
            st.subheader(item["title"])
            st.write(item["url"])
            st.caption(
                f"{item['domain']} · {item['source_type']} · {item['reliability_class']} · "
                f"durum={item['status']} · yayın={item.get('published_at') or 'bilinmiyor'} · erişim={item.get('accessed_at') or 'henüz yok'}"
            )
            if item["description"]:
                st.write(item["description"])
            if item["status"] in {"discovered", "failed"}:
                if st.button("Bu tek sayfayı indir, yerel snapshot oluştur ve indeksle", key=f"approve-web-{item['id']}", disabled=not web_consent):
                    if safe(lambda source_id=item["id"]: coach.approve_web_source(source_id), "Kaynak onaylandı, yerel snapshot oluşturuldu ve artımlı indekslendi."):
                        st.rerun()
            elif item["status"] == "approved":
                st.success(f"Onaylı yerel snapshot · SHA-256: {item['content_sha256']}")

elif page == PAGES[13]:
    require_profile()
    st.title("Grafikler ve odak sayacı")
    tasks = coach.database.tasks(profile["id"])
    session = coach.focus_session(profile["id"])
    task_options = {f"{item['title']} · {item['id'][-6:]}": item["id"] for item in tasks if item["status"] != "completed"}
    selected_focus_label = st.selectbox("Odak görevi", list(task_options), disabled=bool(session) or not task_options)
    elapsed = int(session.get("elapsed_seconds", 0)) if session else 0
    st.metric("Odak süresi", f"{elapsed // 3600:02d}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}")
    timer_cols = st.columns(5)
    if timer_cols[0].button("Başlat", disabled=bool(session) or not task_options):
        safe(lambda: coach.focus_action("start", profile["id"], task_options[selected_focus_label]), "Odak sayacı başlatıldı.")
        st.rerun()
    if timer_cols[1].button("Duraklat", disabled=not session or session["status"] != "running"):
        safe(lambda: coach.focus_action("pause", profile["id"]), "Sayaç duraklatıldı.")
        st.rerun()
    if timer_cols[2].button("Devam et", disabled=not session or session["status"] != "paused"):
        safe(lambda: coach.focus_action("resume", profile["id"]), "Sayaç devam ediyor.")
        st.rerun()
    if timer_cols[3].button("Bitir", disabled=not session):
        finished = safe(lambda: coach.focus_action("finish", profile["id"]), "Odak oturumu bitirildi; görev otomatik tamamlanmadı.")
        if finished:
            st.session_state["finished_focus"] = finished
            st.rerun()
    if timer_cols[4].button("Sıfırla", disabled=not session):
        safe(lambda: coach.focus_action("reset", profile["id"]), "Sayaç sıfırlandı; görev durumu değişmedi.")
        st.rerun()
    finished_focus = st.session_state.get("finished_focus")
    if finished_focus and finished_focus.get("task_id"):
        corrected_minutes = st.number_input("Göreve aktarılacak düzeltilmiş dakika", min_value=0, value=max(0, round(finished_focus["elapsed_seconds"] / 60)))
        if st.button("Süreyi göreve aktar"):
            if safe(lambda: coach.apply_focus_duration(finished_focus["task_id"], int(corrected_minutes) * 60), "Gerçek süre göreve aktarıldı; görev tamamlanmadı."):
                st.session_state.pop("finished_focus", None)
                st.rerun()

    st.subheader("Gerçek SQLite verilerinden grafikler")
    analytics = coach.analytics(profile["id"])
    chart_tasks = analytics["tasks"]
    if chart_tasks:
        st.write("Planlanan / gerçek çalışma süresi")
        st.bar_chart({
            "Planlanan": [item["estimated_minutes"] for item in chart_tasks],
            "Gerçek": [item["actual_minutes"] or 0 for item in chart_tasks],
        })
        quiz_values = [item["quiz_score"] for item in chart_tasks if item["quiz_score"] is not None]
        if quiz_values:
            st.write("Sınav puanı gelişimi")
            st.line_chart({"Puan": quiz_values})
        else:
            st.info("Sınav puanı grafiği için henüz veri yok.")
        difficulties = [item["difficulty_rating"] for item in chart_tasks if item["difficulty_rating"] is not None]
        if difficulties:
            st.write("Zorluk dağılımı")
            st.bar_chart({"Zorluk": difficulties})
        else:
            st.info("Zorluk grafiği için henüz veri yok.")
    else:
        st.info("Plan grafikleri için henüz görev verisi yok.")
    if analytics["sessions"]:
        st.write("Günlük görev tamamlama")
        st.line_chart({"Tamamlanan": [item["completed_tasks"] for item in analytics["sessions"]]})
    else:
        st.info("Günlük tamamlama grafiği için henüz veri yok.")
    if analytics["sufficiency"]:
        st.write("Kaynak yeterlilik puanları")
        st.line_chart({"Kapsam": [item["coverage_score"] for item in analytics["sufficiency"]]})
    else:
        st.info("Kaynak yeterlilik grafiği için henüz veri yok.")
    dashboard = coach.dashboard(profile["id"])
    st.write("Yerel / internet kaynak dağılımı")
    if sum(dashboard["source_counts"].values()):
        st.bar_chart({"Kaynak sayısı": [dashboard["source_counts"]["local"], dashboard["source_counts"]["web"]]})
    else:
        st.info("Kaynak dağılımı grafiği için henüz rota kaynağı yok.")
