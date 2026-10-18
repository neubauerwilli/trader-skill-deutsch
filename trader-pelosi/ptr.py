#!/usr/bin/env python3
"""Kongress-Handelstracker für trader-pelosi. Nur Standardbibliothek.

Liest den öffentlichen Index der Finanzoffenlegungen des House Clerk und die
Periodic Transaction Reports (PTRs), die Mitglieder des Repräsentantenhauses
einreichen, wenn sie, ihr Ehepartner oder ein abhängiges Kind eine Aktie
kaufen oder verkaufen.

    python3 ptr.py index --member pelosi                 # die PTRs dieses Jahres eines Mitglieds
    python3 ptr.py index --year 2026 --since 2026-10-01  # jede PTR seit einem Datum
    python3 ptr.py new --member pelosi                   # nur, was seit der letzten Prüfung neu ist
    python3 ptr.py fetch 20035143                        # eine PTR herunterladen und lesen
    python3 ptr.py parse ptr.txt --filed 2026-08-21      # Zeilen aus `pdftotext -layout`-Text

Woher die Daten kommen:
  - Der Index: https://disclosures-clerk.house.gov/public_disc/financial-pdfs/<YEAR>FD.zip
    (ein ZIP mit <YEAR>FD.xml: ein <Member> je Meldung, FilingType P = PTR).
  - Jede PTR: https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/<YEAR>/<DocID>.pdf
  - Zum Lesen einer PDF braucht dieses Tool `pdftotext` (poppler), wenn du
    daraus Zeilen haben willst. Ohne `pdftotext` gibt das Tool den PDF-Pfad aus
    und Claude liest die PDF selbst.

Was eine PTR ist und was nicht:
  - Sie ist eine ÖFFENTLICHE Offenlegung nach dem STOCK Act, fällig 30 Tage,
    nachdem das Mitglied vom Handel erfahren hat, und spätestens 45 Tage nach
    dem Handel. Sie kommt also Tage oder Wochen nach dem Handel, und Beträge
    sind Spannen, keine Preise.
  - Sie schnell zu lesen ist keine Insiderinformation. Hier gibt es keine.
  - Das hier deckt das Repräsentantenhaus ab. Senats-PTRs liegen auf
    efdsearch.senate.gov hinter einer Vereinbarung, die du selbst annehmen
    musst; den manuellen Weg beschreibt SKILL.md.

Zustand: `new` merkt sich in trader/state/pelosi.json, welche DocIDs es schon
gezeigt hat, sodass jede PTR nur einmal meldet. Offline: --offline DATEI liest
ein gespeichertes <YEAR>FD.zip (oder die .xml darin) statt des Netzes. Die
Tests nutzen das.
"""

import argparse
import datetime as _dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/%d%s.zip"
PTR_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/%d/%s.pdf"
FD_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/%d/%s.pdf"
UA = "trader-skill/1.0 (+https://github.com/Jakeschincariol/trader-skill)"
FILING_TYPES = {
    "P": "Periodic Transaction Report", "O": "Jahresbericht", "A": "Änderung",
    "C": "Kandidatenbericht", "X": "Fristverlängerung", "T": "Abschlussbericht",
    "W": "Befreiung", "D": "Offenlegung (neues Mitglied oder Kandidat)", "H": "Bericht neuer Meldepflichtiger",
}
TXN_TYPES = {"P": "Kauf", "S": "Verkauf", "S (partial)": "Teilverkauf", "E": "Tausch"}
OWNERS = {"SP": "Ehepartner", "JT": "gemeinsam", "DC": "abhängiges Kind", "": "selbst"}
LATE_DAYS = 45


class PtrError(Exception):
    pass


# ---------------------------------------------------------------- der Index

def iso(mdy):
    """'8/21/2026' oder '08/21/2026' -> '2026-08-21' (zweistellige Jahre -> 20xx)."""
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$", mdy or "")
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def download(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise PtrError("HTTP %s für %s" % (exc.code, url))
    except urllib.error.URLError as exc:
        raise PtrError("%s nicht erreichbar: %s" % (url, exc.reason))


def read_index_bytes(data):
    """Index-XML-Bytes aus dem ZIP (oder die XML selbst)."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".xml")]
            if not names:
                raise PtrError("keine XML im ZIP (Dateien: %s)" % ", ".join(z.namelist()))
            return z.read(names[0])
    return data


def parse_index(xml_bytes, year):
    """Jede Meldung einer <YEAR>FD.xml als dict, neueste zuerst."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise PtrError("Der Index ist keine gültige XML: %s" % exc)
    out = []
    for m in root.iter("Member"):
        f = {c.tag: (c.text or "").strip() for c in m}
        doc = f.get("DocID", "")
        kind = f.get("FilingType", "")
        y = int(f.get("Year") or year)
        url = (PTR_URL if kind == "P" else FD_URL) % (y, doc)
        name = " ".join(x for x in (f.get("Prefix"), f.get("First"), f.get("Last"),
                                    f.get("Suffix")) if x)
        out.append({"doc_id": doc, "type": kind, "type_name": FILING_TYPES.get(kind, kind),
                    "last": f.get("Last", ""), "first": f.get("First", ""), "name": name,
                    "district": f.get("StateDst", ""), "year": y,
                    "filed": iso(f.get("FilingDate", "")), "url": url})
    out.sort(key=lambda r: (r["filed"] or "", r["doc_id"]), reverse=True)
    return out


def load_index(year, offline=None):
    if offline:
        with open(offline, "rb") as fh:
            data = fh.read()
    else:
        data = download(INDEX_URL % (year, "FD"))
    return parse_index(read_index_bytes(data), year)


def member_match(row, member):
    """'pelosi', 'Nancy Pelosi' oder 'Pelosi, Nancy' gegen eine Indexzeile."""
    member = (member or "").strip().lower()
    if not member:
        return True
    if "," in member:
        last, first = [x.strip() for x in member.split(",", 1)]
    else:
        parts = member.split()
        last, first = parts[-1], " ".join(parts[:-1])
    if row["last"].lower() != last:
        return False
    return not first or row["first"].lower().startswith(first)


def select(rows, members=None, kind="P", since=None):
    out = []
    for r in rows:
        if kind and r["type"] != kind:
            continue
        if members and not any(member_match(r, m) for m in members):
            continue
        if since and (r["filed"] or "") < since:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------- Zustand

def load_state(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"seen": [], "last_check": None}


def save_state(path, state):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")


def new_since(rows, state, today, first_days=LATE_DAYS):
    """Zeilen, die noch nicht gesehen wurden. Beim ersten Lauf nur die der
    letzten first_days Tage, damit dich nicht das ganze Jahr überschwemmt."""
    seen = set(state.get("seen", []))
    if state.get("last_check") is None:
        cutoff = (today - _dt.timedelta(days=first_days)).isoformat()
        return [r for r in rows if (r["filed"] or "") >= cutoff]
    return [r for r in rows if r["doc_id"] not in seen]


# ---------------------------------------------------------------- eine PTR lesen

TXN = re.compile(
    r"^\s*(?:(?P<owner>SP|JT|DC)\s+)?(?P<asset>.*?)\s+(?P<type>S \(partial\)|P|S|E)\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<notified>\d{2}/\d{2}/\d{4})\s+(?P<amount>\S.*?)\s*$")
LABELS = [
    ("status", re.compile(r"^\s*F(?:ILING)?\s+S(?:TATUS)?\s*:\s*(.*)$", re.I)),
    ("description", re.compile(r"^\s*D(?:ESCRIPTION)?\s*:\s*(.*)$", re.I)),
    ("account", re.compile(r"^\s*S(?:UBHOLDING)?\s+O(?:F)?\s*:\s*(.*)$", re.I)),
    ("location", re.compile(r"^\s*L(?:OCATION)?\s*:\s*(.*)$", re.I)),
]
# Spaltenköpfe wiederholen sich oben auf jeder Seite, manchmal mitten in einer
# Zeile, die auf der nächsten Seite weitergeht: überspringen, ohne die Zeile zu beenden.
PAGE_HEADER = re.compile(r"^\s*(ID\s+Owner\s+Asset|Type\s+Date\b|Type$|Date$|Gains >$|\$200\?$)",
                         re.I)
END = re.compile(r"^\s*(\* For the complete|I CERTIFY|Digitally Signed|Filing ID #)", re.I)
AMOUNT = re.compile(r"\$([\d,]+)")
OPTION = re.compile(r"([\d,]+)\s+(call|put)\s+options?\b.*?strike price of \$([\d,.]+)"
                    r".*?expiration date of (\d{1,2}/\d{1,2}/\d{2,4})", re.I | re.S)
SHARES = re.compile(r"(?:purchased|sold|bought)\s+([\d,]+)\s+shares", re.I)


def _amount(text):
    nums = [int(x.replace(",", "")) for x in AMOUNT.findall(text or "")]
    low = nums[0] if nums else None
    high = nums[1] if len(nums) > 1 else None
    return low, high


def _finish(row, filed):
    asset = " ".join(row.pop("_asset").split())
    tick = re.findall(r"\(([A-Z][A-Z0-9.\-/]{0,9})\)", asset)
    code = re.findall(r"\[([A-Z]{2})\]", asset)
    row["ticker"] = tick[-1] if tick else None
    row["asset_type"] = code[-1] if code else None
    name = re.sub(r"\s*\[[A-Z]{2}\]", "", asset)
    if tick:
        name = re.sub(r"\s*\(%s\)" % re.escape(tick[-1]), "", name)
    row["asset"] = name.strip(" -")
    row["amount"] = " ".join(row["amount"].split())
    row["amount_low"], row["amount_high"] = _amount(row["amount"])
    row["description"] = " ".join(row.get("description", "").split()) or None
    opt = OPTION.search(row["description"] or "")
    if opt:
        row["option"] = {"contracts": int(opt.group(1).replace(",", "")),
                         "kind": opt.group(2).lower(),
                         "strike": float(opt.group(3).replace(",", "")),
                         "expires": iso(opt.group(4)),
                         "exercised": bool(re.match(r"\s*exercised\b", row["description"], re.I))}
    sh = SHARES.search(row["description"] or "")
    if sh:
        row["shares"] = int(sh.group(1).replace(",", ""))
    if filed and row["date"]:
        days = (_dt.date.fromisoformat(filed) - _dt.date.fromisoformat(row["date"])).days
        row["days_to_disclose"] = days
        row["late"] = days > LATE_DAYS
    return row


def parse_ptr_text(text, filed=None):
    """Zeilen aus der `pdftotext -layout`-Ausgabe einer House-PTR.

    Gibt {"name", "status", "district", "doc_id", "filed", "rows": [...]} zurück.
    Jede Zeile: owner, owner_name, asset, ticker, asset_type, type, type_name,
    date, notified, amount, amount_low, amount_high, status, description,
    account, option {contracts, kind, strike, expires}, shares,
    days_to_disclose, late."""
    head = {"name": None, "status": None, "district": None, "doc_id": None, "filed": filed}
    rows, cur, mode = [], None, None
    amount_open = False
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        m = re.match(r"^Name:\s+(.*)$", s)
        if m and not head["name"]:
            head["name"] = m.group(1).strip()
            continue
        m = re.match(r"^Status:\s+(.*)$", s)
        if m and not head["status"]:
            head["status"] = m.group(1).strip()
            continue
        m = re.match(r"^State/District:\s*(\S+)", s)
        if m:
            head["district"] = m.group(1)
            continue
        m = re.search(r"Filing ID #(\d+)", s)
        if m:
            head["doc_id"] = m.group(1)
            continue
        m = re.match(r"^Digitally Signed:.*?,\s*(\d{2}/\d{2}/\d{4})", s)
        if m:
            head["filed"] = head["filed"] or iso(m.group(1))
            continue
        t = TXN.match(line)
        if t and iso(t.group("date")):
            if cur:
                rows.append(cur)
            owner = t.group("owner") or ""
            cur = {"owner": owner, "owner_name": OWNERS.get(owner, owner),
                   "_asset": t.group("asset"), "type": t.group("type"),
                   "type_name": TXN_TYPES.get(t.group("type"), t.group("type")),
                   "date": iso(t.group("date")), "notified": iso(t.group("notified")),
                   "amount": t.group("amount"), "status": None, "account": None}
            amount_open = cur["amount"].endswith("-")
            mode = "asset"
            continue
        if cur is None:
            continue
        if not s:
            if mode in ("description", "asset"):
                mode = "labels" if mode == "description" or "[" in cur["_asset"] else mode
            continue
        if PAGE_HEADER.match(s):
            continue
        if END.match(s):
            mode = "labels"
            continue
        label = next(((k, rx.match(line)) for k, rx in LABELS if rx.match(line)), None)
        if label:
            key, lm = label
            value = lm.group(1).strip()
            if key == "description":
                cur["description"] = value
                mode = "description"
            elif key in ("status", "account"):
                cur[key] = value
                mode = "labels"
            else:
                mode = "labels"
            continue
        if mode == "asset":
            parts = re.split(r"\s{2,}", s)
            if amount_open and re.fullmatch(r"\$[\d,]+", parts[-1]):
                cur["amount"] += " " + parts.pop()
                amount_open = False
            if parts and parts[0]:
                cur["_asset"] += " " + " ".join(parts)
        elif mode == "description":
            cur["description"] = cur.get("description", "") + " " + s
    if cur:
        rows.append(cur)
    head["rows"] = [_finish(r, head["filed"]) for r in rows]
    return head


def pdf_to_text(pdf_path):
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-layout", pdf_path, "-"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None


# ---------------------------------------------------------------- Ausgabe

def watchlist(args):
    tickers = {t.upper() for t in (args.tickers or [])}
    path = getattr(args, "portfolio", None)
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for p in json.load(fh).get("positions", []):
                if p.get("ticker"):
                    tickers.add(p["ticker"].upper())
    return tickers


def describe(row):
    bits = ["%s %s" % (row["owner_name"], row["type_name"])]
    what = row["asset"]
    if row.get("ticker"):
        what += " (%s)" % row["ticker"]
    if row.get("option"):
        o = row["option"]
        what += ": %s%d %s-Optionen, $%g Strike, Verfall %s" % (
            "ausgeübt: " if o.get("exercised") else "", o["contracts"], o["kind"],
            o["strike"], o["expires"])
    elif row.get("shares"):
        what += ": %s Aktien" % "{:,}".format(row["shares"])
    bits.append(what)
    bits.append(row["amount"])
    when = "gehandelt am %s" % row["date"]
    if row.get("days_to_disclose") is not None:
        when += ", %d Tage später gemeldet%s" % (row["days_to_disclose"],
                                                   " (ZU SPÄT: über 45)" if row.get("late") else "")
    bits.append(when)
    if row.get("account"):
        bits.append("Konto: %s" % row["account"])
    return ", ".join(bits)


def render_report(report, watch=()):
    out = ["%s (%s), %s. PTR %s, eingereicht am %s." % (
        report.get("name") or "?", report.get("district") or "?", report.get("status") or "?",
        report.get("doc_id") or "?", report.get("filed") or "?")]
    for r in report["rows"]:
        flag = "  <- %s steht auf deiner Liste" % r["ticker"] if r.get("ticker") in watch else ""
        out.append("  - %s%s" % (describe(r), flag))
        if r.get("description") and not r.get("option") and not r.get("shares"):
            out.append("      %s" % r["description"])
    if not report["rows"]:
        out.append("  keine Handelszeilen gefunden: Lies die PDF direkt")
    out.append("Öffentliche Offenlegung nach dem STOCK Act, bis zu 45 Tage nach dem Handel eingereicht. "
               "Beträge sind Spannen. Keine Insiderinformation. Keine Anlageberatung.")
    return "\n".join(out) + "\n"


def render_index(rows):
    out = []
    for r in rows:
        out.append("%s  %-28s %-5s %-4s doc %-9s %s" % (
            r["filed"], r["name"][:28], r["district"], r["type"], r["doc_id"], r["url"]))
    return "\n".join(out) + ("\n" if out else "")


# ---------------------------------------------------------------- Kommandozeile

def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    year = _dt.date.today().year

    def common(p):
        p.add_argument("--year", type=int, default=year)
        p.add_argument("--member", action="append",
                       help="Nachname oder 'Vorname Nachname'; wiederholen für mehr Mitglieder")
        p.add_argument("--type", default="P", help="FilingType (Standard P = PTR; '' für alle)")
        p.add_argument("--since", help="nur Meldungen ab diesem Datum (JJJJ-MM-TT)")
        p.add_argument("--offline", metavar="FILE", help="ein gespeichertes <YEAR>FD.zip oder .xml")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("index", help="Meldungen aus dem Jahresindex auflisten")
    common(p)
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("new", help="Meldungen, die bei der letzten Prüfung noch nicht da waren")
    common(p)
    p.add_argument("--state", default="trader/state/pelosi.json")
    p.add_argument("--today", help="JJJJ-MM-TT, für reproduzierbare Läufe")
    p.add_argument("--dry-run", action="store_true", help="Zustandsdatei nicht aktualisieren")
    p.add_argument("--read", action="store_true",
                   help="jede neue PTR auch herunterladen und lesen (braucht pdftotext)")
    p.add_argument("--pdf-dir", default="trader/state/ptr-pdfs")
    p.add_argument("--tickers", nargs="*", help="Handel mit diesen Tickern markieren")
    p.add_argument("--portfolio", default="trader/portfolio.json")

    p = sub.add_parser("fetch", help="eine PTR-PDF herunterladen und lesen")
    p.add_argument("doc_id")
    p.add_argument("--year", type=int, default=year)
    p.add_argument("--filed", help="Einreichungsdatum JJJJ-MM-TT, falls bekannt")
    p.add_argument("--pdf-dir", default="trader/state/ptr-pdfs")
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("parse", help="Zeilen aus dem pdftotext-layout-Text einer PTR")
    p.add_argument("text_file")
    p.add_argument("--filed", help="Einreichungsdatum JJJJ-MM-TT (sonst aus der Unterschrift gelesen)")
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--json", action="store_true")
    return ap


def _fetch_and_read(doc_id, year, pdf_dir, filed=None):
    os.makedirs(pdf_dir, exist_ok=True)
    path = os.path.join(pdf_dir, "%s.pdf" % doc_id)
    if not os.path.isfile(path):
        data = download(PTR_URL % (year, doc_id))
        if not data.startswith(b"%PDF"):
            raise PtrError("Dokument %s kam nicht als PDF zurück" % doc_id)
        with open(path, "wb") as fh:
            fh.write(data)
    text = pdf_to_text(path)
    if text is None:
        return path, None
    report = parse_ptr_text(text, filed=filed)
    report["pdf"] = path
    report["url"] = PTR_URL % (year, doc_id)
    return path, report


def main(argv=None):
    args = _parser().parse_args(argv)
    if not args.cmd:
        _parser().print_help()
        return 2
    try:
        return _run(args)
    except (PtrError, OSError) as exc:
        print("ptr: %s" % exc, file=sys.stderr)
        return 2


def _run(args):
    if args.cmd == "parse":
        with open(args.text_file, encoding="utf-8", errors="replace") as fh:
            report = parse_ptr_text(fh.read(), filed=args.filed)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            sys.stdout.write(render_report(report, watchlist(args)))
        return 0 if report["rows"] else 1

    if args.cmd == "fetch":
        path, report = _fetch_and_read(args.doc_id, args.year, args.pdf_dir, args.filed)
        if report is None:
            print("%s gespeichert. pdftotext ist nicht installiert, also lies die PDF direkt "
                  "(Claude kann sie öffnen) oder installiere poppler (brew install poppler)." % path)
            return 0
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            sys.stdout.write(render_report(report, watchlist(args)))
        return 0 if report["rows"] else 1

    rows = load_index(args.year, args.offline)
    if args.cmd == "new" and not args.offline:
        today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()
        if today.month == 1 and args.year == today.year:
            try:
                rows += load_index(args.year - 1)
            except PtrError:
                pass
    picked = select(rows, args.member, args.type or None, args.since)

    if args.cmd == "index":
        picked = picked[:args.limit]
        if args.json:
            print(json.dumps(picked, indent=2))
        else:
            sys.stdout.write(render_index(picked))
            if not picked:
                print("Keine passenden Meldungen im Index %d." % args.year)
        return 0 if picked else 1

    # new
    today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()
    state = load_state(args.state)
    fresh = new_since(picked, state, today)
    watch = watchlist(args)
    reports = []
    if args.read:
        for r in fresh:
            if r["type"] != "P":
                continue
            path, report = _fetch_and_read(r["doc_id"], r["year"], args.pdf_dir, r["filed"])
            reports.append({"filing": r, "pdf": path, "report": report})
    if args.json:
        print(json.dumps({"new": fresh, "reports": reports}, indent=2))
    else:
        if not fresh:
            print("Nichts Neues seit %s." % (state.get("last_check") or "der letzten Prüfung"))
        for r in fresh:
            print("NEU %s: %s (%s) eingereicht am %s, doc %s" % (
                r["type_name"], r["name"], r["district"], r["filed"], r["doc_id"]))
            print("    %s" % r["url"])
        for rep in reports:
            if rep["report"] is None:
                print("    gelesen: %s (pdftotext nicht installiert)" % rep["pdf"])
            else:
                sys.stdout.write(render_report(rep["report"], watch))
    if not args.dry_run:
        seen = set(state.get("seen", []))
        seen.update(r["doc_id"] for r in picked)
        state["seen"] = sorted(seen)
        state["last_check"] = _dt.datetime.now().replace(microsecond=0).isoformat()
        save_state(args.state, state)
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())
