#!/usr/bin/env python3
"""Depot-Ausgleich (Rebalancing) für trader-ausgleich. Nur Standardbibliothek, kein Netz.

Vergleicht, was du hältst (trader/portfolio.json zu trader/prices.json), mit
deinen Zielgewichten (trader/targets.json, begrenzt durch deine Regeln) und schlägt
die Trades vor, die jede Aktie außerhalb des Bands zurück auf ihr Ziel bringen.

    python3 rebalance.py                      # nur Bericht
    python3 rebalance.py --band 5 --write     # schreibt trader/orders.json (Vorschläge)
    python3 rebalance.py --sell-untracked     # verkauft auch Positionen, die nicht in den Zielen stehen
    python3 rebalance.py --json

Die Regeln:
  value         Depotwert = Bargeld + jede Position zu ihrem Kurs
  targets       Gewichte auf 100 umgerechnet über den Aktienanteil, das ist der
                Depotwert minus dein Bargeld-Puffer; keine Aktie über max_position_pct
                des Depots (der Überschuss wird verteilt, wie bei trader-budget)
  drift         aktueller % des Depots minus Ziel-%, in Prozentpunkten
  band          nur Aktien, die MEHR als das Band abweichen, werden gehandelt (Standard 5
                Punkte, oder "rebalance_band_pct" in der Konfiguration); ein Trade bringt
                die Aktie ganz zurück auf ihr Ziel
  sells first   Verkäufe stehen vor den Käufen, damit das Geld da ist
  cash buffer   Käufe geben nur Bargeld über dem Puffer aus; reicht es nicht,
                werden alle Käufe um denselben Anteil verkleinert
  min / max     Trades unter min_order_usd werden übersprungen; ein Trade über
                max_order_usd wird auf das Maximum gekürzt (nach der Ausführung nochmal laufen lassen)
  untracked     Positionen, die nicht in den Zielen stehen, bleiben liegen und werden
                aufgelistet, außer mit --sell-untracked

Exit-Code 0, wenn es Trades vorzuschlagen gibt, 1, wenn nichts außerhalb des Bands liegt.
Keine Anlageberatung. Es sind nur Vorschläge, nichts passiert ohne dein Ja.
"""

import argparse
import datetime
import json
import math
import os
import sys

DEFAULTS = {"cash_buffer_pct": 10, "max_position_pct": 25, "max_order_usd": 2000, "min_order_usd": 5,
            "fractional": True, "blocked": [], "rebalance_band_pct": 5, "paper": True}
EPS = 1e-9


class RebalanceError(Exception):
    pass


def number(value, what, minimum=0):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise RebalanceError("%s muss eine endliche Zahl sein" % what)
    if not math.isfinite(result) or result < minimum:
        raise RebalanceError("%s muss endlich und mindestens %s sein" % (what, minimum))
    return result


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RebalanceError("Datei fehlt für %s: %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RebalanceError("%s ist kein gültiges JSON: %s" % (path, e))


def money(x):
    x = float(x)
    sign = "-" if x < -0.004 else ""
    s = "{:,.2f}".format(abs(x))
    return sign + "$" + (s[:-3] if s.endswith(".00") else s)


def fmt_qty(x):
    s = ("%.4f" % float(x)).rstrip("0").rstrip(".")
    return s or "0"


def parse_targets(doc, blocked=()):
    rows = doc.get("weights", doc) if isinstance(doc, dict) else doc
    out = {}
    if isinstance(rows, dict):
        rows = [{"ticker": k, "weight": v} for k, v in rows.items()]
    if not isinstance(rows, list):
        raise RebalanceError("Zielgewichte müssen eine Liste oder ein Objekt sein")
    if blocked is not None and not isinstance(blocked, (list, tuple, set)):
        raise RebalanceError("blocked muss eine Liste von Tickern sein")
    blocked = {str(b).strip().upper() for b in blocked or []}
    for r in rows or []:
        if not isinstance(r, dict):
            raise RebalanceError("jedes Zielgewicht muss ein Objekt sein")
        t = str(r.get("ticker") or r.get("symbol") or "").strip().upper()
        if not t:
            raise RebalanceError("Zielgewicht braucht einen Ticker")
        w = number(r.get("weight", 0), "Gewicht für %s" % t)
        if t and w > 0 and t not in blocked:
            out[t] = out.get(t, 0.0) + w
    tot = sum(out.values())
    if not math.isfinite(tot):
        raise RebalanceError("Summe der Gewichte ist zu groß")
    if not tot:
        raise RebalanceError("keine Zielgewichte (starte trader-kopie oder schreib trader/targets.json)")
    return {t: (w / tot) * 100.0 for t, w in out.items()}


def capped_targets(weights, sleeve, cap):
    """Dollar-Ziel pro Ticker, proportional zum Gewicht, keines über dem Deckel."""
    alloc, active, money_left = {}, dict(weights), sleeve
    while active:
        wsum = sum(active.values())
        trial = {t: money_left * (w / wsum) for t, w in active.items()}
        over = [t for t, v in trial.items() if v > cap + EPS]
        if not over:
            alloc.update(trial)
            break
        for t in over:
            alloc[t] = cap
            money_left -= cap
            del active[t]
        if money_left <= EPS:
            for t in active:
                alloc[t] = 0.0
            break
    return alloc


def holdings_of(portfolio):
    out = {}
    if not isinstance(portfolio, dict) or not isinstance(portfolio.get("positions", []), list):
        raise RebalanceError("Depot muss ein Objekt mit einer Positionsliste sein")
    for p in (portfolio or {}).get("positions", []):
        if not isinstance(p, dict):
            raise RebalanceError("jede Position muss ein Objekt sein")
        t = str(p.get("ticker") or p.get("symbol") or "").strip().upper()
        if not t:
            raise RebalanceError("Position braucht einen Ticker")
        n = p.get("shares", p.get("qty"))
        if n is None:
            lots = p.get("lots", [])
            if not isinstance(lots, list) or any(not isinstance(l, dict) for l in lots):
                raise RebalanceError("Lots müssen eine Liste von Objekten sein")
            n = sum(number(l.get("shares", 0), "Lot-Stückzahl") for l in lots)
        out[t] = out.get(t, 0.0) + number(n, "Stückzahl für %s" % t)
    return out


def plan(portfolio, prices, weights, cfg, band=None, sell_untracked=False):
    """Reine Funktion: die Abweichungstabelle und die vorgeschlagenen Trades."""
    c = dict(DEFAULTS)
    if cfg is not None and not isinstance(cfg, dict):
        raise RebalanceError("Konfiguration muss ein Objekt sein")
    c.update(cfg or {})
    band = number(c["rebalance_band_pct"] if band is None else band, "Band")
    if not isinstance(portfolio, dict):
        raise RebalanceError("Depot muss ein Objekt sein")
    cash = number(portfolio.get("cash", 0), "Bargeld")
    held = holdings_of(portfolio)
    weights = parse_targets(weights, c.get("blocked"))
    if not isinstance(prices, dict):
        raise RebalanceError("Kurse müssen ein Objekt sein")
    prices = {str(t).strip().upper(): number(p, "Kurs für %s" % t) for t, p in prices.items()}
    need = sorted(set(held) | set(weights), key=lambda t: (-weights.get(t, 0.0), t))
    missing = [t for t in need if not prices.get(t)]
    if missing:
        raise RebalanceError("kein Kurs für %s: trag ihn in trader/prices.json ein (trader-alarm: alerts.py quote %s --write)"
                             % (", ".join(missing), " ".join(missing)))
    value = {t: held.get(t, 0.0) * prices[t] for t in need}
    total = cash + sum(value.values())
    if not math.isfinite(total) or total <= 0:
        raise RebalanceError("das Depot ist leer: nichts auszugleichen (trader-budget berechnet einen ersten Kauf)")
    buf_pct = number(c["cash_buffer_pct"], "cash_buffer_pct")
    cap_pct = number(c["max_position_pct"], "max_position_pct")
    if not 0 <= buf_pct < 100 or not 0 < cap_pct <= 100 or band > 100:
        raise RebalanceError("ungültiges Prozentlimit oder Band (0 bis 100)")
    if not isinstance(c.get("fractional", True), bool):
        raise RebalanceError("fractional muss true oder false sein")
    buf = total * (buf_pct / 100.0)
    cap = total * (cap_pct / 100.0)
    target = capped_targets(weights, total - buf, cap)
    whole = not c.get("fractional", True)
    min_o = number(c.get("min_order_usd", 0), "min_order_usd")
    max_o = number(c.get("max_order_usd", 0), "max_order_usd")
    if max_o and min_o > max_o:
        raise RebalanceError("Mindestauftrag darf den Höchstauftrag nicht übersteigen")

    rows, sells, buys, notes = [], [], [], []
    for t in need:
        tracked = t in weights
        tgt = target.get(t, 0.0)
        cur_pct = (value[t] / total) * 100.0
        tgt_pct = (tgt / total) * 100.0
        drift = cur_pct - tgt_pct
        row = {"ticker": t, "shares": round(held.get(t, 0.0), 6), "price": prices[t], "value": round(value[t], 2),
               "current_pct": round(cur_pct, 2), "target_pct": round(tgt_pct, 2),
               "drift": round(drift, 2) if abs(drift) >= 0.005 else 0.0,
               "target_usd": round(tgt, 2), "tracked": tracked, "action": "halten"}
        rows.append(row)
        if not tracked:
            if sell_untracked and held.get(t, 0) > EPS:
                sells.append({"ticker": t, "usd": value[t], "qty": held[t], "all": True,
                              "why": "steht nicht in deinen Zielen; verkaufbare Stücke nach Limits und Präzision (--sell-untracked)"})
                row["action"] = "alles verkaufen"
            else:
                row["action"] = "nicht im Ziel, bleibt liegen"
            continue
        if abs(drift) <= band + EPS:
            continue
        delta = tgt - value[t]
        why = "%+.1f Punkte vom Ziel von %.1f%% abgewichen (Band %s)" % (drift, tgt_pct, _n(band))
        if delta < 0:
            sells.append({"ticker": t, "usd": -delta, "qty": None, "all": False, "why": why})
            row["action"] = "verkaufen"
        else:
            buys.append({"ticker": t, "usd": delta, "why": why})
            row["action"] = "kaufen"

    orders = []
    cash_after = cash
    for s in sells:
        p = prices[s["ticker"]]
        usd = s["usd"]
        if max_o and usd > max_o + EPS:
            notes.append("%s Verkauf auf deinen Höchstauftrag von %s gekürzt; nach der Ausführung nochmal laufen lassen" % (s["ticker"], money(max_o)))
            usd = max_o
        qty = min(usd / p, held[s["ticker"]])
        qty = math.floor(qty + EPS) if whole else math.floor(qty * 10000 + EPS) / 10000.0
        if qty <= EPS or qty * p < min_o - EPS:
            notes.append("%s Verkauf über %s liegt unter deinem Mindestauftrag von %s: übersprungen"
                         % (s["ticker"], money(qty * p), money(min_o)))
            continue
        cash_after += qty * p
        orders.append({"symbol": s["ticker"], "side": "sell", "qty": fmt_qty(qty), "type": "market",
                       "time_in_force": "day", "why": s["why"], "_usd": round(qty * p, 2)})

    spendable = max(cash_after - buf, 0.0)
    want = sum(b["usd"] for b in buys)
    scale = 1.0
    if want > spendable + EPS:
        scale = spendable / want if want else 0.0
        notes.append("Käufe auf %.0f%% verkleinert: nur %s sind über deinem Bargeld-Puffer von %s frei"
                     % (100 * scale, money(spendable), money(buf)))
    for b in buys:
        p = prices[b["ticker"]]
        usd = b["usd"] * scale
        if max_o and usd > max_o + EPS:
            notes.append("%s Kauf auf deinen Höchstauftrag von %s gekürzt; nach der Ausführung nochmal laufen lassen" % (b["ticker"], money(max_o)))
            usd = max_o
        if whole:
            qty = math.floor(usd / p + EPS)
            if qty < 1 or qty * p < min_o - EPS:
                notes.append("%s Kauf über %s reicht nicht für eine ganze Aktie oder deinen Mindestauftrag: übersprungen"
                             % (b["ticker"], money(usd)))
                continue
            o = {"symbol": b["ticker"], "side": "buy", "qty": str(int(qty)), "_usd": round(qty * p, 2)}
        else:
            usd = math.floor(usd + EPS)
            if usd < min_o - EPS or usd < 1:
                notes.append("%s Kauf über %s liegt unter deinem Mindestauftrag von %s: übersprungen" % (b["ticker"], money(usd), money(min_o)))
                continue
            o = {"symbol": b["ticker"], "side": "buy", "notional": str(int(usd)), "_usd": float(usd)}
        o.update({"type": "market", "time_in_force": "day", "why": b["why"]})
        orders.append(o)

    spent = sum(o["_usd"] for o in orders if o["side"] == "buy")
    return {"account_value": round(total, 2), "cash": round(cash, 2), "cash_buffer": round(buf, 2),
            "cap": round(cap, 2), "band": band, "rows": rows, "orders": orders, "notes": notes,
            "cash_after": round(cash_after - spent, 2)}


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def clean(orders):
    return [{k: v for k, v in o.items() if not k.startswith("_")} for o in orders]


def report(res):
    lines = ["Depot %s (Bargeld %s, Puffer %s, Deckel %s pro Aktie), Band %s Punkte"
             % (money(res["account_value"]), money(res["cash"]), money(res["cash_buffer"]), money(res["cap"]),
                _n(res["band"])), ""]
    lines.append("%-7s %10s %10s %11s %9s %9s %8s  %s" % ("Ticker", "Stück", "Kurs", "Wert", "jetzt %",
                                                        "Ziel %", "Abw.", "Aktion"))
    for r in res["rows"]:
        lines.append("%-7s %10s %10s %11s %8.2f%% %8.2f%% %+8.2f  %s"
                     % (r["ticker"], fmt_qty(r["shares"]), money(r["price"]), money(r["value"]),
                        r["current_pct"], r["target_pct"], r["drift"], r["action"]))
    lines.append("")
    if res["orders"]:
        lines.append("Vorgeschlagene Aufträge, Verkäufe zuerst (Argumente für place_stock_order, nichts wurde aufgegeben):")
        for o in res["orders"]:
            amt = ('notional "%s"' % o["notional"]) if "notional" in o else ('qty "%s"' % o["qty"])
            lines.append("  %s %s %s, %s, %s  (etwa %s)" % (o["side"], o["symbol"], amt, o["type"],
                                                             o["time_in_force"], money(o["_usd"])))
        lines.append("Bargeld danach, wenn alle ausgeführt werden: etwa %s." % money(res["cash_after"]))
    else:
        lines.append("Keine ausführbaren Vorschläge; beachte die Hinweise zu Limits und Mindestaufträgen."
                     if res["notes"] else "Nichts liegt außerhalb des Bands. Keine Trades.")
    for n in res["notes"]:
        lines.append("Hinweis: " + n)
    lines.append("")
    lines.append("Ein Verkauf kann einen steuerpflichtigen Gewinn auslösen: prüf zuerst trader-steuer (lots.py sell). Danach "
                 "prüft trader-risiko jeden Auftrag, und trader-broker fragt dich bei jedem einzeln nach Ja oder Nein.")
    lines.append("Lernwerkzeug. Keine Anlageberatung.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bringt das Depot zurück auf seine Zielgewichte (trader-ausgleich).")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--targets", default="trader/targets.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--band", type=float, help="Abweichungsband in Prozentpunkten (Standard 5)")
    ap.add_argument("--sell-untracked", action="store_true", help="Positionen verkaufen, die nicht in den Zielen stehen")
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="die Vorschläge schreiben (Standard trader/orders.json)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = read_json(a.config, "Konfiguration", required=False) or {}
        if not isinstance(cfg, dict):
            raise RebalanceError("Konfiguration muss ein Objekt sein")
        portfolio = read_json(a.portfolio, "Depot (deine Positionen und dein Bargeld)")
        pdoc = read_json(a.prices, "Kurse (trader-alarm: alerts.py quote --holdings --write)")
        if not isinstance(pdoc, dict) or not isinstance(pdoc.get("prices", pdoc), dict):
            raise RebalanceError("Kursdatei muss ein Objekt mit Kursen sein")
        prices = pdoc.get("prices", pdoc)
        weights = parse_targets(read_json(a.targets, "Ziele"), cfg.get("blocked"))
        res = plan(portfolio, prices, weights, cfg, a.band, a.sell_untracked)
        if a.write and res["orders"]:
            d = os.path.dirname(a.write)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(a.write, "w", encoding="utf-8") as fh:
                json.dump({"created_by": "trader-ausgleich",
                           "created_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
                           "status": "proposed", "paper": bool(cfg.get("paper", True)),
                           "note": ("Nur Vorschläge, Verkäufe zuerst. Nichts wird aufgegeben, bis trader-risiko "
                                    "jeden Auftrag freigibt und du im Chat Ja dazu sagst."),
                           "prices_as_of": pdoc.get("as_of", ""), "orders": clean(res["orders"])}, fh, indent=2)
                fh.write("\n")
    except RebalanceError as e:
        print("rebalance.py: %s" % e, file=sys.stderr)
        return 1
    if a.json:
        out = dict(res)
        out["orders"] = clean(res["orders"])
        print(json.dumps(out, indent=2))
    else:
        print(report(res))
        if a.write and res["orders"]:
            print("%d vorgeschlagene(r) Auftrag/Aufträge nach %s geschrieben." % (len(res["orders"]), a.write))
    return 0 if res["orders"] else 1


if __name__ == "__main__":
    sys.exit(main())
