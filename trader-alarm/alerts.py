#!/usr/bin/env python3
"""Kursalarme und die NYSE-Uhr für trader-alarm. Nur Standardbibliothek.

    python3 alerts.py clock                          # Ist die NYSE offen? Nächste Öffnung / Schluss
    python3 alerts.py quote AAPL KO --write          # Kurse -> trader/prices.json
    python3 alerts.py quote --holdings --write       # jeder Ticker aus Depot und Zielen
    python3 alerts.py add --symbol AAPL --above 350  # Regeln liegen in trader/alerts.json
    python3 alerts.py add --symbol BAC --stop 50
    python3 alerts.py add --symbol KO --pct 3
    python3 alerts.py add --opening-bell
    python3 alerts.py check                          # läutet, was getroffen wurde, einmal; Exit 1, wenn nichts läutete
    python3 alerts.py list
    python3 alerts.py reset aapl-above-350

Regeltypen:
  above         der Kurs liegt bei "price" oder darüber (ein Kursziel)
  below / stop  der Kurs liegt bei "price" oder darunter (ein Stopp oder ein Rücksetzer zum Kaufen)
  pct_move      der Kurs hat sich um "pct" Prozent oder mehr bewegt, gegenüber dem Vortagesschluss
                (oder gegenüber "ref", wenn du eins angibst); "direction": "up" oder "down"
  opening_bell  die NYSE hat heute geöffnet (läutet einmal pro Handelstag)

Ein above- / below-Alarm läutet EINMAL und bleibt dann still, bis du ihn zurücksetzt,
außer die Regel hat "rearm": true (dann wird er wieder scharf, sobald der Kurs zurückkreuzt).
Ein pct_move-Alarm läutet höchstens einmal pro Tag.

Kurse (kostenlos, verzögert, nur für Alarme, nie zum Aufgeben von Aufträgen):
  yahoo   https://query1.finance.yahoo.com/v8/finance/chart/AAPL (inoffizielles
          JSON, eine Anfrage pro Ticker; der Standard)
  stooq   https://stooq.com/q/l/?s=aapl.us&f=sd2t2ohlcv&h&e=csv (kostenloses CSV
          von Stooq; am 13.10.2026 kam 404, der Parser bleibt für den Fall, dass es wieder geht)
  --offline DIR liest DIR/<source>-<TICKER>.json|csv statt aus dem Netz.

NYSE-Kalender: die Feiertage 2026 und 2027 und die Früh-Schlüsse um 13:00 Uhr, wie
veröffentlicht auf https://www.nyse.com/markets/hours-calendars (gelesen am 13.10.2026).
Außerhalb dieser Jahre sagt die Uhr, dass die Feiertagsliste unbekannt ist.

Keine Anlageberatung. Alarme sind Information, keine Aufträge.
"""

import argparse
import csv
import datetime
import io
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; trader-skill/1.0; +https://github.com/Jakeschincariol/trader-skill)"
PAUSE = 0.5  # Sekunden zwischen Anfragen: höflich zu kostenlosen Diensten

NYSE_HOLIDAYS = {
    "2026-01-01": "New Year's Day (Neujahr)",
    "2026-01-19": "Martin Luther King, Jr. Day (Gedenktag)",
    "2026-02-16": "Washington's Birthday (Presidents Day)",
    "2026-04-03": "Good Friday (Karfreitag)",
    "2026-05-25": "Memorial Day (Gedenktag)",
    "2026-06-19": "Juneteenth National Independence Day (Gedenktag)",
    "2026-07-03": "Independence Day (Nationalfeiertag, nachgeholt)",
    "2026-09-07": "Labor Day (Tag der Arbeit)",
    "2026-11-26": "Thanksgiving Day (Erntedank)",
    "2026-12-25": "Christmas Day (1. Weihnachtstag)",
    "2027-01-01": "New Year's Day (Neujahr)",
    "2027-01-18": "Martin Luther King, Jr. Day (Gedenktag)",
    "2027-02-15": "Washington's Birthday (Presidents Day)",
    "2027-03-26": "Good Friday (Karfreitag)",
    "2027-05-31": "Memorial Day (Gedenktag)",
    "2027-06-18": "Juneteenth National Independence Day (Gedenktag, nachgeholt)",
    "2027-07-05": "Independence Day (Nationalfeiertag, nachgeholt)",
    "2027-09-06": "Labor Day (Tag der Arbeit)",
    "2027-11-25": "Thanksgiving Day (Erntedank)",
    "2027-12-24": "Christmas Day (1. Weihnachtstag, vorgezogen)",
}
NYSE_EARLY_CLOSE = {  # 13:00 Uhr New Yorker Zeit
    "2026-11-27": "Tag nach Thanksgiving",
    "2026-12-24": "Heiligabend",
    "2027-11-26": "Tag nach Thanksgiving",
}
CALENDAR_YEARS = (2026, 2027)
OPEN = datetime.time(9, 30)
CLOSE = datetime.time(16, 0)
EARLY = datetime.time(13, 0)


class AlertError(Exception):
    pass


# ---------------------------------------------------------------- time

def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def eastern_offset(utc):
    """Stunden gegenüber UTC für New York: -4 in der Sommerzeit, sonst -5.
    Die Sommerzeit läuft vom zweiten Sonntag im März, 2:00 Uhr, bis zum
    ersten Sonntag im November, 2:00 Uhr (US-Regel seit 2007)."""
    y = utc.year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(7, 0))   # 2:00 EST
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(6, 0))    # 2:00 EDT
    naive = utc.replace(tzinfo=None)
    return -4 if start <= naive < end else -5


def to_eastern(utc):
    return utc.replace(tzinfo=None) + datetime.timedelta(hours=eastern_offset(utc))


def eastern_to_utc(local):
    for off in (-4, -5):
        utc = local - datetime.timedelta(hours=off)
        if eastern_offset(utc) == off:
            return utc
    raise AlertError("Diese New Yorker Ortszeit existiert wegen der Sommerzeitumstellung nicht; gib eine gültige Zeit mit Offset an")


def parse_now(text):
    """ISO-Zeit -> naive UTC. Eine Zeit ohne Offset gilt als New Yorker Zeit."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise AlertError("--now braucht eine ISO-Zeit wie 2026-10-13T09:31 (New York) oder ...Z (UTC)")
    if dt.tzinfo is None:
        return eastern_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    """(ist_handelstag, Grund). Wochenenden und die NYSE-Feiertagsliste."""
    if d.weekday() >= 5:
        return False, "Wochenende"
    name = NYSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NYSE-Feiertag: " + name
    if d.year not in CALENDAR_YEARS:
        return False, "Feiertagskalender unbekannt; Öffnung nicht bestätigt"
    return True, ""


def session(d):
    """(Öffnung, Schluss) in New Yorker Zeit, oder None, wenn den ganzen Tag geschlossen."""
    ok, _ = trading_day(d)
    if not ok:
        return None
    return OPEN, (EARLY if d.isoformat() in NYSE_EARLY_CLOSE else CLOSE)


def market_status(now_utc):
    local = to_eastern(now_utc)
    d = local.date()
    ok, reason = trading_day(d)
    st = {"now_new_york": local.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(),
          "weekday": local.strftime("%A"), "calendar_known": d.year in CALENDAR_YEARS,
          "open": False, "reason": reason, "early_close": NYSE_EARLY_CLOSE.get(d.isoformat())}
    s = session(d)
    if s:
        o, c = s
        st["opens"], st["closes"] = o.strftime("%H:%M"), c.strftime("%H:%M")
        if o <= local.time() < c:
            st["open"] = True
            st["reason"] = "regulärer Handel"
        elif local.time() < o:
            st["reason"] = "vor der Eröffnung"
        else:
            st["reason"] = "nach Handelsschluss"
    # nächste Öffnung
    nd = d if (s and local.time() < s[0]) else d + datetime.timedelta(days=1)
    for _ in range(15):
        if session(nd):
            st["next_open"] = "%s 09:30" % nd.isoformat()
            break
        nd += datetime.timedelta(days=1)
    return st


# ---------------------------------------------------------------- quotes

def stooq_url(sym):
    return "https://stooq.com/q/l/?s=%s.us&f=sd2t2ohlcv&h&e=csv" % sym.lower().replace(".", "-")


def yahoo_url(sym):
    return "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=1d" % (
        sym.upper().replace(".", "-"))


def parse_stooq(text, sym):
    """Symbol,Date,Time,Open,High,Low,Close,Volume -> Kurs. N/D heißt: keine Daten."""
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    if not rows:
        raise AlertError("Stooq hat für %s keine Zeilen geliefert" % sym)
    r = {k.strip().lower(): (v or "").strip() for k, v in rows[0].items() if k}
    close = r.get("close", "")
    if not close or close.upper() == "N/D":
        raise AlertError("Stooq hat keine Daten für %s (prüf den Ticker)" % sym)

    def f(k):
        v = r.get(k, "")
        try:
            number = float(v)
            return number if math.isfinite(number) else None
        except ValueError:
            return None
    price = f("close")
    if price is None or price <= 0:
        raise AlertError("Stooq hat keinen gültigen positiven Kurs für %s" % sym)
    return {"symbol": sym.upper(), "price": price, "open": f("open"), "high": f("high"),
            "low": f("low"), "prev_close": None, "time": ("%s %s" % (r.get("date", ""), r.get("time", ""))).strip(),
            "source": "stooq"}


def parse_yahoo(text, sym):
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise AlertError("Yahoo hat für %s etwas Unerwartetes geliefert" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise AlertError("Yahoo hat keine Daten für %s: %s" % (sym, err.get("description", "unbekannter Ticker")))
    m = res[0].get("meta", {})
    price = m.get("regularMarketPrice")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise AlertError("Yahoo hat keinen gültigen positiven Kurs für %s" % sym)
    t = m.get("regularMarketTime")
    when = (datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if t else "")
    q = (res[0].get("indicators", {}).get("quote") or [{}])[0]
    opens = [x for x in (q.get("open") or []) if x is not None]
    return {"symbol": sym.upper(), "price": float(price), "open": opens[-1] if opens else None,
            "high": m.get("regularMarketDayHigh"), "low": m.get("regularMarketDayLow"),
            "prev_close": m.get("previousClose") or m.get("chartPreviousClose"), "time": when,
            "currency": m.get("currency"), "source": "yahoo"}


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise AlertError("%s antwortete mit HTTP %d" % (url.split("?")[0], e.code))
    except urllib.error.URLError as e:
        raise AlertError("%s nicht erreichbar: %s" % (url.split("/")[2], e.reason))


def get_quotes(symbols, source="yahoo", offline=None, pause=PAUSE):
    """{SYM: Kurs} und [Fehler]. Eine Anfrage pro Ticker, mit Pause dazwischen."""
    quotes, errors = {}, []
    for i, sym in enumerate(symbols):
        sym = sym.upper()
        try:
            if offline:
                ext = "csv" if source == "stooq" else "json"
                path = os.path.join(offline, "%s-%s.%s" % (source, sym, ext))
                if not os.path.exists(path):
                    raise AlertError("keine Offline-Kursdatei %s" % path)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            else:
                if i and pause:
                    time.sleep(pause)
                text = fetch(stooq_url(sym) if source == "stooq" else yahoo_url(sym))
            quotes[sym] = parse_stooq(text, sym) if source == "stooq" else parse_yahoo(text, sym)
        except AlertError as e:
            errors.append(str(e))
    return quotes, errors


def write_prices(path, quotes, now_utc, source):
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        except (ValueError, OSError):
            old = {}
    prices = dict(old.get("prices", {}))
    detail = dict(old.get("detail", {}))
    for s, q in quotes.items():
        prices[s] = q["price"]
        detail[s] = {"prev_close": q.get("prev_close"), "time": q.get("time"), "source": q.get("source")}
    doc = {"as_of": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": source,
           "note": "Verzögerte Kurse für Alarme und Größenprüfungen. Zum Kurs deines Brokers wird ausgeführt.",
           "prices": prices, "detail": detail}
    _write(path, doc)
    return doc


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise AlertError("%s ist kein gültiges JSON: %s" % (path, e))


# ---------------------------------------------------------------- rules

PRICE_TYPES = {"above", "below", "stop", "target", "pct_move", "opening_bell"}


def validate(rule):
    t = rule.get("type")
    if t not in PRICE_TYPES:
        raise AlertError("Regel %s: unbekannter Typ %r" % (rule.get("id"), t))
    if t != "opening_bell" and not rule.get("symbol"):
        raise AlertError("Regel %s braucht ein symbol" % rule.get("id"))
    if t in ("above", "below", "stop", "target") and not isinstance(rule.get("price"), (int, float)):
        raise AlertError("Regel %s braucht einen numerischen price" % rule.get("id"))
    if t == "pct_move" and not isinstance(rule.get("pct"), (int, float)):
        raise AlertError("Regel %s braucht ein numerisches pct" % rule.get("id"))


def evaluate(rules, quotes, status, state, now_utc):
    """Reine Funktion. Gibt (Alarme, neuer_Zustand) zurück. Jeder Alarm: {id, symbol, message}."""
    state = json.loads(json.dumps(state or {}))
    rung = state.setdefault("rung", {})
    today = status["date"]
    rings = []
    for r in rules:
        validate(r)
        rid = r.get("id") or "%s-%s" % (r.get("symbol", "market"), r["type"])
        typ = {"stop": "below", "target": "above"}.get(r["type"], r["type"])
        if typ == "opening_bell":
            if status["open"] and rung.get(rid, "")[:10] != today:
                rings.append({"id": rid, "symbol": "", "message": "Eröffnungsglocke: die NYSE ist geöffnet (%s New York)%s."
                              % (status["now_new_york"], (", heute Früh-Schluss um 13:00 Uhr, "
                                                          + status["early_close"]) if status.get("early_close") else "")})
                rung[rid] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            continue
        sym = r["symbol"].upper()
        q = quotes.get(sym)
        if not q:
            continue
        p = q["price"]
        if typ in ("above", "below"):
            hit = p >= r["price"] if typ == "above" else p <= r["price"]
            if hit and rid not in rung:
                word = "auf oder über" if typ == "above" else "auf oder unter"
                label = {"stop": "deinem Stopp", "target": "deinem Ziel"}.get(r["type"], "deiner Marke")
                rings.append({"id": rid, "symbol": sym, "message": "%s %s $%.2f, das ist %s %s $%s%s."
                              % (sym, "steht bei" if status["open"] else "zuletzt bei", p, word, label, _n(r["price"]),
                                 (" (" + r["note"] + ")") if r.get("note") else "")})
                rung[rid] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            elif not hit and rid in rung and r.get("rearm"):
                del rung[rid]
        elif typ == "pct_move":
            ref = r.get("ref") or q.get("prev_close") or q.get("open")
            if not ref:
                continue
            move = 100.0 * (p / float(ref) - 1)
            want = r.get("direction")
            hit = abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0))
            if hit and rung.get(rid, "")[:10] != today:
                rings.append({"id": rid, "symbol": sym, "message": "%s hat sich um %+.2f%% bewegt, jetzt $%.2f (von $%.2f)."
                              % (sym, move, p, float(ref))})
                rung[rid] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    state["last_check"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    return rings, state


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


# ---------------------------------------------------------------- commands

def holdings(a):
    syms = []
    pf = _read(a.portfolio, {})
    for p in pf.get("positions", []):
        syms.append(str(p.get("ticker") or p.get("symbol") or "").upper())
    tg = _read(a.targets, {})
    for w in tg.get("weights", []) if isinstance(tg, dict) else []:
        syms.append(str(w.get("ticker") or w.get("symbol") or "").upper())
    return [s for s in syms if s]


def _dedupe(items):
    out = []
    for s in items:
        if s and s not in out:
            out.append(s)
    return out


def cmd_clock(a):
    st = market_status(parse_now(a.now))
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        print("New York %s (%s): NYSE %s, %s." % (st["now_new_york"], st["weekday"],
                                                  ("UNBEKANNT" if not st["calendar_known"] else
                                                   "OFFEN" if st["open"] else "geschlossen"), st["reason"]))
        if st.get("opens"):
            print("Heutige Handelszeit: %s bis %s%s." % (st["opens"], st["closes"],
                                                    (" (Früh-Schluss: %s)" % st["early_close"]) if st.get("early_close") else ""))
        if not st["open"] and st.get("next_open"):
            print("Nächste Öffnung: %s New Yorker Zeit." % st["next_open"])
        if not st["calendar_known"]:
            print("Die Feiertagsliste gilt nur für %s: prüf nyse.com für dieses Datum." % " und ".join(map(str, CALENDAR_YEARS)))
    return 0 if st["open"] else 1


def cmd_quote(a):
    syms = _dedupe([s.upper() for s in a.symbols] + (holdings(a) if a.holdings else []))
    if not syms:
        raise AlertError("welche Ticker? alerts.py quote AAPL KO, oder --holdings")
    now = parse_now(a.now)
    quotes, errors = get_quotes(syms, a.source, a.offline)
    if a.write and quotes:
        write_prices(a.write, quotes, now, a.source)
    if a.json:
        print(json.dumps({"quotes": quotes, "errors": errors}, indent=2))
    else:
        for s in syms:
            q = quotes.get(s)
            if q:
                chg = ""
                if q.get("prev_close"):
                    chg = " (%+.2f%% zum Vortagesschluss $%.2f)" % (100.0 * (q["price"] / q["prev_close"] - 1), q["prev_close"])
                print("%-6s $%.2f%s  %s  [%s]" % (s, q["price"], chg, q.get("time", ""), q["source"]))
        for e in errors:
            print("Fehler: " + e)
        if a.write and quotes:
            print("%d Kurs(e) nach %s geschrieben." % (len(quotes), a.write))
        print("Kurse sind verzögert und nur für Alarme. Zum Kurs deines Brokers wird ausgeführt.")
    return 0 if quotes else 1


def cmd_check(a):
    doc = _read(a.rules, None)
    if not doc or not doc.get("rules"):
        raise AlertError("keine Regeln in %s: leg eine an mit alerts.py add --symbol AAPL --above 350" % a.rules)
    rules = doc["rules"]
    now = parse_now(a.now)
    status = market_status(now)
    syms = _dedupe([str(r.get("symbol", "")).upper() for r in rules if r.get("type") != "opening_bell"])
    quotes, errors = (get_quotes(syms, a.source, a.offline) if syms else ({}, []))
    state = _read(a.state, {})
    rings, state = evaluate(rules, quotes, status, state, now)
    if not a.no_write:
        _write(a.state, state)
        if quotes:
            write_prices(a.prices_out, quotes, now, a.source)
    if a.json:
        print(json.dumps({"market": status, "rings": rings, "errors": errors,
                          "quotes": {s: q["price"] for s, q in quotes.items()}}, indent=2))
    else:
        print("NYSE %s (%s New York, %s)." % ("UNBEKANNT" if not status["calendar_known"] else "OFFEN" if status["open"] else "geschlossen", status["now_new_york"],
                                              status["reason"]))
        for r in rings:
            print("ALARM " + r["message"])
        if not rings:
            print("Nichts getroffen. %d Regel(n) überwacht." % len(rules))
        for e in errors:
            print("error: " + e)
        if rings:
            print("Ein Alarm ist Information, kein Auftrag. Willst du handeln, lass die Größe berechnen (trader-budget oder "
                  "trader-ausgleich), prüfen (trader-risiko) und sag Ja dazu (trader-broker). Keine Anlageberatung.")
    return 0 if rings else 1


def cmd_add(a):
    doc = _read(a.rules, {"rules": []})
    doc.setdefault("rules", [])
    if a.opening_bell:
        rule = {"id": a.id or "opening-bell", "type": "opening_bell"}
    else:
        if not a.symbol:
            raise AlertError("--symbol ist nötig (oder --opening-bell)")
        sym = a.symbol.upper()
        if a.above is not None:
            rule = {"type": "above", "price": a.above}
        elif a.below is not None:
            rule = {"type": "below", "price": a.below}
        elif a.stop is not None:
            rule = {"type": "stop", "price": a.stop}
        elif a.pct is not None:
            rule = {"type": "pct_move", "pct": a.pct}
            if a.direction:
                rule["direction"] = a.direction
        else:
            raise AlertError("gib eines von --above, --below, --stop oder --pct an")
        val = rule.get("price", rule.get("pct"))
        rule = dict({"id": a.id or ("%s-%s-%s" % (sym.lower(), rule["type"], _n(val)))}, symbol=sym, **rule)
        if a.rearm:
            rule["rearm"] = True
        if a.note:
            rule["note"] = a.note
    validate(rule)
    if any(r.get("id") == rule["id"] for r in doc["rules"]):
        raise AlertError("eine Regel mit der id %s gibt es schon" % rule["id"])
    doc["rules"].append(rule)
    _write(a.rules, doc)
    print("Hinzugefügt %s: %s" % (rule["id"], json.dumps(rule)))
    return 0


def cmd_list(a):
    doc = _read(a.rules, {"rules": []})
    state = _read(a.state, {})
    if not doc.get("rules"):
        print("Noch keine Regeln.")
        return 1
    for r in doc["rules"]:
        rid = r.get("id")
        rung = state.get("rung", {}).get(rid)
        print("%-24s %s%s" % (rid, json.dumps({k: v for k, v in r.items() if k != "id"}),
                              ("  (läutete %s)" % rung) if rung else ""))
    return 0


def cmd_reset(a):
    state = _read(a.state, {})
    if a.id not in state.get("rung", {}):
        print("%s hat nicht geläutet (nichts zurückzusetzen)." % a.id)
        return 1
    del state["rung"][a.id]
    _write(a.state, state)
    print("%s ist wieder scharf." % a.id)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Kursalarme und die NYSE-Uhr (trader-alarm).")
    sub = ap.add_subparsers(dest="cmd")

    def net(p):
        p.add_argument("--source", default="yahoo", choices=["yahoo", "stooq"])
        p.add_argument("--offline", metavar="DIR", help="liest <source>-<TICKER>.json|csv-Dateien, kein Netz")
        p.add_argument("--now", help="tut so, als wäre es diese Zeit (ISO; ohne Offset = New Yorker Zeit)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("clock", help="ist die NYSE offen?")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("quote", help="neueste verzögerte Kurse")
    net(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true", help="Ticker aus Depot und Zielen dazunehmen")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--targets", default="trader/targets.json")
    p.add_argument("--write", nargs="?", const="trader/prices.json", metavar="PATH",
                   help="in prices.json speichern (Standard trader/prices.json)")
    p = sub.add_parser("check", help="jede Regel prüfen, läuten was getroffen wurde")
    net(p)
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--prices-out", default="trader/prices.json")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("add", help="eine Regel hinzufügen")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--symbol")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--above", type=float)
    g.add_argument("--below", type=float)
    g.add_argument("--stop", type=float)
    g.add_argument("--pct", type=float)
    g.add_argument("--opening-bell", action="store_true")
    p.add_argument("--direction", choices=["up", "down"])
    p.add_argument("--rearm", action="store_true")
    p.add_argument("--note")
    p.add_argument("--id")
    p = sub.add_parser("list", help="die Regeln auflisten")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p = sub.add_parser("reset", help="eine geläutete Regel wieder scharf stellen")
    p.add_argument("id")
    p.add_argument("--state", default="trader/state/alerts.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "quote": cmd_quote, "check": cmd_check, "add": cmd_add,
                "list": cmd_list, "reset": cmd_reset}[a.cmd](a)
    except AlertError as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
