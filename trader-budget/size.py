#!/usr/bin/env python3
"""Positionsgrößen für trader-budget. Nur Standardbibliothek, kein Netzwerk.

Macht aus Zielgewichten (dem Depot, das du kopieren willst) und deinen Regeln
(Budget, Bargeldpuffer, Obergrenze für jede einzelne Aktie, Mindestauftrag)
Dollarbeträge je Aktie und daraus vorgeschlagene Aufträge in der Form von
Alpacas place_stock_order. Es schickt nie etwas ab: Es schreibt Vorschläge nach
trader/orders.json, die trader-risiko prüft und trader-broker dir einzeln zeigt.

    python3 size.py                                  # trader/targets.json + trader/config.json
    python3 size.py --budget 5000 --write            # write trader/orders.json
    python3 size.py --weights AAPL=22.04,AXP=17.14,GOOGL=12.62,KO=10.86,BAC=9.20
    python3 size.py --whole-shares --prices trader/prices.json
    python3 size.py --json

So wird das Geld verteilt:

  1. Bargeldpuffer budget x cash_buffer_pct bleibt Bargeld, wird nie investiert
  2. Gewichte      auf 100 neu verteilt über die Aktien, die bleiben
                   (gesperrte Ticker fliegen zuerst raus; --top N behält die
                   N größten)
  3. Obergrenze    keine Aktie bekommt mehr als max_position_pct des Budgets;
                   was über der Grenze liegt, wird nach Gewicht auf die
                   anderen verteilt, immer wieder, bis keine mehr drüber ist
  4. Mindestauftrag ein Anteil unter min_order_usd fällt weg und sein Gewicht
                   wird verteilt, damit nie ein Auftrag über $2 entsteht
  5. Runden        standardmäßig ganze Dollar (--cents für Cent); der
                   Rundungsdollar geht an die größte Position ohne Deckel,
                   damit jeder Dollar des investierbaren Gelds genutzt wird
  6. Ganze Aktien  nur mit --whole-shares oder "fractional": false; braucht
                   Kurse; kauft ganze Aktien und gibt den Rest Aktie für
                   Aktie an die Position, die am weitesten unter ihrem Anteil
                   liegt

Geld, das sich nicht investieren lässt (jede Aktie an der Obergrenze oder
Reste bei ganzen Aktien), wird als "ungenutzt" ausgewiesen, nie versteckt.
"""

import argparse
import datetime
import json
import math
import os
import sys

DEFAULTS = {
    "budget": None,
    "cash_buffer_pct": 10,
    "max_position_pct": 25,
    "max_order_usd": 2000,
    "min_order_usd": 5,
    "fractional": True,
    "broker": "alpaca",
    "paper": True,
    "blocked": [],
    "allow_options": False,
}
EPS = 1e-9


class SizeError(Exception):
    pass


# ---------------------------------------------------------------- Eingaben

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("keine %s unter %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s ist kein gültiges JSON: %s" % (path, e))


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        cfg.update(read_json(path, "Konfiguration"))
    return cfg


def parse_weights(data):
    """Nimmt targets.json, ein {ticker: weight}-Dict oder eine Liste von Zeilen.

    Gibt [{"ticker", "name", "weight"}] zurück, das Gewicht als float."""
    if isinstance(data, dict) and "weights" in data:
        data = data["weights"]
    rows = []
    if isinstance(data, dict):
        for t, w in data.items():
            rows.append({"ticker": t, "name": "", "weight": w})
    elif isinstance(data, list):
        for i, r in enumerate(data):
            if not isinstance(r, dict):
                raise SizeError("weights[%d] ist kein Objekt" % i)
            t = r.get("ticker") or r.get("symbol")
            w = r.get("weight", r.get("weight_pct", r.get("pct")))
            if not t or w is None:
                raise SizeError("weights[%d] braucht einen Ticker und ein Gewicht" % i)
            rows.append({"ticker": t, "name": r.get("name", ""), "weight": w})
    else:
        raise SizeError("weights muss eine Liste oder ein Objekt sein")
    out = []
    for r in rows:
        try:
            w = float(r["weight"])
        except (TypeError, ValueError):
            raise SizeError("Gewicht für %s ist keine Zahl: %r" % (r["ticker"], r["weight"]))
        out.append({"ticker": str(r["ticker"]).strip().upper(), "name": r["name"] or "", "weight": w})
    return out


def weights_from_arg(text):
    """AAPL=22.04,AXP=17.14 -> {ticker: weight}"""
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SizeError("--weights erwartet Paare der Form TICKER=GEWICHT, bekommen: %r" % part)
        t, w = part.split("=", 1)
        out[t.strip().upper()] = w.strip()
    return out


def load_prices(path):
    if not path:
        return {}
    data = read_json(path, "Kursdatei")
    prices = data.get("prices", data) if isinstance(data, dict) else {}
    out = {}
    for t, p in prices.items():
        try:
            out[str(t).upper()] = float(p)
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------- die Rechnung

def normalise(rows, blocked=(), top=None):
    """Gesperrte und nicht positive Gewichte entfernen, die Top N behalten, auf 100 neu verteilen."""
    blocked = {str(b).upper() for b in (blocked or [])}
    dropped, keep, seen = [], [], set()
    for r in rows:
        if r["ticker"] in seen:
            raise SizeError("%s steht zweimal in den Gewichten" % r["ticker"])
        seen.add(r["ticker"])
        if r["ticker"] in blocked:
            dropped.append({"ticker": r["ticker"], "reason": "steht auf deiner Sperrliste"})
        elif r["weight"] <= 0:
            dropped.append({"ticker": r["ticker"], "reason": "Gewicht ist null oder negativ"})
        else:
            keep.append(dict(r))
    keep.sort(key=lambda r: -r["weight"])
    if top:
        for r in keep[top:]:
            dropped.append({"ticker": r["ticker"], "reason": "außerhalb der Top %d" % top})
        keep = keep[:top]
    total = sum(r["weight"] for r in keep)
    for r in keep:
        r["source_weight"] = r["weight"]
        r["weight"] = 100.0 * r["weight"] / total if total else 0.0
    return keep, dropped


def allocate(rows, investable, cap):
    """Genaue (ungerundete) Dollar je Ticker mit durchgesetzter Obergrenze.

    Proportional zum Gewicht; jede Aktie über der Grenze wird auf die Grenze
    gesetzt und das übrige Geld auf die anderen verteilt, so lange, bis keine
    mehr drüber ist. Gibt ({ticker: dollars}, Menge der gedeckelten Ticker) zurück."""
    alloc, capped = {}, set()
    active = [r for r in rows]
    money = investable
    while active:
        wsum = sum(r["weight"] for r in active)
        trial = {r["ticker"]: money * r["weight"] / wsum for r in active}
        over = [r for r in active if trial[r["ticker"]] > cap + EPS]
        if not over:
            alloc.update(trial)
            break
        for r in over:
            alloc[r["ticker"]] = cap
            capped.add(r["ticker"])
            money -= cap
        active = [r for r in active if r["ticker"] not in capped]
        if money <= EPS:
            for r in active:
                alloc[r["ticker"]] = 0.0
            break
    return alloc, capped


def round_to_unit(exact, capped, order, cap, unit):
    """Auf ganze Dollar (unit 1) oder Cent (unit 0.01) runden und die Summe halten.

    Die eingesetzte Summe ist die genaue Summe, auf die Einheit ABGERUNDET, damit
    nie Geld ausgegeben wird, das du nicht hast. Jeder Anteil wird auf die
    nächste Einheit gerundet, dann geht die Differenz (meist eine Einheit) an
    die größte Aktie ohne Deckel, dann an die nächste, und niemand wird über
    die Obergrenze geschoben."""
    def units(x):
        return int(math.floor(x / unit + EPS))

    cap_u = units(cap)
    total_u = units(sum(exact.values()))
    out = {}
    for t, v in exact.items():
        out[t] = cap_u if t in capped else int(math.floor(v / unit + 0.5 + EPS))
    diff = total_u - sum(out.values())
    takers = [t for t in order if t not in capped]
    guard = 0
    while diff != 0 and takers and guard < 100000:
        moved = False
        for t in takers:
            if diff == 0:
                break
            if diff > 0 and out[t] + 1 <= cap_u:
                out[t] += 1
                diff -= 1
                moved = True
            elif diff < 0 and out[t] > 0:
                out[t] -= 1
                diff += 1
                moved = True
        if not moved:
            break
        guard += 1
    return {t: round(u * unit, 2) for t, u in out.items()}


def whole_shares(dollars, prices, order, cap, money):
    """Ganze Aktien: jeden Anteil abrunden, dann Aktie für Aktie bei der Position
    nachkaufen, die am weitesten unter ihrem Anteil liegt, solange Geld da ist
    und die Obergrenze hält."""
    shares = {}
    for t in order:
        shares[t] = int(math.floor(dollars[t] / prices[t] + EPS)) if dollars[t] > 0 else 0
    spent = sum(shares[t] * prices[t] for t in order)
    left = money - spent
    while True:
        best, gap = None, None
        for t in order:
            p = prices[t]
            if p > left + EPS or (shares[t] + 1) * p > cap + EPS:
                continue
            g = dollars[t] - shares[t] * p
            if best is None or g > gap + EPS:
                best, gap = t, g
        if best is None:
            break
        shares[best] += 1
        left -= prices[best]
    return shares


def size(rows, cfg, budget=None, prices=None, whole=None, cents=False, top=None):
    """Reine Funktion: Gewichte + Regeln -> das volle Ergebnis der Berechnung (ein Dict)."""
    budget = float(budget if budget is not None else (cfg.get("budget") or 0))
    if budget <= 0:
        raise SizeError("kein Budget: gib --budget an oder setze \"budget\" in trader/config.json")
    buf_pct = float(cfg.get("cash_buffer_pct", 10))
    cap_pct = float(cfg.get("max_position_pct", 25))
    min_order = float(cfg.get("min_order_usd", 5) or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct muss zwischen 0 und 100 liegen")
    if not 0 < cap_pct <= 100:
        raise SizeError("max_position_pct muss über 0 und höchstens 100 liegen")
    if whole is None:
        whole = not cfg.get("fractional", True)
    prices = prices or {}

    keep, dropped = normalise(rows, cfg.get("blocked"), top)
    if not keep:
        raise SizeError("nichts zu berechnen: nach der Sperrliste sind keine Gewichte übrig")

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    investable = round(budget - cash_buffer, 2)
    cap = round(budget * cap_pct / 100.0, 2)
    unit = 0.01 if (cents or whole) else 1.0

    if whole:
        missing = [r["ticker"] for r in keep if not prices.get(r["ticker"])]
        if missing:
            raise SizeError("ganze Aktien brauchen einen Kurs für: %s (trage sie in trader/prices.json ein, "
                            "z. B. mit trader-alarm: alerts.py quote %s --write)"
                            % (", ".join(missing), " ".join(missing)))

    while True:
        live = [dict(r) for r in keep]
        tot = sum(r["source_weight"] for r in live)
        for r in live:
            r["weight"] = 100.0 * r["source_weight"] / tot
        exact, capped = allocate(live, investable, cap)
        order = [r["ticker"] for r in sorted(live, key=lambda r: -exact[r["ticker"]])]
        dollars = round_to_unit(exact, capped, order, cap, unit)
        shares = None
        if whole:
            shares = whole_shares(dollars, prices, order, cap, sum(dollars.values()))
            dollars = {t: round(shares[t] * prices[t], 2) for t in order}
        small = [r for r in live if dollars[r["ticker"]] < min_order - EPS]
        if not small:
            break
        worst = min(small, key=lambda r: (r["source_weight"], r["ticker"]))
        if whole and shares is not None and shares[worst["ticker"]] == 0 and prices.get(worst["ticker"], 0) > 0:
            reason = ("eine Aktie kostet $%s, mehr als ihr Anteil; sie lässt sich nicht als ganze Aktie kaufen"
                      % _num(prices[worst["ticker"]]))
        else:
            reason = "ihr Anteil ($%s) liegt unter deinem Mindestauftrag von $%s" % (
                _num(dollars[worst["ticker"]]), _num(min_order))
        dropped.append({"ticker": worst["ticker"], "reason": reason})
        keep = [r for r in keep if r["ticker"] != worst["ticker"]]
        if not keep:
            raise SizeError("jeder Anteil liegt unter deinem Mindestauftrag; erhöhe das Budget oder nimm weniger Aktien")

    positions = []
    for r in sorted(live, key=lambda r: -r["weight"]):
        t = r["ticker"]
        p = {"ticker": t, "name": r["name"], "weight": round(r["weight"], 2),
             "source_weight": r["source_weight"], "dollars": dollars[t],
             "capped": t in capped}
        if prices.get(t):
            p["price"] = prices[t]
            p["shares"] = shares[t] if whole else round(dollars[t] / prices[t], 4)
        positions.append(p)
    invested = round(sum(p["dollars"] for p in positions), 2)
    return {
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer,
        "investable": investable, "max_position_pct": cap_pct, "cap": cap,
        "min_order_usd": min_order, "whole_shares": bool(whole),
        "positions": positions, "invested": invested,
        "idle": round(investable - invested, 2), "dropped": dropped,
    }


# ---------------------------------------------------------------- Aufträge

def _num(x):
    """1250 -> "1250", 1118.13 -> "1118.13" (Alpaca will diese als Strings)."""
    x = round(float(x), 2)
    if abs(x - round(x)) < 0.005:
        return str(int(round(x)))
    return "%.2f" % x


def orders_from(result, source=""):
    """Vorgeschlagene Kaufaufträge, einer je Position, in der Form von Alpacas place_stock_order."""
    out = []
    for p in result["positions"]:
        if p["dollars"] <= 0:
            continue
        why = "%.2f%% des Aktienanteils" % p["weight"]
        if source:
            why = "%s, kopiert von %s" % (why, source)
        if p["capped"]:
            why += "; gedeckelt bei %s%% des Budgets von %s" % (_num(result["max_position_pct"]), money(result["budget"]))
        o = {"symbol": p["ticker"], "side": "buy"}
        if result["whole_shares"]:
            o["qty"] = str(int(p["shares"]))
        else:
            o["notional"] = _num(p["dollars"])
        o.update({"type": "market", "time_in_force": "day", "why": why})
        out.append(o)
    return out


def write_orders(path, orders, result, cfg, source=""):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc = {
        "created_by": "trader-budget",
        "created_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "status": "proposed",
        "paper": bool(cfg.get("paper", True)),
        "note": ("Nur Vorschläge. Nichts wird abgeschickt, bevor trader-risiko jeden Auftrag "
                 "freigibt und du im Chat Ja dazu sagst."),
        "source": source,
        "orders": orders,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------- Bericht

def money(x):
    x = float(x)
    if abs(x - round(x)) < 0.005:
        return "$" + "{:,.0f}".format(round(x))
    return "$" + "{:,.2f}".format(x)


def report(result, orders, source=""):
    r = result
    lines = []
    if source:
        lines.append("Kopiert wird: %s" % source)
    lines.append("Budget %s, Bargeldpuffer %s%% = %s, investiert werden %s, Obergrenze %s%% = %s je Aktie"
                 % (money(r["budget"]), _num(r["cash_buffer_pct"]), money(r["cash_buffer"]),
                    money(r["investable"]), _num(r["max_position_pct"]), money(r["cap"])))
    lines.append("")
    has_price = any("price" in p for p in r["positions"])
    head = "%-7s %-22s %8s %11s" % ("Ticker", "Name", "Gewicht", "Dollar")
    if has_price:
        head += " %10s %10s" % ("Kurs", "Aktien")
    lines.append(head + "  Hinweis")
    for p in r["positions"]:
        row = "%-7s %-22s %7.2f%% %11s" % (p["ticker"], (p["name"] or "")[:22], p["weight"], money(p["dollars"]))
        if has_price:
            row += " %10s %10s" % (money(p["price"]) if "price" in p else "", p.get("shares", ""))
        if p["capped"]:
            row += "  gedeckelt bei %s%%" % _num(r["max_position_pct"])
        lines.append(row)
    lines.append("%-7s %-22s %8s %11s" % ("", "investiert", "", money(r["invested"])))
    lines.append("%-7s %-22s %8s %11s" % ("", "Bargeldpuffer", "", money(r["cash_buffer"])))
    lines.append("%-7s %-22s %8s %11s" % ("", "ungenutzt", "", money(r["idle"])))
    if r["idle"] >= 1:
        lines.append("")
        lines.append("Ungenutzt %s: %s" % (money(r["idle"]), (
            "ganze Aktien gehen nicht glatt auf" if r["whole_shares"]
            else "jede Aktie ist an ihrer Obergrenze; nimm mehr Aktien dazu oder erhöhe max_position_pct")))
    for d in r["dropped"]:
        lines.append("Gestrichen %s: %s" % (d["ticker"], d["reason"]))
    lines.append("")
    lines.append("Vorgeschlagene Aufträge (Argumente für place_stock_order, nichts wurde abgeschickt):")
    for o in orders:
        amount = ("notional \"%s\"" % o["notional"]) if "notional" in o else ("qty \"%s\"" % o["qty"])
        lines.append("  %s %s %s, %s, %s" % (o["side"], o["symbol"], amount, o["type"], o["time_in_force"]))
    lines.append("")
    lines.append("Als Nächstes: trader-risiko prüft jeden Auftrag, danach fragt dich trader-broker bei jedem einzelnen nach Ja oder Nein.")
    lines.append("Bildung und Werkzeug. Keine Anlageberatung.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Positionsgrößen auf dein Budget zuschneiden (trader-budget).")
    ap.add_argument("--targets", default="trader/targets.json", help="Zielgewichte (Standard trader/targets.json)")
    ap.add_argument("--weights", help="Gewichte direkt angeben statt per Datei: AAPL=22.04,AXP=17.14")
    ap.add_argument("--config", default="trader/config.json", help="Regeln (Standard trader/config.json)")
    ap.add_argument("--budget", type=float, help="Budget aus der Konfiguration überschreiben")
    ap.add_argument("--top", type=int, help="nur die N größten Gewichte behalten")
    ap.add_argument("--prices", help="Kurs-JSON (nötig für --whole-shares; zeigt Aktienanzahlen)")
    ap.add_argument("--whole-shares", action="store_true", help="nur ganze Aktien kaufen (braucht --prices)")
    ap.add_argument("--cents", action="store_true", help="auf den Cent genau rechnen statt auf ganze Dollar")
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="die vorgeschlagenen Aufträge schreiben (Standard trader/orders.json)")
    ap.add_argument("--json", action="store_true", help="JSON ausgeben statt der Tabelle")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        source = ""
        if a.weights:
            rows = parse_weights(weights_from_arg(a.weights))
        else:
            data = read_json(a.targets, "Zieldatei (starte trader-kopie oder nutze --weights)")
            rows = parse_weights(data)
            if isinstance(data, dict):
                source = data.get("source", "")
        prices = load_prices(a.prices)
        if not prices and os.path.exists("trader/prices.json") and not a.prices:
            prices = load_prices("trader/prices.json")
        whole = True if a.whole_shares else None
        result = size(rows, cfg, a.budget, prices, whole, a.cents, a.top)
        orders = orders_from(result, source)
        if a.write:
            write_orders(a.write, orders, result, cfg, source)
    except SizeError as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps({"result": result, "orders": orders}, indent=2))
    else:
        print(report(result, orders, source))
        if a.write:
            print("%d vorgeschlagene Aufträge nach %s geschrieben" % (len(orders), a.write))
    return 0


if __name__ == "__main__":
    sys.exit(main())
