#!/usr/bin/env python3
"""Nachrichten-Wächter für trader-nachrichten. Nur Standardbibliothek.

Beobachtet Schlagzeilen und SEC-Meldungen zu den Aktien, die du hältst (oder
kopieren willst), und zeigt nur, was seit der letzten Prüfung neu ist, neueste
zuerst, mit einer Markierung bei allem, was deine Positionen betrifft.

    python3 news.py                                  # Ticker aus trader/portfolio.json oder targets.json
    python3 news.py --tickers AAPL AXP GOOGL KO BAC
    python3 news.py --tickers AAPL --all --hours 72  # alles der letzten 3 Tage, gesehen oder nicht
    python3 news.py --no-sec                         # nur Schlagzeilen

Quellen, je eine Anfrage pro Ticker:
  - Yahoo-Finance-Schlagzeilen-RSS:
    https://feeds.finance.yahoo.com/rss/2.0/headline?s=<TICKER>&region=US&lang=en-US
  - SEC-EDGAR-Atom-Feed mit den Meldungen des Unternehmens (standardmäßig 8-K:
    Quartalszahlen, Führungswechsel, Übernahmen, Korrekturen):
    https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<CIK>&type=8-K&count=10&output=atom
    Die SEC verlangt einen User-Agent mit deinem Namen und deiner E-Mail
    (sec_user_agent in trader/config.json oder SEC_USER_AGENT). Ohne ihn
    wird der SEC-Teil übersprungen und das Tool sagt es; die Schlagzeilen
    laufen trotzdem.

Zustand: trader/state/news.json merkt sich, was du gesehen hast (30 Tage),
sodass jeder Eintrag nur einmal erscheint. Offline: --offline ORDNER liest
yahoo-<TICKER>.xml, sec-<FORM>-<CIK>.xml und company_tickers.json aus ORDNER.
Die Tests nutzen das.

Schlagzeilen sind Hinweise, keine Fakten: Öffne die Meldung, bevor du
handelst, und nimm nichts hier als Beratung.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US"
SEC_FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%s&type=%s"
            "&dateb=&owner=include&count=%d&output=atom")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
PLACEHOLDER = "you@example.com"
BROWSER_UA = "Mozilla/5.0 (compatible; trader-skill/1.0; +https://github.com/Jakeschincariol/trader-skill)"
KEEP_DAYS = 30

ITEMS_8K = {
    "1.01": "wesentliche Vereinbarung", "1.02": "Vereinbarung beendet", "1.03": "Insolvenz",
    "1.05": "Cybersicherheitsvorfall", "2.01": "Kauf oder Verkauf von Vermögenswerten",
    "2.02": "Quartalszahlen", "2.03": "neue Schulden", "2.04": "Schulden vorzeitig fällig",
    "2.05": "Restrukturierungskosten", "2.06": "Wertminderung", "3.01": "Delisting-Mitteilung",
    "3.02": "nicht registrierter Aktienverkauf", "3.03": "Änderung der Aktionärsrechte",
    "4.01": "Wechsel des Abschlussprüfers", "4.02": "frühere Abschlüsse nicht mehr verlässlich",
    "5.01": "Wechsel der Kontrolle", "5.02": "Wechsel bei Vorstand oder Aufsichtsrat",
    "5.03": "Änderung von Satzung oder Geschäftsjahr", "5.07": "Ergebnisse der Aktionärsabstimmung",
    "7.01": "Regulation-FD-Offenlegung", "8.01": "sonstige Ereignisse", "9.01": "Anlagen",
}
IMPORTANT_ITEMS = {"1.03", "1.05", "2.02", "2.04", "2.06", "3.01", "4.02", "5.01", "5.02"}
KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bEPS\b|\bguidance\b|\bforecast\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|\boutperform\b|\bunderperform\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bSEC charges\b|\bantitrust\b|\bjudge\b|\bcourt\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bbuyout\b|\btakeover\b|\bdeal\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\brepurchase\b|\bstock split\b|\boffering\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\brecall\b|\bbankrupt|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- Eingaben

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", targets="trader/targets.json"):
    """{Ticker: Name oder None} aus deinen Beständen, sonst aus deinen Zielen."""
    out = {}
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        if p.get("ticker"):
            out[p["ticker"].upper()] = p.get("name")
    if not out:
        tg = load_json(targets) or {}
        for w in tg.get("weights", []):
            if w.get("ticker"):
                out[w["ticker"].upper()] = w.get("name")
    return out


def sec_user_agent(cli=None, config="trader/config.json"):
    ua = (cli or os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua:
        cfg = load_json(config) or {}
        ua = str(cfg.get("sec_user_agent") or "").strip()
    if not ua or "@" not in ua or PLACEHOLDER in ua:
        return None
    return ua


# ---------------------------------------------------------------- Abrufen

class Fetcher(object):
    def __init__(self, sec_ua=None, offline=None, pause=0.15):
        self.sec_ua = sec_ua
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name, sec=False):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: keine Datei %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        if sec and not self.sec_ua:
            raise NewsError("kein SEC-User-Agent gesetzt")
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        headers = {"User-Agent": self.sec_ua if sec else BROWSER_UA, "Accept-Encoding": "gzip"}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s für %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("%s nicht erreichbar: %s" % (url, exc.reason))


def yahoo_symbol(ticker):
    return ticker.upper().replace(".", "-")


def cik_map(fetcher, state):
    """Ticker -> 10-stellige CIK, aus company_tickers.json der SEC (im Zustand zwischengespeichert)."""
    cached = state.setdefault("ciks", {})
    if cached:
        return cached
    data = json.loads(fetcher.get(TICKERS_URL, "company_tickers.json", sec=True).decode("utf-8"))
    for v in data.values():
        cached.setdefault(v["ticker"].upper().replace("-", "."), str(v["cik_str"]).zfill(10))
    return cached


# ---------------------------------------------------------------- Auswerten

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS) oder ISO 8601 (Atom) -> UTC-datetime mit Zeitzone, oder None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_rss(data, ticker):
    """Yahoo-Schlagzeilen-RSS -> Einträge."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS für %s ist keine gültige XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "rss:" + guid, "source": "Yahoo Finance", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_sec_atom(data, ticker):
    """EDGAR-Atom mit Unternehmensmeldungen -> Einträge."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("SEC-Feed für %s ist keine gültige XML: %s" % (ticker, exc))
    company = ""
    for el in root.iter():
        if _local(el.tag) == "conformed-name":
            company = (el.text or "").strip()
            break
    out = []
    for entry in root.iter():
        if _local(entry.tag) != "entry":
            continue
        form = _text(entry, "filing-type")
        acc = _text(entry, "accession-number")
        items = re.findall(r"\d+\.\d+", _text(entry, "items-desc"))
        labels = [ITEMS_8K.get(i, "Punkt " + i) for i in items if i != "9.01"]
        link = ""
        for c in entry.iter():
            if _local(c.tag) == "link":
                link = c.get("href", "")
        title = "%s hat ein %s eingereicht" % (company or ticker, form)
        if labels:
            title += ": " + ", ".join(labels)
        out.append({"id": "sec:" + acc, "source": "SEC EDGAR", "kind": "filing", "form": form,
                    "ticker": ticker, "title": title, "summary": _text(entry, "form-name"),
                    "items": items, "important": bool(set(items) & IMPORTANT_ITEMS),
                    "link": link, "time": parse_time(_text(entry, "updated"))})
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        alts = [r"\(%s\)" % re.escape(t), r"\$%s\b" % re.escape(t),
                r"\b(?:NYSE|NASDAQ|Nasdaq)\s*:\s*%s\b" % re.escape(t)]
        if name and len(name) >= 4:
            alts.append(r"\b%s\b" % re.escape(name).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch):
    """Welche deiner Ticker jeder Eintrag berührt, und warum er wichtig ist."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        if it["kind"] == "filing" and "2.02" in it.get("items", []) and "earnings" not in it["tags"]:
            it["tags"].insert(0, "earnings")
    return items


def dedupe(items):
    """Ein Eintrag pro Meldung: gleiche ID, gleicher Link oder gleicher Titel am selben Tag."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- die Prüfung

def collect(fetcher, watch, state, sec=True, forms=("8-K",), count=10, errors=None):
    errors = [] if errors is None else errors
    items = []
    for t in sorted(watch):
        try:
            items += parse_rss(fetcher.get(YAHOO % yahoo_symbol(t), "yahoo-%s.xml" % t), t)
        except NewsError as exc:
            errors.append("Schlagzeilen für %s: %s" % (t, exc))
    if sec:
        try:
            ciks = cik_map(fetcher, state)
        except NewsError as exc:
            errors.append("SEC-Tickerliste: %s" % exc)
            ciks = {}
        for t in sorted(watch):
            cik = ciks.get(t)
            if not cik:
                if ciks:
                    errors.append("keine SEC-CIK für %s" % t)
                continue
            for form in forms:
                try:
                    data = fetcher.get(SEC_FEED % (cik, form, count),
                                       "sec-%s-%s.xml" % (form, cik), sec=True)
                    items += parse_sec_atom(data, t)
                except NewsError as exc:
                    errors.append("SEC %s für %s: %s" % (form, t, exc))
    return items


def select(items, state, now, hours=48, show_all=False):
    """Einträge im Zeitfenster; nur ungesehene, außer bei show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    out = []
    for it in items:
        if it["time"] is not None and it["time"] < cutoff:
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render(items, errors, now):
    out = []
    if not items:
        out.append("Nichts Neues.")
    for it in items:
        when = it["time"].strftime("%Y-%m-%d %H:%M UTC") if it["time"] else "Zeit unbekannt"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-11s %-13s %s" % (mark, when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("Tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("Hinweis: %s" % e)
    out.append("Geprüft %s. Schlagzeilen sind Hinweise, keine Fakten; öffne die Meldung, bevor du handelst. "
               "Keine Anlageberatung." % now.strftime("%Y-%m-%d %H:%M UTC"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="Standard: trader/portfolio.json, sonst targets.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--targets", default="trader/targets.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="Einträge ignorieren, die älter sind")
    ap.add_argument("--all", action="store_true", help="auch gesehene Einträge zeigen")
    ap.add_argument("--forms", default="8-K", help="zu beobachtende SEC-Formulare, durch Komma getrennt")
    ap.add_argument("--no-sec", action="store_true", help="nur Schlagzeilen")
    ap.add_argument("--user-agent", help="SEC-User-Agent: \"Dein Name you@example.com\"")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="ISO-Zeit, für reproduzierbare Läufe")
    ap.add_argument("--dry-run", action="store_true", help="Zustandsdatei nicht aktualisieren")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.tickers:
        watch = {t.upper(): None for t in args.tickers}
        known = watch_from_files(args.portfolio, args.targets)
        watch.update({t: known[t] for t in watch if known.get(t)})
    else:
        watch = watch_from_files(args.portfolio, args.targets)
    if not watch:
        print("news: keine Ticker. Gib --tickers an oder lege zuerst trader/portfolio.json oder "
              "trader/targets.json an (/trader-kopie schreibt die Ziele).", file=sys.stderr)
        return 2
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    state = load_json(args.state) or {}
    errors = []
    ua = sec_user_agent(args.user_agent, args.config)
    sec = not args.no_sec
    if sec and not ua and not args.offline:
        errors.append("SEC-Meldungen übersprungen: Setze sec_user_agent in trader/config.json oder "
                      "SEC_USER_AGENT (\"Dein Name you@example.com\"), um sie einzubeziehen")
        sec = False
    fetcher = Fetcher(ua, offline=args.offline)
    forms = [f.strip().upper() for f in args.forms.split(",") if f.strip()]
    items = collect(fetcher, watch, state, sec=sec, forms=forms, errors=errors)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    if args.json:
        print(json.dumps({"checked": now.isoformat(), "items": _json_ready(shown),
                          "notes": errors}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
