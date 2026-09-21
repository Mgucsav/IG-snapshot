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


def mask(text: str) -> str:
    """Hata metinlerinde bot tokenını gizler (requests istisnaları URL'yi içerir)."""
    tok = config.TELEGRAM_BOT_TOKEN
    return text.replace(tok, "***") if tok else text


def esc(value) -> str:
    return html.escape(str(value), quote=False)


def _post(method: str, payload: dict, token: str | None = None, timeout: int = 20) -> dict:
    url = API.format(token=token or config.TELEGRAM_BOT_TOKEN, method=method)
    resp = requests.post(url, json=payload, timeout=timeout)
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
        log.error("Telegram gönderilemedi: %s", mask(str(exc)))
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


def _plus(n) -> str:
    return "–" if n is None else ("+" if n > 0 else "") + _fmt(n)


def short(n) -> str:
    """Kısa sayı: 657 · 6,1K · 71,1K · 296K · 1,03M · 38,5M"""
    if n is None:
        return "–"
    a = abs(n)
    if a < 1000:
        return f"{n:,}".replace(",", ".")
    if a < 100_000:
        v = f"{n / 1000:.1f}K"
    elif a < 1_000_000:
        v = f"{n / 1000:.0f}K"
    elif a < 10_000_000:
        v = f"{n / 1_000_000:.2f}M"
    else:
        v = f"{n / 1_000_000:.1f}M"
    return v.replace(".", ",")


def _sgn(n) -> str:
    return "–" if n is None else ("+" if n > 0 else "") + short(n)


def _post_line(i, p, metric: str) -> str:
    cap = p.caption[:30].strip() + ("…" if len(p.caption) > 30 else "")
    return f"{i}. <b>{metric}</b> · @{esc(p.username)} · <a href=\"{esc(p.permalink)}\">{esc(cap) or 'gönderi'}</a>"


def build_snapshot_messages(result: dict, token: dict, elapsed_sec: float, waited_sec: int = 0,
                            highlights: dict | None = None, gains: dict | None = None) -> list[str]:
    """İki mesaj: (1) hesap blokları + kontrol + token, (2) günün top 5 / en kötü 5 listeleri."""
    ok, failed, stats = result["ok"], result["failed"], result.get("stats", {})
    gains = gains or {}
    highlights = highlights or {}
    total = len(ok) + len(failed)
    day: date = result["date"]
    icon = "✅" if not failed else ("⚠️" if ok else "🚨")

    # --- Mesaj 1: hesaplar ---------------------------------------------------
    lines = [f"📸 <b>{day:%d.%m.%Y} — Günlük özet</b>",
             f"{icon} {len(ok)}/{total} hesap alındı" + (f" · limit beklemesi {waited_sec // 60} dk" if waited_sec else "")]
    warnings: list[str] = []
    for u in ok:
        s = stats.get(u, {})
        g = gains.get(u)
        lines += ["", f"▬ <b>@{esc(u)}</b>",
                  f"👥 {_fmt(s.get('followers'))}{_delta(s.get('followers_delta'))}",
                  f"📝 {s.get('posts_today', 0)} içerik · {s.get('reels_today', 0)} Reels · {s.get('feed_today', 0)} Feed"]
        if g is not None:
            gr, gf = g["by_group"]["Reels"], g["by_group"]["Feed"]
            lines += [f"▶️ {_sgn(gr.views_or_none)} izlenme  (bugünkü {short(g['today_views'])} · arşiv {short(g['archive_views'])})",
                      f"❤️ {_sgn(g['total'].likes)} beğeni  (Reels {short(gr.likes)} · Feed {short(gf.likes)})",
                      f"💬 {_sgn(g['total'].comments)} yorum"]
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
        lines += ["", f"❌ <b>@{esc(u)}</b> — {esc(err)[:160]}"]
        warnings.append(f"@{esc(u)} alınamadı")

    lines.append("")
    if warnings:
        lines += ["<b>⚠️ Veri kontrolü</b>"] + [f"• {w}" for w in warnings]
    else:
        lines.append("✅ Veri kontrolü: sorun yok")
    tw = token_warning(token)
    if tw:
        lines.append(tw)
    elif token.get("days") is not None:
        lines.append(f"🔑 Token {token['days']} gün · 📁 raporlar güncellendi")
    else:
        lines.append("📁 raporlar güncellendi")
    msg1 = "\n".join(lines)

    # --- Mesaj 2: günün en iyileri / en kötüleri --------------------------------
    blocks = []
    reels, reels_worst = highlights.get("reels") or [], highlights.get("reels_worst") or []
    feed, feed_worst = highlights.get("feed") or [], highlights.get("feed_worst") or []
    if reels:
        blocks.append(["<b>🎬 Günün Reels top 5</b> (izlenme)"] +
                      [_post_line(i, p, f"{short(p.last_views)} · {short(p.last_likes)}❤️") for i, p in enumerate(reels, 1)])
    if reels_worst:
        blocks.append(["<b>🎬 En kötü Reels 5</b> (izlenme)"] +
                      [_post_line(i, p, f"{short(p.last_views)} · {short(p.last_likes)}❤️") for i, p in enumerate(reels_worst, 1)])
    if feed:
        blocks.append(["<b>🖼 Günün Feed top 5</b> (beğeni)"] +
                      [_post_line(i, p, f"{short(p.last_likes)}❤️ · {short(p.last_comments)}💬") for i, p in enumerate(feed, 1)])
    if feed_worst:
        blocks.append(["<b>🖼 En kötü Feed 5</b> (beğeni)"] +
                      [_post_line(i, p, f"{short(p.last_likes)}❤️ · {short(p.last_comments)}💬") for i, p in enumerate(feed_worst, 1)])
    if not blocks:
        return [msg1]
    lines2 = [f"🏁 <b>{day:%d.%m.%Y} — Günün içerikleri</b> (bugün paylaşılanlar)"]
    for b in blocks:
        lines2 += [""] + b
    return [msg1, "\n".join(lines2)]


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
