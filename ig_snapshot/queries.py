"""Telegram soru-cevap: 'dün ne oldu', 'dün feed', 'bu hafta reels', 'geçen ay', 'eylül feed @account' ...

Dönem  : bugün · dün · bu hafta · geçen hafta · son N gün · bu ay · geçen ay · <ay adı> [yıl] · YYYY-MM ·
         DD.MM[.YYYY] · bu yıl · geçen yıl
Tür    : feed · reels (ikisi de yoksa genel özet)
Hesap  : @kullanıcıadı (isteğe bağlı, birden fazla olabilir)
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from . import config, db
from .content import group_of
from .notify import esc
from .report import MONTHS_TR, build_account_report, fmt, fmt_day, fmt_delta, fmt_pct, month_bounds
from .daily_report import PostPerf, load_posts

MONTH_NAMES = {name.lower(): i + 1 for i, name in enumerate(MONTHS_TR)}
MONTH_NAMES.update({"subat": 2, "mayis": 5, "agustos": 8, "eylul": 9, "kasim": 11, "aralik": 12})
TOP_N = 5
LIST_CAP = 12   # tek günlük listelerde hesap başına en fazla gönderi

HELP = (
    "<b>Ne sorabilirsin?</b>\n"
    "• <code>dün ne oldu</code> / <code>bugün</code> — takipçi, içerik, kazanılan izlenme/beğeni, top 5'ler\n"
    "• <code>dün feed</code> / <code>dün reels</code> — o günün gönderileri: biz / rakipler / genel top 5\n"
    "• <code>bu hafta reels</code> · <code>geçen hafta feed</code> · <code>son 3 gün</code>\n"
    "• <code>bu ay</code> · <code>geçen ay feed</code> · <code>eylül reels</code> · <code>2026-08</code>\n"
    "• <code>bu yıl</code> · <code>geçen yıl reels</code>\n"
    "• Hesap süzmek için @ ekle: <code>geçen hafta reels @account</code>\n"
    "• <code>durum</code> — son çalışma ve token"
)


@dataclass
class Query:
    start: date
    end: date
    label: str
    kind: str                 # "Reels" | "Feed" | "all"
    accounts: list[str]


def _norm(t: str) -> str:
    return (t.lower().replace("ı", "i").replace("ğ", "g").replace("ü", "u")
            .replace("ş", "s").replace("ö", "o").replace("ç", "c").replace("â", "a"))


def parse(text: str, today: date | None = None) -> Query | None:
    today = today or date.today()
    raw = text.strip()
    t = _norm(raw)
    if not t or t in ("/start", "/yardim", "/help", "yardim", "help", "?"):
        return None

    accounts = [a.lower() for a in re.findall(r"@([A-Za-z0-9._]+)", raw)]
    known = set(config.load_accounts())
    for tok in re.findall(r"[a-z0-9._]{3,}", t):
        if tok in known and tok not in accounts:
            accounts.append(tok)

    kind = "all"
    if re.search(r"\breels?\b|\bvideo", t):
        kind = "Reels"
    elif re.search(r"\bfeed|\bfoto|\bcarousel|\bgonderi", t):
        kind = "Feed"

    start = end = None
    label = ""
    m = re.search(r"son\s+(\d+)\s+gun", t)
    if m:
        n = int(m.group(1))
        start, end = today - timedelta(days=n - 1), today
        label = f"Son {n} gün"
    elif "dun" in t.split() or t.startswith("dun") or " dun " in f" {t} ":
        start = end = today - timedelta(days=1)
        label = "Dün"
    elif "bugun" in t:
        start = end = today
        label = "Bugün"
    elif "gecen hafta" in t or "onceki hafta" in t:
        this_mon = today - timedelta(days=today.weekday())
        start, end = this_mon - timedelta(days=7), this_mon - timedelta(days=1)
        label = "Geçen hafta"
    elif "bu hafta" in t or "haftalik" in t or t.strip() == "hafta":
        start, end = today - timedelta(days=today.weekday()), today
        label = "Bu hafta"
    elif "gecen ay" in t or "onceki ay" in t:
        y, mth = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        s, e = month_bounds(f"{y}-{mth:02d}")
        start, end = date.fromisoformat(s), min(date.fromisoformat(e), today)
        label = f"{MONTHS_TR[mth - 1]} {y}"
    elif "bu ay" in t or "aylik" in t:
        start, end = today.replace(day=1), today
        label = f"{MONTHS_TR[today.month - 1]} {today.year}"
    elif "gecen yil" in t or "onceki yil" in t:
        start, end = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
        label = f"{today.year - 1}"
    elif "bu yil" in t or "yillik" in t:
        start, end = date(today.year, 1, 1), today
        label = f"{today.year}"
    else:
        m = re.search(r"\b(20\d{2})-(\d{2})\b", t)
        m2 = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(20\d{2}))?\b", t)
        m3 = next(((name, MONTH_NAMES[name]) for name in MONTH_NAMES if re.search(rf"\b{name}", t)), None)
        if m:
            y, mth = int(m.group(1)), int(m.group(2))
            s, e = month_bounds(f"{y}-{mth:02d}")
            start, end = date.fromisoformat(s), min(date.fromisoformat(e), today)
            label = f"{MONTHS_TR[mth - 1]} {y}"
        elif m2:
            d, mth = int(m2.group(1)), int(m2.group(2))
            y = int(m2.group(3)) if m2.group(3) else today.year
            try:
                start = end = date(y, mth, d)
            except ValueError:
                return None
            label = fmt_day(start.isoformat())
        elif m3:
            mth = m3[1]
            ym = re.search(r"\b(20\d{2})\b", t)
            y = int(ym.group(1)) if ym else (today.year if mth <= today.month else today.year - 1)
            s, e = month_bounds(f"{y}-{mth:02d}")
            start, end = date.fromisoformat(s), min(date.fromisoformat(e), today)
            label = f"{MONTHS_TR[mth - 1]} {y}"
        else:
            start = end = today - timedelta(days=1)
            label = "Dün"
    if start == end:
        label += f" ({fmt_day(start.isoformat())})"
    else:
        label += f" ({fmt_day(start.isoformat())} → {fmt_day(end.isoformat())})"
    return Query(start, end, label, kind, accounts)


# --- veri -----------------------------------------------------------------------

def _months_between(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y}-{m:02d}")
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out


def posts_in_range(conn, usernames: list[str], start: date, end: date, kind: str) -> list[PostPerf]:
    posts: list[PostPerf] = []
    s, e = start.isoformat(), end.isoformat()
    for u in usernames:
        for month in _months_between(start, end):
            for p in load_posts(conn, u, month):
                if s <= p.day <= e and (kind == "all" or p.group == kind):
                    posts.append(p)
    return posts


@dataclass
class PeriodSummary:
    username: str
    followers_start: int | None
    followers_end: int | None
    posts_reels: int
    posts_feed: int
    views: int
    likes_reels: int
    likes_feed: int
    comments: int
    days: int

    @property
    def followers_delta(self):
        if self.followers_start is None or self.followers_end is None:
            return None
        return self.followers_end - self.followers_start

    @property
    def followers_pct(self):
        return self.followers_delta / self.followers_start * 100 if self.followers_delta is not None and self.followers_start else None


def period_summary(conn, username: str, start: date, end: date) -> PeriodSummary | None:
    s, e = start.isoformat(), end.isoformat()
    rows = []
    for month in _months_between(start, end):
        r = build_account_report(conn, username, month, with_prev=False)
        if r:
            rows += [d for d in r.daily if s <= d.day <= e]
    if not rows:
        return None
    before = db.last_profile_before(conn, username, s)
    series = db.profile_series(conn, username, s, e)
    f_start = before["followers_count"] if before else (series[0]["followers_count"] if series else None)
    f_end = series[-1]["followers_count"] if series else None
    return PeriodSummary(
        username, f_start, f_end,
        sum(d.posts_in_group("Reels") for d in rows), sum(d.posts_in_group("Feed") for d in rows),
        sum(d.gain_by_group["Reels"].views for d in rows),
        sum(d.gain_by_group["Reels"].likes for d in rows), sum(d.gain_by_group["Feed"].likes for d in rows),
        sum(d.gain.comments for d in rows), len(series),
    )


# --- cevap ----------------------------------------------------------------------

def _post_line(p: PostPerf, with_account: bool = True, idx: int | None = None) -> str:
    cap = esc(p.caption[:45]) + ("…" if len(p.caption) > 45 else "")
    if p.group == "Reels":
        metric = f"{fmt(p.last_views)} izl · {fmt(p.last_likes)} beğ"
    else:
        metric = f"{fmt(p.last_likes)} beğ · {fmt(p.last_comments)} yor · {esc(p.content_type)}"
    head = f"{idx}. " if idx else "• "
    acc = f"@{esc(p.username)} · " if with_account else ""
    return f"{head}{acc}<b>{metric}</b> · <a href=\"{esc(p.permalink)}\">{cap or 'gönderi'}</a>"


def _rank_key(p: PostPerf):
    return (p.last_views or 0, p.last_likes or 0) if p.group == "Reels" else (p.last_likes or 0, p.last_comments or 0)


def answer(conn, q: Query) -> str:
    groups = config.load_account_groups()
    usernames = q.accounts or list(groups) or [a["username"] for a in db.list_accounts(conn)]
    own = [u for u in usernames if groups.get(u) == "biz"]
    rivals = [u for u in usernames if groups.get(u) != "biz"]
    kind_label = {"Reels": "Reels", "Feed": "Feed", "all": "Genel özet"}[q.kind]
    lines = [f"📅 <b>{esc(q.label)} — {kind_label}</b>"]

    if q.kind == "all":
        summaries = [period_summary(conn, u, q.start, q.end) for u in usernames]
        summaries = [s for s in summaries if s]
        if not summaries:
            return lines[0] + "\nBu dönem için veri yok."
        for title, members in (("🏠 Biz", own), ("🏁 Rakipler", rivals)):
            block = [s for s in summaries if s.username in members]
            if not block:
                continue
            lines += ["", f"<b>{title}</b>"]
            for s in block:
                lines.append(
                    f"<b>@{esc(s.username)}</b> {fmt(s.followers_end)} takipçi ({fmt_delta(s.followers_delta)}, {fmt_pct(s.followers_pct)})"
                    f" · {s.posts_reels + s.posts_feed} içerik (R{s.posts_reels}/F{s.posts_feed})"
                    f" · izlenme {fmt_delta(s.views)} · beğeni {fmt_delta(s.likes_reels + s.likes_feed)}"
                    f" (R {fmt_delta(s.likes_reels)} / F {fmt_delta(s.likes_feed)}) · yorum {fmt_delta(s.comments)}"
                )
        posts = posts_in_range(conn, usernames, q.start, q.end, "all")
        for title, grp in (("🎬 Top 5 Reels (izlenme)", "Reels"), ("🖼 Top 5 Feed (beğeni)", "Feed")):
            top = sorted([p for p in posts if p.group == grp], key=_rank_key, reverse=True)[:TOP_N]
            if top:
                lines += ["", f"<b>{title}</b>"] + [_post_line(p, idx=i) for i, p in enumerate(top, 1)]
        return "\n".join(lines)

    posts = posts_in_range(conn, usernames, q.start, q.end, q.kind)
    if not posts:
        return lines[0] + f"\nBu dönemde {kind_label} gönderisi yok."
    single_day = q.start == q.end
    for title, members in (("🏠 Biz", own), ("🏁 Rakipler", rivals)):
        block = [p for p in posts if p.username in members]
        if not block:
            continue
        lines += ["", f"<b>{title}</b>"]
        for u in members:
            mine = sorted([p for p in block if p.username == u], key=_rank_key, reverse=True)
            if not mine:
                continue
            if q.kind == "Reels":
                tot = f"{fmt(sum(p.last_views or 0 for p in mine))} izlenme · {fmt(sum(p.last_likes or 0 for p in mine))} beğeni"
            else:
                tot = f"{fmt(sum(p.last_likes or 0 for p in mine))} beğeni · {fmt(sum(p.last_comments or 0 for p in mine))} yorum"
            lines.append(f"<b>@{esc(u)}</b> — {len(mine)} {kind_label} · {tot}")
            if single_day:
                lines += ["   " + _post_line(p, with_account=False) for p in mine[:LIST_CAP]]
                if len(mine) > LIST_CAP:
                    lines.append(f"   … +{len(mine) - LIST_CAP} gönderi daha")
            else:
                lines += ["   " + _post_line(p, with_account=False) for p in mine[:3]]
    top = sorted(posts, key=_rank_key, reverse=True)[:TOP_N]
    lines += ["", f"<b>🏆 Genel top {len(top)} (biz dahil)</b>"] + [_post_line(p, idx=i) for i, p in enumerate(top, 1)]
    return "\n".join(lines)


def status_text(conn) -> str:
    run = db.last_run(conn)
    if not run:
        return "Henüz snapshot alınmadı."
    lines = [f"🕒 Son çalışma: {esc(run['started_at'][:16].replace('T', ' '))} · {run['ok_count']} OK / {run['fail_count']} hata"]
    if run["notes"]:
        lines.append(esc(run["notes"][:300]))
    for a in db.list_accounts(conn):
        lp = db.latest_profile(conn, a["username"])
        lines.append(f"@{esc(a['username'])}: {fmt(lp['followers_count']) if lp else '–'} takipçi · son ölçüm {fmt_day(lp['snapshot_date']) if lp else '–'}"
                     + (f" · ⚠️ {esc(a['last_error'][:60])}" if a["last_error"] else ""))
    return "\n".join(lines)


def handle(conn, text: str, token_info: dict | None = None) -> str:
    t = _norm(text.strip())
    if t in ("durum", "/durum", "status", "/status"):
        out = status_text(conn)
        if token_info and token_info.get("days") is not None:
            out += f"\n🔑 Token: {token_info['days']} gün kaldı ({token_info['expires']})"
        return out
    q = parse(text)
    if q is None:
        return HELP
    return answer(conn, q)
