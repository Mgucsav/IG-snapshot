"""Haftalık Telegram raporu: Pazartesi→Pazar dönemi, Pazar gecesi 23:30 çekiminden sonra gönderilir.

Mesaj 1: hesap blokları (takipçi Δ, içerik, kazanılan izlenme/beğeni/yorum, önceki haftayla kıyas)
Mesaj 2: haftanın Reels top 5 / en kötü 5, Feed top 5 / en kötü 5 (o hafta yayınlananlar arasından)
Tekrar gönderimi `meta.weekly_sent = "YYYY-Www"` engeller; Pazar çekimi kaçarsa Pzt/Salı telafi eder.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import config, db
from .notify import _delta, _fmt, _post_line, _sgn, esc, short, token_warning
from .queries import period_summary, posts_in_range
from .report import fmt_day

log = logging.getLogger(__name__)
TOP_N = 5


def week_bounds(any_day: date) -> tuple[date, date]:
    """any_day'in içinde bulunduğu Pazartesi→Pazar."""
    start = any_day - timedelta(days=any_day.weekday())
    return start, start + timedelta(days=6)


def week_key(start: date) -> str:
    y, w, _ = start.isocalendar()
    return f"{y}-W{w:02d}"


def _pct(cur: int | None, prev: int | None) -> str:
    if cur is None or not prev:
        return ""
    ch = (cur - prev) / prev * 100
    return f" ({'+' if ch > 0 else ''}{ch:.0f}%)"


def build_weekly_messages(conn, start: date, end: date, token: dict | None = None) -> list[str]:
    groups = config.load_account_groups()
    usernames = list(groups) or [a["username"] for a in db.list_accounts(conn)]
    prev_start, prev_end = start - timedelta(days=7), start - timedelta(days=1)
    _, wk, _ = start.isocalendar()

    lines = [f"📅 <b>Haftalık özet — {fmt_day(start.isoformat())} → {fmt_day(end.isoformat())}</b> ({wk}. hafta)"]
    all_posts = posts_in_range(conn, usernames, start, end, "all")
    any_data = False
    for u in usernames:
        s = period_summary(conn, u, start, end)
        if not s:
            lines += ["", f"▬ <b>@{esc(u)}</b>", "veri yok"]
            continue
        any_data = True
        p = period_summary(conn, u, prev_start, prev_end)
        if p and p.days == 0:
            p = None  # önceki hafta ölçüm yoksa kıyas gösterme
        mine = [x for x in all_posts if x.username == u]
        reels = [x for x in mine if x.group == "Reels" and x.last_views is not None]
        feed = [x for x in mine if x.group == "Feed" and x.last_likes is not None]
        cohort_views = sum(x.last_views for x in reels)
        cohort_likes = sum((x.last_likes or 0) for x in mine)
        archive_views = max(0, s.views - cohort_views)
        archive_likes = max(0, s.likes_reels + s.likes_feed - cohort_likes)
        total_likes = s.likes_reels + s.likes_feed
        lines += ["", f"▬ <b>@{esc(u)}</b>",
                  f"👥 {_fmt(s.followers_end)}{_delta(s.followers_delta)}"
                  + (f"  ↔ önceki hafta {_sgn(p.followers_delta)}" if p and p.followers_delta is not None else ""),
                  f"📝 {s.posts_reels + s.posts_feed} içerik · {s.posts_reels} Reels · {s.posts_feed} Feed"
                  + (f"  ↔ {p.posts_reels + p.posts_feed}" if p else ""),
                  f"▶️ {_sgn(s.views)} izlenme  (haftanın içerikleri {short(cohort_views)} · arşiv {short(archive_views)})"
                  + (f"  ↔ {_sgn(p.views)}{_pct(s.views, p.views)}" if p else ""),
                  f"❤️ {_sgn(total_likes)} beğeni  (Reels {short(s.likes_reels)} · Feed {short(s.likes_feed)})"
                  + (f"  ↔ {_sgn(p.likes_reels + p.likes_feed)}{_pct(total_likes, p.likes_reels + p.likes_feed)}" if p else ""),
                  f"💬 {_sgn(s.comments)} yorum",
                  f"📊 Reels ort. {short(round(cohort_views / len(reels)) if reels else None)} izlenme · "
                  f"Feed ort. {short(round(sum(x.last_likes for x in feed) / len(feed)) if feed else None)} ❤️"
                  + (f"  · {s.days}/7 gün ölçüldü" if s.days < 7 else "")]
    if not any_data:
        return []
    lines.append("")
    tw = token_warning(token or {})
    lines.append(tw if tw else "↔ = önceki hafta · 📁 haftanın ayrıntısı aylık ve kanal dosyalarında")
    msg1 = "\n".join(lines)

    reels = sorted([x for x in all_posts if x.group == "Reels"], key=lambda x: (x.last_views or 0, x.last_likes or 0), reverse=True)
    feed = sorted([x for x in all_posts if x.group == "Feed"], key=lambda x: (x.last_likes or 0, x.last_comments or 0), reverse=True)
    blocks = []
    if reels:
        blocks.append(["<b>🎬 Haftanın Reels top 5</b> (izlenme)"] +
                      [_post_line(i, x, f"{short(x.last_views)} · {short(x.last_likes)}❤️") for i, x in enumerate(reels[:TOP_N], 1)])
        worst = list(reversed(reels[TOP_N:]))[:TOP_N]
        if worst:
            blocks.append(["<b>🎬 Haftanın en kötü Reels 5</b>"] +
                          [_post_line(i, x, f"{short(x.last_views)} · {short(x.last_likes)}❤️") for i, x in enumerate(worst, 1)])
    if feed:
        blocks.append(["<b>🖼 Haftanın Feed top 5</b> (beğeni)"] +
                      [_post_line(i, x, f"{short(x.last_likes)}❤️ · {short(x.last_comments)}💬") for i, x in enumerate(feed[:TOP_N], 1)])
        worst = list(reversed(feed[TOP_N:]))[:TOP_N]
        if worst:
            blocks.append(["<b>🖼 Haftanın en kötü Feed 5</b>"] +
                          [_post_line(i, x, f"{short(x.last_likes)}❤️ · {short(x.last_comments)}💬") for i, x in enumerate(worst, 1)])
    if not blocks:
        return [msg1]
    lines2 = [f"🏁 <b>Haftanın içerikleri — {fmt_day(start.isoformat())} → {fmt_day(end.isoformat())}</b>"]
    for b in blocks:
        lines2 += [""] + b
    return [msg1, "\n".join(lines2)]


def due_week(snapshot_date: date) -> tuple[date, date] | None:
    """Bu çekimden sonra gönderilmesi gereken hafta: Pazar günü (ya da kaçtıysa Pzt/Salı telafisi)."""
    last_sunday = snapshot_date - timedelta(days=(snapshot_date.weekday() + 1) % 7)
    if (snapshot_date - last_sunday).days > 2:
        return None
    start = last_sunday - timedelta(days=6)
    if config.WEEKLY_FROM and start < config.WEEKLY_FROM:
        return None
    return start, last_sunday


def send_weekly(conn, start: date, end: date, token: dict | None, force: bool = False) -> bool:
    from . import notify

    key = week_key(start)
    if not force and db.get_meta(conn, "weekly_sent") == key:
        return False
    msgs = build_weekly_messages(conn, start, end, token)
    if not msgs:
        log.info("Haftalık rapor: %s için veri yok", key)
        return False
    ok = all(notify.send(m) for m in msgs)
    if ok:
        with conn:
            db.set_meta(conn, "weekly_sent", key)
        log.info("Haftalık rapor gönderildi: %s", key)
    return ok
