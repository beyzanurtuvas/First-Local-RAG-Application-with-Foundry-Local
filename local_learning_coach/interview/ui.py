from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Callable

import streamlit as st

from local_learning_coach.interview.runner import CodeRunnerError


INTERVIEW_PAGES = (
    "1. Kariyer profili",
    "2. Başlangıç değerlendirmesi",
    "3. Yetkinlik ve eksiklik haritası",
    "4. Kişisel hazırlık planı",
    "5. Deneme mülakatı",
    "6. İlerleme ve raporlar",
    "7. Gelişmiş ayarlar",
)


def _safe(action: Callable[[], Any], success: str | None = None) -> Any:
    try:
        result = action()
        if success:
            st.success(success)
        return result
    except (ValueError, CodeRunnerError) as exc:
        st.error(str(exc))
    except Exception as exc:  # pragma: no cover - Streamlit güvenlik ağı
        logging.exception("Teknik mülakat ekranı işlemi başarısız")
        st.error(f"İşlem tamamlanamadı: {exc}")
    return None


def _ensure_general_profile(coach: Any, profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if profile:
        return profile
    st.info("Teknik mülakat verilerini ilişkilendirmek için önce yerel bir profil adı oluşturun.")
    with st.form("interview-base-profile"):
        name = st.text_input("Profil adı")
        submitted = st.form_submit_button("Yerel profili oluştur")
    if submitted:
        today = date.today()
        data = {
            "name": name.strip(), "goal": "Teknik mülakata hazırlanmak", "goal_description": "",
            "source_method": "Henüz belgem yok", "preferred_route": "unassigned", "weekly_days": 5,
            "daily_minutes": 60, "preferred_days": ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma"],
            "start_date": today.isoformat(), "target_end_date": (today + timedelta(days=60)).isoformat(),
            "theory_practice_preference": "Dengeli", "completed_topics": [], "difficult_topics": [],
            "review_preference": "Haftalık", "declared_level": "Başlangıç",
        }
        created = _safe(lambda: coach.create_profile(data, {}), "Yerel profil oluşturuldu.")
        if created:
            st.rerun()
    return None


def _career_page(coach: Any, profile: dict[str, Any]) -> None:
    st.title("Kariyer ve mülakat hedefi")
    current = coach.interview.career_profile(profile["id"])
    tracks = coach.interview.tracks()
    labels = {f"{item['title']} · {'tam' if item['support_level'] == 'full' else 'beta'}": item["id"] for item in tracks}
    reverse = {value: key for key, value in labels.items()}
    if current:
        st.success(f"Aktif hedef: {reverse.get(current['target_track'], current['target_track'])} · {current['target_level']}")
    default_track = list(labels).index(reverse[current["target_track"]]) if current else 0
    with st.form("career-profile-form"):
        c1, c2 = st.columns(2)
        department = c1.text_input("Bölüm / alan", value=current["department"] if current else "Yazılım Mühendisliği")
        education_type = c2.text_input("Eğitim türü", value=current.get("education_type") or "Lisans" if current else "Lisans")
        education_status = c1.selectbox("Eğitim durumu", ["Öğrenci", "Mezun", "Çalışıyor"], index=0)
        experience = c2.selectbox("Deneyim", ["Yok", "0-1 yıl", "1-3 yıl", "3+ yıl"], index=0)
        track_label = c1.selectbox("Hedef alan", list(labels), index=default_track)
        target_role = c2.text_input("Hedef rol", value=current["target_role"] if current else "Junior Software Engineer")
        target_level = c1.selectbox("Hedef seviye", ["intern", "junior", "mid", "senior"],
                                    index=["intern", "junior", "mid", "senior"].index(current["target_level"]) if current else 1)
        language = c2.selectbox("Tercih edilen programlama dili", ["Python", "Java", "C#", "JavaScript", "C++", "Diğer"])
        technologies = c1.text_area("Diğer teknolojiler (satır başına)", value="\n".join(current["other_technologies"]) if current else "")
        company_type = c2.selectbox("Şirket türü", ["Fark etmez", "Startup", "Kurumsal", "Ürün şirketi", "Danışmanlık"])
        interview_language = c1.selectbox("Mülakat dili", ["Türkçe", "İngilizce", "Karma"])
        target_date = c2.date_input("Hedef mülakat tarihi", value=date.fromisoformat(current["target_interview_date"]) if current else date.today() + timedelta(days=60))
        weekly_days = c1.slider("Haftalık çalışma günü", 1, 7, value=int(current["weekly_days"]) if current else 5)
        daily_minutes = c2.number_input("Günlük çalışma süresi (dk)", 10, 720, value=int(current["daily_minutes"]) if current else 60, step=5)
        preferred_days = st.multiselect("Tercih edilen günler", ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"],
                                        default=current["preferred_days"] if current else ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma"])
        strengths = c1.text_area("Beyan edilen güçlü yönler", value="\n".join(current["declared_strengths"]) if current else "")
        weaknesses = c2.text_area("Beyan edilen gelişim alanları", value="\n".join(current["declared_weaknesses"]) if current else "")
        job_url = c1.text_input("İş ilanı URL'si (isteğe bağlı; otomatik indirilmez)", value=current.get("job_url") or "" if current else "")
        job_text = c2.text_area("İlan metni (isteğe bağlı)", value=current.get("job_text") or "" if current else "")
        submitted = st.form_submit_button("Kariyer profilini kaydet")
    if submitted:
        data = {
            "department": department, "education_type": education_type, "education_status": education_status,
            "experience": experience, "target_track": labels[track_label], "target_role": target_role,
            "target_level": target_level, "preferred_language": language,
            "other_technologies": technologies.splitlines(), "company_type": company_type,
            "interview_language": interview_language, "target_interview_date": target_date.isoformat(),
            "weekly_days": weekly_days, "daily_minutes": int(daily_minutes), "preferred_days": preferred_days,
            "declared_strengths": strengths.splitlines(), "declared_weaknesses": weaknesses.splitlines(),
            "job_url": job_url, "job_text": job_text,
        }
        saved = _safe(lambda: coach.interview.save_career_profile(profile["id"], data), "Kariyer profili kaydedildi.")
        if saved:
            st.rerun()
    if current and next(item for item in tracks if item["id"] == current["target_track"])["support_level"] == "beta":
        st.warning("Bu alanın yetkinlik iskeleti hazırdır; tam soru bankası ve otomatik değerlendirme henüz beta kapsamındadır.")


def _assessment_page(coach: Any, profile: dict[str, Any]) -> None:
    st.title("Kanıta dayalı seviye tespiti")
    career = coach.interview.career_profile(profile["id"])
    if not career:
        st.info("Önce Kariyer profili ekranını tamamlayın.")
        return
    session_id = st.session_state.get("interview_assessment_id")
    session = _safe(lambda: coach.interview.assessment_session(session_id)) if session_id else None
    if not session or session["status"] in {"completed", "abandoned"}:
        st.write("Değerlendirme 10 teknik, 2 algoritma, 1 kodlama, 1 debugging, 1 SQL, 1 sistem tasarımı ve 2 davranışsal sorudan oluşur.")
        if st.button("Yeni değerlendirme başlat", type="primary"):
            result = _safe(lambda: coach.interview.start_assessment(profile["id"]))
            if result:
                st.session_state["interview_assessment_id"] = result["id"]
                st.rerun()
        return
    answered, total = len(session["responses"]), len(session["question_ids"])
    st.progress(answered / max(1, total), text=f"{answered}/{total} soru · {session['elapsed_seconds']} saniye")
    a, b = st.columns(2)
    if session["status"] == "active" and a.button("Duraklat"):
        _safe(lambda: coach.interview.assessment_action(session_id, "pause"), "Oturum duraklatıldı.")
        st.rerun()
    if session["status"] == "paused" and a.button("Devam et"):
        _safe(lambda: coach.interview.assessment_action(session_id, "resume"), "Oturum sürdürüldü.")
        st.rerun()
    if b.button("Oturumu bırak"):
        _safe(lambda: coach.interview.assessment_action(session_id, "abandon"), "Oturum bırakıldı.")
        st.rerun()
    if session["status"] != "active":
        st.info("Yanıtlamak için oturuma devam edin.")
        return
    question = coach.interview.current_assessment_question(session_id)
    if not question:
        st.success("Değerlendirme tamamlandı. Eksik haritası ekranına geçin.")
        return
    st.subheader(f"{answered + 1}. soru · {question['question_type']}")
    st.write(question["prompt"])
    with st.form(f"interview-answer-{question['id']}"):
        if question["question_type"] == "technical":
            response = st.radio("Yanıt", question["options"], index=None)
        else:
            response = st.text_area("Yanıt" if question["question_type"] != "coding" else "Python kodu", height=220)
        runner_approved = False
        if question["question_type"] == "coding":
            st.caption("Kod yalnız LLC_CODE_RUNNER_ENABLED=1 ise, ayrı geçici süreçte ve süre/çıktı sınırıyla çalıştırılır. İşletim sistemi düzeyinde ağ izolasyonu garanti edilmez.")
            runner_approved = st.checkbox("Bu yanıtı yerel test sürecinde çalıştırmayı onaylıyorum")
        use_foundry = st.checkbox("Yanıtı Foundry Local rubric ile de incele", value=False,
                                  disabled=question["question_type"] == "technical")
        submit = st.form_submit_button("Yanıtı kaydet")
        skip = st.form_submit_button("Atla — ölçülmedi olarak işaretle")
    if submit or skip:
        saved = _safe(lambda: coach.interview.submit_assessment_response(
            session_id, question["id"], response or "", skipped=skip, runner_approved=runner_approved,
            use_foundry=use_foundry,
        ))
        if saved is not None:
            st.rerun()


def _gap_page(coach: Any, profile: dict[str, Any]) -> None:
    st.title("Yetkinlik ve eksik haritası")
    report = coach.interview.latest_gap_report(profile["id"])
    if not report:
        st.info("Eksik haritası için seviye tespit değerlendirmesini tamamlayın.")
        return
    c1, c2 = st.columns(2)
    c1.metric("Genel yeterlilik", f"%{report['overall_score']:.1f}")
    c2.metric("Ölçüm güveni", f"%{report['confidence_score']:.1f}")
    st.caption("Beyan edilen güçlü/zayıf yönler puana dönüştürülmez; yalnız ölçülmüş yanıtlardan ayrı tutulur.")
    rows = [{"Yetkinlik": item["name"], "Yeterlilik": item["proficiency_score"],
             "Güven": item["confidence_score"], "Öncelik": item["priority"],
             "Ölçülen kanıt": len(item["evidence"]), "Önerilen çalışma": item.get("recommended_study_type", "-")}
            for item in report["items"]]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _plan_page(coach: Any, profile: dict[str, Any]) -> None:
    st.title("Kişisel teknik mülakat planı")
    plan = coach.interview.plan(profile["id"])
    if not plan:
        if st.button("Eksik haritasından plan taslağı oluştur", type="primary"):
            result = _safe(lambda: coach.interview.create_plan(profile["id"]), "Düzenlenebilir plan taslağı oluşturuldu.")
            if result:
                st.rerun()
        return
    st.info(f"Plan durumu: {plan['status']} · Hedef tarih: {plan['target_interview_date']}")
    date_col, action_col = st.columns([2, 1])
    changed_target = date_col.date_input(
        "Plan hedef mülakat tarihi", value=date.fromisoformat(plan["target_interview_date"]),
        key=f"interview-plan-target-{plan['id']}",
    )
    if action_col.button("Takvimi yeniden hesapla", key=f"interview-reschedule-{plan['id']}"):
        if _safe(lambda: coach.interview.reschedule_plan(
            plan["id"], profile["id"], changed_target.isoformat(),
        ), "Açık görevler yeniden takvimlendi; içerik ve tamamlanan görevler korundu."):
            st.rerun()
    rows = [{"id": item["id"], "Sıra": item["position"], "Dahil": item["included"], "Görev": item["title"],
             "Süre": item["estimated_minutes"], "Tür": item["task_type"], "Zorluk": item["difficulty"],
             "Ön koşullar": ", ".join(item["prerequisite_task_ids"]),
             "Neden": item["reason"], "Kaynak": item["source_id"], "Kaynak konumu": item["source_locator"],
             "Üretim": item["generation_method"]} for item in plan["tasks"]]
    edited = st.data_editor(rows, use_container_width=True, hide_index=True, disabled=["id", "Tür", "Zorluk", "Ön koşullar", "Neden", "Kaynak", "Kaynak konumu", "Üretim"],
                            num_rows="fixed", key=f"interview-plan-editor-{plan['id']}")
    if plan["status"] == "draft":
        with st.expander("Taslağa görev ekle"):
            competencies = coach.interview.competencies(coach.interview.career_profile(profile["id"])["target_track"])
            competency_labels = {item["name"]: item["id"] for item in competencies}
            with st.form(f"interview-add-task-{plan['id']}"):
                selected_competency = st.selectbox("Yetkinlik", list(competency_labels))
                new_title = st.text_input("Yeni görev adı")
                new_type = st.selectbox("Görev türü", ["konu okuma", "teknik soru", "algoritma", "kodlama", "debugging", "SQL", "sistem tasarımı", "davranışsal cevap", "tekrar", "süreli mini mülakat", "tam deneme mülakatı"])
                new_minutes = st.number_input("Tahmini süre", 10, 720, 30, 5)
                add_submitted = st.form_submit_button("Görevi taslağa ekle")
            if add_submitted and _safe(lambda: coach.interview.add_plan_task(
                plan["id"], competency_labels[selected_competency], new_title, new_type, int(new_minutes),
            ), "Görev taslağa eklendi."):
                st.rerun()
        c1, c2 = st.columns(2)
        if c1.button("Düzenlemeleri kaydet"):
            edits = [{"id": item["id"], "position": item["Sıra"], "included": item["Dahil"],
                      "title": item["Görev"], "estimated_minutes": item["Süre"]} for item in edited]
            if _safe(lambda: coach.interview.update_plan_tasks(plan["id"], edits), "Plan düzenlendi.") is not None:
                st.rerun()
    else:
        st.subheader("Görev ilerlemesi")
        for task in plan["tasks"]:
            if not task["included"]:
                continue
            with st.container(border=True):
                st.write(f"**{task['title']}** · {task['estimated_minutes']} dk · `{task['status']}`")
                actions = {
                    "pending": [("Başlat", "in_progress"), ("Atla", "skipped")],
                    "in_progress": [("Tamamla", "completed"), ("Tekrar gerekli", "needs_review")],
                    "completed": [("Tekrar gerekli", "needs_review")],
                    "needs_review": [("Yeniden başlat", "in_progress"), ("Tamamla", "completed")],
                    "skipped": [("Plana geri al", "pending")],
                }
                columns = st.columns(max(1, len(actions.get(task["status"], []))))
                for column, (label, target) in zip(columns, actions.get(task["status"], [])):
                    if column.button(label, key=f"interview-task-{task['id']}-{target}"):
                        if _safe(lambda task_id=task["id"], value=target: coach.interview.update_plan_task_status(task_id, value)):
                            st.rerun()
    with st.expander("Planı arşivle"):
        expected = f"PLANI ARŞİVLE {plan['id']}"
        st.caption(f"Görev geçmişini silmeden planı etkin görünümden kaldırmak için `{expected}` yazın.")
        archive_confirmation = st.text_input("Arşivleme onayı", key=f"interview-archive-confirm-{plan['id']}")
        if st.button("Planı arşivle", key=f"interview-archive-{plan['id']}"):
            if _safe(lambda: coach.interview.archive_plan(plan["id"], profile["id"], archive_confirmation), "Plan arşivlendi."):
                st.rerun()
        if c2.button("Planı onayla", type="primary"):
            if _safe(lambda: coach.interview.confirm_plan(plan["id"], profile["id"]), "Plan onaylandı."):
                st.rerun()


def _mock_page(coach: Any, profile: dict[str, Any]) -> None:
    st.title("Kalıcı deneme mülakatı")
    career = coach.interview.career_profile(profile["id"])
    if not career:
        st.info("Önce Kariyer profili ekranını tamamlayın.")
        return
    types = ["Hızlı teknik tarama", "Algoritma mülakatı", "Python backend mülakatı", "Sistem tasarımı mülakatı", "Davranışsal mülakat", "Tam karma mülakat"]
    mock_id = st.session_state.get("interview_mock_id")
    mock = _safe(lambda: coach.interview.mock_interview(mock_id)) if mock_id else None
    if not mock or mock["status"] == "completed":
        selected = st.selectbox("Mülakat türü", types)
        if st.button("Deneme mülakatı başlat", type="primary"):
            result = _safe(lambda: coach.interview.start_mock_interview(profile["id"], selected))
            if result:
                st.session_state["interview_mock_id"] = result["id"]
                st.rerun()
        if mock and mock.get("report"):
            st.subheader("Son oturum raporu")
            st.json(mock["report"])
        return
    st.caption(f"Durum: {mock['status']} · Geçen süre: {mock['elapsed_seconds']} sn")
    if mock["status"] == "paused":
        if st.button("Mülakata devam et"):
            _safe(lambda: coach.interview.mock_action(mock_id, "resume"))
            st.rerun()
        return
    if st.button("Duraklat"):
        _safe(lambda: coach.interview.mock_action(mock_id, "pause"))
        st.rerun()
    question = mock["current_question"]
    if question:
        st.subheader(question["question_type"])
        st.write(question["prompt"])
        with st.form(f"mock-response-{question['id']}"):
            response = (st.radio("Yanıt", question["options"], index=None)
                        if question["question_type"] == "technical" else st.text_area("Yanıt", height=220))
            hint_count = st.number_input("Kullanılan ipucu sayısı", min_value=0, max_value=10, value=0)
            approved = st.checkbox("Kod sorusuysa yerel test çalıştırmayı onaylıyorum") if question["question_type"] == "coding" else False
            use_foundry = st.checkbox("Foundry Local rubric ile destekle", value=False,
                                      disabled=question["question_type"] == "technical")
            submitted = st.form_submit_button("Yanıtı değerlendir")
            skipped = st.form_submit_button("Atla — ölçülmedi")
        if submitted or skipped:
            result = _safe(lambda: coach.interview.submit_mock_response(
                mock_id, response or "", runner_approved=approved, use_foundry=use_foundry,
                hint_count=int(hint_count), skipped=skipped,
            ))
            if result:
                if result.get("follow_up"):
                    st.info(result["follow_up"])
                st.rerun()
    elif st.button("Mülakatı tamamla", type="primary"):
        if _safe(lambda: coach.interview.mock_action(mock_id, "finish")):
            st.rerun()


def _dashboard_page(coach: Any, profile: dict[str, Any]) -> None:
    st.title("Teknik mülakat paneli")
    data = coach.interview.dashboard(profile["id"])
    if data["empty"]:
        st.info("Kariyer profili oluşturulduğunda hedef sayaç ve ölçümler burada görünecek.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Mülakata kalan gün", data["remaining_days"])
    c2.metric("Genel hazırlık / güven", f"%{data['overall_score']:.1f} / %{data['confidence_score']:.1f}" if data["overall_score"] is not None else "Ölçülmedi")
    c3.metric("Plan riski", data["risk"])
    st.caption(f"Hedef: {data['target_role']} · {data['target_level']} · Açık çalışma {data['remaining_minutes']} dk")
    metric_cols = st.columns(3)
    metric_cols[0].metric("Algoritma başarısı", f"%{data['algorithm_success_rate']:.1f}" if data["algorithm_success_rate"] is not None else "Ölçülmedi")
    metric_cols[1].metric("Sistem tasarımı", f"%{data['system_design_score']:.1f}" if data["system_design_score"] is not None else "Ölçülmedi")
    metric_cols[2].metric("STAR tamamlanması", f"%{data['star_completion_rate']:.1f}" if data["star_completion_rate"] is not None else "Ölçülmedi")
    if data["gap"]:
        chart = {item["name"]: item["proficiency_score"] for item in data["gap"]["items"]}
        st.bar_chart(chart, horizontal=True)
    st.subheader("Uyarlama kararları")
    if data["adaptive_decisions"]:
        st.dataframe(data["adaptive_decisions"], hide_index=True, use_container_width=True)
    else:
        st.caption("Henüz uyarlama kararı yok.")
    st.subheader("Bugünün görevleri")
    if data["today_tasks"]:
        st.dataframe(data["today_tasks"], hide_index=True, use_container_width=True)
    else:
        st.caption("Bugün için açık görev yok.")
    st.subheader("Kanıt kaynakları")
    if data["sources"]:
        st.dataframe(data["sources"], hide_index=True, use_container_width=True)
    else:
        st.caption("Henüz ölçülmüş kaynak kanıtı yok.")


def _advanced_page(coach: Any, profile: dict[str, Any]) -> None:
    st.title("Teknik mülakat gelişmiş ayarları")
    foundry = _safe(lambda: coach.foundry_status()) if hasattr(coach, "foundry_status") else None
    st.write("Foundry Local:", foundry or "Durum alınamadı")
    st.write("Yerel kod çalıştırıcı:", "Etkin" if coach.settings.code_runner_enabled else "Kapalı (varsayılan)")
    st.caption("Foundry yalnız localhost üzerinden isteğe bağlı rubric iyileştirmesi içindir; deterministik değerlendirme onsuz çalışır.")
    st.subheader("Mevcut gelişmiş araçlar")
    st.caption("Belge yükleme, web onayı, OCR, Foundry ve localhost OpenSearch yönetimi genel koçtaki mevcut güvenli ekranları kullanır.")
    navigation = {
        "Belge ve rota yönetimi": "11. Belge ve rota yönetimi",
        "Açık internet kaynakları": "13. Açık internet kaynakları",
        "Foundry, OCR ve OpenSearch ayarları": "10. Ayarlar",
    }
    nav_columns = st.columns(3)
    for column, (label, target) in zip(nav_columns, navigation.items()):
        if column.button(label, key=f"interview-advanced-{target}"):
            st.session_state["app-mode"] = "Genel Öğrenme Koçu"
            st.session_state["general-page"] = target
            st.rerun()
    cache = coach.interview.cache_status()
    st.subheader("Yerel önbellek")
    st.json(cache)
    confirmation = st.text_input("Önbelleği temizleme onayı", placeholder="YEREL ÖNBELLEĞİ TEMİZLE")
    if st.button("Önbelleği temizle"):
        _safe(lambda: coach.interview.clear_cache(confirmation), "Yerel önbellek temizlendi.")
    st.subheader("Onaylanmış iş ilanı kaynakları")
    sources = coach.database.list_source_documents(source_kind="web", include_archived=False)
    options = {f"{item['title']} · {item.get('source_url') or ''}": item["id"] for item in sources}
    selected = st.multiselect("Analiz edilecek onaylanmış snapshot'lar", list(options))
    if st.button("İlan gereksinimlerini çıkar"):
        result = _safe(lambda: coach.interview.extract_job_requirements([options[item] for item in selected]))
        if result:
            st.json(result)


def render_interview_app(coach: Any, profile: dict[str, Any] | None) -> None:
    """Eski öğrenme ekranlarından bağımsız teknik mülakat modu."""
    st.sidebar.caption("Teknik mülakat verileri yerel SQLite içinde tutulur.")
    page = st.sidebar.radio("Teknik mülakat ekranı", INTERVIEW_PAGES, key="interview-page")
    profile = _ensure_general_profile(coach, profile)
    if not profile:
        return
    handlers = (
        _career_page, _assessment_page, _gap_page, _plan_page, _mock_page, _dashboard_page, _advanced_page,
    )
    handlers[INTERVIEW_PAGES.index(page)](coach, profile)
