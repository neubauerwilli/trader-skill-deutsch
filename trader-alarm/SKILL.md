---
name: trader-alarm
description: >-
  Kursalarme (Price Alerts). Läutet, wenn ein Kursziel, ein Stopp oder die
  Eröffnungsglocke erreicht ist: Regeln für über / unter / Stopp, Prozentbewegungen
  gegenüber dem Vortagesschluss und die Eröffnungsglocke der NYSE, jeder Alarm
  läutet nur einmal, damit du nicht zugespammt wirst. Kennt die NYSE-Handelszeiten
  mit den Feiertagen 2026 und 2027 und den Früh-Schlüssen um 13:00 Uhr (New Yorker
  Zeit), holt kostenlose, verzögerte Kurse (Yahoo-Chart-JSON, Stooq-CSV) Ticker für
  Ticker und schreibt trader/prices.json für die anderen Skills. Nutze es, wenn der
  Nutzer sagt "alert me when", "tell me if AAPL hits", "set a stop", "price target",
  "watch my stocks", "is the market open", "when does the market open",
  "get me prices", oder auf Deutsch "sag mir Bescheid, wenn", "benachrichtige mich,
  wenn Apple unter 320 fällt", "setz einen Stopp", "Kursziel", "Alarm setzen",
  "beobachte meine Aktien", "ist die Börse offen", "wann öffnet die Börse",
  "hol mir die Kurse", oder um Kurse zu aktualisieren, bevor Größe, Risiko oder
  Steuer berechnet werden.
argument-hint: "[Ticker] [Kurs]"
---

# trader-alarm

**Befehle:** Die vollständigen Aufrufe unten gelten für die Plugininstallation. Bei einzeln nach `.claude/skills/` kopierten Ordnern entfällt der Präfix `trader-skill-deutsch:`. Verwende beim Wechsel zu einem anderen Skill den in deiner Sitzung tatsächlich angebotenen Namen.


Kursalarme beobachtet die Zahlen, damit du es nicht tun musst. Ein Alarm ist
eine Information, nie ein Auftrag: Wenn ein Stopp läutet, wird nichts verkauft.
Willst du handeln, läuft der normale Weg (Größe, Risiko, dein Ja).

`${CLAUDE_SKILL_DIR}` ist unten der Ordner dieses Skills (dort, wo diese SKILL.md liegt).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                         # NYSE offen oder geschlossen, nächste Öffnung
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote AAPL KO --write         # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write      # Ticker aus Depot und Zielen
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol AAPL --above 350
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol BAC --stop 50 --note "mein Stopp"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol KO --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                         # Exit 0 = etwas hat geläutet
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset aapl-above-350
```

## Was er liest und schreibt

| Datei | Was |
| --- | --- |
| `trader/alerts.json` | die Regeln (`alerts.example.json` in diesem Ordner) |
| `trader/state/alerts.json` | welche Regeln wann geläutet haben, damit jede nur einmal läutet |
| `trader/prices.json` | die neuesten Kurse, eingemischt, für trader-budget, trader-risiko, trader-steuer und trader-ausgleich |

## Regeltypen

| Typ | läutet, wenn | läutet wieder |
| --- | --- | --- |
| `above` | Kurs bei `price` oder darüber | nach `reset`, oder von selbst bei `"rearm": true`, wenn der Kurs zurückgefallen ist |
| `below` / `stop` | Kurs bei `price` oder darunter | genauso |
| `pct_move` | Kurs hat sich um `pct` Prozent gegenüber dem Vortagesschluss (oder `ref`) bewegt, optional `direction` up oder down | höchstens einmal pro Tag |
| `opening_bell` | die NYSE ist offen | einmal pro Handelstag |

## Kurse

- `yahoo` (Standard): `query1.finance.yahoo.com/v8/finance/chart/<TICKER>`, ein
  inoffizieller, kostenloser JSON-Dienst, eine Anfrage pro Ticker, eine halbe
  Sekunde Pause dazwischen.
- `stooq`: `stooq.com/q/l/?s=aapl.us&f=sd2t2ohlcv&h&e=csv`, das kostenlose CSV
  von Stooq. Beim Schreiben (13.10.2026) kam 404; der Parser bleibt für den
  Fall, dass es wieder geht. Mit `--source stooq` kannst du es probieren.
- Kostenlose Kurse sind verzögert. Für Alarme und zum Prüfen von Größen reichen
  sie, nicht zum Timen eines Auftrags. Zum Kurs deines Brokers wird ausgeführt.
- Sind beide Dienste down, lies die Kurse über den Broker-Connector (Alpaca
  `get_stock_snapshot`, IBKR `get_price_snapshot`) und schreib sie als
  `{"as_of": "...", "prices": {"AAPL": 341.27}}` in `trader/prices.json`.

## Die NYSE-Uhr

Regulärer Handel 9:30 bis 16:00 Uhr New Yorker Zeit, geschlossen an Wochenenden
und an diesen Feiertagen, laut nyse.com/markets/hours-calendars (gelesen am 13.10.2026):

- 2026: 1. Jan, 19. Jan, 16. Feb, 3. Apr (Good Friday, also Karfreitag), 25. Mai,
  19. Jun, 3. Jul (Independence Day, nachgeholt), 7. Sep, 26. Nov, 25. Dez.
  Früh-Schluss um 13:00 Uhr am 27. Nov und am 24. Dez.
- 2027: 1. Jan, 18. Jan, 15. Feb, 26. Mär (Good Friday, also Karfreitag), 31. Mai,
  18. Jun (Juneteenth, nachgeholt), 5. Jul (Independence Day, nachgeholt),
  6. Sep, 25. Nov, 24. Dez (Weihnachten, vorgezogen). Früh-Schluss um 13:00 Uhr am 26. Nov.

Die Sommerzeit rechnet das Tool selbst aus, es braucht also keine
Zeitzonen-Dateien. Außerhalb 2026/2027 ist der Handelsstatus unbekannt: keine bestätigte Öffnung, kein Eröffnungsglocken-Alarm und keine erfundene nächste Öffnung. Eine wegen der Sommerzeitumstellung nicht existierende New Yorker Ortszeit wird abgelehnt; gib bei unklarer Ortszeit einen ausdrücklichen UTC-Offset an.

## Schritte

1. Frag, was beobachtet werden soll, in den Worten des Nutzers ("sag mir
   Bescheid, wenn Apple unter 320 USD fällt"), und leg die Regel an. Bestätige sie
   zurück: Ticker, Typ, Marke.
2. Führe `check` jetzt aus, und nach Zeitplan, wenn der Nutzer das will (eine
   geplante Aufgabe in Claude oder Cron, zu Handelszeiten alle 15 Minuten ist
   reichlich). Jeder Lauf holt nur die Ticker aus den Regeln und läutet jeden
   Alarm einmal.
3. Sag dem Nutzer, was geläutet hat, eine Zeile je Alarm. Will er handeln,
   schick ihn zur Größenberechnung und zu `/trader-skill-deutsch:trader-risiko`. Handle nie für ihn.

## Beispiel: das Depot aus dem Reel

Nachdem das Kopie-Depot gekauft ist: `add --opening-bell`, `add --symbol AAPL
--above 350` und `add --symbol BAC --stop 50`. Am 13.10.2026 schloss AAPL bei
341.27 USD und BAC bei 53.96 USD, also druckt `check` an diesem Abend "Nichts getroffen"
und endet mit Exit 1. Um 9:30 Uhr am nächsten Morgen läutet die Eröffnungsglocke einmal.

## Was er nie tut

Er gibt nie einen Auftrag auf, storniert oder ändert nie einen, behauptet nie,
ein Kurs sei in Echtzeit, und sagt nie voraus, wohin ein Kurs läuft.

## Sicherheit

- Bildung und Werkzeug. Keine Anlageberatung. Ein Alarm ist eine Tatsache über einen Kurs, kein Signal zum Handeln.
- Standardmäßig Paper Trading, also Spielgeld (`"paper": true`). Scharf zu gehen ist deine eigene Entscheidung, in deiner eigenen Konfiguration.
- In diesem Paket gibt nur trader-broker einen Auftrag auf, und nur wenn alle drei Dinge stimmen: trader-risiko
  hat genau diesen Auftrag heute geprüft und freigegeben, du hast im Chat für genau diesen einen Auftrag ausdrücklich
  „Ja“ geschrieben (nichts passiert ohne dein „Ja“), und du hast auf Claudes eigener Karte einmal auf „Erlauben“ geklickt, weil das
  Order-Tool auf „Needs approval“ (Genehmigung nötig) steht (Customize > Connectors > tool permissions). Stell ein Order-Tool nie auf „Always allow“ (Immer erlauben).
- Keine Optionen, kein Hebel (Margin), kein Leerverkauf (Short Selling) und keine Hebelprodukte, außer die Konfiguration erlaubt es und du bestätigst es.
- Broker-Schlüssel liegen in Umgebungsvariablen oder in der Claude-Desktop-Konfiguration, nie im Chat.
