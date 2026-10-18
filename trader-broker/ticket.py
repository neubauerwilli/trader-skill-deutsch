#!/usr/bin/env python3
"""Auftrags-Tickets und das Entscheidungsprotokoll für trader-broker. Nur Standardbibliothek.

Keine Anlageberatung. Dieses Tool spricht nie mit einem Broker und platziert
nie etwas. Es macht drei Dinge rund um den einen Moment, der zählt, dein Ja.
Als Ja zählt eine Antwort, die mit "ja" oder "yes" (oder "y") beginnt und
keine Bedingung enthält ("ja, aber ...", "ja zu allen" zählen nicht).

  show      zeigt jeden vorgeschlagenen Auftrag als Ticket in klarem Text plus
            genau das JSON, das Claude an Alpacas Werkzeug place_stock_order
            schickt. Ein Auftrag ist nur dann BEREIT (READY), wenn trader-risiko
            GENAU diesen Auftrag heute bestanden hat (geprüft über den
            Fingerabdruck) und noch niemand darüber entschieden hat.
  approve   erfasst dein ausdrückliches Ja für EINEN Auftrag, mit deiner Antwort
            Wort für Wort. Es lehnt "yes to all", "sure", "ok", ein Ja, das eine
            andere Aktie oder einen anderen Betrag nennt, und jeden Auftrag ab,
            der nicht BEREIT ist.
  decline   erfasst ein Nein.
  result    erfasst, was nach der Karte Allow once / Deny passiert ist:
            placed (mit der Auftrags-ID des Brokers), denied oder failed.
  fill      trägt einen ausgeführten Auftrag als Steuer-Lot in trader/portfolio.json ein.
  log       zeigt die Entscheidungen von heute.

    python3 ticket.py show                    # every order, READY or not
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --broker-id 61e6...
    python3 ticket.py fill --symbol AAPL --side buy --qty 3.6628 --price 341.27
    python3 ticket.py log

Alles wird in trader/state/broker.json geschrieben (nur anhängen).
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

ALPACA_FIELDS = ("symbol", "side", "qty", "notional", "type", "time_in_force", "limit_price",
                 "stop_price", "trail_price", "trail_percent", "extended_hours", "client_order_id",
                 "order_class", "take_profit_limit_price", "stop_loss_stop_price",
                 "stop_loss_limit_price", "advanced_instructions")
STRING_FIELDS = ("qty", "notional", "limit_price", "stop_price", "trail_price", "trail_percent",
                 "take_profit_limit_price", "stop_loss_stop_price", "stop_loss_limit_price")
TIF_WORDS = {"day": "gültig für heute", "gtc": "gültig bis Widerruf", "opg": "zur Eröffnung",
             "cls": "zum Handelsschluss", "ioc": "sofort oder gar nicht", "fok": "komplett oder gar nicht"}
SIDE_WORDS = {"BUY": "KAUF", "SELL": "VERKAUF"}
STATUS_WORDS = {"placed": "platziert", "denied": "verweigert", "failed": "fehlgeschlagen",
                "declined": "abgelehnt"}
YES_WORDS = ("yes", "y", "ja")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each",
               "alle", "allen", "allem", "alles", "restlichen", "übrigen", "beide", "beiden", "jeden", "jede", "jedes", "stapel"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if",
               "nein", "nicht", "kein", "keine", "warte", "moment", "abbrechen", "stopp", "aber",
               "vielleicht", "später", "halt", "außer", "wenn", "falls", "doch", "erst"}


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("keine Datei für %s unter %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s ist kein gültiges JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def rec_day(rec):
    """Der Handelstag, zu dem eine Entscheidung gehört: ihr "date" (das --today, unter dem sie fiel), sonst der Tag von "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the order

def fingerprint(order):
    """Dieselbe Funktion wie in trader-risiko/check.py: erkennt genau diesen Auftrag."""
    core = {k: order[k] for k in ALPACA_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _str_num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(f)
    return str(v)


def tool_input(order):
    """Die genauen Argumente für place_stock_order: Alpacas Parameternamen,
    Zahlen als Strings (seine Signatur typisiert qty und notional als str)."""
    out = {}
    for k in ALPACA_FIELDS:
        if k in order and order[k] is not None:
            out[k] = _str_num(order[k]) if k in STRING_FIELDS else order[k]
    return out


def _money(v):
    f = float(v)
    s = "{:,.2f}".format(f)
    return "$" + (s[:-3] if s.endswith(".00") else s)


def plain(order):
    """KAUF $1,250 von AAPL, Market-Order, gültig für heute"""
    side = str(order.get("side", "")).upper()
    side = SIDE_WORDS.get(side, side)
    sym = order.get("symbol", "?")
    if order.get("notional") is not None:
        what = "%s von %s" % (_money(order["notional"]), sym)
    else:
        q = _str_num(order.get("qty"))
        what = "%s Aktie%s von %s" % (q, "" if q == "1" else "n", sym)
    typ = str(order.get("type", "market"))
    if typ == "limit":
        how = "Limit-Order zu %s" % _money(order.get("limit_price"))
    elif typ == "stop":
        how = "Stop-Order zu %s" % _money(order.get("stop_price"))
    elif typ == "stop_limit":
        how = "Stop %s, Limit %s" % (_money(order.get("stop_price")), _money(order.get("limit_price")))
    else:
        how = "%s-Order" % typ.replace("_", " ").capitalize()
    tif = TIF_WORDS.get(str(order.get("time_in_force", "day")), order.get("time_in_force"))
    return "%s %s, %s, %s" % (side, what, how, tif)


def amount_numbers(order):
    nums = []
    for k in ("notional", "qty", "limit_price"):
        if order.get(k) is not None:
            try:
                nums.append(float(order[k]))
            except (TypeError, ValueError):
                pass
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Ist diese Antwort ein ausdrückliches Ja für GENAU DIESEN Auftrag? Gibt (ok, why) zurück."""
    raw = (reply or "").strip()
    if not raw:
        return False, "die Antwort ist leer"
    if "?" in raw:
        return False, "die Antwort ist eine Frage, kein Ja"
    words = re.findall(r"[a-zäöüß']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "nur eine Antwort, die mit \"ja\" oder \"yes\" beginnt, zählt (nicht ok, passt, mach, sure, go); tippe ja"
    if BATCH_WORDS & set(words):
        return False, "ein Ja gilt für jeweils einen Auftrag, nicht für einen ganzen Stapel"
    if DOUBT_WORDS & set(words[1:]):
        return False, "die Antwort enthält eine Bedingung oder einen Zweifel; frag noch einmal"
    sym = str(order.get("symbol", "")).upper()
    tokens = set(re.findall(r"[A-Z][A-Z.\-]{0,6}", raw.upper()))
    others = {s.upper() for s in batch_symbols} - {sym}
    named = tokens & others
    if named:
        return False, "die Antwort nennt %s, aber dieser Auftrag gilt für %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "die Antwort sagt %s, aber dieser Auftrag lautet %s" % (
                ", ".join(_str_num(n) for n in nums), plain(order))
    return True, "ausdrückliches Ja für %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    """Die letzte heute erfasste Entscheidung zu diesem Fingerabdruck, oder None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risiko hat diese Aufträge nicht geprüft (starte check.py)"
    if risk.get("date") != today:
        return None, "die Risikoprüfung ist vom %s, nicht von heute; starte check.py erneut" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "genau dieser Auftrag war nicht in der letzten Risikoprüfung (er wurde geändert oder ist neu); starte check.py erneut"


def status_of(order, risk, state, today):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NICHT BEREIT: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "GESPERRT durch trader-risiko: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "FREIGEGEBEN von dir um %s, wartet auf den Aufruf von place_stock_order" % rec.get("at"), rv
        return False, "ERLEDIGT: %s um %s" % (STATUS_WORDS.get(st, st), rec.get("at")), rv
    return True, "BEREIT: trader-risiko PASS, wartet auf dein Ja", rv


# ---------------------------------------------------------------- rendering

def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    broker = str(cfg.get("broker", "alpaca")).lower()
    paper = cfg.get("paper", True)
    acct = "PAPER-Konto (Spielgeld)" if paper else "LIVE-KONTO, ECHTES GELD"
    lines = ["AUFTRAG %d von %d, %s, %s" % (idx, total, acct, "Interactive Brokers" if broker == "ibkr" else "Alpaca"),
             "  " + plain(order)]
    if order.get("why"):
        lines.append("  Grund: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  Hinweis: " + w)
    if broker == "ibkr":
        lines.append("  Claude entwirft das mit dem Interactive-Brokers-Connector. IBKR schickt es an dein")
        lines.append("  IBKR-Konto, und an den Markt geht es erst, nachdem du es dort freigegeben hast.")
    else:
        lines.append("  Claude ruft place_stock_order genau so auf:")
        for l in json.dumps(tool_input(order), indent=2).splitlines():
            lines.append("    " + l)
    if ready:
        lines.append("  Antworte \"yes\" (englisch getippt), um genau diesen einen Auftrag zu platzieren, oder \"no\", um ihn zu überspringen.")
        if broker != "ibkr":
            lines.append("  Claudes eigene Karte fragt danach noch einmal: Allow once platziert ihn, Deny stoppt ihn.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "paper": bool(cfg.get("paper", True)),
            "broker": cfg.get("broker", "alpaca"), "tool": "place_stock_order",
            "tool_input": tool_input(order), "why": order.get("why", "")}


# ---------------------------------------------------------------- fills -> lots

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Trägt einen ausgeführten Auftrag in trader/portfolio.json ein (ein Lot bei einem Kauf, FIFO bei einem Verkauf)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("noch keine Datei %s und kein budget in der Config, um sie daraus zu starten" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = symbol.upper()
    qty, price = float(qty), float(price)
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("Verkauf von %s %s kann nicht eingetragen werden: das Depot hält %s"
                              % (qty, symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


# ---------------------------------------------------------------- commands

def _load(a):
    doc = read_json(a.orders, "Auftragsdatei (starte zuerst trader-budget, trader-ausgleich oder trader-steuer)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("keine Aufträge in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    today = a.today or datetime.date.today().isoformat()
    return orders, cfg, risk, state, today


def _pick(orders, index):
    if index is None:
        raise TicketError("sag, welcher Auftrag: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("es gibt keinen Auftrag %d (es gibt %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nImmer ein Auftrag nach dem anderen: Ein Ja gilt für ein Ticket, nie für den Stapel. Bildung und Werkzeug, "
          "keine Anlageberatung.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("Auftrag %d ist nicht bereit: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Nicht erfasst: %s. Frag den Nutzer noch einmal, mit dem Ticket zu Auftrag %d." % (why, a.index))
        return 1
    rec = {"at": now_iso(), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"),
           "plain": plain(order), "status": "approved", "reply": a.reply,
           "paper": bool(cfg.get("paper", True)), "broker": cfg.get("broker", "alpaca")}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Dein Ja zu Auftrag %d ist erfasst: %s." % (a.index, plain(order)))
    if str(cfg.get("broker", "alpaca")).lower() == "ibkr":
        print("Entwirf ihn jetzt mit dem Interactive-Brokers-Connector; gib ihn in deinem IBKR-Konto frei.")
    else:
        print("Ruf jetzt place_stock_order genau so auf:")
        print(json.dumps(tool_input(order), indent=2))
        print("Claudes Karte zeigt Allow once / Deny. Danach: ticket.py result --index %d --status "
              "placed|denied|failed" % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today = _load(a)
    order = _pick(orders, a.index)
    rec = {"at": now_iso(), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"),
           "plain": plain(order), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Erfasst: Du hast Nein zu Auftrag %d (%s) gesagt. Es wurde nichts abgeschickt." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("zu Auftrag %d ist heute kein Ja erfasst; ein Ergebnis kann nur auf eine Freigabe folgen" % a.index)
    new = {"at": now_iso(), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status}
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    write_json(a.state, state)
    print("Auftrag %d als %s erfasst." % (a.index, a.status))
    if a.status == "placed" and a.filled_qty and a.filled_price:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        print("Die Ausführung wurde in %s eingetragen." % a.portfolio)
    elif a.status == "placed":
        print("Sobald er ausgeführt ist, trag ihn für trader-steuer ein: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    date = a.date or datetime.date.today().isoformat()
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    print("Erfasst: %s %s %s zu %s am %s. Bargeld jetzt %s." % (a.side, a.qty, a.symbol.upper(),
                                                          _money(a.price), date, _money(pf["cash"])))
    return 0


def cmd_log(a):
    state = load_state(a.state)
    today = a.today or datetime.date.today().isoformat()
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("Keine Entscheidungen erfasst%s." % ("" if a.all else " für heute"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        print("%s  #%s %-9s %s%s%s" % (r.get("at"), r.get("index"), r.get("status"), r.get("plain"), reply, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d platziert%s." % (placed, "" if a.all else " heute"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Auftrags-Tickets und dein Ja (trader-broker). Platziert nie Aufträge. Keine Anlageberatung.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        p.add_argument("--today", help="JJJJ-MM-TT (Standard: heute)")

    p = sub.add_parser("show", help="die Tickets anzeigen")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="das ausdrückliche Ja des Nutzers zu einem Auftrag erfassen")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="die Antwort des Nutzers, Wort für Wort")
    p = sub.add_parser("decline", help="ein Nein erfassen")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="erfassen, was nach der Karte Allow once / Deny passiert ist")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed"])
    p.add_argument("--broker-id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("fill", help="einen ausgeführten Auftrag in trader/portfolio.json eintragen")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p = sub.add_parser("log", help="die Entscheidungen von heute")
    p.add_argument("--state", default="trader/state/broker.json")
    p.add_argument("--today")
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
