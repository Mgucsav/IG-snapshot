"""Instagram Graph API (Business Discovery) istemcisi."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://graph.facebook.com"

PROFILE_FIELDS = ["id", "username", "name", "followers_count", "follows_count", "media_count"]
MEDIA_FIELDS = [
    "id", "media_type", "media_product_type", "like_count", "comments_count",
    "view_count", "caption", "timestamp", "permalink",
]

# Meta'nın rate limit hata kodları (4: app, 17: user, 32: page, 613: custom, 8000x: iş kullanımı/BUC)
RATE_LIMIT_CODES = {4, 17, 32, 613, 80001, 80002, 80003, 80004, 80005, 80006, 80008}
USAGE_HEADERS = ("x-app-usage", "x-business-use-case-usage", "x-page-usage")
TRANSIENT_CODES = {1, 2}


class GraphAPIError(Exception):
    def __init__(self, message: str, code: int | None = None, subcode: int | None = None,
                 http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.http_status = http_status

    def __str__(self) -> str:
        return f"[code={self.code} sub={self.subcode} http={self.http_status}] {self.args[0]}"


class RateLimited(GraphAPIError):
    pass


class GraphClient:
    def __init__(self, token: str, version: str = "v25.0", timeout: int = 30):
        self.token = token
        self.version = version
        self.timeout = timeout
        self.session = requests.Session()
        self.calls = 0                       # yapılan HTTP istek sayısı (yeniden denemeler dahil)
        self.last_usage: dict[str, Any] = {}  # son yanıttaki kullanım başlıkları

    def _url(self, path: str) -> str:
        return f"{BASE_URL}/{self.version}/{path.lstrip('/')}"

    def get(self, path: str, params: dict[str, Any] | None = None, retries: int = 3) -> dict:
        params = dict(params or {})
        params["access_token"] = self.token
        delay = 30
        for attempt in range(retries + 1):
            try:
                resp = self.session.get(self._url(path), params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt < retries:
                    log.warning("Ağ hatası (%s), %ss sonra tekrar", exc, delay)
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise GraphAPIError(f"Ağ hatası: {exc}") from exc

            self.calls += 1
            self._capture_usage(resp.headers)
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            err = payload.get("error") if isinstance(payload, dict) else None
            if resp.ok and not err:
                return payload

            code = (err or {}).get("code")
            subcode = (err or {}).get("error_subcode")
            message = (err or {}).get("message") or resp.text[:300] or f"HTTP {resp.status_code}"
            is_rate = code in RATE_LIMIT_CODES or resp.status_code == 429
            is_transient = resp.status_code >= 500 or code in TRANSIENT_CODES
            if (is_rate or is_transient) and attempt < retries:
                log.warning("%s: %s — %ss sonra tekrar (%d/%d)",
                            "Rate limit" if is_rate else "Geçici hata", message, delay, attempt + 1, retries)
                time.sleep(delay)
                delay *= 2
                continue
            exc_cls = RateLimited if is_rate else GraphAPIError
            raise exc_cls(message, code=code, subcode=subcode, http_status=resp.status_code)
        raise GraphAPIError("beklenmeyen durum")  # pragma: no cover

    # --- kullanım / limit takibi ---------------------------------------------

    def _capture_usage(self, headers) -> None:
        usage = {}
        for name in USAGE_HEADERS:
            raw = headers.get(name)
            if raw:
                try:
                    usage[name] = json.loads(raw)
                except ValueError:
                    usage[name] = raw
        if usage:
            self.last_usage = usage

    def usage_summary(self) -> dict[str, float | None]:
        """Son yanıttaki limit yüzdeleri: app.* (1 saatlik pencere), buc.* (24 saatlik pencere)."""
        out: dict[str, float | None] = {"app.call_count": None, "app.total_time": None, "app.total_cputime": None,
                                        "buc.call_count": None, "buc.total_time": None, "buc.total_cputime": None,
                                        "buc.regain_min": None, "buc.type": None}
        app = self.last_usage.get("x-app-usage")
        if isinstance(app, dict):
            for k in ("call_count", "total_time", "total_cputime"):
                out[f"app.{k}"] = app.get(k)
        buc = self.last_usage.get("x-business-use-case-usage")
        if isinstance(buc, dict):
            entries = [e for v in buc.values() if isinstance(v, list) for e in v if isinstance(e, dict)]
            for k in ("call_count", "total_time", "total_cputime"):
                vals = [e.get(k) for e in entries if e.get(k) is not None]
                out[f"buc.{k}"] = max(vals) if vals else None
            regain = [e.get("estimated_time_to_regain_access") for e in entries if e.get("estimated_time_to_regain_access")]
            out["buc.regain_min"] = max(regain) if regain else 0
            types = {e.get("type") for e in entries if e.get("type")}
            out["buc.type"] = ",".join(sorted(types)) if types else None
        return out

    # --- token / hesap ----------------------------------------------------

    def debug_token(self) -> dict:
        """Tokenın geçerliliği, süresi ve izinleri."""
        return self.get("debug_token", {"input_token": self.token}).get("data", {})

    def list_ig_accounts(self) -> list[dict]:
        """Tokenın erişebildiği Facebook sayfalarına bağlı Instagram profesyonel hesapları.

        User token → /me/accounts; Page token → /me sayfanın kendisidir.
        """
        fields = "name,instagram_business_account{id,username}"
        try:
            data = self.get("me/accounts", {"fields": fields, "limit": 100})
            pages = data.get("data", [])
        except GraphAPIError as exc:
            if exc.code == 100 and "accounts" in exc.args[0]:
                pages = [self.get("me", {"fields": fields})]
            else:
                raise
        out = []
        for page in pages:
            ig = page.get("instagram_business_account")
            if ig:
                out.append({"page": page.get("name"), "ig_id": ig["id"], "ig_username": ig.get("username")})
        return out

    def exchange_long_lived(self, app_id: str, app_secret: str) -> dict:
        """Mevcut (hâlâ geçerli) uzun ömürlü tokenı yeni bir 60 günlük tokenla değiştirir."""
        return self.get("oauth/access_token", {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": self.token,
        })

    # --- business discovery ----------------------------------------------

    def business_discovery(self, ig_user_id: str, username: str,
                           page_size: int = 50, max_media: int = 100,
                           stop_before: str | None = None,
                           completed_ids: set[str] | None = None) -> dict:
        """Rakip hesabın profil bilgisi + son gönderileri.

        Sayfalama şu durumlarda durur: sayfadaki tüm gönderiler stop_before ('YYYY-MM-DD' UTC) tarihinden
        eskiyse, sayfada takibi sürecek (tamamlanmamış ve ufuk içinde) tek gönderi kalmadıysa ya da
        max_media sınırına ulaşıldıysa.
        """
        completed_ids = completed_ids or set()
        media_fields = list(MEDIA_FIELDS)
        profile: dict | None = None
        media: list[dict] = []
        after: str | None = None

        while True:
            media_spec = f"media.limit({page_size})"
            if after:
                media_spec += f".after({after})"
            media_spec += "{" + ",".join(media_fields) + "}"
            fields = (f"business_discovery.username({username})"
                      "{" + ",".join(PROFILE_FIELDS + [media_spec]) + "}")
            try:
                data = self.get(ig_user_id, {"fields": fields})
            except GraphAPIError as exc:
                if exc.code == 100 and "view_count" in exc.args[0] and "view_count" in media_fields:
                    log.warning("view_count alanı kabul edilmedi; alan çıkarılıp tekrar deneniyor")
                    media_fields.remove("view_count")
                    continue
                raise

            bd = data.get("business_discovery") or {}
            if profile is None:
                profile = {k: bd.get(k) for k in PROFILE_FIELDS}
            page = bd.get("media") or {}
            rows = page.get("data") or []
            media.extend(rows)
            paging = page.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            # İç içe kenarda Meta çoğu zaman 'next' vermez; tam sayfa geldiyse ve cursor varsa devam et
            if not rows or len(rows) < page_size or not after:
                break
            if len(media) >= max_media:
                if stop_before and (rows[-1].get("timestamp") or "") >= stop_before:
                    log.warning("@%s: %d gönderi sınırına ulaşıldı, %s öncesine inilemedi",
                                username, max_media, stop_before)
                break
            if stop_before and (rows[-1].get("timestamp") or "") < stop_before:
                break  # sayfanın en eski gönderisi izleme ufkunun dışında
            if completed_ids and not any(
                m.get("id") not in completed_ids and (not stop_before or (m.get("timestamp") or "") >= stop_before)
                for m in rows
            ):
                break  # bu sayfada hâlâ izlenen gönderi yok; daha eskilerde de olmaz

        return {"profile": profile or {}, "media": media[:max_media]}
