import logging
import random
import time


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# impersonate="chrome124" ile aynı sürüm olmalı.
DEFAULT_HEADERS = {
    "user-agent": DEFAULT_UA,
    "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


class BlockedError(Exception):
    """401/403/429 geldi: site engelledi. Çağıran bu çalıştırmayı durdurmalı."""


class HttpError(Exception):
    """Beklenmeyen HTTP durumu. Mesajda yanıt gövdesinin başı bulunur."""


class Fetcher:
    def __init__(self, session, logger=None, min_delay=1.0, max_delay=2.5,
                 timeout=20, server_retries=1, retry_wait=8.0):
        self.session = session
        self.log = logger or logging.getLogger("indirim")
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self.server_retries = server_retries
        self.retry_wait = retry_wait
        self._last = 0.0

    def _pace(self):
        """İki istek arasına rastgele bekleme koyar."""
        target = random.uniform(self.min_delay, self.max_delay)
        elapsed = time.monotonic() - self._last
        if elapsed < target:
            time.sleep(target - elapsed)
        self._last = time.monotonic()

    def _request(self, method, url, *, headers=None, timeout=None, **kwargs):
        """
        Tüm HTTP çağrılarının geçtiği tek yer.
        - 401/403/429 -> BlockedError
        - 5xx -> bir kez daha dener, sonra HttpError (gövde mesajda)
        - Diğer 4xx -> HttpError (gövde mesajda)
        """
        h = {**DEFAULT_HEADERS, **(headers or {})}
        r = None
        for attempt in range(self.server_retries + 1):
            self._pace()
            r = self.session.request(method, url, headers=h, timeout=timeout or self.timeout, **kwargs)

            if r.status_code in (401, 403, 429):
                raise BlockedError(f"{r.status_code} {url}")

            if r.status_code < 500:
                if r.status_code >= 400:
                    raise HttpError(f"{r.status_code} {url} -> {r.text[:300]!r}")
                return r

            self.log.warning(f"{r.status_code} {url} (deneme {attempt + 1}) -> {r.text[:300]!r}")
            if attempt < self.server_retries:
                time.sleep(self.retry_wait)

        raise HttpError(f"{r.status_code} {url} -> {r.text[:300]!r}")

    def get(self, url, *, headers=None, etag=None, last_modified=None, timeout=None):
        h = dict(headers or {})
        if etag:
            h["If-None-Match"] = etag
        if last_modified:
            h["If-Modified-Since"] = last_modified

        r = self._request("GET", url, headers=h, timeout=timeout)
        if r.status_code == 304:
            return None, {"etag": etag, "last_modified": last_modified}

        meta = {
            "etag": r.headers.get("etag"),
            "last_modified": r.headers.get("last-modified"),
        }
        return r.content, meta

    def json(self, url, *, headers=None):
        """JSON endpoint'i için. JSON olmayan yanıt gelirse anlaşılır hata verir."""
        r = self._request("GET", url, headers=headers)
        ctype = r.headers.get("content-type", "")
        if "json" not in ctype:
            raise ValueError(f"JSON değil ({ctype}): {url}")
        return r.json()

    def post_json(self, url, *, json_body, headers=None):
        """POST ile JSON gönderip JSON alır."""
        r = self._request("POST", url, headers=headers, json=json_body)
        return r.json()