import hashlib
import json
import logging
import os
from pathlib import Path

_log = logging.getLogger("indirim")


def sha16(data: bytes) -> str:
    """Byte dizisinden 16 karakterlik SHA-256 özeti üretir."""
    return hashlib.sha256(data).hexdigest()[:16]


def compact_json(data) -> bytes:
    """Boşluksuz, Türkçe karakterleri olduğu gibi tutan JSON byte dizisi."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def atomic_write(filepath, data: bytes):
    """Önce .tmp dosyasına yazar, fsync eder, sonra atomik olarak hedefin yerine koyar."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_manifest(path) -> dict:
    """
    Manifest'i okur.
    - Dosya yoksa: sessizce boş manifest döner (ilk çalıştırmada normal).
    - Bozuk JSON veya okunamayan dosya: uyarı loglanır, boş manifest döner.
    Boş manifest en kötü durumda görselleri yeniden indirtir, veri kaybettirmez.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("pages", {})
            return data
        _log.warning(f"manifest beklenen yapıda değil, sıfırlandı: {path}")
    except FileNotFoundError:
        pass
    except json.JSONDecodeError as e:
        _log.warning(f"manifest bozuk, sıfırlandı ({path}): {e}")
    except OSError as e:
        _log.warning(f"manifest okunamadı, sıfırlandı ({path}): {e}")
    return {"pages": {}}


def save_manifest(manifest: dict, path):
    """Manifest'i atomik olarak yazar. Hata olursa exception yukarı iletilir."""
    atomic_write(path, compact_json(manifest))