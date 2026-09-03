# Local Learning Coach

Belgelerden kişisel öğrenme rotası oluşturan ve teknik mülakat hazırlığını takip eden yerel bir Streamlit uygulaması.

Uygulama DOCX ve PDF belgelerini parçalara ayırır, dense embedding + BM25 + RRF ile arar ve kaynak göstererek yanıt üretir. Profil, görev, ilerleme ve indeks verileri bilgisayarda tutulur. Foundry Local isteğe bağlıdır; temel rota, arama ve değerlendirme özellikleri model olmadan da çalışır.

## Özellikler

- DOCX/PDF yükleme ve aynı belgenin tekrar yüklenmesini engelleme
- Bir veya birden fazla kaynaktan düzenlenebilir öğrenme rotası
- Kaynak bölümünü ve chunk kimliğini taşıyan görevler
- Belge yeterlilik analizi, ön koşullar ve adaptif tekrar önerileri
- Belgeden sınav ve kaynaklı soru-cevap
- Günlük/haftalık plan, ilerleme, odak sayacı ve grafikler
- Yazılım mühendisliği ve Python backend için teknik mülakat koçu
- Algoritma, kodlama, SQL, sistem tasarımı ve davranışsal mülakat çalışmaları
- İsteğe bağlı yerel Foundry Local ve localhost OpenSearch desteği

## Kurulum

Gereksinimler: Windows 10/11, PowerShell ve Python 3.11+.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

Manuel kurulum:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python cli.py setup
```

## Çalıştırma

```powershell
.\.venv\Scripts\streamlit run app.py
```

Tarayıcı otomatik açılmazsa terminalde gösterilen `http://localhost:8501` adresine gidin.

## Kullanım

1. Öğrenme hedefinizi ve çalışma sürenizi girin.
2. Kendi DOCX/PDF belgenizi yükleyin veya hazır rotalardan birini seçin.
3. Belge yeterlilik raporunu inceleyin.
4. Oluşturulan görevleri düzenleyip rotayı onaylayın.
5. Günlük görevleri tamamlayın; sınav, tekrar ve ilerleme sonuçlarını takip edin.

Teknik mülakat çalışması için sol menüden `Teknik Mülakat Koçu` modunu seçin. Kariyer profilini doldurduktan sonra seviye tespitini tamamlayabilir, eksik haritası çıkarabilir ve kişisel mülakat planı oluşturabilirsiniz.

## Hazır belgeler

Hazır üç rota aşağıdaki dosya adlarını kullanır:

- `Python Pandas Titanic Project Plan.docx`
- `One-Month Machine Learning Plan.docx`
- `Quantum Programming Month Plan.docx`

Bu belgeler depoya dahil değildir. Belgelerin bulunduğu klasörü `.env` dosyasında ayarlayın:

```powershell
Copy-Item .env.example .env
```

Ardından `.env` içindeki `LLC_SOURCE_DIR` değerini kendi klasörünüzle değiştirin. Hazır belgeler isteğe bağlıdır; kullanıcı kendi belgesiyle doğrudan başlayabilir.

## Foundry Local (isteğe bağlı)

```powershell
winget install Microsoft.FoundryLocal
.\.venv\Scripts\python -m pip install foundry-local-sdk-winml
.\.venv\Scripts\python scripts\start_foundry_service.py --model qwen2.5-0.5b
```

Scriptin gösterdiği yerel endpoint ve model kimliğini ortam değişkenlerine yazın. Uygulama yalnızca `localhost`, `127.0.0.1` veya `::1` adreslerini kabul eder; bulut LLM fallback'i yoktur.

## Testler

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python scripts\smoke_test.py
```

## Yerel veriler

`.env`, sanal ortam, SQLite veritabanı, yüklenen belgeler, indeksler, web snapshot'ları ve loglar `.gitignore` kapsamındadır. Bunlar GitHub'a gönderilmez.
