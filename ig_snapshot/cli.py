"""Komut satırı: check / snapshot / report / status / token-refresh."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from logging.handlers import RotatingFileHandler

from . import config, db
from .api import GraphAPIError, GraphClient

log = logging.getLogger("ig_snapshot")


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def setup_logging(verbose: bool = False) -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(config.LOGS_DIR / "ig_snapshot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.handlers = [fh]
    if sys.stdout is not None:  # pythonw (bot görevi) altında konsol yok
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.handlers.append(ch)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _print_token_info(client: GraphClient) -> bool:
    info = client.debug_token()
    valid = bool(info.get("is_valid"))
    exp = info.get("expires_at") or 0
    if exp:
        exp_dt = datetime.fromtimestamp(exp)
        days = (exp_dt - datetime.now()).days
        exp_text = f"{exp_dt:%d.%m.%Y %H:%M} ({days} gün kaldı)"
    else:
        exp_text = "süresiz"
    print(f"Token: {'GEÇERLİ' if valid else 'GEÇERSİZ'} · tür: {info.get('type', '?')} · bitiş: {exp_text}")
    scopes = info.get("scopes") or []
    if scopes:
        print(f"İzinler: {', '.join(scopes)}")
    needed = {"instagram_basic", "pages_read_engagement"}
    missing = needed - set(scopes)
    if scopes and missing:
        print(f"UYARI: eksik izin(ler): {', '.join(sorted(missing))}")
    if exp and days < 10:
        print("UYARI: token 10 günden az kaldı. FB_APP_ID/FB_APP_SECRET tanımlıysa `token-refresh` çalıştır.")
    return valid


def cmd_check(args) -> int:
    if not config.ACCESS_TOKEN:
        print(".env dosyasında IG_ACCESS_TOKEN yok. .env.example dosyasını .env olarak kopyalayıp doldur.")
        return 1
    client = GraphClient(config.ACCESS_TOKEN, config.GRAPH_VERSION)

    try:
        if not _print_token_info(client):
            return 1
    except GraphAPIError as exc:
        print(f"Token doğrulanamadı: {exc}")
        return 1

    ig_user_id = config.IG_USER_ID
    if not ig_user_id:
        try:
            accounts = client.list_ig_accounts()
        except GraphAPIError as exc:
            print(f"Instagram hesabı listelenemedi: {exc}")
            return 1
        if not accounts:
            print("Bu tokenın erişebildiği sayfalara bağlı Instagram profesyonel hesabı bulunamadı.")
            return 1
        for a in accounts:
            print(f"  Sayfa: {a['page']} → Instagram @{a['ig_username']} (id {a['ig_id']})")
        ig_user_id = accounts[0]["ig_id"]
        config.save_env_value("IG_USER_ID", ig_user_id)
        print(f"IG_USER_ID={ig_user_id} olarak .env dosyasına yazıldı"
              + (" (birden fazla hesap var; gerekirse değiştir)." if len(accounts) > 1 else "."))
    else:
        print(f"IG_USER_ID: {ig_user_id}")

    targets = config.load_accounts()
    print(f"Takip listesi: {len(targets)} hesap ({config.ACCOUNTS_FILE.name})")
    if not targets:
        return 0

    sample = targets[0]
    print(f"Deneme sorgusu: @{sample} ...")
    try:
        data = client.business_discovery(ig_user_id, sample, page_size=5, max_media=5)
    except GraphAPIError as exc:
        print(f"  HATA: {exc}")
        print("  Hesap Business/Creator değilse ya da kullanıcı adı yanlışsa bu hata alınır.")
        return 1
    p = data["profile"]
    print(f"  @{p.get('username')} — {p.get('name')} · takipçi {p.get('followers_count')} · gönderi {p.get('media_count')}")
    for m in data["media"]:
        print(f"  {m.get('timestamp', '')[:10]} {m.get('media_product_type')}/{m.get('media_type')} "
              f"izlenme={m.get('view_count')} beğeni={m.get('like_count')} yorum={m.get('comments_count')}")
    if data["media"] and all(m.get("view_count") is None for m in data["media"]):
        print("  NOT: view_count dönmedi; izlenme takibi bu hesap için sınırlı olacak.")
    print("Her şey hazır. Günlük çalıştırma: python -m ig_snapshot snapshot")
    return 0


def cmd_snapshot(args) -> int:
    from . import channel_log, daily_report, notify, overall, report, snapshot

    snapshot_date = date.fromisoformat(args.date) if args.date else date.today()
    try:
        result = snapshot.run_snapshot(snapshot_date)
    except SystemExit:
        raise
    except BaseException as exc:  # çökme → Telegram alarmı, sonra hatayı yükselt
        log.exception("Snapshot çöktü")
        if notify.enabled():
            notify.send(notify.build_crash_message(exc))
        raise
    if not args.no_report:
        month = snapshot_date.strftime("%Y-%m")
        # Önceki ay da her gece yenilenir: gönderileri TRACK_DAYS boyunca ölçülmeye devam ettiği için
        # "güncel" sütunları (sürüklenme) birikmeye devam eder; ay içi metrikler değişmez.
        months = [month, report.prev_month(month)]
        for m in months:
            try:
                report.generate(m)
                daily_report.generate(m)
            except Exception:  # rapor hatası snapshot'ı geçersiz kılmasın
                log.exception("%s raporu üretilemedi", m)
        try:
            overall.generate()
        except Exception:
            log.exception("Genel rapor üretilemedi")
        try:
            channel_log.generate()
        except Exception:
            log.exception("Kanal dosyaları üretilemedi")
    print(f"Snapshot {result['date']}: {len(result['ok'])} hesap OK, {len(result['failed'])} hata")
    for u, e in result["failed"].items():
        print(f"  @{u}: {e}")
    if notify.enabled():
        token = notify.token_status(result["client"])
        highlights, gains = {}, {}
        try:
            conn = db.connect()
            highlights = daily_report.highlights_today(conn, snapshot_date)
            gains = today_gains(conn, snapshot_date, result["ok"])
            conn.close()
        except Exception:  # noqa: BLE001
            log.exception("Öne çıkanlar / günlük kazanım hesaplanamadı")
        sent = notify.send(notify.build_snapshot_message(result, token, result["elapsed"], result["waited"],
                                                         highlights, gains))
        print("Telegram bildirimi gönderildi." if sent else "Telegram bildirimi gönderilemedi (log'a bak).")
        # Ay kapanışı: yeni ayın ilk günlerinde, bir kez
        prev = report.prev_month(snapshot_date.strftime("%Y-%m"))
        if snapshot_date.day <= 10:
            try:
                send_month_end(prev, force=False)
            except Exception:  # noqa: BLE001
                log.exception("Ay kapanış mesajı gönderilemedi")
    return 0 if not result["failed"] or result["ok"] else 1


def today_gains(conn, day: date, usernames: list[str]) -> dict:
    """Her hesap için bugün kazanılanlar: toplam (tüm içerikler), bugünkü içeriklerden gelen ve arşivden gelen.

    Döner: {username: {"total": Gain, "by_group": {Reels: Gain, Feed: Gain},
                       "today_views", "today_likes", "today_comments", "archive_views", "archive_likes"}}
    """
    from . import daily_report, report

    month = day.strftime("%Y-%m")
    today = day.isoformat()
    cohorts, _ = daily_report.build(conn, month)
    out = {}
    for u in usernames:
        r = report.build_account_report(conn, u, month, with_prev=False)
        row = next((d for d in r.daily if d.day == today), None) if r else None
        if row is None:
            continue
        c = cohorts.get(u, {}).get(today)
        tv = (c._sum("last_views") or 0) if c else 0
        tl = (c._sum("last_likes") or 0) if c else 0
        tc = (c._sum("last_comments") or 0) if c else 0
        out[u] = {
            "total": row.gain, "by_group": row.gain_by_group,
            "today_views": tv, "today_likes": tl, "today_comments": tc,
            "archive_views": max(0, row.gain.views - tv),
            "archive_likes": max(0, row.gain.likes - tl),
            "archive_comments": max(0, row.gain.comments - tc),
        }
    return out


def build_month_end(month: str) -> str | None:
    from . import daily_report, notify, report

    conn = db.connect()
    try:
        reports = report.build_month_report(conn, month)
        if not reports:
            return None
        _, posts = daily_report.build(conn, month)
    finally:
        conn.close()
    return notify.build_month_end_message(month, report.month_label(month), reports, posts, config.TRACK_DAYS)


def send_month_end(month: str, force: bool) -> bool:
    """Ay kapanış mesajını gönderir; force değilse aynı ay için yalnızca bir kez."""
    from . import notify

    conn = db.connect()
    try:
        if not force and db.get_meta(conn, "month_end_sent") == month:
            return False
        text = build_month_end(month)
        if not text:
            return False
        ok = notify.send(text)
        if ok:
            with conn:
                db.set_meta(conn, "month_end_sent", month)
        return ok
    finally:
        conn.close()


def cmd_month_summary(args) -> int:
    from . import report

    month = args.month or report.prev_month(date.today().strftime("%Y-%m"))
    if args.send:
        ok = send_month_end(month, force=True)
        print(f"{month} kapanış mesajı {'gönderildi' if ok else 'gönderilemedi / veri yok'}.")
        return 0 if ok else 1
    text = build_month_end(month)
    if not text:
        print(f"{month} için veri yok.")
        return 1
    import re
    print(re.sub(r"<[^>]+>", "", text))
    print()
    print("(Göndermek için: --send)")
    return 0


def cmd_bot(args) -> int:
    from .bot import run_bot
    run_bot()
    return 0


def cmd_ask(args) -> int:
    """Telegram'a göndermeden soru-cevabı dene."""
    import re
    from . import queries

    conn = db.connect()
    try:
        text = queries.handle(conn, " ".join(args.text))
    finally:
        conn.close()
    print(re.sub(r"<[^>]+>", "", text))
    return 0


def cmd_telegram_test(args) -> int:
    from . import notify

    if not config.TELEGRAM_BOT_TOKEN:
        print(".env içinde TELEGRAM_BOT_TOKEN yok. Telegram'da @BotFather → /newbot → tokenı .env'e yaz.")
        return 1
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        try:
            chats = notify.discover_chats(config.TELEGRAM_BOT_TOKEN)
        except Exception as exc:  # noqa: BLE001
            print(f"Bot doğrulanamadı: {exc}")
            return 1
        if not chats:
            print("Bota henüz mesaj gelmemiş. Telegram'da botu bul, /start yaz (ya da gruba ekleyip bir mesaj at), sonra tekrar çalıştır.")
            return 1
        for cid, title in chats:
            print(f"  chat_id {cid}: {title}")
        chat_id = chats[0][0]
        config.save_env_value("TELEGRAM_CHAT_ID", chat_id)
        config.TELEGRAM_CHAT_ID = chat_id
        print(f"TELEGRAM_CHAT_ID={chat_id} .env dosyasına yazıldı" + (" (birden fazla sohbet var; gerekirse değiştir)." if len(chats) > 1 else "."))

    client = GraphClient(config.ACCESS_TOKEN, config.GRAPH_VERSION) if config.ACCESS_TOKEN else None
    token = notify.token_status(client) if client else {"valid": False, "days": None, "expires": None, "error": "token yok"}
    tw = notify.token_warning(token)
    lines = ["🔔 <b>IG Snapshot test mesajı</b>",
             "Bildirimler bu sohbete gelecek. Günlük özet her gece 23:30 çalışmasından sonra.",
             tw or (f"🔑 Instagram tokenı: {token['days']} gün kaldı ({token['expires']}) — "
                    f"{config.TOKEN_WARN_DAYS} gün kala her gün uyarı gelecek.")]
    text = "\n".join(lines)
    ok = notify.send(text, chat_id)
    print("Test mesajı gönderildi." if ok else "Gönderilemedi — log'a bak.")
    return 0 if ok else 1


def cmd_report(args) -> int:
    from . import channel_log, daily_report, overall, report

    conn = db.connect()
    try:
        if args.all:
            months = db.months_with_data(conn)
        elif args.overall:
            months = []
        else:
            months = [args.month or date.today().strftime("%Y-%m")]
        if not months and not args.overall:
            print("Veritabanında henüz veri yok.")
            return 1
        for m in months:
            out = report.generate(m, conn)
            if out:
                print(f"{m}: {out['md']}")
                print(f"{'':>7} {out['xlsx']}")
                g = daily_report.generate(m, conn)
                if g:
                    print(f"{'':>7} {g}")
            else:
                print(f"{m}: veri yok")
        out = overall.generate(conn)
        if out:
            print(f"genel:   {out['md']}")
            print(f"{'':>8} {out['xlsx']}")
        else:
            print("genel: veri yok")
        files = channel_log.generate(conn)
        if files:
            print(f"kanallar: {files[0].parent} ({len(files)} dosya)")
    finally:
        conn.close()
    return 0


def cmd_status(args) -> int:
    conn = db.connect()
    try:
        run = db.last_run(conn)
        if run:
            print(f"Son çalışma: {run['started_at']} → {run['finished_at']} · "
                  f"{run['ok_count']} OK / {run['fail_count']} hata")
            if run["notes"]:
                print(f"  {run['notes']}")
        else:
            print("Henüz hiç snapshot alınmadı.")
        months = db.months_with_data(conn)
        if months:
            print(f"Veri olan aylar: {', '.join(months)}")
        print()
        print(f"{'Hesap':<24} {'Son ölçüm':<11} {'Takipçi':>10} {'Gönderi':>8}  Durum")
        for a in db.list_accounts(conn):
            lp = db.latest_profile(conn, a["username"])
            n = db.media_count_tracked(conn, a["username"])
            status = f"HATA: {a['last_error'][:60]}" if a["last_error"] else "ok"
            followers = f"{(lp['followers_count'] if lp else 0) or 0:,}".replace(",", ".")
            print(f"@{a['username']:<23} {lp['snapshot_date'] if lp else '-':<11} "
                  f"{followers:>10} {n:>8}  {status}")
    finally:
        conn.close()
    return 0


def cmd_token_refresh(args) -> int:
    if not (config.APP_ID and config.APP_SECRET):
        print(".env içinde FB_APP_ID ve FB_APP_SECRET gerekli (Meta uygulama ayarları → Temel).")
        return 1
    client = GraphClient(config.ACCESS_TOKEN, config.GRAPH_VERSION)
    try:
        data = client.exchange_long_lived(config.APP_ID, config.APP_SECRET)
    except GraphAPIError as exc:
        print(f"Token yenilenemedi: {exc}")
        print("Mevcut token süresi dolmuşsa Graph API Explorer'dan yeni token alıp .env'e yazman gerekir.")
        return 1
    new_token = data.get("access_token")
    if not new_token:
        print(f"Beklenmeyen yanıt: {data}")
        return 1
    config.save_env_value("IG_ACCESS_TOKEN", new_token)
    print("Yeni token .env dosyasına yazıldı.")
    _print_token_info(GraphClient(new_token, config.GRAPH_VERSION))
    return 0


def cmd_limit_test(args) -> int:
    from . import limit_test

    usernames = [u.lstrip("@").lower() for u in args.usernames] or config.load_accounts()
    if not usernames:
        print("Kullanıcı adı ver: python -m ig_snapshot limit-test hesap1 hesap2  (ya da accounts.txt doldur)")
        return 1
    return limit_test.run(usernames, burst_calls=args.calls)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ig_snapshot", description="Instagram rakip takibi: günlük snapshot + aylık rapor")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("check", help="Token, hesap ID ve takip listesini doğrula")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("snapshot", help="Bugünün verisini çek ve kaydet (ay raporunu da günceller)")
    s.add_argument("--date", help="Snapshot tarihi (YYYY-MM-DD), varsayılan bugün")
    s.add_argument("--no-report", action="store_true", help="Rapor üretme")
    s.set_defaults(func=cmd_snapshot)

    s = sub.add_parser("report", help="Aylık raporu ve genel görünümü üret")
    s.add_argument("--month", help="YYYY-MM, varsayılan içinde bulunulan ay")
    s.add_argument("--all", action="store_true", help="Veri olan tüm ayların raporunu yeniden üret")
    s.add_argument("--overall", action="store_true", help="Sadece genel (aylar arası) raporu üret")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("status", help="Veritabanı ve hesap durumu")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("bot", help="Telegram sohbet botunu çalıştır (sürekli; sorulara cevap verir)")
    s.set_defaults(func=cmd_bot)

    s = sub.add_parser("ask", help="Bir soruyu Telegram olmadan dene: ask dün feed")
    s.add_argument("text", nargs="+")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("month-summary", help="Ay kapanış mesajını göster / Telegram'a gönder")
    s.add_argument("--month", help="YYYY-MM, varsayılan bir önceki ay")
    s.add_argument("--send", action="store_true", help="Telegram'a gönder (yoksa sadece ekrana yazar)")
    s.set_defaults(func=cmd_month_summary)

    s = sub.add_parser("telegram-test", help="Telegram botunu doğrula, chat id'yi bul ve test mesajı gönder")
    s.set_defaults(func=cmd_telegram_test)

    s = sub.add_parser("limit-test", help="Örnek hesaplarla çağrı maliyeti ve rate-limit kapasitesini ölç")
    s.add_argument("usernames", nargs="*", help="Test edilecek kullanıcı adları (boşsa accounts.txt)")
    s.add_argument("--calls", type=int, default=20, help="Yük testindeki ardışık çağrı sayısı (varsayılan 20)")
    s.set_defaults(func=cmd_limit_test)

    s = sub.add_parser("token-refresh", help="Uzun ömürlü tokenı yenile (FB_APP_ID/SECRET gerekir)")
    s.set_defaults(func=cmd_token_refresh)
    return p


def main(argv: list[str] | None = None) -> None:
    _utf8_console()
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        code = args.func(args)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code)
            code = 1
        else:
            code = exc.code or 0
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)
