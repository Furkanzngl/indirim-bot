"""
Migros katalogları: PDF'leri bölümlere ayırır, sayfa görsellerini WebP'ye çevirir.

Her katalog tanımı kendi PDF'i, bölüm başlangıçları ve tarih aralığıyla çalışır.

Çıktılar (data/ altında):
  data/catalog/<anahtar>/catalog.json              üretim durumu (kaynak PDF bilgisi)
  data/catalog/<anahtar>/section_N/cover_thumb.webp
  data/catalog/<anahtar>/section_N/page_NNN_full.webp
  data/campaigns/migros/<anahtar>_bolum_N.json     bölüm detay dosyası (kampanya formatı)

Tek başına: python migros_catalog.py
run.py tarafından: build_catalog(logger, fallback=...)
"""
import hashlib
import json
import logging
import sys
from datetime import date
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from core import store

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
CATALOG_ROOT = DATA / "catalog"
DETAIL_DIR = DATA / "campaigns" / "migros"
MARKET = "migros"

RENDER_SCALE = 1.5           # ~108 DPI
COVER_W, COVER_Q = 360, 70   # kapak thumb'ı (liste)
FULL_W, FULL_Q = 1000, 75    # sayfa full (detay)

# Katalog tanımları. Her katalog için yeni bir giriş eklemek yeterli.
# section_starts: 0 tabanlı sayfa indeksleri. 1 -> 0, 112 -> 111.
# start_date / end_date: kapaktaki tarih aralığı (YYYY-MM-DD).
CATALOGS = [
    {
        "key": "migroskop",
        "pdf": "katalog.pdf",
        "title": "Migroskop",
        "section_starts": [0, 108],
        "start_date": "2026-10-08",
        "end_date": "2026-10-21",
    },
    {
        "key": "5m",
        "pdf": "5mkatalog.pdf",
        "title": "5M",
        "section_starts": [0, 111],
        "start_date": "2026-10-08",   # kapaktaki tarih aralığını buraya yaz
        "end_date": "2026-10-21",
    },
]

log = logging.getLogger("indirim")


# ---------------------------------------------------------------- yardımcılar

def _resize_w(img: Image.Image, width: int) -> Image.Image:
    if img.width <= width:
        return img
    h = round(img.height * width / img.width)
    return img.resize((width, h), Image.LANCZOS)


def _save_webp(img: Image.Image, width: int, quality: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _resize_w(img, width).save(path, "WEBP", quality=quality, method=6)


def _sections(cat: dict, total: int) -> list[tuple[int, int, int]]:
    """(bölüm_no, başlangıç, bitiş) listesi. Başlangıç dahil, bitiş hariç, 0 tabanlı."""
    starts = cat["section_starts"]
    out = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else total
        out.append((k + 1, s, e))
    return out


def _rel_full(cat: dict, n: int, idx: int) -> str:
    return f"catalog/{cat['key']}/section_{n}/page_{idx + 1:03d}_full.webp"


def _rel_cover(cat: dict, n: int) -> str:
    return f"catalog/{cat['key']}/section_{n}/cover_thumb.webp"


def _is_current(cat: dict, meta: dict) -> bool:
    """Aynı PDF ve tüm dosyalar yerindeyse render atlanır."""
    path = CATALOG_ROOT / cat["key"] / "catalog.json"
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False

    if (prev.get("source"), prev.get("size"), prev.get("total")) != (
        meta["source"], meta["size"], meta["total"]
    ):
        return False

    for n, s, e in _sections(cat, meta["total"]):
        if not (DATA / _rel_cover(cat, n)).exists():
            return False
        for idx in range(s, e):
            if not (DATA / _rel_full(cat, n, idx)).exists():
                return False
    return True


def _render_all(cat: dict, pdf_path: Path, total: int, logger) -> None:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for n, s, e in _sections(cat, total):
            logger.info(f"[catalog:{cat['key']}] Bölüm {n}: sayfa {s + 1}-{e} render ediliyor")
            for idx in range(s, e):
                page = pdf[idx]
                bitmap = page.render(scale=RENDER_SCALE)
                img = bitmap.to_pil().convert("RGB")
                _save_webp(img, FULL_W, FULL_Q, DATA / _rel_full(cat, n, idx))
                if idx == s:
                    _save_webp(img, COVER_W, COVER_Q, DATA / _rel_cover(cat, n))
                bitmap.close()
                page.close()
    finally:
        pdf.close()


def _write_detail(cat: dict, n: int, s: int, e: int) -> dict:
    """Bölümün detay dosyasını yazar, index kaydını döner."""
    pages = []
    for idx in range(s, e):
        full_rel = _rel_full(cat, n, idx)
        thumb_rel = _rel_cover(cat, n) if idx == s else full_rel
        pages.append({
            "index": idx - s + 1,
            "hash": hashlib.sha256(full_rel.encode("utf-8")).hexdigest()[:16],
            "thumb": thumb_rel,
            "full": full_rel,
        })

    cid = f"{cat['key']}_bolum_{n}"
    detail_rel = f"campaigns/{MARKET}/{cid}.json"
    title = f"{cat['title']} Bölüm {n}"
    detail = {
        "market": MARKET,
        "id": cid,
        "title": title,
        "start_date": cat["start_date"],
        "end_date": cat["end_date"],
        "pages": pages,
    }
    store.atomic_write(DATA / detail_rel, store.compact_json(detail))

    return {
        "market": MARKET,
        "id": cid,
        "title": title,
        "start_date": cat["start_date"],
        "end_date": cat["end_date"],
        "page_count": len(pages),
        "cover_thumb": _rel_cover(cat, n),
        "detail_path": detail_rel,
    }


def _valid_fallback(entries: list) -> list:
    """Detay dosyası hâlâ duran ve süresi geçmemiş kayıtları alır."""
    today = date.today().isoformat()
    return [
        e for e in entries
        if (not e.get("end_date") or e["end_date"] >= today)
        and (DATA / e["detail_path"]).exists()
    ]


def _build_one(cat: dict, logger) -> list[dict]:
    pdf_path = BASE / cat["pdf"]
    if not pdf_path.exists():
        logger.info(f"[catalog:{cat['key']}] {cat['pdf']} yok, önceki kayıtlar korunuyor")
        return []

    pdf = pdfium.PdfDocument(str(pdf_path))
    total = len(pdf)
    pdf.close()

    meta = {
        "source": pdf_path.name,
        "size": pdf_path.stat().st_size,
        "total": total,
        "updated": date.today().isoformat(),
    }
    out_dir = CATALOG_ROOT / cat["key"]
    out_dir.mkdir(parents=True, exist_ok=True)

    if _is_current(cat, meta):
        logger.info(f"[catalog:{cat['key']}] kaynak değişmedi, render atlandı")
    else:
        _render_all(cat, pdf_path, total, logger)
        store.atomic_write(out_dir / "catalog.json", store.compact_json(meta))
        logger.info(f"[catalog:{cat['key']}] {total} sayfa üretildi ({pdf_path.name})")

    return [_write_detail(cat, n, s, e) for n, s, e in _sections(cat, total)]


# ------------------------------------------------------------------ ana giriş

def build_catalog(logger=None, fallback=None) -> list[dict]:
    """
    Tüm katalog tanımlarını işler, index.json'a girecek kayıtları döner.
    PDF'i olmayan katalog için önceki (fallback) kayıtlar korunur.
    Bir katalogun hatası diğerlerini durdurmaz.
    """
    logger = logger or log
    fallback = fallback or []
    entries = []

    for cat in CATALOGS:
        try:
            built = _build_one(cat, logger)
        except Exception:
            logger.exception(f"[catalog:{cat['key']}] üretim hatası")
            built = []

        if built:
            entries.extend(built)
        else:
            # Üretilemediyse bu kataloğun önceki kayıtlarını koru
            prefix = f"{cat['key']}_bolum_"
            old = [e for e in fallback if e["id"].startswith(prefix)]
            entries.extend(_valid_fallback(old))

    return entries


# -------------------------------------------------------- tek başına çalıştırma

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    for e in build_catalog(log):
        print(f"{e['title']}: {e['page_count']} sayfa -> {e['detail_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())