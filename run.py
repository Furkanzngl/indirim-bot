"""
İndirim botu: kampanyaları çeker, görselleri WebP'ye çevirir, yayın verisini yazar.

Çalıştırma:  python run.py
Çıkış kodu:  0 = hepsi tamam, 1 = en az bir market başarısız
"""
import json
import os
import re
import time
import traceback
from datetime import date, datetime
from pathlib import Path

from curl_cffi import requests

from core import imgopt, log, store
from core.fetch import BlockedError, Fetcher
from markets import bim, sok, a101, migros

# Görev Zamanlayıcı çalışma dizinini garanti etmez, o yüzden yolları scriptin yerinden hesapla.
BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
LOG_PATH = BASE / "logs" / "run.log"
LOCK_PATH = BASE / "logs" / "run.lock"

# fetch.py içindeki DEFAULT_UA ve sec-ch-ua ile aynı sürüm olmalı.
IMPERSONATE = "chrome124"

MARKETS = [bim, sok, a101]


# ---------------------------------------------------------------- yardımcılar

def safe_id(value) -> str:
    """Kampanya ID'sini dosya adı için güvenli hale getirir."""
    s = re.sub(r"[^A-Za-z0-9_-]", "_", str(value))
    if not s:
        raise ValueError("Boş kampanya ID'si")
    return s


def load_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def images_exist(content_hash: str) -> bool:
    img = DATA / "img"
    return (img / f"{content_hash}_thumb.webp").exists() and (img / f"{content_hash}_full.webp").exists()


def summary_of(detail: dict, detail_rel: str) -> dict:
    return {
        "market": detail["market"],
        "id": detail["id"],
        "title": detail["title"],
        "start_date": detail["start_date"],
        "end_date": detail["end_date"],
        "page_count": len(detail["pages"]),
        "cover_thumb": detail["pages"][0]["thumb"],
        "detail_path": detail_rel,
    }


# ---------------------------------------------------------- sayfa / kampanya

FULL_CHECK_DAYS = 2  # bir sayfa bu kadar gün sonra yeniden indirilip karşılaştırılır


def sync_page(fetcher: Fetcher, manifest: dict, page: dict, logger) -> str:
    """
    Sayfayı senkronize eder ve içerik hash'ini döner.
    - URL daha önce işlendiyse, dosyalar diskteyse ve son kontrol FULL_CHECK_DAYS'ten yeniyse istek atmaz.
    - Aksi halde görseli indirir, hash'ini hesaplar, değişmişse yeni WebP'leri yazar.
    A101 CDN'i ETag/Last-Modified göndermediği için koşullu istek kullanılmaz.
    """
    src = page["src"]
    prev = manifest["pages"].get(src, {})
    today = date.today()

    if prev.get("hash") and images_exist(prev["hash"]) and prev.get("checked"):
        age = (today - date.fromisoformat(prev["checked"])).days
        if age < FULL_CHECK_DAYS:
            return prev["hash"]

    # ŞOK gibi PDF'ten belleğe çıkarılan sayfalar için:
    if "raw_bytes" in page:
        raw = page["raw_bytes"]
    else:
        raw, _ = fetcher.get(src)

    content_hash = store.sha16(raw)

    if not images_exist(content_hash):
        variants = imgopt.encode_variants(raw)
        store.atomic_write(DATA / "img" / f"{content_hash}_thumb.webp", variants["thumb"])
        store.atomic_write(DATA / "img" / f"{content_hash}_full.webp", variants["full"])
        logger.info(f"yeni görsel: {content_hash} (sayfa {page['index']})")

    manifest["pages"][src] = {"hash": content_hash, "checked": today.isoformat()}
    return content_hash


def process_campaign(mod, fetcher: Fetcher, manifest: dict, c: dict, logger) -> dict:
    if not c.get("pages"):
        raise ValueError("Kampanyada sayfa yok")

    cid = safe_id(c["id"])
    detail_rel = f"campaigns/{mod.NAME}/{cid}.json"
    detail_path = DATA / detail_rel

    pages = []
    for p in c["pages"]:
        h = sync_page(fetcher, manifest, p, logger)
        pages.append({
            "index": p["index"],
            "hash": h,
            "thumb": f"img/{h}_thumb.webp",
            "full": f"img/{h}_full.webp",
        })

    detail = {
        "market": mod.NAME,
        "id": cid,
        "title": c["title"],
        "start_date": c["start_date"],
        "end_date": c["end_date"],
        "pages": pages,
    }

    # İçerik aynıysa dosyayı yeniden yazma: git'te gereksiz değişiklik oluşmaz.
    if load_json(detail_path) != detail:
        store.atomic_write(detail_path, store.compact_json(detail))

    return summary_of(detail, detail_rel)


def process_market(mod, fetcher: Fetcher, manifest: dict, today: str, logger) -> dict:
    campaigns = mod.fetch_campaigns(fetcher)
    
    # Tarih filtresini kaldır: Sitede yayında olan tüm kampanyaları aktif kabul et
    active = campaigns
    logger.info(f"[{mod.NAME}] {len(campaigns)} kampanya bulundu, hepsi işleniyor")

    results = {}
    for c in active:
        try:
            s = process_campaign(mod, fetcher, manifest, c, logger)
            results[s["id"]] = s
        except BlockedError:
            raise
        except Exception:
            logger.error(f"[{mod.NAME}] kampanya {c.get('id')} hatası:\n{traceback.format_exc()}")
    return results


# ------------------------------------------------------------ birleştirme

def merge(old_campaigns: list, fresh_by_market: dict, today: str) -> list:
    """
    Kural:
    - Taze gelen kampanya her zaman yeni halini alır.
    - Taze gelmeyen (market hatalı, engel, veya kampanya hata verdi) eski kayıt,
      bitiş tarihi geçmediyse ve detay dosyası hâlâ diskteyse korunur.
    """
    out = []
    for mod in MARKETS:
        fresh = fresh_by_market.get(mod.NAME, {})
        out.extend(fresh.values())
        out.extend(
            s for s in old_campaigns
            if s["market"] == mod.NAME
            and s["id"] not in fresh
            and (not s.get("end_date") or s["end_date"] >= today)
            and (DATA / s["detail_path"]).exists()
        )
    out.sort(key=lambda s: (s["market"], s.get("start_date") or "", s["id"]))
    return out


def write_index(campaigns: list, logger):
    current = load_json(DATA / "index.json", {}) or {}
    if current.get("campaigns") == campaigns:
        logger.info("index.json değişmedi, yazılmadı")
        return
    payload = {
        "updated": datetime.now().isoformat(timespec="minutes"),
        "campaigns": campaigns,
    }
    store.atomic_write(DATA / "index.json", store.compact_json(payload))
    logger.info(f"index.json güncellendi ({len(campaigns)} kampanya)")


def cleanup_tmp(logger):
    """Yarım kalmış .tmp dosyalarını siler (atomic_write kesintiye uğramış olabilir)."""
    for path in DATA.rglob("*.tmp"):
        path.unlink(missing_ok=True)
        logger.info(f"yarım kalmış dosya silindi: {path.relative_to(DATA)}")


def prune(campaigns: list, logger):
    """
    Index'te olmayan detay dosyalarını ve görselleri siler.
    Güvenlik: bir detay okunamazsa silme işlemi tamamen atlanır.
    Döner: korunan görsel hash'leri kümesi, atlandıysa None.
    """
    keep_details = set()
    keep_hashes = set()
    for s in campaigns:
        detail_path = DATA / s["detail_path"]
        d = load_json(detail_path)
        if d is None:
            logger.warning(f"Detay okunamadı, temizlik atlandı: {s['detail_path']}")
            return None
        keep_details.add(detail_path)
        keep_hashes.update(p["hash"] for p in d["pages"])

    for path in (DATA / "campaigns").rglob("*.json"):
        if path not in keep_details:
            path.unlink()
            logger.info(f"eski detay silindi: {path.relative_to(DATA)}")

    for path in (DATA / "img").glob("*.webp"):
        if path.name.split("_")[0] not in keep_hashes:
            path.unlink()

    return keep_hashes


# ------------------------------------------------------------- kilit / ana

def acquire_lock(max_age_seconds: int = 3 * 3600) -> bool:
    """Önceki çalıştırma hâlâ sürüyorsa yenisini başlatma. Çöken kilit 3 saatte geçersiz olur."""
    if LOCK_PATH.exists() and time.time() - LOCK_PATH.stat().st_mtime < max_age_seconds:
        return False
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    return True


def run(logger) -> int:
    today = date.today().isoformat()
    logger.info("=" * 50)
    logger.info(f"Başladı: {datetime.now():%Y-%m-%d %H:%M} (impersonate={IMPERSONATE})")

    manifest = store.load_manifest(DATA / "manifest.json")
    manifest.setdefault("pages", {})
    old_campaigns = (load_json(DATA / "index.json", {}) or {}).get("campaigns", [])

    fetcher = Fetcher(requests.Session(impersonate=IMPERSONATE), logger)
    fresh_by_market = {}
    failed = []
    blocked = False

    for mod in MARKETS:
        try:
            fresh_by_market[mod.NAME] = process_market(mod, fetcher, manifest, today, logger)
            logger.info(f"[{mod.NAME}] tamam: {len(fresh_by_market[mod.NAME])} kampanya güncel")
        except BlockedError as e:
            failed.append(mod.NAME)
            blocked = True
            logger.error(f"[{mod.NAME}] ENGEL ({e}). Bu çalıştırma durduruluyor.")
            break  # engel gelince diğer marketlere de geçme
        except Exception:
            failed.append(mod.NAME)
            logger.error(f"[{mod.NAME}] beklenmeyen hata:\n{traceback.format_exc()}")

    # Migros: ürün listesi, index.json'a girmez, kendi paketlerini yazar
    if not blocked:
        try:
            migros.sync(fetcher, DATA, logger)
        except BlockedError as e:
            failed.append(migros.NAME)
            blocked = True
            logger.error(f"[migros] ENGEL ({e})")
        except Exception:
            failed.append(migros.NAME)
            logger.error(f"[migros] hata:\n{traceback.format_exc()}")
    else:
        logger.warning("[migros] engel nedeniyle bu çalıştırmada atlandı")

    campaigns = merge(old_campaigns, fresh_by_market, today)
    write_index(campaigns, logger)

    cleanup_tmp(logger)
    kept_hashes = prune(campaigns, logger)
    if kept_hashes is not None:
        manifest["pages"] = {
            src: meta for src, meta in manifest["pages"].items()
            if meta.get("hash") in kept_hashes
        }
    store.save_manifest(manifest, DATA / "manifest.json")

    logger.info(f"Bitti. Yayında: {len(campaigns)} kampanya. Hatalı market: {failed or 'yok'}")
    return 1 if failed else 0


def main() -> int:
    logger = log.setup(str(LOG_PATH))
    if not acquire_lock():
        logger.warning("Önceki çalıştırma hâlâ sürüyor, bu tetikleme atlandı.")
        return 0
    try:
        return run(logger)
    except Exception:
        logger.error(f"Beklenmeyen üst düzey hata:\n{traceback.format_exc()}")
        return 1
    finally:
        LOCK_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())