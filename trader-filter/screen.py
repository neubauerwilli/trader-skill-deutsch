#!/usr/bin/env python3
"""Aktienfilter für trader-filter. Nur Standardbibliothek, kein Netzwerk.

Prüft jede Firma in einer Kennzahlen-Datei gegen deine Regeln, sagt genau, warum
sie besteht oder durchfällt, und sortiert die, die bestehen, in eine Rangliste.

    python3 screen.py trader/metrics.json --rules rules.example.json
    python3 screen.py trader/metrics.json --rules my-rules.json --prices trader/prices.json
    python3 screen.py companies.csv --rules my-rules.json --out trader/shortlist.json --json

Kennzahlen: eine JSON-Datei ({"companies": [...]}, so schreibt sie facts.py
--metrics von trader-analyst, oder eine einfache Liste von Objekten) oder eine
CSV mit Kopfzeile. Jede Firma braucht einen "ticker"; jede andere Spalte ist
eine Kennzahl. Zahlen in einer CSV dürfen $, % und Tausenderkommas enthalten.

Kurse (optional): trader/prices.json ({"prices": {"AAPL": 341.27}}). Mit
Kursen ergänzt der Filter:
    price          der Kurs, den du angegeben hast
    pe             Kurs / eps_ttm (nur wenn eps_ttm über null liegt)
    earnings_yield 100 * eps_ttm / Kurs, in %

Regeln (siehe rules.example.json):
    {"name": "...",
     "rules": [{"metric": "net_margin", "op": ">=", "value": 15}, ...],
     "rank":  [{"metric": "revenue_growth_yoy", "order": "desc", "weight": 2}, ...],
     "missing": "fail",            # oder "skip": eine Regel auf eine fehlende Kennzahl wird übersprungen
     "top": 10}
    ops: > >= < <= == != between (value [low, high]) in / not in (eine Liste)

Rangliste: Unter den Firmen, die bestehen, gibt jede Rang-Kennzahl eine Note von 0
(schlechteste) bis 1 (beste); die Endnote ist der gewichtete Durchschnitt. Fehlt
einer Firma eine Rang-Kennzahl, wird sie nach den anderen bewertet und die Lücke
wird angezeigt.

Exit-Code 0, wenn mindestens eine Firma besteht, 1, wenn keine besteht, 2 bei
falscher Eingabe. Ein Filter ist ein Filter, keine Empfehlung.
"""

import argparse
import csv
import json
import math
import os
import sys

OPS = (">", ">=", "<", "<=", "==", "!=", "between", "in", "not in")


class ScreenError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def finite_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def to_number(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v if finite_number(v) else None
    s = str(v).strip()
    if s == "" or s.lower() in ("na", "n/a", "none", "null", "-"):
        return None
    t = s.replace(",", "").replace("$", "").replace("%", "")
    try:
        number = float(t)
        return number if math.isfinite(number) else None
    except ValueError:
        return s


def load_metrics(path):
    if not os.path.isfile(path):
        raise ScreenError("Datei nicht gefunden: %s" % path)
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = [{(k or "").strip(): to_number(v) for k, v in r.items()}
                    for r in csv.DictReader(fh)]
    else:
        with open(path, encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except ValueError as exc:
                raise ScreenError("%s ist kein gültiges JSON: %s" % (path, exc))
        rows = data.get("companies", data.get("rows")) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ScreenError("%s: erwartet wird eine Liste von Firmen oder {\"companies\": [...]}" % path)
        rows = [{k: (None if isinstance(v, float) and not math.isfinite(v) else v)
                 for k, v in dict(r).items()} for r in rows]
    out = []
    for i, r in enumerate(rows):
        t = str(r.get("ticker") or r.get("symbol") or "").strip().upper()
        if not t:
            raise ScreenError("Firma Nr. %d hat keinen Ticker" % (i + 1))
        r["ticker"] = t
        out.append(r)
    return out


def add_prices(rows, prices_path):
    with open(prices_path, encoding="utf-8") as fh:
        data = json.load(fh)
    prices = {k.upper(): v for k, v in (data.get("prices", data) or {}).items()}
    for r in rows:
        p = to_number(prices.get(r["ticker"]))
        if not finite_number(p) or p <= 0:
            continue
        r["price"] = p
        eps = to_number(r.get("eps_ttm"))
        if finite_number(eps):
            r["earnings_yield"] = round(100.0 * eps / p, 2)
            if eps > 0:
                r["pe"] = round(p / eps, 1)
    return data.get("as_of")


def load_rules(path):
    with open(path, encoding="utf-8") as fh:
        try:
            spec = json.load(fh)
        except ValueError as exc:
            raise ScreenError("%s ist kein gültiges JSON: %s" % (path, exc))
    check_rules(spec)
    return spec


def check_rules(spec):
    if not isinstance(spec, dict) or not isinstance(spec.get("rules", []), list):
        raise ScreenError("rules muss {\"rules\": [...]} sein")
    for i, r in enumerate(spec.get("rules", [])):
        if "metric" not in r or "op" not in r or "value" not in r:
            raise ScreenError("Regel Nr. %d braucht metric, op und value: %r" % (i + 1, r))
        if r["op"] not in OPS:
            raise ScreenError("Regel Nr. %d: unbekannter op %r (erlaubt: %s)" % (i + 1, r["op"],
                                                                          ", ".join(OPS)))
        if r["op"] == "between" and not (isinstance(r["value"], list) and len(r["value"]) == 2):
            raise ScreenError("Regel Nr. %d: between braucht [niedrig, hoch]" % (i + 1))
        if r["op"] in ("in", "not in") and not isinstance(r["value"], list):
            raise ScreenError("Regel Nr. %d: %s braucht eine Liste" % (i + 1, r["op"]))
        if r["op"] in (">", ">=", "<", "<=") and not finite_number(r["value"]):
            raise ScreenError("Regel Nr. %d braucht eine endliche Zahl" % (i + 1))
        if r["op"] == "between" and (not all(finite_number(v) for v in r["value"])
                                      or r["value"][0] > r["value"][1]):
            raise ScreenError("Regel Nr. %d braucht endliche Grenzen niedrig <= hoch" % (i + 1))
        if isinstance(r["value"], float) and not math.isfinite(r["value"]):
            raise ScreenError("Regel Nr. %d enthält keine endliche Zahl" % (i + 1))
    for i, k in enumerate(spec.get("rank", [])):
        if "metric" not in k or k.get("order", "desc") not in ("asc", "desc"):
            raise ScreenError("Rang Nr. %d braucht metric und order asc oder desc" % (i + 1))
        if not finite_number(k.get("weight", 1)) or k.get("weight", 1) <= 0:
            raise ScreenError("Rang Nr. %d braucht ein positives endliches Gewicht" % (i + 1))
    if spec.get("missing", "fail") not in ("fail", "skip"):
        raise ScreenError("missing muss \"fail\" oder \"skip\" sein")


# ---------------------------------------------------------------- screening

def _fmt(v):
    if isinstance(v, float):
        return ("%.2f" % v).rstrip("0").rstrip(".")
    if isinstance(v, list):
        return "[%s]" % ", ".join(_fmt(x) for x in v)
    return str(v)


def check(company, rule):
    """(passed, reason). passed ist None, wenn die Kennzahl fehlt."""
    m, op, want = rule["metric"], rule["op"], rule["value"]
    have = company.get(m)
    if isinstance(have, float) and not math.isfinite(have):
        have = None
    if have is None or have == "":
        return None, "%s fehlt" % m
    if op in ("in", "not in"):
        bag = [str(x).upper() for x in want]
        inside = str(have).upper() in bag
        ok = inside if op == "in" else not inside
        return ok, "%s %s %s %s" % (m, _fmt(have), "ist in" if inside else "ist nicht in", _fmt(want))
    if op in ("==", "!=") and not isinstance(have, (int, float)):
        ok = (str(have).upper() == str(want).upper()) == (op == "==")
        return ok, "%s %s %s %s" % (m, _fmt(have), op, _fmt(want))
    if not finite_number(have):
        return False, "%s ist %r, keine Zahl" % (m, have)
    if op == "between":
        lo, hi = want
        ok = lo <= have <= hi
        return ok, "%s %s %s %s bis %s" % (m, _fmt(have), "liegt innerhalb von" if ok else "liegt außerhalb von",
                                          _fmt(lo), _fmt(hi))
    ok = {">": lambda: have > want, ">=": lambda: have >= want,
          "<": lambda: have < want, "<=": lambda: have <= want,
          "==": lambda: have == want, "!=": lambda: have != want}[op]()
    if ok:
        return True, "%s %s %s %s" % (m, _fmt(have), op, _fmt(want))
    return False, "%s %s, braucht %s %s" % (m, _fmt(have), op, _fmt(want))


def screen(companies, spec):
    """Jede Firma mit bestanden/durchgefallen und Gründen; die bestandenen sind gerankt."""
    rules = spec.get("rules", [])
    missing = spec.get("missing", "fail")
    results = []
    for c in companies:
        fails, passes, gaps = [], [], []
        for r in rules:
            ok, why = check(c, r)
            if ok is None:
                (fails if missing == "fail" else gaps).append(why)
            elif ok:
                passes.append(why)
            else:
                fails.append(why)
        results.append({"ticker": c["ticker"], "name": c.get("name"), "passed": not fails,
                        "fails": fails, "passes": passes, "skipped": gaps, "metrics": c})
    rank(results, spec.get("rank", []))
    results.sort(key=lambda r: (not r["passed"], -(r.get("score") or 0), len(r["fails"]),
                                r["ticker"]))
    return results


def rank(results, keys):
    winners = [r for r in results if r["passed"]]
    for r in winners:
        r["score"], r["rank_parts"] = 0.0, {}
    if not keys or not winners:
        for r in winners:
            r["score"] = None
        return
    for k in keys:
        m, desc = k["metric"], k.get("order", "desc") == "desc"
        vals = [(r, r["metrics"].get(m)) for r in winners]
        vals = [(r, v) for r, v in vals if finite_number(v)]
        if not vals:
            continue
        ordered = sorted(v for _, v in vals)
        n = len(ordered)
        for r, v in vals:
            below = sum(1 for x in ordered if x < v)
            equal = sum(1 for x in ordered if x == v)
            pos = (below + (equal - 1) / 2.0) / (n - 1) if n > 1 else 1.0
            r["rank_parts"][m] = round(pos if desc else 1.0 - pos, 3)
    weights = {k["metric"]: float(k.get("weight", 1)) for k in keys}
    for r in winners:
        parts = r["rank_parts"]
        total = sum(weights[m] for m in parts)
        r["score"] = round(sum(parts[m] * weights[m] for m in parts) / total, 3) if total else None
        r["rank_gaps"] = [k["metric"] for k in keys if k["metric"] not in parts]


# ---------------------------------------------------------------- output

def render(results, spec, top=None, prices_as_of=None):
    name = spec.get("name") or "screen"
    passed = [r for r in results if r["passed"]]
    out = ["%s: %d von %d Firmen bestehen." % (name, len(passed), len(results))]
    if prices_as_of:
        out.append("Kurse vom %s." % prices_as_of)
    out.append("")
    shown = passed[:top] if top else passed
    for i, r in enumerate(shown, 1):
        score = "" if r.get("score") is None else "  score %.2f" % r["score"]
        out.append("%2d. PASS %-6s %s%s" % (i, r["ticker"], (r.get("name") or "")[:32], score))
        for why in r["passes"]:
            out.append("        ok    %s" % why)
        for why in r["skipped"]:
            out.append("        übersprungen  %s" % why)
        if r.get("rank_gaps"):
            out.append("        nicht gerankt nach: %s" % ", ".join(r["rank_gaps"]))
    if top and len(passed) > top:
        out.append("    ... %d weitere bestehen" % (len(passed) - top))
    failed = [r for r in results if not r["passed"]]
    if failed:
        out.append("")
        for r in failed:
            out.append("    FAIL %-6s %s" % (r["ticker"], "; ".join(r["fails"])))
    out.append("")
    out.append("Ein Filter prüft gemeldete Zahlen und ist keine Empfehlung. Lies die "
               "Berichte (/trader-analyst), bevor etwas in die Nähe von /trader-budget kommt.")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("metrics", help="Kennzahlen als JSON oder CSV (trader/metrics.json)")
    ap.add_argument("--rules", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "rules.example.json"))
    ap.add_argument("--prices", help="trader/prices.json, ergänzt price, pe und earnings_yield")
    ap.add_argument("--top", type=int, help="nur die N besten zeigen, die bestehen")
    ap.add_argument("--out", help="die Shortlist als JSON hierhin schreiben (trader/shortlist.json)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        spec = load_rules(args.rules)
        companies = load_metrics(args.metrics)
        prices_as_of = add_prices(companies, args.prices) if args.prices else None
    except (ScreenError, OSError) as exc:
        print("screen: %s" % exc, file=sys.stderr)
        return 2
    results = screen(companies, spec)
    top = args.top or spec.get("top")
    passed = [r for r in results if r["passed"]]
    shortlist = {
        "name": spec.get("name"), "rules": spec.get("rules", []), "rank": spec.get("rank", []),
        "prices_as_of": prices_as_of,
        "passed": [{"ticker": r["ticker"], "name": r.get("name"), "score": r.get("score"),
                    "why": r["passes"], "skipped": r["skipped"]}
                   for r in (passed[:top] if top else passed)],
        "failed": [{"ticker": r["ticker"], "why": r["fails"]} for r in results if not r["passed"]],
        "note": "Ein Filter auf gemeldete Zahlen, keine Empfehlung.",
    }
    if args.json:
        print(json.dumps(shortlist, indent=2))
    else:
        sys.stdout.write(render(results, spec, top, prices_as_of))
    if args.out:
        folder = os.path.dirname(args.out)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(shortlist, fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("geschrieben: %s" % args.out)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
