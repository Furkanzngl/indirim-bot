from curl_cffi import requests
import json

m = json.load(open("data/manifest.json", encoding="utf-8"))
src = next(iter(m["pages"]))
print("URL:", src)

r = requests.get(src, impersonate="chrome124", timeout=20)
print("Status:", r.status_code)
print("ETag:", r.headers.get("etag"))
print("Last-Modified:", r.headers.get("last-modified"))
print("Cache-Control:", r.headers.get("cache-control"))