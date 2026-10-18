#!/usr/bin/env python3
"""13F-Leser für trader-kopie. Nur Standardbibliothek.

Liest die öffentlichen 13F-HR-Meldungen eines Investors auf SEC EDGAR und baut
das Depot als Gewichte nach, die du kopieren kannst.

    python3 edgar13f.py find buffett                  # mitgelieferte Liste bekannter Einreicher
    python3 edgar13f.py find "scion asset" --sec      # alle EDGAR-Einreicher nach Namen durchsuchen
    python3 edgar13f.py filings 1067983               # seine 13F-HR-Meldungen, neueste zuerst
    python3 edgar13f.py holdings 1067983 --combine    # letztes Quartal, Aktiengattungen addiert
    python3 edgar13f.py diff 1067983                  # neu / verkauft / aufgestockt / reduziert
    python3 edgar13f.py copy 1067983 --top 5 --out trader/targets.json

    python3 edgar13f.py holdings --xml infotable.xml  # eine Datei, die du selbst heruntergeladen hast

Jeder Aufruf bei der SEC trägt einen User-Agent mit deinem Namen und deiner
E-Mail, so wie die SEC es verlangt (https://www.sec.gov/os/accessing-edgar-data).
Das Werkzeug liest ihn aus --user-agent, dann aus der Umgebungsvariable
SEC_USER_AGENT, dann aus "sec_user_agent" in trader/config.json, und ruft die
SEC nicht auf, wenn keiner da ist. Zwischen den Aufrufen wartet es, damit es
weit unter den 10 Anfragen pro Sekunde der SEC bleibt.

Was eine 13F ist und was nicht:
  - Sie wird einmal im Quartal eingereicht, bis zu 45 Tage nach Quartalsende.
    Der Bestand ist eine Momentaufnahme vom letzten Tag des Quartals und kann
    beim Lesen schon viereinhalb Monate alt sein.
  - Sie listet Long-Positionen in US-börsennotierten Aktien (und einige
    Optionen und Wandelanleihen). Sie zeigt keine Shorts, kein Bargeld, keine
    Anleihen, keine ausländischen Aktien, keine privaten Firmen und nichts,
    was innerhalb des Quartals gekauft und verkauft wurde.
  - Sie wird von der Firma eingereicht, nicht von der Person, für die die
    Firma bekannt ist.

Werte: Meldungen ab 2023-01-03 geben den Wert in Dollar an, ältere in
Tausend. Das Werkzeug rechnet ältere Meldungen in Dollar um.

Gewichte: pct_of_13f ist der Wert jeder Position geteilt durch den Gesamtwert
der ganzen Informationstabelle. "copy" behält die Aktienpositionen, die du
wählst, und skaliert ihr pct_of_13f (auf 2 Nachkommastellen gerundet, die
Zahlen aus der Holdings-Tabelle) auf Summe 100. Optionszeilen (PUT/CALL) und
Anleihezeilen (PRN) werden nie kopiert: Sie werden als ausgeschlossen
aufgeführt.

Offline: --offline DIR liest jede URL aus Dateien in DIR statt aus dem Netz
(Archives-Dateien als DIR/<Accession-Ordner>/<Dateiname>, alles andere als
DIR/<Dateiname>). Die Tests nutzen das.
"""

import argparse
import datetime as _dt
import gzip
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
FILERS = os.path.join(HERE, "filers.json")
CLASSES = os.path.join(HERE, "share_classes.json")

SUBMISSIONS = "https://data.sec.gov/submissions/CIK%s.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/%s/%s/"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index?keysTyped=%s"
FIGI_URL = "https://api.openfigi.com/v3/mapping"
PLACEHOLDER = "you@example.com"
DOLLARS_FROM = _dt.date(2023, 1, 3)
FORMS = ("13F-HR", "13F-HR/A")

UA_HELP = (
    "Die SEC verlangt bei jeder Anfrage einen User-Agent mit deinem Namen und deiner E-Mail "
    "(https://www.sec.gov/os/accessing-edgar-data). Richte ihn einmal ein, auf einem dieser Wege:\n"
    "  1. trage  \"sec_user_agent\": \"Dein Name you@example.com\"  in trader/config.json ein "
    "(mit DEINEM Namen und DEINER E-Mail, you@example.com ist nur ein Platzhalter)\n"
    "  2. export SEC_USER_AGENT=\"Dein Name you@example.com\"\n"
    "  3. gib --user-agent \"Dein Name you@example.com\" mit")


class EdgarError(Exception):
    pass


# ---------------------------------------------------------------- Abrufen

def user_agent(cli=None, config_path="trader/config.json"):
    """Der SEC-User-Agent aus dem Flag, der Umgebung oder der Konfiguration, sonst None."""
    ua = (cli or os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua and config_path and os.path.isfile(config_path):
        try:
            with open(config_path, encoding="utf-8") as fh:
                ua = str(json.load(fh).get("sec_user_agent") or "").strip()
        except (OSError, ValueError):
            ua = ""
    if not ua or "@" not in ua or PLACEHOLDER in ua:
        return None
    return ua


class Fetcher(object):
    """GET und POST mit User-Agent, gzip und einer Pause zwischen den Aufrufen."""

    def __init__(self, ua=None, offline=None, pause=0.15):
        self.ua = ua
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def local_path(self, url):
        parsed = urllib.parse.urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if parsed.netloc == "efts.sec.gov":
            q = urllib.parse.parse_qs(parsed.query).get("keysTyped", [""])[0]
            name = "search-%s.json" % re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")
        elif "Archives" in parts and len(parts) >= 2:
            name = os.path.join(parts[-2], parts[-1])
        else:
            name = parts[-1] if parts else "index"
        return os.path.join(self.offline, name)

    def _wait(self):
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()

    def get(self, url, sec=True):
        if self.offline:
            path = self.local_path(url)
            if not os.path.isfile(path):
                raise EdgarError("offline: keine Datei %s für %s" % (path, url))
            with open(path, "rb") as fh:
                return fh.read()
        if sec and not self.ua:
            raise EdgarError(UA_HELP)
        headers = {"User-Agent": self.ua or "trader-skill/1.0",
                   "Accept-Encoding": "gzip"}
        return self._open(urllib.request.Request(url, headers=headers), url)

    def post_json(self, url, payload, headers=None):
        if self.offline:
            path = os.path.join(self.offline, "openfigi.json")
            if not os.path.isfile(path):
                raise EdgarError("offline: keine Datei %s für %s" % (path, url))
            with open(path, "rb") as fh:
                return json.loads(fh.read().decode("utf-8"))
        h = {"Content-Type": "application/json", "User-Agent": "trader-skill/1.0"}
        h.update(headers or {})
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=h, method="POST")
        return json.loads(self._open(req, url).decode("utf-8"))

    def _open(self, req, url):
        self._wait()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            if exc.code == 403 and "sec.gov" in url:
                raise EdgarError("Die SEC antwortete 403 Forbidden für %s. Meist fehlt der "
                                 "User-Agent oder er ist zu allgemein, oder es waren zu viele Anfragen. %s"
                                 % (url, UA_HELP))
            raise EdgarError("HTTP %s für %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise EdgarError("%s nicht erreichbar: %s" % (url, exc.reason))

    def get_json(self, url, sec=True):
        try:
            return json.loads(self.get(url, sec=sec).decode("utf-8"))
        except ValueError as exc:
            raise EdgarError("%s hat kein JSON geliefert: %s" % (url, exc))


# ---------------------------------------------------------------- Einreicher

def cik10(value):
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        raise EdgarError("keine CIK: %r" % value)
    return digits.zfill(10)


def load_filers(path=FILERS):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["filers"]


def find_bundled(query, filers=None):
    """Mitgelieferte Einreicher, deren Name, Person oder Alias jedes Wort der Suche enthält."""
    filers = load_filers() if filers is None else filers
    q = query.lower().strip()
    if re.fullmatch(r"\d{1,10}", q):
        return [f for f in filers if f["cik"] == cik10(q)]
    words = q.split()
    hits = []
    for f in filers:
        hay = " ".join([f["name"], f.get("person", "")] + f.get("aliases", [])).lower()
        if all(w in hay for w in words):
            hits.append(f)
    return hits


def search_sec(fetcher, query):
    """Jeder EDGAR-Einreicher, dessen Name passt (das Firmensuchfeld von EDGAR)."""
    data = fetcher.get_json(SEARCH_URL % urllib.parse.quote(query))
    out = []
    for hit in data.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        out.append({"cik": cik10(hit.get("_id", "0")), "name": src.get("entity", "")})
    return out


# ---------------------------------------------------------------- Meldungen

def list_filings(submissions, forms=FORMS):
    """13F-Meldungen aus einem Submissions-JSON, neueste Meldung zuerst."""
    rec = submissions.get("filings", {}).get("recent", {})
    out = []
    for i, form in enumerate(rec.get("form", [])):
        if form not in forms:
            continue
        out.append({
            "form": form,
            "accession": rec["accessionNumber"][i],
            "filed": rec["filingDate"][i],
            "period": rec.get("reportDate", [""] * (i + 1))[i],
            "primary": rec.get("primaryDocument", [""] * (i + 1))[i],
        })
    out.sort(key=lambda f: (f["filed"], f["accession"]), reverse=True)
    return out


def pick_filing(filings, period=None, accession=None, before=None):
    """Die ursprüngliche 13F-HR für einen Zeitraum (Standard: die neueste), plus etwaige Änderungsmeldungen."""
    if accession:
        hit = [f for f in filings if f["accession"] == accession]
        if not hit:
            raise EdgarError("Accession %s steht nicht in der 13F-Liste dieses Einreichers" % accession)
        chosen = hit[0]
    else:
        originals = [f for f in filings if f["form"] == "13F-HR"]
        if period:
            originals = [f for f in originals if f["period"] == period]
        if before:
            originals = [f for f in originals if f["period"] < before]
        if not originals:
            raise EdgarError("keine 13F-HR gefunden%s" % (" für den Zeitraum %s" % period if period else ""))
        originals.sort(key=lambda f: (f["period"], f["filed"]), reverse=True)
        chosen = originals[0]
    amendments = [f for f in filings if f["form"] == "13F-HR/A"
                  and f["period"] == chosen["period"] and f is not chosen]
    return chosen, amendments


def archive_url(cik, accession):
    return ARCHIVE % (int(cik10(cik)), accession.replace("-", ""))


def info_table_name(index_json):
    """Die XML der Informationstabelle im Ordner einer Meldung (index.json)."""
    names = [it["name"] for it in index_json.get("directory", {}).get("item", [])]
    xmls = [n for n in names if n.lower().endswith(".xml")
            and n.lower() not in ("primary_doc.xml", "filingsummary.xml")]
    if not xmls:
        raise EdgarError("keine XML der Informationstabelle in dieser Meldung (Dateien: %s). Meldungen "
                         "vor Mitte 2013 sind reiner Text und werden nicht unterstützt."
                         % ", ".join(names))
    for n in xmls:
        if re.search(r"info|table|13f", n, re.I):
            return n
    return xmls[0]


def value_multiplier(filed):
    """1 für Dollar (eingereicht am oder nach 2023-01-03), 1000 für ältere Meldungen."""
    try:
        d = _dt.datetime.strptime(filed[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return 1
    return 1 if d >= DOLLARS_FROM else 1000


# ---------------------------------------------------------------- Einlesen

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_info_table(data, multiplier=1):
    """Zeilen einer 13F-Informationstabelle (XML) als Dicts. Werte in Dollar."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise EdgarError("Informationstabelle ist kein gültiges XML: %s" % exc)
    rows = []
    for el in root.iter():
        if _local(el.tag) != "infoTable":
            continue
        f = {}
        for child in el.iter():
            name = _local(child.tag)
            if name not in f and child.text and child.text.strip():
                f[name] = child.text.strip()
        try:
            value = float(f.get("value", "0").replace(",", "")) * multiplier
            shares = float(f.get("sshPrnamt", "0").replace(",", ""))
        except ValueError:
            raise EdgarError("fehlerhafte Zahl in der Zeile für %s" % f.get("nameOfIssuer"))
        if not math.isfinite(value) or not math.isfinite(shares) or value < 0 or shares < 0:
            raise EdgarError("ungültiger Wert oder Bestand für %s" % f.get("nameOfIssuer"))
        if not f.get("cusip"):
            raise EdgarError("CUSIP fehlt für %s; Position nicht eindeutig zuordenbar" % f.get("nameOfIssuer"))
        rows.append({
            "issuer": f.get("nameOfIssuer", ""),
            "title": f.get("titleOfClass", ""),
            "cusip": f.get("cusip", "").upper(),
            "figi": f.get("figi", ""),
            "value": value,
            "shares": shares,
            "share_type": f.get("sshPrnamtType", "SH").upper(),
            "put_call": f.get("putCall", "").upper(),
            "manager": f.get("otherManager", ""),
        })
    if not rows:
        raise EdgarError("keine infoTable-Zeilen gefunden. Ist das eine 13F-Informationstabelle?")
    return rows


def parse_cover(data):
    """tableValueTotal, tableEntryTotal und die Deckblatt-Angaben aus primary_doc.xml."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    want = {"tableValueTotal", "tableEntryTotal", "periodOfReport", "isAmendment",
            "amendmentType", "reportType", "otherIncludedManagersCount", "name"}
    out = {}
    for el in root.iter():
        name = _local(el.tag)
        if name in want and name not in out and el.text and el.text.strip():
            out[name] = el.text.strip()
    return out


def is_stock(p):
    return p["share_type"] == "SH" and not p["put_call"]


def aggregate(rows):
    """Eine Position je CUSIP (und je PUT/CALL), die auf Manager aufgeteilten Zeilen summiert."""
    total = sum(r["value"] for r in rows)
    groups = {}
    order = []
    for r in rows:
        key = (r["cusip"], r["put_call"], r["share_type"])
        if key not in groups:
            groups[key] = {"issuer": r["issuer"], "title": r["title"], "cusip": r["cusip"],
                           "figi": r["figi"], "put_call": r["put_call"],
                           "share_type": r["share_type"], "value": 0.0, "shares": 0.0,
                           "rows": 0}
            order.append(key)
        g = groups[key]
        g["value"] += r["value"]
        g["shares"] += r["shares"]
        g["rows"] += 1
    positions = [groups[k] for k in order]
    for p in positions:
        p["pct_of_13f"] = round(100.0 * p["value"] / total, 2) if total else 0.0
        p["cusips"] = [p["cusip"]]
    positions.sort(key=lambda p: (-p["value"], p["cusip"]))
    return positions, total


def combine_classes(positions, total):
    """Addiert ausdrücklich bezeichnete Aktienklassen eines Emittenten.

    Ein CUSIP-Emittentencode allein genügt nicht: ETF-Produkte eines Trusts
    dürfen nicht als verschiedene Klassen derselben Aktie verschmolzen werden.

    Nur Aktienzeilen werden zusammengefasst. Die zusammengefasste Position behält
    Ticker und Namen ihrer größten Gattung und listet jede CUSIP und jeden Ticker, die sie hält."""
    out, by_issuer = [], {}
    for p in positions:
        if not is_stock(p) or not class_letter(p["title"]):
            out.append(dict(p))
            continue
        key = p["cusip"][:6]
        if key not in by_issuer:
            q = dict(p)
            q["cusips"] = [p["cusip"]]
            q["tickers"] = [p.get("ticker")] if p.get("ticker") else []
            q["classes"] = [p["title"]]
            by_issuer[key] = q
            out.append(q)
            continue
        q = by_issuer[key]
        q["value"] += p["value"]
        q["shares"] += p["shares"]
        q["rows"] += p["rows"]
        q["cusips"].append(p["cusip"])
        q["classes"].append(p["title"])
        if p.get("ticker"):
            q["tickers"].append(p["ticker"])
    for q in out:
        q["pct_of_13f"] = round(100.0 * q["value"] / total, 2) if total else 0.0
    out.sort(key=lambda p: (-p["value"], p["cusip"]))
    return out


# ---------------------------------------------------------------- Ticker

SUFFIX_STATE = re.compile(r"\s*/\s*[A-Z]{2,5}\s*/?\s*$")
ABBREV = {
    "AMER": "AMERICA", "AMERN": "AMERICAN", "FINL": "FINANCIAL", "FIN": "FINANCIAL",
    "PETE": "PETROLEUM", "HLDGS": "HOLDINGS", "HLDG": "HOLDING", "HOLDG": "HOLDING",
    "INTL": "INTERNATIONAL", "GRP": "GROUP", "SVCS": "SERVICES", "SVC": "SERVICE",
    "SYS": "SYSTEMS", "MGMT": "MANAGEMENT", "INDS": "INDUSTRIES", "NATL": "NATIONAL",
    "ENTMT": "ENTERTAINMENT", "PAC": "PACIFIC", "LABS": "LABORATORIES",
    "PPTYS": "PROPERTIES", "RLTY": "REALTY", "TECHS": "TECHNOLOGIES", "TECH": "TECHNOLOGY",
    "COMMNS": "COMMUNICATIONS", "COMMUNICATN": "COMMUNICATIONS", "MFG": "MANUFACTURING",
    "ELEC": "ELECTRIC", "PHARMACEUTICALS": "PHARMACEUTICALS", "PHARMS": "PHARMACEUTICALS",
    "BANCORPORATION": "BANCORP", "RES": "RESOURCES", "SOLUTIONS": "SOLUTIONS",
    "WTR": "WATER", "MTRS": "MOTORS", "AIRLS": "AIRLINES", "CHEM": "CHEMICAL",
}
DROP = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED",
        "PLC", "LLC", "LP", "SA", "NV", "AG", "SE", "THE", "AND", "OF"}
NONCOMMON = re.compile(r"-P[A-Z]*$|-WT[A-Z]*$|-WS$|-U$|-UN$|-R$|-RT$")


def norm_name(name):
    s = (name or "").upper().replace("&", " AND ")
    for _ in range(2):
        s = SUFFIX_STATE.sub("", s)
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    words = [ABBREV.get(w, w) for w in s.split()]
    return [w for w in words if w not in DROP]


def name_keys(name):
    words = norm_name(name)
    return "".join(words), " ".join(sorted(words))


def norm_ticker(t):
    t = (t or "").strip().upper()
    return re.sub(r"[-/ ]([A-Z]{1,2})$", r".\1", t) if not NONCOMMON.search(t) else t


def class_letter(title):
    m = re.search(r"\b(?:CL|CLASS|SER|SERIES)\s*([A-Z])\b", (title or "").upper())
    return m.group(1) if m else None


def load_company_tickers(data):
    """company_tickers.json -> geordnete Liste von (cik10, ticker, title)."""
    rows = []
    items = data.values() if isinstance(data, dict) else data
    for v in items:
        rows.append((cik10(v["cik_str"]), v["ticker"], v["title"]))
    return rows


class TickerIndex(object):
    """Namenssuche über die company_tickers.json der SEC."""

    def __init__(self, rows):
        self.by_cik = {}
        self.titles = {}
        self.spaceless = {}
        self.sorted_words = {}
        for cik, ticker, title in rows:
            self.by_cik.setdefault(cik, []).append(ticker)
            if cik in self.titles:
                continue
            self.titles[cik] = title
            a, b = name_keys(title)
            if a:
                self.spaceless.setdefault(a, set()).add(cik)
                self.sorted_words.setdefault(b, set()).add(cik)

    def match_cik(self, issuer):
        a, b = name_keys(issuer)
        if not a:
            return None, None
        for hits, how in ((self.spaceless.get(a), "name"), (self.sorted_words.get(b), "name")):
            if hits and len(hits) == 1:
                return next(iter(hits)), how
        # Die 13F kürzt oder verziert Namen ("JEFFERIES FINANCIAL GROUP IN").
        best = []
        for key, ciks in self.spaceless.items():
            if len(key) >= 8 and len(ciks) == 1 and (a.startswith(key) or key.startswith(a)):
                best.append((len(key), next(iter(ciks))))
        if best:
            best.sort(reverse=True)
            if len(best) == 1 or best[0][0] > best[1][0]:
                return best[0][1], "name-prefix"
        return None, None

    def ticker_for(self, cik, title_of_class):
        """(ticker, needs_check) für eine Firma und einen titleOfClass aus der 13F."""
        tickers = self.by_cik.get(cik, [])
        cands = [t for t in tickers if not NONCOMMON.search(t)] or tickers
        if not cands:
            return None, True
        if len(cands) == 1:
            return norm_ticker(cands[0]), False
        letter = class_letter(title_of_class)
        if letter:
            for t in cands:
                if t.upper().endswith(("-" + letter, "." + letter, "/" + letter)):
                    return norm_ticker(t), False
            for t in cands:
                if len(t) >= 4 and t.upper().endswith(letter):
                    return norm_ticker(t), True
        return norm_ticker(cands[0]), True


def load_overrides(paths):
    out = {}
    for path in paths:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            table = data.get("cusips", data) if isinstance(data, dict) else {}
            for k, v in table.items():
                if re.fullmatch(r"[0-9A-Z]{9}", str(k).upper()):
                    out[str(k).upper()] = norm_ticker(v)
    return out


def figi_lookup(fetcher, cusips, api_key=None, batch=None, pause=2.5):
    """CUSIP -> Ticker über OpenFIGI (US composite). Kostenlos, kein Schlüssel nötig:
    10 CUSIPs pro Anfrage und 25 Anfragen pro Minute ohne Schlüssel."""
    batch = batch or (100 if api_key else 10)
    headers = {"X-OPENFIGI-APIKEY": api_key} if api_key else {}
    out = {}
    cusips = list(dict.fromkeys(cusips))
    for i in range(0, len(cusips), batch):
        chunk = cusips[i:i + batch]
        jobs = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in chunk]
        if i and not fetcher.offline:
            time.sleep(pause)
        answer = fetcher.post_json(FIGI_URL, jobs, headers=headers)
        out.update(parse_figi(chunk, answer))
    return out


def parse_figi(cusips, answer):
    out = {}
    for c, res in zip(cusips, answer):
        data = (res or {}).get("data") or []
        stocks = [d for d in data if d.get("marketSector") == "Equity"] or data
        if stocks and stocks[0].get("ticker"):
            out[c] = norm_ticker(stocks[0]["ticker"])
    return out


def pretty_name(issuer, sec_title=None):
    name = sec_title if sec_title and re.search(r"[a-z]", sec_title) else (sec_title or issuer)
    name = SUFFIX_STATE.sub("", name or "")
    name = re.sub(r",?\s+(Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Company|Ltd\.?|"
                  r"Limited|plc|PLC|LLC|L\.P\.|N\.V\.|S\.A\.)$", "", name.strip(), flags=re.I)
    name = re.sub(r",?\s+(INC|CORP|CO|LTD|PLC)$", "", name.strip())
    if name.isupper():
        small = {"OF", "AND", "THE", "FOR", "IN"}
        words = name.lower().split()
        name = " ".join(w if (w.upper() in small and i) else w.capitalize()
                        for i, w in enumerate(words))
    return name.strip()


def resolve_tickers(positions, index=None, overrides=None, figi=None):
    """Gibt jeder Position einen Ticker, die Herkunft und ob er geprüft werden soll."""
    overrides = overrides or {}
    figi = figi or {}
    for p in positions:
        c = p["cusip"]
        sec_title = None
        cik = None
        if index is not None:
            cik, how = index.match_cik(p["issuer"])
            if cik:
                sec_title = index.titles.get(cik)
                p["issuer_cik"] = cik
        if c in overrides:
            p["ticker"], p["ticker_source"], p["ticker_check"] = overrides[c], "override", False
        elif c in figi:
            p["ticker"], p["ticker_source"], p["ticker_check"] = figi[c], "openfigi", False
        elif cik:
            t, check = index.ticker_for(cik, p["title"])
            p["ticker"], p["ticker_source"] = t, how
            p["ticker_check"] = bool(check or how == "name-prefix")
        else:
            p["ticker"], p["ticker_source"], p["ticker_check"] = None, "none", True
        p["name"] = pretty_name(p["issuer"], sec_title)
    return positions


# ---------------------------------------------------------------- Auswertung

def diff(current, prior):
    """Zwei Quartale per CUSIP vergleichen: NEW, SOLD OUT, ADDED, TRIMMED, UNCHANGED."""
    before = {(p["cusip"], p["put_call"]): p for p in prior}
    now = {(p["cusip"], p["put_call"]): p for p in current}
    out = []
    for key, p in now.items():
        q = before.get(key)
        if q is None:
            status, change = "NEW", p["shares"]
        elif p["shares"] > q["shares"]:
            status, change = "ADDED", p["shares"] - q["shares"]
        elif p["shares"] < q["shares"]:
            status, change = "TRIMMED", p["shares"] - q["shares"]
        else:
            status, change = "UNCHANGED", 0.0
        out.append({"status": status, "issuer": p["issuer"], "title": p["title"],
                    "cusip": p["cusip"], "ticker": p.get("ticker"), "put_call": p["put_call"],
                    "shares_before": q["shares"] if q else 0.0, "shares_after": p["shares"],
                    "change": change,
                    "change_pct": (round(100.0 * change / q["shares"], 1)
                                   if q and q["shares"] else None),
                    "value_after": p["value"], "value_before": q["value"] if q else 0.0})
    for key, q in before.items():
        if key not in now:
            out.append({"status": "SOLD OUT", "issuer": q["issuer"], "title": q["title"],
                        "cusip": q["cusip"], "ticker": q.get("ticker"),
                        "put_call": q["put_call"], "shares_before": q["shares"],
                        "shares_after": 0.0, "change": -q["shares"], "change_pct": -100.0,
                        "value_after": 0.0, "value_before": q["value"]})
    rank = {"NEW": 0, "SOLD OUT": 1, "ADDED": 2, "TRIMMED": 3, "UNCHANGED": 4}
    out.sort(key=lambda d: (rank[d["status"]], -max(d["value_after"], d["value_before"])))
    return out


def copy_weights(positions, top=None, min_pct=0.0, exclude=(), need_ticker=True):
    """Die zu kopierenden Aktienpositionen wählen und auf 100 skalieren.

    Gibt (weights, skipped) zurück. Die Gewichte sind pct_of_13f, auf Summe 100
    skaliert und auf 2 Nachkommastellen gerundet."""
    exclude = {e.upper() for e in exclude}
    chosen, skipped = [], []
    for p in positions:
        why = None
        if not is_stock(p):
            why = "%s-Zeile, keine Aktie, die du kopieren kannst" % (p["put_call"] or p["share_type"])
        elif need_ticker and not p.get("ticker"):
            why = "kein Ticker gefunden (trage ihn in trader/tickers.json ein oder nutze --figi)"
        elif p.get("ticker") and p["ticker"].upper() in exclude:
            why = "von dir ausgeschlossen"
        elif p["pct_of_13f"] < min_pct:
            why = "unter --min-pct %.2f" % min_pct
        if why:
            skipped.append({"issuer": p["issuer"], "ticker": p.get("ticker"), "why": why})
            continue
        chosen.append(p)
    if top:
        for p in chosen[top:]:
            skipped.append({"issuer": p["issuer"], "ticker": p.get("ticker"),
                            "why": "außerhalb der Top %d" % top})
        chosen = chosen[:top]
    base = sum(p["pct_of_13f"] for p in chosen)
    if not chosen or base <= 0:
        raise EdgarError("nichts mehr zu kopieren")
    weights = []
    for p in chosen:
        w = {"ticker": p["ticker"], "name": p.get("name") or p["issuer"],
             "weight": round(100.0 * p["pct_of_13f"] / base, 2),
             "pct_of_13f": p["pct_of_13f"], "value": p["value"], "shares": p["shares"],
             "cusips": p.get("cusips", [p["cusip"]])}
        if p.get("tickers") and len(p["tickers"]) > 1:
            w["classes"] = p["tickers"]
        if p.get("ticker_check"):
            w["check_ticker"] = True
        weights.append(w)
    return weights, skipped


CAVEATS = [
    "Eine 13F ist eine Momentaufnahme vom letzten Tag des Quartals, eingereicht bis zu "
    "45 Tage später. Der Investor kann seitdem verkauft oder zugekauft haben.",
    "Sie zeigt nur Long-Positionen in US-börsennotierten Aktien: keine Shorts, kein Bargeld, "
    "keine Anleihen, keine ausländischen Aktien, keine privaten Firmen und keine Geschäfte "
    "innerhalb des Quartals.",
    "Sie wird von der Firma eingereicht, nicht von der Person, für die die Firma bekannt ist.",
    "Bildung und Werkzeug. Keine Anlageberatung. Nichts hier schickt einen Auftrag ab.",
]


# ---------------------------------------------------------------- Ausgabe

def _money(v):
    return "${:,.0f}".format(v)


def _shares(v):
    return "{:,.0f}".format(v) if float(v).is_integer() else "{:,.2f}".format(v)


def render_holdings(meta, positions, total, top=None, combined=False):
    out = []
    out.append("%s  13F-HR für das Quartal bis %s, eingereicht %s (Accession %s)" % (
        meta.get("filer", "?"), meta.get("period", "?"), meta.get("filed", "?"),
        meta.get("accession", "?")))
    out.append("Gesamt %s in %d Positionen%s. Die Gewichte sind %% dieser Summe." % (
        _money(total), len(positions), ", Aktiengattungen zusammengefasst" if combined else ""))
    if meta.get("total_check"):
        out.append(meta["total_check"])
    out.append("")
    out.append("%3s  %-11s %-32s %16s %18s %7s" % ("#", "Ticker", "Emittent", "Aktien", "Wert", "%"))
    shown = positions[:top] if top else positions
    for i, p in enumerate(shown, 1):
        tick = p.get("ticker") or "?"
        if p.get("tickers") and len(p["tickers"]) > 1:
            tick = "+".join(p["tickers"])
        flag = " *" if p.get("ticker_check") else ""
        kind = "" if is_stock(p) else " [%s]" % (p["put_call"] or p["share_type"])
        out.append("%3d  %-11s %-32s %16s %18s %7.2f%s" % (
            i, tick, (p["issuer"] + kind)[:32], _shares(p["shares"]), _money(p["value"]),
            p["pct_of_13f"], flag))
    if top and len(positions) > top:
        out.append("     ... %d weitere" % (len(positions) - top))
    if any(p.get("ticker_check") for p in shown):
        out.append("")
        out.append("* Ticker per Name oder Gattung geraten: Bestätige ihn, bevor du "
                   "kopierst, oder nutze --figi.")
    for note in meta.get("notes", []):
        out.append("")
        out.append(note)
    out.append("")
    out.append("Bestand zum %s. Eine 13F kommt vierteljährlich und bis zu 45 Tage verspätet, "
               "nur Long-Positionen in US-Aktien." % meta.get("period", "Quartalsende"))
    return "\n".join(out) + "\n"


def render_diff(meta, rows):
    labels = {"NEW": "NEU", "SOLD OUT": "VERKAUFT", "ADDED": "AUFGESTOCKT",
              "TRIMMED": "REDUZIERT", "UNCHANGED": "UNVERÄNDERT"}
    out = ["%s: %s gegen %s" % (meta.get("filer", "?"), meta.get("period", "?"),
                                meta.get("prior_period", "?")), ""]
    for status in ("NEW", "SOLD OUT", "ADDED", "TRIMMED", "UNCHANGED"):
        group = [r for r in rows if r["status"] == status]
        if not group:
            continue
        out.append("%s (%d)" % (labels[status], len(group)))
        for r in group:
            tick = r.get("ticker") or "?"
            label = "%s %s" % (r["issuer"], r["title"])
            if status == "UNCHANGED":
                out.append("  %-7s %-40s %s Aktien" % (tick, label[:40], _shares(r["shares_after"])))
            elif status == "SOLD OUT":
                out.append("  %-7s %-40s %s Aktien (%s am Ende des Vorquartals)" % (
                    tick, label[:40], _shares(r["shares_before"]), _money(r["value_before"])))
            else:
                pct = "" if r["change_pct"] is None else " (%+.1f%%)" % r["change_pct"]
                out.append("  %-7s %-40s %s -> %s%s" % (
                    tick, label[:40], _shares(r["shares_before"]), _shares(r["shares_after"]), pct))
        out.append("")
    out.append("Aktienzahlen zu zwei Quartalsenden. Geschäfte innerhalb des Quartals und ihre "
               "Preise stehen nicht in einer 13F-Meldung.")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- der Ablauf

def load_quarter(fetcher, cik, period=None, accession=None, before=None):
    """Ein Quartal herunterladen und einlesen. Gibt (meta, rows, filings) zurück."""
    sub = fetcher.get_json(SUBMISSIONS % cik10(cik))
    filings = list_filings(sub)
    if not filings:
        raise EdgarError("%s (CIK %s) hat keine 13F-HR-Meldungen in der Liste der letzten Meldungen. "
                         "Ist das ein 13F-Einreicher? Probiere: edgar13f.py find NAME --sec" % (sub.get("name"), cik10(cik)))
    chosen, amendments = pick_filing(filings, period=period, accession=accession, before=before)
    base = archive_url(cik, chosen["accession"])
    index = fetcher.get_json(base + "index.json")
    table = info_table_name(index)
    mult = value_multiplier(chosen["filed"])
    rows = parse_info_table(fetcher.get(base + table), multiplier=mult)
    meta = {"filer": sub.get("name"), "cik": cik10(cik), "period": chosen["period"],
            "filed": chosen["filed"], "accession": chosen["accession"], "form": chosen["form"],
            "info_table_url": base + table, "notes": []}
    try:
        cover = parse_cover(fetcher.get(base + "primary_doc.xml"))
    except EdgarError:
        cover = {}
    if cover.get("tableValueTotal"):
        meta["cover_total"] = float(cover["tableValueTotal"]) * mult
    if amendments:
        meta["notes"].append("Für dieses Quartal wurde außerdem eingereicht: %s. Eine Änderungsmeldung kann "
                             "die Tabelle neu fassen oder vertraulich gehaltene Positionen nachtragen. "
                             "Lies sie mit --accession." % ", ".join("%s am %s" % (a["accession"], a["filed"])
                                                       for a in amendments))
    return meta, rows, filings


def build(meta, rows, index=None, overrides=None, figi=None, combine=False):
    positions, total = aggregate(rows)
    resolve_tickers(positions, index, overrides, figi)
    if meta.get("cover_total"):
        gap = abs(meta["cover_total"] - total)
        meta["total_check"] = ("Stimmt mit der Gesamtsumme auf dem Deckblatt überein." if gap < 1000 else
                               "Das Deckblatt nennt %s; die Zeilen ergeben zusammen %s." % (
                                   _money(meta["cover_total"]), _money(total)))
    if combine:
        positions = combine_classes(positions, total)
    return positions, total


def ticker_index(fetcher, path=None):
    if path:
        with open(path, encoding="utf-8") as fh:
            return TickerIndex(load_company_tickers(json.load(fh)))
    return TickerIndex(load_company_tickers(fetcher.get_json(TICKERS_URL)))


def _common(ap):
    ap.add_argument("--user-agent", help="SEC-User-Agent: \"Dein Name you@example.com\"")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--offline", metavar="DIR", help="jede URL aus Dateien in DIR lesen")
    ap.add_argument("--json", action="store_true", help="JSON ausgeben")


def _quarter_args(ap):
    ap.add_argument("cik", nargs="?", help="die CIK des Einreichers (finde sie mit: find NAME)")
    ap.add_argument("--period", help="Quartalsende, JJJJ-MM-TT (Standard: das neueste)")
    ap.add_argument("--accession", help="eine bestimmte Meldung, 0001193125-26-352200")
    ap.add_argument("--xml", help="diese Datei der Informationstabelle lesen statt EDGAR")
    ap.add_argument("--thousands", action="store_true",
                    help="mit --xml: Werte sind in Tausend (Meldungen vor 2023)")
    ap.add_argument("--tickers", metavar="FILE",
                    help="company_tickers.json, die statt eines Downloads genutzt wird")
    ap.add_argument("--map", default="trader/tickers.json",
                    help="deine eigenen CUSIP-zu-Ticker-Korrekturen (Standard trader/tickers.json)")
    ap.add_argument("--figi", action="store_true",
                    help="nicht gefundene oder unsichere Ticker bei OpenFIGI nachschlagen")
    ap.add_argument("--figi-key", default=os.environ.get("OPENFIGI_API_KEY"))
    ap.add_argument("--no-tickers", action="store_true", help="den Ticker-Abgleich überspringen")


def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("find", help="die CIK eines Einreichers per Name oder Person finden")
    p.add_argument("query", nargs="+")
    p.add_argument("--sec", action="store_true", help="zusätzlich alle EDGAR-Einreicher durchsuchen")
    _common(p)

    p = sub.add_parser("filings", help="die 13F-Meldungen eines Einreichers auflisten")
    p.add_argument("cik")
    p.add_argument("--limit", type=int, default=8)
    _common(p)

    for name, text in (("holdings", "die Positionen in einem Quartal"),
                       ("diff", "was sich seit dem Quartal davor geändert hat"),
                       ("copy", "trader/targets.json aus den größten Positionen schreiben")):
        p = sub.add_parser(name, help=text)
        _quarter_args(p)
        _common(p)
        p.add_argument("--combine", action="store_true",
                       help="Aktiengattungen einer Firma addieren (Alphabet A + C)")
        p.add_argument("--top", type=int, help="nur die N größten Positionen")
        if name == "diff":
            p.add_argument("--prior-xml", help="mit --xml: die Tabelle des Vorquartals")
        if name == "copy":
            p.add_argument("--min-pct", type=float, default=0.0,
                           help="Positionen unter diesem %% der 13F überspringen")
            p.add_argument("--exclude", nargs="*", default=[], help="Ticker, die weggelassen werden")
            p.add_argument("--separate", action="store_true",
                           help="Aktiengattungen getrennt lassen (Standard: zusammengefasst)")
            p.add_argument("--out", default="trader/targets.json")
            p.add_argument("--dry-run", action="store_true", help="ausgeben, nicht schreiben")
    return ap


def _fetcher(args, sec=True):
    ua = user_agent(args.user_agent, args.config)
    if sec and not ua and not args.offline:
        raise EdgarError(UA_HELP)
    return Fetcher(ua, offline=args.offline)


def _positions(args, fetcher, period=None, before=None, xml=None):
    if xml:
        with open(xml, "rb") as fh:
            rows = parse_info_table(fh.read(), multiplier=1000 if args.thousands else 1)
        meta = {"filer": os.path.basename(xml), "period": args.period or "?", "filed": "?",
                "accession": "?", "notes": []}
        filings = []
    else:
        if not args.cik:
            raise EdgarError("gib eine CIK an oder --xml DATEI")
        meta, rows, filings = load_quarter(fetcher, args.cik, period=period,
                                           accession=None if before else args.accession,
                                           before=before)
    index = None
    if not args.no_tickers:
        try:
            index = ticker_index(fetcher, args.tickers)
        except EdgarError as exc:
            meta["notes"].append("Ticker per Name übersprungen: %s" % exc)
    overrides = load_overrides([CLASSES, args.map])
    figi = {}
    if args.figi:
        positions, _ = aggregate(rows)
        resolve_tickers(positions, index, overrides)
        unsure = [p["cusip"] for p in positions if p.get("ticker_check") or not p.get("ticker")]
        if unsure:
            figi = figi_lookup(fetcher, unsure, api_key=args.figi_key)
    return meta, rows, filings, index, overrides, figi


def main(argv=None):
    args = _parser().parse_args(argv)
    if not args.cmd:
        _parser().print_help()
        return 2
    try:
        return _run(args)
    except EdgarError as exc:
        print("edgar13f: %s" % exc, file=sys.stderr)
        return 2


def _run(args):
    if args.cmd == "find":
        query = " ".join(args.query)
        hits = [dict(f, source="bundled") for f in find_bundled(query)]
        if args.sec:
            fetcher = _fetcher(args)
            known = {h["cik"] for h in hits}
            hits += [dict(h, source="sec") for h in search_sec(fetcher, query)
                     if h["cik"] not in known]
        if args.json:
            print(json.dumps(hits, indent=2))
        elif not hits:
            print("Kein Einreicher passt zu %r. Probiere --sec, um alle EDGAR-Einreicher zu durchsuchen, "
                  "oder einen kürzeren Namen." % query)
        else:
            for h in hits:
                extra = h.get("person") or ""
                print("%s  %s%s%s" % (h["cik"], h["name"], "  (%s)" % extra if extra else "",
                                      "  [%s]" % h["status"] if h.get("status") else ""))
        return 0 if hits else 1

    fetcher = _fetcher(args, sec=not (getattr(args, "xml", None) and args.tickers
                                      and not getattr(args, "figi", False)))

    if args.cmd == "filings":
        sub = fetcher.get_json(SUBMISSIONS % cik10(args.cik))
        rows = list_filings(sub)[:args.limit]
        if args.json:
            print(json.dumps({"filer": sub.get("name"), "cik": cik10(args.cik),
                              "filings": rows}, indent=2))
        else:
            print("%s (CIK %s)" % (sub.get("name"), cik10(args.cik)))
            for f in rows:
                print("  %-9s Quartal %s  eingereicht %s  %s" % (f["form"], f["period"], f["filed"],
                                                           f["accession"]))
            if not rows:
                print("  keine 13F-Meldungen")
        return 0 if rows else 1

    meta, rows, filings, index, overrides, figi = _positions(args, fetcher,
                                                             period=args.period, xml=args.xml)
    combine = args.combine or (args.cmd == "copy" and not args.separate)
    positions, total = build(meta, rows, index, overrides, figi, combine=combine)

    if args.cmd == "holdings":
        if args.json:
            print(json.dumps({"meta": meta, "total": total,
                              "positions": positions[:args.top] if args.top else positions},
                             indent=2))
        else:
            sys.stdout.write(render_holdings(meta, positions, total, top=args.top,
                                             combined=combine))
        return 0

    if args.cmd == "diff":
        if args.xml:
            if not args.prior_xml:
                raise EdgarError("gib mit --xml das Vorquartal als --prior-xml an")
            with open(args.prior_xml, "rb") as fh:
                prior_rows = parse_info_table(fh.read(), multiplier=1000 if args.thousands else 1)
            meta["prior_period"] = "Vorquartalsdatei"
        else:
            pmeta, prior_rows, _ = load_quarter(fetcher, args.cik, before=meta["period"])
            meta["prior_period"] = pmeta["period"]
        cur, _ = aggregate(rows)
        pri, _ = aggregate(prior_rows)
        resolve_tickers(cur, index, overrides, figi)
        resolve_tickers(pri, index, overrides, figi)
        changes = diff(cur, pri)
        if args.json:
            print(json.dumps({"meta": meta, "changes": changes}, indent=2))
        else:
            sys.stdout.write(render_diff(meta, changes))
        return 0

    # copy
    weights, skipped = copy_weights(positions, top=args.top, min_pct=args.min_pct,
                                    exclude=args.exclude)
    targets = {
        "source": "%s 13F-HR, Quartalsende %s, eingereicht %s (Accession %s)" % (
            meta.get("filer"), meta.get("period"), meta.get("filed"), meta.get("accession")),
        "as_of": meta.get("period"),
        "filed": meta.get("filed"),
        "cik": meta.get("cik"),
        "method": "%s Aktienpositionen nach Wert%s, pct_of_13f auf 100 skaliert" % (
            "die %d größten" % args.top if args.top else "alle",
            ", Aktiengattungen zusammengefasst" if combine else ""),
        "weights": weights,
        "skipped": skipped,
        "caveats": CAVEATS,
    }
    if args.json or args.dry_run:
        print(json.dumps(targets, indent=2))
    if not args.dry_run:
        folder = os.path.dirname(args.out)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(targets, fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("%s geschrieben" % args.out)
            for w in weights:
                print("  %-7s %-28s %6.2f%%  (%.2f%% der 13F)%s" % (
                    w["ticker"], w["name"][:28], w["weight"], w["pct_of_13f"],
                    "  Ticker prüfen" if w.get("check_ticker") else ""))
            print("Bestand zum %s, eingereicht %s. Als Nächstes: trader-budget schneidet diese "
                  "auf dein Budget zu." % (meta.get("period"), meta.get("filed")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
