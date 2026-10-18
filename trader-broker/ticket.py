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
import math
import os
import re
import sys
import uuid

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
    except (ValueError, OSError) as e:
        raise TicketError("%s ist kein gültiges JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, allow_nan=False)
        fh.write("\n")
    os.replace(tmp, path)


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def finite_number(value, label, positive=False):
    try:
        number = float(value) if not isinstance(value, bool) else float("nan")
    except (TypeError, ValueError, OverflowError):
        number = float("nan")
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        raise TicketError("%s braucht eine endliche, %s Zahl" % (label, "positive" if positive else "nicht negative"))
    return number


def document_fingerprint(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def canonical_path(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


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
        f = finite_number(v, "Auftragszahl", positive=True)
        return str(int(f)) if f == int(f) else repr(f)
    return str(v)


def tool_input(order):
    """Die genauen Argumente für place_stock_order: Alpacas Parameternamen,
    Zahlen als Strings (seine Signatur typisiert qty und notional als str)."""
    out = {}
    for k in ALPACA_FIELDS:
        if k in order and order[k] is not None:
            if k in STRING_FIELDS:
                finite_number(order[k], k, True)
            out[k] = _str_num(order[k]) if k in STRING_FIELDS else order[k]
    return out


def _money(v):
    try:
        f = float(v) if not isinstance(v, bool) else float("nan")
    except (ValueError, TypeError, OverflowError):
        f = float("nan")
    if not math.isfinite(f):
        raise TicketError("Betrag muss eine endliche Zahl sein")
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
    if not words or words[0] not in YES_WORDS or not re.match(r"^(?:ja|yes|y)\b", raw.lower()):
        return False, "nur eine Antwort, die mit \"ja\" oder \"yes\" beginnt, zählt (nicht ok, passt, mach, sure, go); tippe ja"
    if BATCH_WORDS & set(words):
        return False, "ein Ja gilt für jeweils einen Auftrag, nicht für einen ganzen Stapel"
    if DOUBT_WORDS & set(words[1:]):
        return False, "die Antwort enthält eine Bedingung oder einen Zweifel; frag noch einmal"
    sym = str(order.get("symbol", "")).upper()
    side = str(order.get("side", "")).lower()
    buy_words, sell_words = {"buy", "kauf", "kaufe", "kaufen"}, {"sell", "verkauf", "verkaufe", "verkaufen"}
    if (side == "buy" and sell_words & set(words)) or (side == "sell" and buy_words & set(words)):
        return False, "die Antwort nennt die falsche Kauf-/Verkaufsseite"
    tokens = set(re.findall(r"[A-Z][A-Z.\-]{0,6}", raw.upper()))
    others = {s.upper() for s in batch_symbols} - {sym}
    named = tokens & others
    if named:
        return False, "die Antwort nennt %s, aber dieser Auftrag gilt für %s" % (", ".join(sorted(named)), sym)
    permitted = {"place", "it", "es", "von", "of", "for", "für", "usd", "dollar", "aktie", "aktien", "shares", "stock", "zu", "at"}
    permitted |= buy_words | sell_words | set(re.findall(r"[a-zäöüß']+", sym.lower()))
    if set(words[1:]) - permitted:
        return False, "die Antwort enthält zusätzliche oder unbekannte Angaben; tippe für dieses Ticket nur ja"
    nums = [float(n.replace(",", "")) for n in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(order)
        if not all(math.isfinite(n) and any(abs(n - w) < 0.005 for w in want) for n in nums):
            return False, "die Antwort sagt %s, aber dieser Auftrag lautet %s" % (
                ", ".join(str(n) for n in nums), plain(order))
    return True, "ausdrückliches Ja für %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    state = read_json(path, "broker state", required=False)
    if state is None:
        return {"orders": []}
    if not isinstance(state, dict) or not isinstance(state.get("orders"), list) or any(not isinstance(r, dict) for r in state["orders"]):
        raise TicketError("Broker-Stand muss ein Objekt mit einer Auftragsliste sein")
    for rec in state["orders"]:
        if (rec.get("status") not in ("approved", "declined", "placed", "denied", "failed")
                or not isinstance(rec.get("fingerprint"), str)
                or not re.fullmatch(r"[0-9a-f]{16}", rec["fingerprint"])
                or isinstance(rec.get("index"), bool) or not isinstance(rec.get("index"), int)
                or rec["index"] < 1):
            raise TicketError("ungültiger Entscheidungsdatensatz im Broker-Stand")
    return state


def latest(state, fp, today):
    """Die letzte heute erfasste Entscheidung zu diesem Fingerabdruck, oder None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today, index=None):
    if not risk:
        return None, "trader-risiko hat diese Aufträge nicht geprüft (starte check.py)"
    if risk.get("invalid_reason"):
        return None, risk["invalid_reason"] + "; starte check.py erneut"
    if risk.get("date") != today:
        return None, "die Risikoprüfung ist vom %s, nicht von heute; starte check.py erneut" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp and (index is None or r.get("index") == index):
            return r, None
    return None, "genau dieser Auftrag war nicht in der letzten Risikoprüfung (er wurde geändert oder ist neu); starte check.py erneut"


def status_of(order, risk, state, today, index=None):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "FREIGEGEBEN von dir um %s, wartet auf das Ergebnis des Broker-Werkzeugs" % rec.get("at"), None
        if st == "placed" or not risk or rec.get("risk_check_id") == risk.get("check_id"):
            return False, "ERLEDIGT: %s um %s" % (STATUS_WORDS.get(st, st), rec.get("at")), None
    rv, why = risk_verdict(risk, fp, today, index)
    if rv is None:
        return False, "NICHT BEREIT: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "GESPERRT durch trader-risiko: " + "; ".join(rv.get("reasons", [])), rv
    decisions = {}
    for r in state.get("orders", []):
        decisions[(r.get("fingerprint"), rec_day(r))] = r.get("status")
    if "approved" in decisions.values():
        return False, "NICHT BEREIT: eine andere Freigabe wartet noch auf ihr Broker-Ergebnis", rv
    if "requires" not in rv or any(not (latest(state, prior, today) or {}).get("filled") for prior in rv["requires"]):
        return False, "NICHT BEREIT: frühere PASS-Aufträge sind noch nicht ausgeführt; Kontostand aktualisieren und neu prüfen", rv
    return True, "BEREIT: trader-risiko PASS, wartet auf dein Ja", rv


# ---------------------------------------------------------------- rendering

def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today, idx)
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
        lines.append("  Antworte \"ja\" (oder \"yes\") für genau diesen einen Auftrag, oder \"nein\", um ihn zu überspringen.")
        if broker != "ibkr":
            lines.append("  Claudes eigene Karte fragt danach noch einmal: Allow once platziert ihn, Deny stoppt ihn.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today, idx)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "paper": bool(cfg.get("paper", True)),
            "broker": cfg.get("broker", "alpaca"), "tool": "place_stock_order",
            "tool_input": tool_input(order), "why": order.get("why", "")}


# ---------------------------------------------------------------- fills -> lots

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Trägt einen ausgeführten Auftrag in trader/portfolio.json ein (ein Lot bei einem Kauf, FIFO bei einem Verkauf)."""
    if not isinstance(cfg, dict) or not isinstance(cfg.get("paper", True), bool):
        raise TicketError("Konfiguration und paper-Wert sind ungültig")
    qty, price = finite_number(qty, "qty", True), finite_number(price, "price", True)
    if side not in ("buy", "sell") or not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,24}", symbol):
        raise TicketError("ungültiges Symbol oder side für die Ausführung")
    try:
        datetime.date.fromisoformat(date)
    except (ValueError, TypeError):
        raise TicketError("Ausführungsdatum muss JJJJ-MM-TT sein")
    cost = qty * price
    if not math.isfinite(cost):
        raise TicketError("Wert der Ausführung läuft über")
    cost = round(cost, 2)
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if budget is None:
            raise TicketError("noch keine Datei %s und kein budget in der Config, um sie daraus zu starten" % portfolio_path)
        pf = {"cash": finite_number(budget, "budget", True), "positions": []}
    if not isinstance(pf, dict) or not isinstance(pf.get("positions"), list):
        raise TicketError("Depot braucht eine Positionsliste")
    cash = finite_number(pf.get("cash"), "Depot-Cash")
    for p in pf["positions"]:
        if not isinstance(p, dict) or not isinstance(p.get("lots", []), list):
            raise TicketError("ungültige Depotposition")
        finite_number(p.get("shares", 0), "Depot-Aktienzahl")
        for lot in p.get("lots", []):
            if not isinstance(lot, dict):
                raise TicketError("ungültiger Kaufposten")
            finite_number(lot.get("shares"), "Lot-Aktienzahl", True)
            finite_number(lot.get("cost"), "Lot-Einstandswert")
    symbol = symbol.upper()
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if cash + 0.005 < cost and cfg.get("allow_margin", False) is not True:
            raise TicketError("Ausführung übersteigt den erfassten Cashbestand")
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%s" % (symbol, uuid.uuid4().hex), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("Verkauf von %s %s kann nicht eingetragen werden: das Depot hält %s"
                              % (qty, symbol, pos.get("shares", 0) if pos else 0))
        if sum(float(l["shares"]) for l in pos.get("lots", [])) + 1e-9 < qty:
            raise TicketError("Kaufposten reichen für diesen Verkauf nicht aus")
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
    if not isinstance(orders, list) or not orders or any(not isinstance(o, dict) for o in orders):
        raise TicketError("keine Aufträge in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False)
    cfg = {} if cfg is None else cfg
    if not isinstance(cfg, dict) or not isinstance(cfg.get("paper", True), bool):
        raise TicketError("Konfiguration muss ein Objekt und paper ein Boolean sein")
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    today = a.today or datetime.date.today().isoformat()
    if risk is not None:
        if not isinstance(risk, dict) or not isinstance(risk.get("results"), list) or any(not isinstance(r, dict) for r in risk["results"]):
            raise TicketError("Risikoprüfung braucht eine Ergebnisliste")
        for row in risk["results"]:
            if (row.get("verdict") not in ("PASS", "BLOCK") or not isinstance(row.get("fingerprint"), str)
                    or not isinstance(row.get("index"), int) or isinstance(row.get("index"), bool)
                    or not isinstance(row.get("requires"), list)
                    or any(not isinstance(prior, str) for prior in row["requires"])
                    or any(not isinstance(row.get(key, []), list)
                           or any(not isinstance(item, str) for item in row.get(key, []))
                           for key in ("reasons", "warnings"))):
                raise TicketError("ungültiges Ergebnis in der Risikoprüfung; starte check.py erneut")
        if risk.get("config_fingerprint") != document_fingerprint(cfg):
            risk["invalid_reason"] = "die Konfiguration wurde geändert oder ist nicht an diese Prüfung gebunden"
        if risk.get("order_fingerprints") != [fingerprint(o) for o in orders]:
            risk["invalid_reason"] = "genau dieser Auftrag war nicht in der letzten Risikoprüfung oder die Reihenfolge wurde geändert"
        placed = [r for r in state["orders"] if r.get("status") == "placed"]
        if (not isinstance(risk.get("broker_state_path"), str)
                or canonical_path(risk["broker_state_path"]) != canonical_path(a.state)
                or risk.get("broker_placed_fingerprint") != document_fingerprint(placed)):
            risk["invalid_reason"] = "der Broker-Ausführungsstand wurde seit der Risikoprüfung geändert"
        limit = finite_number(cfg.get("max_orders_per_day", 10), "max_orders_per_day")
        if int(limit) != limit:
            raise TicketError("max_orders_per_day muss eine endliche, nicht negative ganze Zahl sein")
        if limit and sum(rec_day(r) == today for r in placed) >= limit:
            risk["invalid_reason"] = "das aktuelle Tageslimit für platzierte Aufträge ist bereits erreicht"
        if not isinstance(risk.get("inputs"), dict):
            risk["invalid_reason"] = "der Eingabenachweis der Risikoprüfung fehlt"
        for entry in (risk.get("inputs") if isinstance(risk.get("inputs"), dict) else {}).values():
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise TicketError("ungültiger Eingabenachweis der Risikoprüfung")
            current = read_json(entry["path"], "Risikoeingabe", required=False)
            if entry.get("fingerprint") != document_fingerprint(current):
                risk["invalid_reason"] = "Kontodaten, Kurse oder Konfiguration wurden seit der Prüfung geändert"
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
    ready, label, rv = status_of(order, risk, state, today, a.index)
    if not ready:
        raise TicketError("Auftrag %d ist nicht bereit: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Nicht erfasst: %s. Frag den Nutzer noch einmal, mit dem Ticket zu Auftrag %d." % (why, a.index))
        return 1
    rec = {"at": now_iso(), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"),
           "plain": plain(order), "status": "approved", "reply": a.reply,
           "paper": bool(cfg.get("paper", True)), "broker": cfg.get("broker", "alpaca"),
           "risk_check_id": risk.get("check_id"),
           "portfolio_binding": risk.get("inputs", {}).get("portfolio"),
           "config_fingerprint": document_fingerprint(cfg)}
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
           "plain": plain(order), "status": "declined", "reply": a.reply or "no",
           "risk_check_id": (risk or {}).get("check_id")}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Erfasst: Du hast Nein zu Auftrag %d (%s) gesagt. Es wurde nichts abgeschickt." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved" or rec.get("index") != a.index:
        raise TicketError("zu Auftrag %d ist heute kein Ja erfasst; ein Ergebnis kann nur auf eine Freigabe folgen" % a.index)
    if a.status == "placed" and not a.broker_id:
        raise TicketError("placed braucht die tatsächliche Broker-Auftrags-ID")
    if bool(a.filled_qty) != bool(a.filled_price):
        raise TicketError("filled-qty und filled-price müssen zusammen angegeben werden")
    if a.filled_qty is not None:
        binding = rec.get("portfolio_binding")
        if (not isinstance(binding, dict) or not isinstance(binding.get("path"), str)
                or canonical_path(binding["path"]) != canonical_path(a.portfolio)):
            raise TicketError("Ausführung gehört ausschließlich in die bei der Freigabe geprüfte Depotdatei")
        if (rec.get("config_fingerprint") != document_fingerprint(cfg)
                or binding.get("fingerprint") != document_fingerprint(
                    read_json(a.portfolio, "gebundene Depotdatei", required=False))):
            raise TicketError("Konfiguration oder Depot wurde nach der Freigabe geändert; Ausführung zuerst abgleichen")
        filled_qty = finite_number(a.filled_qty, "filled-qty", True)
        filled_price = finite_number(a.filled_price, "filled-price", True)
        if a.status != "placed":
            raise TicketError("Ausführungsdaten gehören nur zu placed")
        if order.get("qty") is not None and filled_qty > finite_number(order["qty"], "Auftragsmenge", True) + 1e-9:
            raise TicketError("Ausführungsmenge übersteigt die freigegebene Auftragsmenge")
        if order.get("notional") is not None and filled_qty * filled_price > finite_number(order["notional"], "Auftragsbetrag", True) + 0.01:
            raise TicketError("Ausführungswert übersteigt den freigegebenen Auftragsbetrag")
    new = {"at": now_iso(), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status,
           "risk_check_id": rec.get("risk_check_id"), "paper": rec.get("paper", True),
           "broker": rec.get("broker", "alpaca")}
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    if a.filled_qty is not None:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        new["filled"] = True
    state["orders"].append(new)
    write_json(a.state, state)
    print("Auftrag %d als %s erfasst." % (a.index, a.status))
    if a.status == "placed" and a.filled_qty and a.filled_price:
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
