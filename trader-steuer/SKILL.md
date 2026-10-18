---
name: trader-steuer
description: >-
  Der Steuer-Helfer (US-Steuerrecht, keine Steuerberatung). Führt für jede
  Position die Kauf-Lots mit Einstandswert (cost basis; FIFO, höchster
  Einstand zuerst oder bestimmte Lots), zeigt nicht realisierte Gewinne und
  Verluste je Lot mit kurzer oder langer Haltefrist, rechnet vor dem Verkauf
  durch, was ein Verkauf realisieren würde, findet Kandidaten für
  Tax-Loss-Harvesting über einer Verlustschwelle, prüft die Wash-Sale-Regel
  (30 Tage davor und danach, GOOG und GOOGL als eine Familie) und schreibt
  Harvesting-Verkäufe nur als Vorschläge. Die Regeln sind US-Steuerrecht; in
  Deutschland gilt die Abgeltungsteuer. Keine Steuerberatung. Nutze ihn, wenn
  der Nutzer sagt: "wie hoch sind meine Gewinne", "Einstandswert", "Lots",
  "Tax-Loss-Harvesting", "was würde ein Verkauf steuerlich bedeuten", "kurz-
  oder langfristig", "Wash Sale", "what are my gains", "cost basis", "tax
  lots", "wash sale", oder bevor irgendetwas verkauft wird.
argument-hint: "[Ticker]"
---

# trader-steuer

**Wichtig vorab: Die Regeln in diesem Skill sind US-Steuerrecht** (cost basis,
wash sale, tax-loss harvesting). Für Privatanleger in Deutschland gilt die
**Abgeltungsteuer**, und die funktioniert anders: Es gibt keine
Wash-Sale-Regel und keine Unterscheidung nach kurz- und langfristiger
Haltefrist wie in den USA. Dieses Tool ersetzt keine Steuerberatung. Es rechnet
nur mit den Lots, die du ihm gibst, nach US-Regeln. Sag das einmal, früh, in
jeder Antwort, die diesen Skill nutzt.

Der Steuer-Helfer rechnet mit den Lots des Nutzers, damit niemand blind
verkauft. Er entscheidet nie, was für die Steuern des Nutzers richtig ist.
**Das ist keine Steuerberatung** und auch **keine Anlageberatung**: Was für
den Nutzer gilt, entscheidet eine Steuerberatung.

`${CLAUDE_SKILL_DIR}` unten ist der Ordner dieses Skills (dort liegt diese
SKILL.md).

```bash
python3 "${CLAUDE_SKILL_DIR}/lots.py" report --lots              # jedes Lot, Gewinn oder Verlust, Frist
python3 "${CLAUDE_SKILL_DIR}/lots.py" sell KO 2 --price 90.11    # was ein Verkauf realisieren würde (FIFO)
python3 "${CLAUDE_SKILL_DIR}/lots.py" sell KO 2 --method specific --lots KO-2
python3 "${CLAUDE_SKILL_DIR}/lots.py" harvest --min-loss 100     # Kandidaten, mit Wash-Sale-Prüfung
python3 "${CLAUDE_SKILL_DIR}/lots.py" harvest --min-loss 100 --write
python3 "${CLAUDE_SKILL_DIR}/lots.py" wash KO                    # wäre ein Kauf von KO heute ein Wash Sale?
```

## Was er liest und schreibt

| Datei | Inhalt |
| --- | --- |
| `trader/portfolio.json` | Positionen mit Lots: `{"id", "date", "shares", "cost"}` (cost = gesamter Einstandswert); Aufbau: `portfolio.example.json` in diesem Ordner |
| `trader/prices.json` | aktuelle Kurse (trader-alarm: `alerts.py quote --holdings --write`) |
| `trader/orders.json` | geplante Käufe zählen für Wash Sales; `harvest --write` ersetzt sie durch Verkaufsvorschläge |
| `trader/state/tax.json` | die realisierten Verkäufe, die er mit `sell --record` protokolliert |

Die Lots kommen aus den Ausführungen, die trader-broker einträgt (`ticket.py
fill`), oder aus dem Depotauszug des Nutzers. Hat eine Position Aktien, aber
keine Lots, frag nach Kaufdaten und Kosten. Erfinde nie einen Einstandswert.

## Die Regeln, die er anwendet (US-Bundessteuer, nur Rechnung)

- **Frist**: langfristig (long term), wenn mehr als ein Jahr nach dem
  Kaufdatum verkauft wird, sonst kurzfristig (short term). `report --lots`
  zeigt das Datum, ab dem jedes Lot langfristig wird.
- **Lot-Methoden**: `fifo` (ältestes zuerst, das macht die Mehrheit der Broker
  standardmäßig), `hifo` (höchster Einstandswert zuerst) und `specific`
  (genannte Lots). Der Auftrag an den Broker trägt keine Lot-IDs: Um bestimmte
  Lots zu verkaufen, muss der Nutzer sie in der Steuer-Lot-Einstellung des
  Brokers selbst wählen, sonst nimmt der Broker seine Standardmethode.
- **Wash Sale**: Ein Verlust wird nicht anerkannt, soweit dieselbe oder eine
  im Wesentlichen gleiche Aktie innerhalb von 30 Tagen vor oder nach dem
  Verkauf gekauft wird. Das Tool prüft die anderen Lots, die noch gehalten
  werden, geplante Käufe in `trader/orders.json` und die Verlustverkäufe, die
  es protokolliert hat. Ein IRA-Konto, die Konten des Partners oder einen
  anderen Broker sieht es nicht, und die zählen auch. Sag das dazu.

## Schritte

1. Aktualisiere die Kurse, dann `lots.py report --lots`. Zeig Gewinne und
   Verluste je Aktie und welche Lots kurz- oder langfristig sind.
2. Vor jedem Verkauf (Rebalancing, ein Stopp, ein Sinneswandel) starte
   `lots.py sell` mit den geplanten Aktien und der Methode und zeig, was er
   realisieren würde.
3. Fürs Harvesting: `lots.py harvest --min-loss 100` (und `--min-pct`, wenn
   der Nutzer eine Prozentschwelle will). Zeig für jeden Kandidaten den
   geschätzten Verlust, die Aufteilung in kurz- und langfristig, jeden
   Wash-Sale-Konflikt und den ersten Tag, an dem er wieder kaufen dürfte.
   Kandidaten mit Konflikt werden nicht vorgeschlagen.
4. Nur wenn der Nutzer Vorschläge will: `harvest --write`, dann
   `/trader-risiko`, dann `/trader-broker`, ein Ja pro Auftrag.
5. Nachdem ein Verkauf ausgeführt ist, aktualisiert `lots.py sell ...
   --record` die Lots und protokolliert das Ergebnis für das Jahr.

## Beispiel: das Depot aus dem Reel

Das Kopier-Depot, gekauft am 2026-10-13 (AAPL $1,250, AXP $1,119, GOOGL $823,
KO $708, BAC $600), hat ein Lot pro Aktie, alle kurzfristig bis 2027-10-14.
Fällt BAC später unter seinen Einstand von $600, zeigt `harvest --min-loss 25`
das Lot BAC-1 mit seinem Verlust und "nicht wieder kaufen vor" einem Datum
31 Tage nach dem Verkauf. Hat der Nutzer zusätzlich einen BAC-Kauf in
`trader/orders.json` geplant, meldet das Tool einen Wash Sale, statt den
Verkauf vorzuschlagen.

## Was er nie tut

Er reicht nichts ein, nennt nie einen Steuersatz oder eine Steuerrechnung,
sagt nie, was der Nutzer schuldet, löst nie einen Auftrag aus und schlägt nie
ein Harvesting vor, von dem er sieht, dass es ein Wash Sale wäre.

## Sicherheit

- Keine Steuerberatung, und Bildung und Werkzeug, **keine Anlageberatung**.
  Sprich mit einer Steuerberatung. Die Regeln hier sind US-Steuerrecht; in
  Deutschland gilt die Abgeltungsteuer.
- Standardmäßig Paper Trading, also Spielgeld (`"paper": true`). Der Wechsel
  auf echtes Geld ist die eigene Entscheidung des Nutzers, in seiner eigenen
  Config.
- Nichts in diesem Paket löst einen Auftrag aus außer trader-broker, und nur,
  wenn alle drei Bedingungen zutreffen: trader-risiko hat genau diesen Auftrag
  heute bestanden, der Nutzer hat im Chat für genau diesen einen Auftrag ein
  ausdrückliches Ja getippt (in trader-broker als "yes"), und der Nutzer hat
  auf Claudes eigener Karte "Allow once" geklickt, weil das Auftragswerkzeug
  auf "Needs approval" steht (Customize > Connectors > tool permissions). Stell
  ein Auftragswerkzeug nie auf "Always allow". Ohne dein Ja passiert nichts.
- Keine Optionen, kein Hebel (Margin), kein Leerverkauf (Short Selling), es sei
  denn, die Config erlaubt es und der Nutzer bestätigt es.
- Broker-Schlüssel liegen in Umgebungsvariablen oder in der Claude-Desktop-
  Config, nie im Chat.
