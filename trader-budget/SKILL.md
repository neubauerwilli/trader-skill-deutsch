---
name: trader-budget
description: >-
  Der Budget-Verwalter (Budget Manager). Schneidet jede Position auf dein
  Budget zu, bevor irgendetwas gekauft wird: hält einen Bargeldpuffer, deckelt
  jede einzelne Aktie (der Überschuss wird auf die anderen verteilt), streicht
  Anteile unter deinem Mindestauftrag, rundet so, dass jeder Dollar genutzt
  wird, kann Bruchteile oder ganze Aktien, und schreibt die vorgeschlagenen
  Kaufaufträge nach trader/orders.json, genau in der Form von Alpacas
  place_stock_order (notional als String). Es wird nichts abgeschickt. Nutze ihn,
  wenn der Nutzer sagt: "ich habe 5.000 Dollar", "I have 5,000 USD", "berechne
  meine Käufe", "size my trades", "wie viel soll ich von jeder kaufen", "verwalte
  mein Budget", "teil mein Geld auf", "10 % Bargeld behalten", "keine Aktie über
  25 %", "setze meine Regeln", oder nachdem /trader-skill-deutsch:trader-kopie die Ziele geschrieben
  hat.
argument-hint: "[Budget in Dollar]"
---

# trader-budget

**Befehle:** Die vollständigen Aufrufe unten gelten für die Plugininstallation. Bei einzeln nach `.claude/skills/` kopierten Ordnern entfällt der Präfix `trader-skill-deutsch:`. Verwende beim Wechsel zu einem anderen Skill den in deiner Sitzung tatsächlich angebotenen Namen.


Der Budget-Verwalter macht aus "kopiere dieses Depot" ein "kaufe genau so viel
von jeder Aktie", innerhalb von Regeln, die der Nutzer einmal festlegt. Er
schreibt nur Vorschläge. trader-risiko prüft sie, und trader-broker fragt den
Nutzer bei jedem einzelnen nach Ja oder Nein.

`${CLAUDE_SKILL_DIR}` ist unten der Ordner dieses Skills (dort, wo diese
SKILL.md liegt).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                       # trader/targets.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --budget 5000 --write # trader/orders.json schreiben
python3 "${CLAUDE_SKILL_DIR}/size.py" --whole-shares --prices trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights AAPL=22.04,AXP=17.14,GOOGL=12.62 --json
```

## Was er liest und schreibt

| Datei | Inhalt |
| --- | --- |
| `trader/config.json` | die Regeln (starte mit `config.example.json` aus diesem Ordner) |
| `trader/targets.json` | die Gewichte zum Kopieren, von trader-kopie geschrieben (`targets.example.json` hier) |
| `trader/prices.json` | nur für ganze Aktien oder Aktienzahlen (trader-alarm schreibt sie) |
| `trader/orders.json` | die vorgeschlagenen Kaufaufträge, die er mit `--write` schreibt |

## Schritt 1: die Regeln einmal festlegen

Gibt es `trader/config.json` noch nicht, kopiere `config.example.json` nach
`trader/config.json` und frag den Nutzer bei jedem Wert in einfachen Worten:

- **budget**: wie viel Geld das ist. Nur Geld, dessen Verlust er verkraften kann.
- **cash_buffer_pct**: wie viel als Bargeld bleibt (Standard 10).
- **max_position_pct**: das Meiste, was eine einzelne Aktie sein darf, in Prozent des Budgets (Standard 25).
- **max_order_usd** und **min_order_usd**: der größte und der kleinste einzelne Auftrag (2000 und 5).
- **fractional**: true, wenn der Broker Bruchteile von Aktien kauft (Alpaca tut das); false für ganze Aktien.
- **blocked**: Ticker, die er nie besitzen will.
- **paper** bleibt true (Paper Trading, also Spielgeld), bis er gesehen hat, wie es auf einem Paper-Konto läuft.

## Schritt 2: berechnen

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Die Rechnung der Reihe nach: Der Bargeldpuffer geht zuerst ab; die Gewichte
werden auf 100 über die verbleibenden Aktien neu verteilt; jede Aktie über der
Obergrenze wird auf die Obergrenze gesetzt und ihr Überschuss nach Gewicht auf
die anderen verteilt, wieder und wieder, bis keine mehr drüber ist; ein Anteil
unter dem Mindestauftrag fällt weg und sein Gewicht wird verteilt; die Beträge
werden auf ganze Dollar gerundet, und der Rundungsdollar geht an die größte
Aktie ohne Deckel, damit nichts aus Versehen liegen bleibt. Geld, das sich
wirklich nicht investieren lässt (jede Aktie an der Obergrenze, oder ganze
Aktien, die nicht glatt aufgehen), wird als "ungenutzt" angezeigt.

## Schritt 3: dem Nutzer zeigen

Zeig die Tabelle: Ticker, Gewicht, Dollar, welche Aktie an die Obergrenze
gestoßen ist, den Bargeldpuffer und was ungenutzt bleibt. Sag, woher die
Gewichte kommen und wie alt sie sind (eine 13F ist bei der Einreichung bis zu
45 Tage alt). Übergib dann an `/trader-skill-deutsch:trader-risiko`.

## Beispiel: das Reel

Die 13F von Berkshire Hathaway für Q2 2026 (eingereicht 2026-08-14), Top 5:
Apple 22.04 %, American Express 17.14 %, Alphabet 12.62 %, Coca-Cola 10.86 %,
Bank of America 9.20 %. Neu auf 100 verteilt über diese fünf: 30.67 / 23.85 /
17.56 / 15.11 / 12.80. Bei einem Budget von 5,000 USD, 10 % Bargeldpuffer und 25 %
Obergrenze:

| Aktie | Dollar | |
| --- | --- | --- |
| AAPL | 1,250 USD | bei 25 % gedeckelt |
| AXP | 1,119 USD | |
| GOOGL | 823 USD | |
| KO | 708 USD | |
| BAC | 600 USD | |
| Bargeldpuffer | 500 USD | |
| ungenutzt | 0 USD | |

Der erste Auftrag, den er schreibt, ist
`{"symbol": "AAPL", "side": "buy", "notional": "1250", "type": "market", "time_in_force": "day"}`.
Behalte die Gewichte auf zwei Nachkommastellen oder mehr: Gewichte, die schon
auf eine Stelle gerundet sind (30.7 / 23.9 / ...), können einen Anteil um einen
Dollar verschieben.

## Regeln

- Nur Vorschläge. Dieser Skill ruft nie ein Broker-Werkzeug auf.
- Berechne nie Geld, von dem der Nutzer nicht gesagt hat, dass es dafür ist. Frag nach dem Budget.
- Jede Zahl kommt aus der Konfiguration, den Zielen oder den Kursen. Nichts wird geraten.
- Keine Prognosen: Sag nie, was die Positionen wert sein oder einbringen werden.
- Ganze Aktien brauchen echte Kurse aus `trader/prices.json`; erfinde nie einen Kurs.
- Budget und Preise müssen endlich und positiv sein; Mindestaufträge dürfen nicht negativ sein. NaN, Infinity und Wahrheitswerte sind keine Geldbeträge. Nicht positive Zielgewichte werden als gestrichen ausgewiesen. Cent-Bruchteile der Pflichtreserve werden auf-, Positionsobergrenzen abgerundet; bei Dollar-Rundung kann deshalb zusätzlich Geld frei bleiben.
- `max_order_usd` wird anschließend von trader-risiko geprüft. Dieser Budgetrechner teilt einen größeren Positionsvorschlag nicht automatisch in mehrere Aufträge auf und garantiert dessen Freigabe nicht.

## Was er nie tut

Er schickt nie einen Auftrag ab, gibt ihn nie frei und plant ihn nie ein,
erhöht nie von selbst ein Limit und berechnet nie Optionen, Margin (geliehenes
Geld) oder Leerverkäufe.

## Sicherheit

- Bildung und Werkzeug. Keine Anlageberatung. Hier wird nichts vorhergesagt und
  keine Rendite versprochen.
- Standardmäßig Paper Trading, also Spielgeld (`"paper": true`). Live zu gehen
  ist die eigene Entscheidung des Nutzers, in seiner eigenen Konfiguration.
- Das Skript schreibt nur Vorschläge. trader-broker bereitet ein Ticket vor und platziert selbst keinen Auftrag. Ein separat verbundener Broker wäre ein eigener Ausführungsweg. Vorgesehen sind trader-risiko, dein ausdrückliches Ja je Auftrag und eine zusätzlich konfigurierte Tool-Genehmigung. Das sind Projektregeln und Berechtigungen, keine technisch unfehlbare Sperre für jeden möglichen Ausführungsweg. Kein Broker-Tool auf "Always allow" setzen.
- Keine Optionen, kein Margin (geliehenes Geld), keine Leerverkäufe, kein
  Hebel, außer die Konfiguration erlaubt es und der Nutzer bestätigt es.
- Broker-Schlüssel liegen in Umgebungsvariablen oder in der Claude-Desktop-
  Konfiguration, nie im Chat.
