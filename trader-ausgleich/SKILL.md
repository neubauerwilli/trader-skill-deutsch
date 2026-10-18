---
name: trader-ausgleich
description: >-
  Depot-Ausgleich (Rebalancing). Bringt das Depot zurück auf seine Zielgewichte,
  wenn es abdriftet: bewertet jede Position zu aktuellen Kursen, rechnet für
  jede Aktie die Abweichung von ihrem Ziel in Prozentpunkten aus, handelt nur
  Aktien außerhalb eines Bands (Standard 5 Punkte), hält den Bargeld-Puffer und
  den Deckel pro Aktie ein, überspringt winzige Trades, listet Verkäufe vor
  Käufen und schreibt die Trades als Vorschläge nach trader/orders.json. Nutze
  es, wenn der Nutzer sagt "rebalance", "my portfolio drifted", "bring it back
  to the targets", "I added money", "the new 13F changed the weights", "am I
  overweight", oder auf Deutsch "gleich mein Depot aus", "mein Depot ist
  abgedriftet", "bring es zurück auf die Ziele", "ich habe Geld eingezahlt",
  "das neue 13F hat die Gewichte geändert", "bin ich übergewichtet", "Rebalancing",
  oder nach Zeitplan (monatlich oder vierteljährlich), nachdem /trader-kopie die Ziele aktualisiert hat.
argument-hint: "[Band in Punkten]"
---

# trader-ausgleich

Der Depot-Ausgleich (Rebalancing) hält das Depot so, wie der Plan es vorsieht.
Kurse bewegen sich, neues Geld kommt dazu und ein neues 13F ändert die Gewichte;
dieser Skill findet die Aktien, die zu weit abgedriftet sind, und schlägt die
Trades vor, die sie zurückbringen. Er schreibt nur Vorschläge: trader-risiko prüft
sie, und trader-broker fragt dich bei jedem einzelnen nach Ja oder Nein.
Nichts passiert ohne dein „Ja“.

`${CLAUDE_SKILL_DIR}` ist unten der Ordner dieses Skills (dort, wo diese SKILL.md liegt).

```bash
python3 "${CLAUDE_SKILL_DIR}/rebalance.py"                    # die Abweichungstabelle, keine Dateien geschrieben
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --band 5 --write   # Vorschläge -> trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --sell-untracked   # verkauft auch Positionen, die nicht in den Zielen stehen
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --json
```

## Was er liest und schreibt

| Datei | Was |
| --- | --- |
| `trader/portfolio.json` | Bargeld und Positionen (Stückzahlen); gleiche Form wie `portfolio.example.json` von trader-steuer |
| `trader/prices.json` | ein Kurs für jede Position und jedes Ziel (trader-alarm schreibt sie) |
| `trader/targets.json` | die Zielgewichte (trader-kopie schreibt sie) |
| `trader/config.json` | Bargeld-Puffer, Deckel, Mindest- und Höchstauftrag, `rebalance_band_pct` |
| `trader/orders.json` | die Vorschläge, die er mit `--write` schreibt, Verkäufe zuerst |

## Wie er entscheidet

- **Depotwert** = Bargeld + jede Position zu ihrem Kurs.
- **Ziele**: die Gewichte, umgerechnet auf den Aktienanteil (Depot minus
  Bargeld-Puffer), wobei keine Aktie mehr als `max_position_pct` des Depots
  bekommt. Der Überschuss wird verteilt, genauso wie trader-budget es macht,
  damit eine gedeckelte Aktie nicht jeden Monat wieder über den Deckel
  hinaus gekauft wird.
- **Abweichung** = aktueller Prozentanteil am Depot minus Ziel-Prozent.
- **Band**: nur Aktien, die MEHR als das Band abweichen, werden gehandelt
  (Standard 5 Punkte). Ein Trade bringt die Aktie ganz zurück auf ihr Ziel.
  Ein kleines Band heißt mehr Trades, ein weites Band weniger.
- **Erst Verkäufe**, dann Käufe. Käufe geben nur Bargeld über dem Puffer aus;
  reicht es nicht, werden alle Käufe um denselben Anteil verkleinert.
- Trades unter `min_order_usd` werden übersprungen. Ein Trade über
  `max_order_usd` wird auf das Maximum gekürzt, mit dem Hinweis, nach der
  Ausführung nochmal zu laufen.
- **Nicht verfolgte** Positionen (nicht in den Zielen) bleiben liegen und werden
  aufgelistet, außer der Nutzer will sie mit `--sell-untracked` verkaufen.

## Schritte

1. Kurse aktualisieren: trader-alarm `alerts.py quote --holdings --write`.
2. Führe `rebalance.py` aus und zeig die Abweichungstabelle in einfachen
   Worten: welche Aktien zu hoch oder zu niedrig liegen, um wie viele Punkte,
   und was er handeln würde.
3. Vor jedem Verkauf führ trader-steuer aus (`lots.py sell TICKER SHARES`) und
   zeig, was der Verkauf realisieren würde. Ein Gewinner-Verkauf kann eine
   Steuerrechnung bedeuten.
4. Will der Nutzer die Trades: `rebalance.py --write`, dann `/trader-risiko`,
   dann `/trader-broker`, ein Ja pro Auftrag.

## Beispiel: das Depot aus dem Reel

Das Kopie-Depot, gekauft am 13.10.2026 (AAPL $1,250 gedeckelt, AXP $1,119,
GOOGL $823, KO $708, BAC $600, $500 Bargeld), zu den Schlusskursen dieses Tages
bewertet, liegt auf seinen Zielen: jede Abweichung ist unter 0.1 Punkt, also
sagt `rebalance.py` "Nichts liegt außerhalb des Bands" und endet mit Exit 1.
Zahlt der Nutzer $2,000 Bargeld ein, ist das Depot $7,000 wert und der Deckel
wird $1,750. AAPL liegt dann 7.1 Punkte unter seinem Ziel und AXP 6.4, beide
außerhalb des 5-Punkte-Bands, also schlägt er vor, $499 AAPL und $446 AXP zu
kaufen. GOOGL, KO und BAC liegen auch unter dem Ziel, aber innerhalb des Bands,
also warten sie. Zahlt er stattdessen $5,000 ein, liegen alle fünf außerhalb des
Bands: fünf Käufe, die den Puffer von $1,000 als Bargeld stehen lassen.

## Was er nie tut

Er gibt nie einen Auftrag auf, handelt nie innerhalb des Bands, um kleinen
Unterschieden hinterherzulaufen, verkauft nie Positionen außerhalb der Ziele,
außer er wird darum gebeten, und sagt nie, dass ein Rebalancing die Rendite verbessert.

## Sicherheit

- Bildung und Werkzeug. Keine Anlageberatung. Ein Rebalancing folgt deinen eigenen Zielen, keiner Prognose.
- Standardmäßig Paper Trading, also Spielgeld (`"paper": true`). Scharf zu gehen ist deine eigene Entscheidung, in deiner eigenen Konfiguration.
- In diesem Paket gibt nur trader-broker einen Auftrag auf, und nur wenn alle drei Dinge stimmen: trader-risiko
  hat genau diesen Auftrag heute geprüft und freigegeben, du hast im Chat für genau diesen einen Auftrag ausdrücklich
  „Ja“ geschrieben (nichts passiert ohne dein „Ja“), und du hast auf Claudes eigener Karte einmal auf „Erlauben“ geklickt, weil das
  Order-Tool auf „Needs approval“ (Genehmigung nötig) steht (Customize > Connectors > tool permissions). Stell ein Order-Tool nie auf „Always allow“ (Immer erlauben).
- Keine Optionen, kein Hebel (Margin), kein Leerverkauf (Short Selling) und keine Hebelprodukte, außer die Konfiguration erlaubt es und du bestätigst es.
- Broker-Schlüssel liegen in Umgebungsvariablen oder in der Claude-Desktop-Konfiguration, nie im Chat.
