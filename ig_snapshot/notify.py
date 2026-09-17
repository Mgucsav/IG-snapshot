"""Telegram bildirimleri: snapshot özeti, veri kalitesi uyarıları, token süresi alarmı."""
from __future__ import annotations

import html
import logging
from datetime import date, datetime

import requests

from . import config
from .api import GraphAPIError, GraphClient

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram sınırı 4096; güvenli pay


def enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def esc(value) -> str:
    return html.escape(str(value), quote=False)


def _post(method: str, payload: dict, token: str | None = None) -> dict:
    url = API.format(token=token or config.TELEGRAM_BOT_TOKEN, method=method)
    resp = requests.post(url, json=payload, timeout=20)
    data = resp.json() if resp.content else {}
    if not resp.ok or not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description') or resp.text[:200]}")
    return data.get("result", {})


def send(text: str, chat_id: str | None = None) -> bool:
    """Metni (gerekirse satır sınırlarından bölerek) gönderir. Başarısızlık snapshot'ı durdurmaz."""
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if not (config.TELEGRAM_BOT_TOKEN and chat_id):
        log.info("Telegram ayarlı değil, bildirim atlandı")
        return False
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > MAX_LEN and cur:
            chunks.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    if cur:
        chunks.append(cur)
    try:
        for chunk in chunks:
            _post("sendMessage", {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                                  "disable_web_page_preview": True})
        return True
    except (requests.RequestException, RuntimeError) as exc:
        log.error("Telegram gönderilemedi: %s", exc)
        return False


def discover_chats(token: str) -> list[tuple[str, str]]:
    """Bota yazan sohbetleri (getUpdates) listeler: [(chat_id, açıklama)]."""
    result = _post("getUpdates", {"timeout": 0}, token=token)
    seen: dict[str, str] = {}
    for upd in result if isinstance(result, list) else []:
        msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member", {}) or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            title = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) \
                or chat.get("username") or chat.get("type")
            seen[str(chat["id"])] = f"{title} ({chat.get('type')})"
    return list(seen.items())


# --- token durumu -----------------------------------------------------------

def token_status(client: GraphClient) -> dict:
    """{'valid': bool, 'days': int|None, 'expires': 'dd.mm.yyyy'|None, 'error': str|None}"""
    try:
        info = client.debug_token()
    except GraphAPIError as exc:
        return {"valid": False, "days": None, "expires": None, "error": str(exc)}
    exp = info.get("expires_at") or 0
    if exp:
        exp_dt = datetime.fromtimestamp(exp)
        days = (exp_dt - datetime.now()).days
        return {"valid": bool(info.get("is_valid")), "days": days, "expires": f"{exp_dt:%d.%m.%Y}", "error": None}
    return {"valid": bool(info.get("is_valid")), "days": None, "expires": None, "error": None}


def token_warning(status: dict) -> str | None:
    if status.get("error") or not status.get("valid"):
        return ("🚨 <b>Instagram tokenı geçersiz / süresi dolmuş.</b> Snapshot alınamıyor.\n"
                "Graph API Explorer → yeni token → Access Token Debugger → Extend → .env IG_ACCESS_TOKEN")
    days = status.get("days")
    if days is not None and days <= config.TOKEN_WARN_DAYS:
        return (f"⚠️ <b>Instagram tokenı {days} gün sonra bitiyor</b> ({status['expires']}). "
                f"Yenile: Graph API Explorer → token → Debugger'da <i>Extend Access Token</i> → .env")
    return None


# --- snapshot özeti -----------------------------------------------------------

def _fmt(n) -> str:
    return "–" if n is None else f"{n:,}".replace(",", ".")


def _delta(n) -> str:
    if n is None:
        return ""
    return f" ({'+' if n > 0 else ''}{_fmt(n)})"


def build_snapshot_message(result: dict, token: dict, elapsed_sec: float, waited_sec: int = 0,
                           highlights: list | None = None) -> str:
    ok, failed, stats = result["ok"], result["failed"], result.get("stats", {})
    total = len(ok) + len(failed)
    day: date = result["date"]
    icon = "✅" if not failed else ("⚠️" if ok else "🚨")
    saved_total = sum(s.get("saved", 0) for s in stats.values())
    lines = [f"📸 <b>IG Snapshot — {day:%d.%m.%Y} {datetime.now():%H:%M}</b>",
             f"{icon} {len(ok)}/{total} hesap · {_fmt(saved_total)} gönderi · {elapsed_sec / 60:.0f} dk"
             + (f" (limit beklemesi {waited_sec // 60} dk)" if waited_sec else ""), ""]

    warnings: list[str] = []
    for u in ok:
        s = stats.get(u, {})
        lines.append(
            f"<b>@{esc(u)}</b>  {_fmt(s.get('followers'))}{_delta(s.get('followers_delta'))}"
            f" · bugün {s.get('posts_today', 0)} içerik (R{s.get('reels_today', 0)}/F{s.get('feed_today', 0)})"
            f" · {s.get('saved', 0)} gönderi"
        )
        if s.get("saved", 0) == 0:
            warnings.append(f"@{esc(u)}: hiç gönderi gelmedi")
        if s.get("followers") is None:
            warnings.append(f"@{esc(u)}: takipçi sayısı boş")
        if s.get("reels_views_missing"):
            warnings.append(f"@{esc(u)}: Reels izlenmeleri boş geldi")
        if s.get("likes_missing"):
            warnings.append(f"@{esc(u)}: beğeni sayıları boş (hesap gizliyor olabilir)")
        fd = s.get("followers_delta")
        fol = s.get("followers") or 0
        if fd is not None and fol and abs(fd) > fol * 0.05:
            warnings.append(f"@{esc(u)}: takipçi bir günde {'+' if fd > 0 else ''}{_fmt(fd)} değişti (%{abs(fd) / fol * 100:.1f})")
    for u, err in failed.items():
        lines.append(f"❌ <b>@{esc(u)}</b> — {esc(err)[:160]}")

    if highlights:
        lines += ["", "<b>🔥 Bugünün öne çıkanları</b>"]
        for p in highlights:
            metric = f"{_fmt(p.last_views)} izlenme" if p.last_views else f"{_fmt(p.last_likes)} beğeni"
            cap = esc(p.caption[:50]) + ("…" if len(p.caption) > 50 else "")
            lines.append(f"@{esc(p.username)} · {esc(p.content_type)} · <b>{metric}</b> · "
                         f"<a href=\"{esc(p.permalink)}\">{cap or 'gönderi'}</a>")

    if warnings:
        lines += ["", "<b>Veri kontrolü</b>"] + [f"⚠️ {w}" for w in warnings]
    else:
        lines += ["", "✅ Veri kontrolü: sorun yok"]

    lines.append("")
    tw = token_warning(token)
    if tw:
        lines.append(tw)
    elif token.get("days") is not None:
        lines.append(f"🔑 Token: {token['days']} gün kaldı ({token['expires']})")
    lines.append(f"📁 reports/{day:%Y-%m} (rapor + gunluk) · reports/genel · reports/kanallar güncellendi")
    return "\n".join(lines)


def build_crash_message(exc: BaseException) -> str:
    return (f"🚨 <b>IG Snapshot çöktü</b> — {datetime.now():%d.%m.%Y %H:%M}\n"
            f"<code>{esc(type(exc).__name__)}: {esc(str(exc))[:500]}</code>\n"
            f"Ayrıntı: logs/ig_snapshot.log")


def _pct(x) -> str:
    return "" if x is None else f" · {'+' if x > 0 else ''}%{x:.1f}".replace(".", ",")


def build_month_end_message(month: str, month_label: str, reports: list, posts: list, track_days: int) -> str:
    """Ay kapanışı: hesap özetleri + ayın en iyi gönderileri (reports: AccountReport, posts: PostPerf)."""
    lines = [f"🏆 <b>{esc(month_label)} kapanışı</b>", ""]
    for r in reports:
        lines.append(
            f"<b>@{esc(r.username)}</b>  {_fmt(r.followers_end)}{_delta(r.followers_delta)}{_pct(r.followers_pct)}"
            f" · {r.total_posts} içerik (R{r.group_count('Reels')}/F{r.group_count('Feed')})"
            f" · izlenme {_fmt(r.gain.views)} · beğeni {_fmt(r.gain.likes)}"
        )

    def line(i, p, metric):
        cap = esc(p.caption[:45]) + ("…" if len(p.caption) > 45 else "")
        return (f"{i}. @{esc(p.username)} · {esc(p.content_type)} · <b>{metric}</b> · "
                f"<a href=\"{esc(p.permalink)}\">{cap or 'gönderi'}</a>")

    top_views = sorted([p for p in posts if p.last_views], key=lambda p: -p.last_views)[:5]
    if top_views:
        lines += ["", "🥇 <b>Ayın en çok izlenen 5 gönderisi</b>"]
        lines += [line(i, p, f"{_fmt(p.last_views)} izlenme · {_fmt(p.last_likes)} beğeni")
                  for i, p in enumerate(top_views, 1)]
    top_feed = sorted([p for p in posts if p.group == "Feed" and p.last_likes], key=lambda p: -p.last_likes)[:3]
    if top_feed:
        lines += ["", "❤️ <b>Feed'de en çok beğenilen 3</b>"]
        lines += [line(i, p, f"{_fmt(p.last_likes)} beğeni · {_fmt(p.last_comments)} yorum")
                  for i, p in enumerate(top_feed, 1)]
    best_by_account = {}
    for p in sorted(posts, key=lambda p: (p.last_views or 0, p.last_likes or 0), reverse=True):
        best_by_account.setdefault(p.username, p)
    if len(best_by_account) > 1:
        lines += ["", "⭐ <b>Hesap bazında en iyi</b>"]
        for u, p in best_by_account.items():
            metric = f"{_fmt(p.last_views)} izlenme" if p.last_views else f"{_fmt(p.last_likes)} beğeni"
            lines.append(f"@{esc(u)} · {esc(p.content_type)} · <b>{metric}</b> · "
                         f"<a href=\"{esc(p.permalink)}\">{esc(p.caption[:40]) or 'gönderi'}</a>")
    lines += ["", f"📎 reports/{month}/rapor-{month}.xlsx · gunluk-{month}.xlsx",
              f"ℹ️ {esc(month_label)} gönderileri {track_days} gün boyunca ölçülmeye devam eder; "
              f"sürüklenme izlenmeleri gunluk dosyasındaki \"güncel\" ve \"ay sonrası sürüklenme\" sütunlarında birikir."]
    return "\n".join(lines)
