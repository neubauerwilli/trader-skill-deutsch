#!/usr/bin/env python3
"""Risikoprüfung vor dem Handel für trader-risiko. Nur Standardbibliothek, kein Netzwerk.

Liest die vorgeschlagenen Aufträge (trader/orders.json) und prüft jeden gegen
deine Regeln (trader/config.json), dein Konto (trader/portfolio.json) und die
Kurse (trader/prices.json). Jeder Auftrag bekommt PASS oder BLOCK mit den
Gründen. Die Aufträge werden in Dateireihenfolge geprüft, als wäre jeder
PASS schon ausgeführt. So fallen fünf Käufe auf, die einzeln in Ordnung sind,
zusammen aber den Bargeldpuffer brechen.

    python3 check.py                       # die trader/-Dateien, schreibt trader/state/risk.json
    python3 check.py --orders trader/orders.json --json
    python3 check.py --no-write            # prüfen, ohne die Urteile zu speichern

Was einen Auftrag blockt:

  Format        Symbol oder Seite fehlt, qty und notional beide oder keins,
                notional bei etwas anderem als einem Markt-Tagesauftrag
                (Alpacas Regel), Limit-Auftrag ohne Limitpreis, unbekannter Typ
  Wertpapier    Optionen (OCC-Symbole), außer "allow_options": true; Krypto
                und alles, was keine Aktie oder kein ETF ist
  Sperrliste    ein Ticker auf deiner "blocked"-Liste
  Größe         unter min_order_usd, über max_order_usd
  Position      die Aktie läge über max_position_pct des Kontos
  Bargeld       der Kauf würde in deinen Bargeldpuffer greifen oder
                Margin (geliehenes Geld) brauchen
  Leerverkauf   mehr verkaufen, als du hältst, außer "allow_short": true
  Doppelt       dasselbe Symbol mit derselben Seite zweimal in diesem Stapel
                oder heute schon abgeschickt (trader/state/broker.json)
  Tageslimit    mehr als max_orders_per_day Aufträge heute abgeschickt

Exit-Code 0, wenn jeder Auftrag besteht, 1, wenn ein Auftrag geblockt wird.
Die Urteile landen in trader/state/risk.json mit einem Fingerabdruck je Auftrag,
damit trader-broker einen Auftrag nur dann als bereit zeigt, wenn GENAU dieser
Auftrag heute bestanden hat.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_usd": 2000,
    "min_order_usd": 5, "max_orders_per_day": 10, "paper": True, "blocked": [],
    "allow_options": False, "allow_margin": False, "allow_short": False,
}
ALPACA_FIELDS = ("symbol", "side", "qty", "notional", "type", "time_in_force", "limit_price",
                 "stop_price", "trail_price", "trail_percent", "extended_hours", "client_order_id",
                 "order_class", "take_profit_limit_price", "stop_loss_stop_price",
                 "stop_loss_limit_price", "advanced_instructions")
TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop"}
TIFS = {"day", "gtc", "opg", "cls", "ioc", "fok"}
OCC = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
TICKER = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")
CENT = 0.005


class RiskError(Exception):
    pass


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("keine %s unter %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s ist kein gültiges JSON: %s" % (path, e))


def fingerprint(order):
    """Dieselbe Funktion wie in trader-broker/ticket.py: der genau geprüfte Auftrag."""
    core = {k: order[k] for k in ALPACA_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def money(x):
    return "$" + "{:,.2f}".format(float(x)).replace(".00", "")


def account(cfg, portfolio, prices):
    """Startkonto: Bargeld und Positionswerte. Kein Depot = ein frisches Konto,
    das mit dem Budget gefüllt ist."""
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("keine trader/portfolio.json und kein Budget in der Konfiguration: "
                            "es gibt nichts, wogegen die Aufträge geprüft werden könnten")
        return {"cash": budget, "shares": {}, "values": {}}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json braucht eine Zahl bei \"cash\"")
    shares, values = {}, {}
    for p in portfolio.get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if not t:
            continue
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0) + n
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            values[t] = basis
            warnings.append("kein Kurs für %s: bewertet zum Einstandswert %s" % (t, money(basis)))
    return {"cash": cash, "shares": shares, "values": values}, warnings


def placed_today(state, today):
    """Aufträge, die trader-broker heute als abgeschickt verzeichnet hat."""
    out = []
    for rec in (state or {}).get("orders", []):
        if rec.get("status") == "placed" and str(rec.get("date") or str(rec.get("at", ""))[:10]) == today:
            out.append(rec)
    return out


def check(orders, cfg, portfolio=None, prices=None, state=None, today=None):
    """Reine Funktion. Gibt {"results": [...], "pass": n, "block": n, "warnings": [...]} zurück."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    prices = {str(k).upper(): float(v) for k, v in (prices or {}).items() if _f(v)}
    today = today or datetime.date.today().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    blocked = {str(b).upper() for b in c.get("blocked") or []}
    done_today = placed_today(state, today)
    count_today = len(done_today)
    placed_keys = {(str(r.get("symbol", "")).upper(), r.get("side")) for r in done_today}
    seen = set()
    results = []
    if not c.get("paper", True):
        top_warn.append("LIVE-Konto (\"paper\": false): Diese Aufträge setzen echtes Geld ein")

    for i, o in enumerate(orders, 1):
        reasons, warns = [], []
        sym = str(o.get("symbol", "")).strip().upper()
        side = str(o.get("side", "")).strip().lower()
        typ = str(o.get("type", "market")).strip().lower()
        tif = str(o.get("time_in_force", "day")).strip().lower()
        qty, notional = o.get("qty"), o.get("notional")
        value = None

        # format
        if not sym:
            reasons.append("Format: kein Symbol")
        if side not in ("buy", "sell"):
            reasons.append("Format: side muss buy oder sell sein, bekommen: %r" % o.get("side"))
        if (qty is None) == (notional is None):
            reasons.append("Format: gib genau eins von qty oder notional an")
        for k in ("qty", "notional", "limit_price", "stop_price"):
            if o.get(k) is not None:
                v = _f(o.get(k))
                if v is None or v <= 0:
                    reasons.append("Format: %s muss eine positive Zahl sein, bekommen: %r" % (k, o.get(k)))
                elif not isinstance(o.get(k), str):
                    warns.append("sende %s als String (\"%s\"), so wie place_stock_order es typisiert"
                                 % (k, o.get(k)))
        if typ not in TYPES:
            reasons.append("Format: unbekannter Auftragstyp %r" % typ)
        if tif not in TIFS:
            reasons.append("Format: unbekanntes time_in_force %r" % tif)
        if notional is not None and (typ != "market" or tif != "day"):
            reasons.append("Format: ein notional-Auftrag (in Dollar) muss ein Markt-Auftrag mit time_in_force day sein")
        if typ in ("limit", "stop_limit") and o.get("limit_price") is None:
            reasons.append("Format: ein %s-Auftrag braucht einen limit_price" % typ)
        if typ in ("stop", "stop_limit") and o.get("stop_price") is None:
            reasons.append("Format: ein %s-Auftrag braucht einen stop_price" % typ)
        if o.get("extended_hours") and not (typ == "limit" and tif == "day"):
            reasons.append("Format: extended hours braucht einen Limit-Auftrag mit time_in_force day")
        if o.get("order_class") or o.get("advanced_instructions"):
            warns.append("erweiterter Auftrag (%s): prüfe jeden Teil, bevor du Ja sagst"
                         % (o.get("order_class") or "advanced_instructions"))

        # asset
        if sym and OCC.match(sym.replace(" ", "")):
            if not c.get("allow_options"):
                reasons.append("Wertpapier: %s ist ein Optionskontrakt; Optionen sind aus "
                               "(\"allow_options\": false)" % sym)
            else:
                warns.append("Optionsauftrag: Optionen können 100%% von dem verlieren, was du zahlst")
        elif sym and ("/" in sym or not TICKER.match(sym)):
            reasons.append("Wertpapier: %s sieht nicht nach einem US-Aktien- oder ETF-Ticker aus; "
                           "dieses Paket handelt nur Aktien und ETFs" % sym)

        # blocked list
        if sym in blocked:
            reasons.append("Sperrliste: %s steht auf deiner Sperrliste" % sym)

        # dollar value of the order
        ref = None
        if typ in ("limit", "stop_limit"):
            ref = _f(o.get("limit_price"))
        elif typ == "stop":
            ref = _f(o.get("stop_price"))
        if ref is None:
            ref = prices.get(sym)
        if notional is not None and _f(notional):
            value = _f(notional)
        elif qty is not None and _f(qty):
            if ref:
                value = _f(qty) * ref
            else:
                reasons.append("Preis: kein Kurs für %s, um einen Auftrag über %s Aktien zu bewerten; trage ihn in "
                               "trader/prices.json ein (trader-alarm: alerts.py quote %s --write)"
                               % (sym, qty, sym))

        if value is not None:
            if value < float(c.get("min_order_usd") or 0) - CENT:
                reasons.append("Größe: %s liegt unter deinem Mindestauftrag von %s"
                               % (money(value), money(c["min_order_usd"])))
            if c.get("max_order_usd") and value > float(c["max_order_usd"]) + CENT:
                reasons.append("Größe: %s liegt über deinem Höchstauftrag von %s"
                               % (money(value), money(c["max_order_usd"])))

        # Wirkung auf das Konto, der Reihe nach simuliert
        cash = acct["cash"]
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        total = cash + sum(acct["values"].values())
        buffer_usd = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy":
            after_cash = cash - value
            if after_cash < -CENT and not c.get("allow_margin"):
                reasons.append("Bargeld: braucht %s, aber nur %s Bargeld da; das wäre Margin (geliehenes Geld)"
                               % (money(value), money(cash)))
            elif after_cash < buffer_usd - CENT:
                reasons.append("Bargeld: lässt %s Bargeld übrig, unter deinem Puffer von %s%% = %s"
                               % (money(max(after_cash, 0)), _pct(c["cash_buffer_pct"]), money(buffer_usd)))
            pos_after = held_value + value
            limit = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > limit + CENT:
                reasons.append("Position: %s wäre %s, %.1f%% des Kontos von %s, über deiner Obergrenze von %s%%"
                               % (sym, money(pos_after), 100.0 * pos_after / total, money(total),
                                  _pct(c["max_position_pct"])))
        if side == "sell" and sym:
            if qty is not None and _f(qty):
                over = _f(qty) > held + 1e-9
            elif value is not None:
                over = value > held_value + CENT
            else:
                over = held <= 0
            if over and not c.get("allow_short"):
                reasons.append("Leerverkauf: Du verkaufst mehr %s, als du hältst (%s Aktien); Leerverkäufe sind aus"
                               % (sym, _num(held)))

        # Doppelte und das Tageslimit
        key = (sym, side)
        if key in seen:
            reasons.append("Doppelt: ein zweites %s %s in diesem Stapel" % (side, sym))
        if key in placed_keys:
            reasons.append("Doppelt: ein %s %s wurde heute schon abgeschickt" % (side, sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            reasons.append("Tageslimit: %d heute abgeschickt plus %d früher in diesem Stapel bestanden, "
                           "dein Limit sind %d am Tag" % (count_today, passed_so_far, cap_n))
        seen.add(key)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS" and value is not None:
            if side == "buy":
                acct["cash"] -= value
                acct["values"][sym] = acct["values"].get(sym, 0.0) + value
                if ref:
                    acct["shares"][sym] = held + value / ref
            else:
                acct["cash"] += value
                acct["values"][sym] = max(acct["values"].get(sym, 0.0) - value, 0.0)
                if qty is not None:
                    acct["shares"][sym] = held - _f(qty)
                elif ref:
                    acct["shares"][sym] = held - value / ref
        results.append({
            "index": i, "symbol": sym, "side": side,
            "amount": money(_f(notional) or 0) if notional is not None else ("%s Aktien" % qty),
            "value_usd": round(value, 2) if value is not None else None,
            "fingerprint": fingerprint(o), "verdict": verdict,
            "reasons": reasons, "warnings": warns,
        })

    return {
        "date": today,
        "paper": bool(c.get("paper", True)),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after": round(acct["cash"], 2),
    }


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0")


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s" % (r["verdict"], r["index"], r["side"], r["symbol"], r["amount"]))
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    lines.append("%d bestanden, %d geblockt. Bargeld nach den bestandenen Aufträgen: %s (%s-Konto)."
                 % (res["pass"], res["block"], money(res["cash_after"]), "Paper" if res["paper"] else "LIVE"))
    if res["block"]:
        lines.append("Geblockte Aufträge gehen nicht an den Broker. Korrigiere den Auftrag oder deine Regeln und prüfe dann erneut.")
    else:
        lines.append("Jeder Auftrag hat bestanden. trader-broker fragt dich trotzdem bei jedem einzelnen nach Ja oder Nein.")
    lines.append("Bildung und Werkzeug. Keine Anlageberatung.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Vorgeschlagene Aufträge gegen deine Limits prüfen (trader-risiko).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="dein Konto; fehlt es, gilt ein frisches Konto, das mit dem Budget gefüllt ist")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json", help="was trader-broker heute abgeschickt hat")
    ap.add_argument("--today", help="JJJJ-MM-TT (Standard: heute)")
    ap.add_argument("--out", default="trader/state/risk.json", help="wo die Urteile gespeichert werden")
    ap.add_argument("--no-write", action="store_true", help="die Urteile nicht speichern")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        doc = read_json(a.orders, "Auftragsdatei (starte zuerst trader-budget oder trader-ausgleich)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("keine Aufträge in %s" % a.orders)
        cfg = read_json(a.config, "Konfiguration", required=False) or {}
        portfolio = read_json(a.portfolio, "Depotdatei", required=False)
        pdoc = read_json(a.prices, "Kursdatei", required=False) or {}
        prices = pdoc.get("prices", pdoc) if isinstance(pdoc, dict) else {}
        state = read_json(a.state, "Broker-Stand", required=False)
        res = check(orders, cfg, portfolio, prices, state, a.today)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = datetime.datetime.now().replace(microsecond=0).isoformat()
            rec["orders_file"] = a.orders
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2)
                fh.write("\n")
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
