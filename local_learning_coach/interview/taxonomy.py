from __future__ import annotations

from typing import Any


TAXONOMY_VERSION = "interview-taxonomy-v1"

TRACKS: tuple[dict[str, str], ...] = (
    {"id": "software-engineering-general", "title": "Genel Yazılım Mühendisliği", "support_level": "full",
     "description": "Stajyer ve junior adaylar için yazılım mühendisliği temelleri."},
    {"id": "backend-python", "title": "Python Backend", "support_level": "full",
     "description": "Stajyer ve junior Python backend pozisyonları."},
    {"id": "frontend", "title": "Frontend", "support_level": "beta", "description": "Genişletilebilir frontend track tanımı."},
    {"id": "mobile", "title": "Mobil", "support_level": "beta", "description": "Genişletilebilir mobil track tanımı."},
    {"id": "devops-cloud", "title": "DevOps / Cloud", "support_level": "beta", "description": "Genişletilebilir DevOps track tanımı."},
    {"id": "data-ai", "title": "Veri ve Yapay Zekâ", "support_level": "beta", "description": "Genişletilebilir veri/AI track tanımı."},
    {"id": "cybersecurity", "title": "Siber Güvenlik", "support_level": "beta", "description": "Genişletilebilir güvenlik track tanımı."},
    {"id": "embedded-systems", "title": "Gömülü Sistemler", "support_level": "beta", "description": "Genişletilebilir gömülü sistem track tanımı."},
    {"id": "game-development", "title": "Oyun Geliştirme", "support_level": "beta", "description": "Genişletilebilir oyun geliştirme track tanımı."},
)

CORE_COMPETENCIES: tuple[tuple[str, str, str, float, tuple[str, ...]], ...] = (
    ("programming", "Programlama temelleri", "Değişkenler, kontrol akışı, fonksiyonlar ve hata yönetimi.", .95, ()),
    ("oop", "Nesne yönelimli programlama", "Sınıf, nesne, kapsülleme, kalıtım ve bileşim.", .80, ("programming",)),
    ("data-structures", "Veri yapıları", "Dizi, liste, yığın, kuyruk, hash tablo ve ağaçlar.", .95, ("programming",)),
    ("algorithms", "Algoritmalar", "Arama, sıralama ve problem çözme stratejileri.", 1.0, ("data-structures",)),
    ("complexity", "Zaman ve alan karmaşıklığı", "Big-O analizi ve kaynak maliyeti karşılaştırması.", .90, ("algorithms",)),
    ("git", "Git ve sürüm kontrolü", "Commit, branch, merge ve işbirliği akışları.", .65, ("programming",)),
    ("sql", "SQL ve veri modelleme", "İlişkisel model, JOIN, indeks ve transaction temelleri.", .85, ("programming",)),
    ("os", "İşletim sistemi temelleri", "Süreç, thread, bellek ve dosya sistemi.", .65, ("programming",)),
    ("networks", "Bilgisayar ağları", "TCP/IP, DNS ve istemci-sunucu iletişimi.", .70, ("os",)),
    ("http-rest", "HTTP ve REST", "HTTP metotları, durum kodları ve kaynak tasarımı.", .80, ("networks",)),
    ("testing", "Test teknikleri", "Birim, entegrasyon ve kenar durum testleri.", .85, ("programming",)),
    ("debugging", "Hata ayıklama", "Hata üretme, izole etme ve kök neden analizi.", .85, ("programming",)),
    ("clean-code", "Temiz kod ve SOLID", "Okunabilirlik, sorumluluklar ve bağımlılık yönetimi.", .75, ("oop", "testing")),
    ("architecture", "Temel yazılım mimarisi", "Katmanlar, bileşenler ve veri akışı.", .70, ("oop", "http-rest")),
    ("security", "Güvenlik temelleri", "Girdi doğrulama, yetkilendirme ve sır yönetimi.", .70, ("http-rest",)),
    ("communication", "Teknik iletişim", "Çözümü, varsayımları ve tercihleri açıklama.", .80, ()),
    ("behavioral", "Davranışsal mülakat", "Deneyimleri STAR yapısıyla açık ve somut anlatma.", .60, ("communication",)),
)

BACKEND_COMPETENCIES: tuple[tuple[str, str, str, float, tuple[str, ...]], ...] = (
    ("python", "Python dil bilgisi", "Python sözdizimi, koleksiyonlar ve fonksiyonlar.", 1.0, ("programming",)),
    ("python-model", "Python veri modeli", "Iterator, context manager, equality ve object protokolü.", .70, ("python", "oop")),
    ("exceptions", "Exception yönetimi", "Hata sınırları ve doğru exception tasarımı.", .80, ("python",)),
    ("packaging", "Paket ve ortam yönetimi", "Sanal ortam, bağımlılıklar ve paket yapısı.", .60, ("python",)),
    ("api", "Python API geliştirme", "FastAPI benzeri yaklaşımla doğrulanmış API geliştirme.", .95, ("python", "http-rest")),
    ("orm", "ORM ve transaction", "ORM kullanımı, transaction sınırları ve veri tutarlılığı.", .85, ("sql", "api")),
    ("auth", "Authentication ve authorization", "Kimlik doğrulama ile yetki kontrolünü ayırma.", .90, ("api", "security")),
    ("caching", "Caching", "Cache key, invalidation, TTL ve tutarlılık tercihleri.", .75, ("api",)),
    ("logging", "Logging ve gözlemlenebilirlik", "Yapılandırılmış log, metrik ve hata izleme.", .70, ("api",)),
    ("async", "Async programlama", "I/O concurrency, event loop ve async sınırları.", .75, ("python", "os")),
    ("api-testing", "API testleri", "API sözleşmesi ve entegrasyon testleri.", .85, ("api", "testing")),
    ("docker", "Docker temelleri", "Tekrarlanabilir yerel çalışma ortamı ve image/container farkı.", .55, ("packaging",)),
    ("scalability", "Temel ölçeklenebilirlik", "Yatay ölçekleme, kuyruk, cache ve darboğazlar.", .75, ("architecture", "caching")),
    ("backend-design", "Backend sistem tasarımı", "API, veri, cache, güvenlik ve gözlemlenebilirlik kararları.", .90, ("architecture", "orm", "auth", "logging")),
)


def competencies_for_track(track_id: str) -> list[dict[str, Any]]:
    if track_id not in {item["id"] for item in TRACKS}:
        raise ValueError(f"Bilinmeyen teknik mülakat track'i: {track_id}")
    source = list(CORE_COMPETENCIES)
    if track_id == "backend-python":
        source.extend(BACKEND_COMPETENCIES)
    if track_id not in {"software-engineering-general", "backend-python"}:
        source = list(CORE_COMPETENCIES[:5]) + [CORE_COMPETENCIES[15], CORE_COMPETENCIES[16]]
    prefix = "se" if track_id == "software-engineering-general" else "bp" if track_id == "backend-python" else track_id
    output: list[dict[str, Any]] = []
    for slug, name, description, weight, prerequisites in source:
        output.append({
            "id": f"{prefix}-{slug}",
            "track_id": track_id,
            "name": name,
            "description": description,
            "level": "intern-junior" if track_id in {"software-engineering-general", "backend-python"} else "beta",
            "role_weight": weight,
            "measurement_methods": ["technical", "coding", "explanation"],
            "prerequisites": [f"{prefix}-{value}" for value in prerequisites],
            "taxonomy_version": TAXONOMY_VERSION,
        })
    validate_competency_graph(output)
    return output


def validate_competency_graph(competencies: list[dict[str, Any]]) -> None:
    ids = {item["id"] for item in competencies}
    graph = {item["id"]: list(item.get("prerequisites", [])) for item in competencies}
    if any(dependency not in ids for values in graph.values() for dependency in values):
        raise ValueError("Yetkinlik ön koşul grafiği bilinmeyen kimlik içeriyor.")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError("Yetkinlik ön koşul grafiğinde döngüye izin verilmez.")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for competency_id in graph:
        visit(competency_id)
