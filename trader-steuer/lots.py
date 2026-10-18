#!/usr/bin/env python3
"""Kauf-Lots (cost basis) für trader-steuer. Nur Standardbibliothek, kein Netzwerk.

ACHTUNG: Die Regeln hier sind US-Steuerrecht (cost basis, wash sale,
tax-loss harvesting). In Deutschland gilt die Abgeltungsteuer. Keine
Steuerberatung: Das Tool rechnet nur mit den Lots, die du ihm gibst. Was für
dich gilt, entscheidet eine Steuerberatung.

    python3 lots.py report                          # nicht realisierter Gewinn/Verlust je Aktie und Lot
    python3 lots.py report --lots                   # jedes Lot, kurz- oder langfristig
    python3 lots.py sell KO 2 --price 88.50         # was der Verkauf von 2 KO realisieren würde (FIFO)
    python3 lots.py sell KO 2 --price 88.50 --method specific --lots KO-2
    python3 lots.py sell KO 2 --price 88.50 --record    # ausgeführt: Lots + Protokoll der Verkäufe aktualisieren
    python3 lots.py harvest --min-loss 100          # Kandidaten für Tax-Loss-Harvesting
    python3 lots.py harvest --min-loss 100 --write  # als Vorschläge nach trader/orders.json schreiben
    python3 lots.py wash KO                         # wäre ein Kauf von KO heute ein Wash Sale?

Die Lots liegen in trader/portfolio.json:

    {"cash": 500.0, "positions": [{"ticker": "KO", "shares": 7.8571, "lots": [
        {"id": "KO-1", "date": "2026-10-13", "shares": 7.8571, "cost": 708.0}]}]}

"cost" ist der gesamte Einstandswert des Lots in Dollar. Ein Lot ohne "id"
bekommt TICKER-N nach seiner Stelle in der Liste.

Die Regeln, die es anwendet (US-Bundessteuer, nur die Rechnung):
  Frist         langfristig, wenn MEHR als ein Jahr nach dem Kaufdatum verkauft
                wird, sonst kurzfristig
  Lot-Methoden  fifo (ältestes zuerst, die Standardeinstellung der meisten
                Broker), hifo (höchster Einstandswert zuerst) und specific
                (du nennst die Lots)
  Wash Sale     ein Verlust wird nicht anerkannt, soweit du dieselbe oder eine
                im Wesentlichen gleiche Aktie innerhalb von 30 Tagen vor oder
                nach dem Verkauf kaufst; GOOG/GOOGL und BRK.A/BRK.B zählen hier
                als eine Familie. Geprüft werden deine anderen Lots, die Käufe
                in trader/orders.json und die Verlustverkäufe, die du
                eingetragen hast. Ein IRA-Konto, die Konten deines Partners
                oder andere Broker sieht das Tool nicht, sie zählen aber auch.
"""

import argparse
import datetime
import json
import os
import sys

FAMILIES = [{"GOOGL", "GOOG"}, {"BRK.A", "BRK.B", "BRK-A", "BRK-B"}, {"FOXA", "FOX"},
            {"NWSA", "NWS"}, {"LLYVA", "LLYVK"}, {"LEN", "LEN.B"}, {"UA", "UAA"}]
EPS = 1e-9


class TaxError(Exception):
    pass


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TaxError("keine Datei für %s unter %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TaxError("%s ist kein gültiges JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def day(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        raise TaxError("kein Datum (JJJJ-MM-TT): %r" % s)


def money(x):
    x = float(x)
    sign = "-" if x < -0.004 else ""
    return "%s$%s" % (sign, "{:,.2f}".format(abs(x)))


def fmt_qty(x):
    s = ("%.6f" % float(x)).rstrip("0").rstrip(".")
    return s or "0"


def family(ticker):
    t = ticker.upper()
    for f in FAMILIES:
        if t in f:
            return set(f)
    return {t}


# ---------------------------------------------------------------- lots

def lots_of(portfolio):
    """{ticker: [lot, ...]} mit id, date (date), shares, cost, per_share; ältestes zuerst."""
    out = {}
    for p in (portfolio or {}).get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if not t:
            continue
        lots = []
        for i, l in enumerate(p.get("lots", []), 1):
            sh, cost = float(l.get("shares", 0)), float(l.get("cost", 0))
            if sh <= EPS:
                continue
            lots.append({"id": l.get("id") or "%s-%d" % (t, i), "date": day(l.get("date")),
                         "shares": sh, "cost": cost, "per_share": cost / sh})
        if not lots and float(p.get("shares", 0) or 0) > 0:
            raise TaxError("%s hat Aktien, aber keine Lots; trage seine Lots (Datum, Stück, Kosten) in "
                           "trader/portfolio.json ein" % t)
        lots.sort(key=lambda l: (l["date"], l["id"]))
        out[t] = lots
    return out


def anniversary(d):
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, day=28)


def term(acquired, sold):
    """long, wenn MEHR als ein Jahr gehalten (Verkauf nach dem ersten Jahrestag)."""
    return "long" if sold > anniversary(acquired) else "short"


def unrealized(portfolio, prices, today):
    rows, totals, missing = [], [], []
    for t, lots in sorted(lots_of(portfolio).items()):
        p = prices.get(t)
        if not p:
            missing.append(t)
            continue
        tot = {"ticker": t, "shares": 0.0, "cost": 0.0, "value": 0.0, "short": 0.0, "long": 0.0}
        for l in lots:
            value = l["shares"] * p
            g = value - l["cost"]
            tm = term(l["date"], today)
            rows.append({"ticker": t, "id": l["id"], "date": l["date"].isoformat(), "shares": l["shares"],
                         "cost": round(l["cost"], 2), "price": p, "value": round(value, 2),
                         "gain": round(g, 2), "term": tm,
                         "long_term_on": (anniversary(l["date"]) + datetime.timedelta(days=1)).isoformat()})
            tot["shares"] += l["shares"]
            tot["cost"] += l["cost"]
            tot["value"] += value
            tot[tm] += g
        tot["gain"] = round(tot["value"] - tot["cost"], 2)
        tot["pct"] = round(100.0 * tot["gain"] / tot["cost"], 2) if tot["cost"] else 0.0
        if abs(tot["pct"]) < 0.005:
            tot["pct"] = 0.0
        for k in ("cost", "value", "short", "long"):
            tot[k] = round(tot[k], 2)
        totals.append(tot)
    return rows, totals, missing


def pick_lots(lots, shares, method="fifo", ids=None):
    """[(lot, shares_taken)] für den Verkauf von `shares` nach der Lot-Methode."""
    held = sum(l["shares"] for l in lots)
    if shares > held + EPS:
        raise TaxError("Verkauf von %s Aktien, aber nur %s sind im Bestand" % (fmt_qty(shares), fmt_qty(held)))
    if method == "fifo":
        order = list(lots)
    elif method == "hifo":
        order = sorted(lots, key=lambda l: -l["per_share"])
    elif method == "specific":
        if not ids:
            raise TaxError("--method specific braucht --lots ID,ID")
        by_id = {l["id"]: l for l in lots}
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise TaxError("kein solches Lot: %s (Lots: %s)" % (", ".join(unknown), ", ".join(by_id)))
        order = [by_id[i] for i in ids]
        if shares > sum(l["shares"] for l in order) + EPS:
            raise TaxError("die genannten Lots enthalten weniger als %s Aktien" % fmt_qty(shares))
    else:
        raise TaxError("unbekannte Methode %r (fifo, hifo, specific)" % method)
    out, left = [], shares
    for l in order:
        if left <= EPS:
            break
        take = min(left, l["shares"])
        out.append((l, take))
        left -= take
    return out


def simulate_sale(lots, shares, price, sold, method="fifo", ids=None):
    used, res = [], {"proceeds": 0.0, "basis": 0.0, "short": 0.0, "long": 0.0}
    for l, take in pick_lots(lots, shares, method, ids):
        basis = l["per_share"] * take
        proceeds = price * take
        tm = term(l["date"], sold)
        used.append({"id": l["id"], "acquired": l["date"].isoformat(), "shares": round(take, 6),
                     "basis": round(basis, 2), "proceeds": round(proceeds, 2),
                     "gain": round(proceeds - basis, 2), "term": tm})
        res["proceeds"] += proceeds
        res["basis"] += basis
        res[tm] += proceeds - basis
    res = {k: round(v, 2) for k, v in res.items()}
    res["gain"] = round(res["proceeds"] - res["basis"], 2)
    res["lots"] = used
    return res


# ---------------------------------------------------------------- wash sales

def planned_buys(orders, prices):
    out = []
    for o in orders or []:
        if str(o.get("side", "")).lower() != "buy":
            continue
        t = str(o.get("symbol", "")).upper()
        sh = None
        if o.get("qty") is not None:
            sh = float(o["qty"])
        elif o.get("notional") is not None and prices.get(t):
            sh = float(o["notional"]) / prices[t]
        out.append({"ticker": t, "shares": sh})
    return out


def wash_check(ticker, sold, used, portfolio, orders, prices):
    """Käufe, die einen Verlust aus diesem Verkauf zum Wash Sale machen würden.

    used: die vom Verkauf verbrauchten Lots [{id, shares}]. Aktien, die du
    innerhalb von 30 Tagen vor oder nach dem Verkauf gekauft hast und danach
    noch hältst, zählen, ebenso geplante Käufe in trader/orders.json."""
    fam = family(ticker)
    sold_from = {u["id"]: u["shares"] for u in used}
    hits = []
    lo, hi = sold - datetime.timedelta(days=30), sold + datetime.timedelta(days=30)
    for t, lots in lots_of(portfolio).items():
        if t not in fam:
            continue
        for l in lots:
            keep = l["shares"] - sold_from.get(l["id"], 0.0)
            if keep > EPS and lo <= l["date"] <= hi:
                hits.append({"ticker": t, "date": l["date"].isoformat(), "shares": round(keep, 6),
                             "source": "Lot %s, das du noch hältst" % l["id"]})
    for b in planned_buys(orders, prices):
        if b["ticker"] in fam:
            hits.append({"ticker": b["ticker"], "date": "planned", "shares": b["shares"],
                         "source": "ein geplanter Kauf in trader/orders.json"})
    return hits


def disallowed(loss, shares_sold, hits):
    """Nicht anerkannter Verlust: im Verhältnis zu den Ersatz-Aktien (alles, wenn unbekannt)."""
    if loss >= 0 or not hits:
        return 0.0
    repl = 0.0
    for h in hits:
        if h["shares"] is None:
            return round(-loss, 2)
        repl += h["shares"]
    return round(-loss * min(1.0, repl / shares_sold), 2) if shares_sold else 0.0


def recent_loss_sales(ticker, when, realized):
    fam = family(ticker)
    out = []
    for r in realized or []:
        if str(r.get("symbol", "")).upper() not in fam or float(r.get("gain", 0)) >= 0:
            continue
        d = day(r.get("date"))
        if d <= when <= d + datetime.timedelta(days=30):
            out.append(r)
    return out


def rebuy_date(sold):
    return (sold + datetime.timedelta(days=31)).isoformat()


# ---------------------------------------------------------------- harvest

def harvest(portfolio, prices, today, min_loss=100.0, min_pct=0.0, orders=None):
    cands = []
    for t, lots in sorted(lots_of(portfolio).items()):
        p = prices.get(t)
        if not p:
            continue
        losers = [l for l in lots if l["shares"] * p < l["cost"] - 0.005]
        if not losers:
            continue
        shares = sum(l["shares"] for l in losers)
        cost = sum(l["cost"] for l in losers)
        loss = shares * p - cost
        pct = 100.0 * loss / cost if cost else 0.0
        if -loss < min_loss or -pct < min_pct:
            continue
        sale = simulate_sale(lots, shares, p, today, "specific", [l["id"] for l in losers])
        hits = wash_check(t, today, sale["lots"], portfolio, orders, prices)
        cands.append({
            "ticker": t, "price": p, "shares": round(shares, 6), "lots": [l["id"] for l in losers],
            "all_lots": len(losers) == len(lots),
            "fifo_same": [l["id"] for l in lots[:len(losers)]] == [l["id"] for l in losers],
            "loss": round(loss, 2), "pct": round(pct, 2),
            "short": sale["short"], "long": sale["long"], "wash": hits,
            "disallowed": disallowed(loss, shares, hits), "rebuy_on_or_after": rebuy_date(today),
        })
    cands.sort(key=lambda c: c["loss"])
    return cands


def harvest_orders(cands, include_wash=False):
    out = []
    for c in cands:
        if c["wash"] and not include_wash:
            continue
        why = ("Vorschlag Tax-Loss-Harvesting: Lots %s verkaufen, geschätzter Verlust %s (%s kurzfristig, %s "
               "langfristig). %s nicht vor dem %s wieder kaufen (Wash Sale). Keine Steuerberatung."
               % (", ".join(c["lots"]), money(c["loss"]), money(c["short"]), money(c["long"]),
                  "/".join(sorted(family(c["ticker"]))), c["rebuy_on_or_after"]))
        if not c["fifo_same"]:
            why += (" Das sind nicht deine ältesten Lots: Sag deinem Broker, dass er genau diese Lots verkaufen soll, "
                    "sonst verkauft er eventuell nach FIFO und realisiert ein anderes Ergebnis.")
        out.append({"symbol": c["ticker"], "side": "sell", "qty": fmt_qty(c["shares"]),
                    "type": "market", "time_in_force": "day", "why": why})
    return out


# ---------------------------------------------------------------- record a sale

def record_sale(portfolio, ticker, sale, sold, price, state):
    t = ticker.upper()
    taken = {u["id"]: u for u in sale["lots"]}
    for p in portfolio.get("positions", []):
        if str(p.get("ticker", "")).upper() != t:
            continue
        new = []
        for i, l in enumerate(p.get("lots", []), 1):
            lid = l.get("id") or "%s-%d" % (t, i)
            l["id"] = lid
            if lid in taken:
                sh = float(l["shares"])
                take = taken[lid]["shares"]
                l["cost"] = round(float(l["cost"]) * (1 - take / sh), 2) if sh else 0.0
                l["shares"] = round(sh - take, 6)
            if float(l["shares"]) > EPS:
                new.append(l)
        p["lots"] = new
        p["shares"] = round(sum(float(l["shares"]) for l in new), 6)
    portfolio["positions"] = [p for p in portfolio.get("positions", []) if float(p.get("shares", 0)) > EPS]
    portfolio["cash"] = round(float(portfolio.get("cash", 0)) + sale["proceeds"], 2)
    state.setdefault("realized", []).append({
        "date": sold.isoformat(), "symbol": t, "shares": round(sum(u["shares"] for u in sale["lots"]), 6),
        "price": price, "proceeds": sale["proceeds"], "basis": sale["basis"], "gain": sale["gain"],
        "short": sale["short"], "long": sale["long"], "wash_disallowed": sale.get("wash_disallowed", 0.0),
        "lots": sale["lots"]})
    return portfolio, state


def realized_ytd(state, year):
    out = {"short": 0.0, "long": 0.0, "disallowed": 0.0, "count": 0}
    for r in (state or {}).get("realized", []):
        if str(r.get("date", "")).startswith(str(year)):
            out["short"] += float(r.get("short", 0))
            out["long"] += float(r.get("long", 0))
            out["disallowed"] += float(r.get("wash_disallowed", 0))
            out["count"] += 1
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in out.items()}


# ---------------------------------------------------------------- CLI

NOT_ADVICE = ("Keine Steuerberatung: nur Rechnung mit deinen Lots, nach US-Steuerrecht (in Deutschland gilt die "
              "Abgeltungsteuer). Die Wash-Sale-Regel zählt auch Käufe in deinem IRA, den Konten deines Partners "
              "und bei anderen Brokern, die dieses Tool nicht sehen kann. Sprich mit einer Steuerberatung, "
              "bevor du handelst.")
TERM_DE = {"short": "kurzfristig", "long": "langfristig"}


def _inputs(a):
    portfolio = read_json(a.portfolio, "portfolio (lots)")
    pdoc = read_json(a.prices, "prices", required=False) or {}
    prices = {str(k).upper(): float(v) for k, v in (pdoc.get("prices", pdoc) or {}).items()
              if isinstance(v, (int, float))}
    state = read_json(a.state, "tax state", required=False) or {"realized": []}
    odoc = read_json(a.orders, "orders", required=False) or {}
    orders = odoc.get("orders", []) if isinstance(odoc, dict) else odoc
    today = day(a.today) if a.today else datetime.date.today()
    return portfolio, prices, state, orders, today


def cmd_report(a):
    portfolio, prices, state, orders, today = _inputs(a)
    rows, totals, missing = unrealized(portfolio, prices, today)
    if a.json:
        print(json.dumps({"lots": rows, "positions": totals, "missing_prices": missing,
                          "realized_ytd": realized_ytd(state, today.year)}, indent=2))
        return 0
    print("%-7s %10s %12s %12s %12s %8s" % ("Ticker", "Stück", "Kosten", "Wert", "Gewinn", "Gew. %"))
    for t in totals:
        print("%-7s %10s %12s %12s %12s %7.2f%%" % (t["ticker"], fmt_qty(t["shares"]), money(t["cost"]),
                                                   money(t["value"]), money(t["gain"]), t["pct"]))
    if a.lots:
        print("")
        print("%-10s %-11s %10s %11s %11s %-12s %s" % ("Lot", "gekauft", "Stück", "Kosten", "Gewinn", "Frist", "langfristig ab"))
        for r in rows:
            print("%-10s %-11s %10s %11s %11s %-12s %s" % (r["id"], r["date"], fmt_qty(r["shares"]), money(r["cost"]),
                                                       money(r["gain"]), TERM_DE[r["term"]], r["long_term_on"]))
    for t in missing:
        print("Kein Kurs für %s: Trage ihn in trader/prices.json ein (trader-alarm: alerts.py quote %s --write)" % (t, t))
    ytd = realized_ytd(state, today.year)
    print("")
    print("Realisiert %d (eingetragene Verkäufe): %s kurzfristig, %s langfristig, %s durch Wash Sales nicht anerkannt."
          % (today.year, money(ytd["short"]), money(ytd["long"]), money(ytd["disallowed"])))
    print(NOT_ADVICE)
    return 0


def cmd_sell(a):
    portfolio, prices, state, orders, today = _inputs(a)
    t = a.ticker.upper()
    lots = lots_of(portfolio).get(t)
    if not lots:
        raise TaxError("keine Lots für %s in %s" % (t, a.portfolio))
    price = a.price or prices.get(t)
    if not price:
        raise TaxError("kein Kurs für %s: gib --price an" % t)
    ids = [i.strip() for i in (a.lots or "").split(",") if i.strip()]
    sale = simulate_sale(lots, float(a.shares), float(price), today, a.method, ids)
    hits = wash_check(t, today, sale["lots"], portfolio, orders, prices) if sale["gain"] < 0 else []
    sale["wash"] = hits
    sale["wash_disallowed"] = disallowed(sale["gain"], float(a.shares), hits)
    if a.json:
        print(json.dumps(sale, indent=2))
    else:
        print("Verkauf von %s %s zu %s am %s (%s):" % (fmt_qty(a.shares), t, money(price), today, a.method))
        for u in sale["lots"]:
            print("  Lot %-8s gekauft %s  %s Aktien  Einstand %s  Erlös %s  %s (%s)"
                  % (u["id"], u["acquired"], fmt_qty(u["shares"]), money(u["basis"]), money(u["proceeds"]),
                     money(u["gain"]), TERM_DE[u["term"]]))
        print("Summe: Erlös %s, Einstand %s, %s %s (%s kurzfristig, %s langfristig)."
              % (money(sale["proceeds"]), money(sale["basis"]), "Gewinn" if sale["gain"] >= 0 else "Verlust",
                 money(sale["gain"]), money(sale["short"]), money(sale["long"])))
        for h in hits:
            print("WASH SALE: %s (%s Aktien, %s)." % (h["source"], fmt_qty(h["shares"]) if h["shares"] else "?",
                                                        h["date"]))
        if hits:
            print("Bis zu %s dieses Verlusts würden nicht anerkannt und auf den Einstandswert der Ersatz-Aktien aufgeschlagen."
                  % money(sale["wash_disallowed"]))
        elif sale["gain"] < 0:
            print("Kein Wash-Sale-Konflikt gefunden. %s nicht vor dem %s wieder kaufen."
                  % ("/".join(sorted(family(t))), rebuy_date(today)))
        print(NOT_ADVICE)
    if a.record:
        portfolio, state = record_sale(portfolio, t, sale, today, float(price), state)
        write_json(a.portfolio, portfolio)
        write_json(a.state, state)
        if not a.json:
            print("Eingetragen: Lots in %s aktualisiert, Verkauf in %s protokolliert." % (a.portfolio, a.state))
    return 0


def cmd_harvest(a):
    portfolio, prices, state, orders, today = _inputs(a)
    cands = harvest(portfolio, prices, today, a.min_loss, a.min_pct,
                    [o for o in orders if str(o.get("side", "")).lower() == "buy"])
    props = harvest_orders(cands, a.include_wash)
    if a.write and props:
        write_json(a.write, {
            "created_by": "trader-steuer", "created_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "status": "proposed",
            "note": ("Vorschläge für Tax-Loss-Harvesting, keine Steuerberatung. Nichts wird abgeschickt, bevor "
                     "trader-risiko jeden Auftrag bestanden hat und du im Chat Ja dazu sagst."),
            "orders": props})
    if a.json:
        print(json.dumps({"candidates": cands, "orders": props}, indent=2))
        return 0 if cands else 1
    if not cands:
        print("Kein Lot hat einen Verlust von mindestens %s%s." % (money(a.min_loss),
                                                      (" und %s%%" % a.min_pct) if a.min_pct else ""))
        print(NOT_ADVICE)
        return 1
    for c in cands:
        print("%-6s %s Aktien verkaufen (Lots %s) zu %s: geschätzter Verlust %s (%.2f%%), %s kurzfristig, %s langfristig"
              % (c["ticker"], fmt_qty(c["shares"]), ", ".join(c["lots"]), money(c["price"]), money(c["loss"]),
                 c["pct"], money(c["short"]), money(c["long"])))
        for h in c["wash"]:
            print("       WASH-SALE-RISIKO: %s (%s)." % (h["source"], h["date"]))
        if c["wash"]:
            print("       Bis zu %s des Verlusts würden nicht anerkannt. Nicht vorgeschlagen%s."
                  % (money(c["disallowed"]), "" if not a.include_wash else ", außer mit --include-wash"))
        else:
            print("       Nach allem, was dieses Tool sieht, kein Wash Sale. %s nicht vor dem %s kaufen."
                  % ("/".join(sorted(family(c["ticker"]))), c["rebuy_on_or_after"]))
    if a.write:
        print("%d vorgeschlagene Verkaufsaufträge nach %s geschrieben. Als Nächstes prüft trader-risiko sie." % (len(props), a.write)
              if props else "Nichts geschrieben: Jeder Kandidat hat einen Wash-Sale-Konflikt.")
    print(NOT_ADVICE)
    return 0


def cmd_wash(a):
    portfolio, prices, state, orders, today = _inputs(a)
    t = a.ticker.upper()
    when = day(a.date) if a.date else today
    hits = recent_loss_sales(t, when, state.get("realized", []))
    if not hits:
        print("Kein eingetragener Verlustverkauf von %s in den 30 Tagen vor dem %s. Ein Kauf dann ist kein Wash Sale "
              "gegenüber etwas, das dieses Tool kennt." % ("/".join(sorted(family(t))), when))
        print(NOT_ADVICE)
        return 0
    for r in hits:
        print("WASH SALE: Du hast am %s %s %s mit %s Verlust verkauft. Ein Kauf von %s vor dem %s würde diesen Verlust nicht anerkennen."
              % (r["date"], fmt_qty(r["shares"]), r["symbol"], money(r["gain"]), t, rebuy_date(day(r["date"]))))
    print(NOT_ADVICE)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Kauf-Lots, Gewinne, Harvesting und Wash Sales (trader-steuer). "
                                             "US-Steuerrecht, keine Steuerberatung.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--prices", default="trader/prices.json")
        p.add_argument("--state", default="trader/state/tax.json")
        p.add_argument("--orders", default="trader/orders.json", help="geplante Käufe zählen für Wash Sales")
        p.add_argument("--today", help="JJJJ-MM-TT (Standard: heute)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("report", help="nicht realisierte Gewinne und Verluste")
    common(p)
    p.add_argument("--lots", action="store_true", help="jedes Lot zeigen")
    p = sub.add_parser("sell", help="was ein Verkauf realisieren würde")
    common(p)
    p.add_argument("ticker")
    p.add_argument("shares", type=float)
    p.add_argument("--price", type=float)
    p.add_argument("--method", default="fifo", choices=["fifo", "hifo", "specific"])
    p.add_argument("--lots", help="Lot-IDs für --method specific: KO-1,KO-2")
    p.add_argument("--record", action="store_true", help="der Verkauf wurde ausgeführt: Lots aktualisieren und protokollieren")
    p = sub.add_parser("harvest", help="Kandidaten für Tax-Loss-Harvesting")
    common(p)
    p.add_argument("--min-loss", type=float, default=100.0, help="Dollar (Standard 100)")
    p.add_argument("--min-pct", type=float, default=0.0, help="Prozent der Kosten (Standard 0)")
    p.add_argument("--include-wash", action="store_true", help="auch Kandidaten mit Wash-Sale-Konflikt vorschlagen")
    p.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                   help="Vorschläge schreiben (Standard trader/orders.json)")
    p = sub.add_parser("wash", help="wäre ein Kauf dieser Aktie jetzt ein Wash Sale?")
    common(p)
    p.add_argument("ticker")
    p.add_argument("--date", help="das geplante Kaufdatum (Standard heute)")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"report": cmd_report, "sell": cmd_sell, "harvest": cmd_harvest, "wash": cmd_wash}[a.cmd](a)
    except TaxError as e:
        print("lots.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
