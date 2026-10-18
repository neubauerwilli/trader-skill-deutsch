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
  oder nach Zeitplan (monatlich oder vierteljährlich), nachdem /trader-skill-deutsch:trader-kopie die Ziele aktualisiert hat.
argument-hint: "[Band in Punkten]"
---

# trader-ausgleich

**Befehle:** Die vollständigen Aufrufe unten gelten für die Plugininstallation. Bei einzeln nach `.claude/skills/` kopierten Ordnern entfällt der Präfix `trader-skill-deutsch:`. Verwende beim Wechsel zu einem anderen Skill den in deiner Sitzung tatsächlich angebotenen Namen.


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
  Ausführung nochmal zu laufen. Das gilt auch bei `--sell-untracked`.
- **Nicht verfolgte** Positionen (nicht in den Zielen) bleiben liegen und werden
  aufgelistet, außer der Nutzer will sie mit `--sell-untracked` verkaufen.
- Der Deckel begrenzt die **Ziele**. Bereits übergroße Positionen innerhalb des gewählten Bands bleiben unangetastet; eine gekürzte Order kann die Abweichung nur teilweise beheben. Ein vorbestehender Mangel an Cash erzwingt ebenfalls keinen Verkauf innerhalb des Bands. Der Bericht ist deshalb keine Garantie, dass danach jeder Ist-Wert den Limits entspricht.
- Verkäufe werden auf die unterstützte Stückpräzision abgerundet (vier Nachkommastellen, bei ganzen Aktien ganze Stücke). Kleine Restbestände können bleiben. Cash nach dem Plan setzt voraus, dass alle vorgeschlagenen Verkäufe und Käufe zu den eingesetzten Kursen ausgeführt werden; Gebühren und Kursbewegungen sind nicht enthalten.

## Schritte

1. Kurse aktualisieren: trader-alarm `alerts.py quote --holdings --write`.
2. Führe `rebalance.py` aus und zeig die Abweichungstabelle in einfachen
   Worten: welche Aktien zu hoch oder zu niedrig liegen, um wie viele Punkte,
   und was er handeln würde.
3. Vor jedem Verkauf führ trader-steuer aus (`lots.py sell TICKER SHARES`) und
   zeig, was der Verkauf realisieren würde. Ein Gewinner-Verkauf kann eine
   Steuerrechnung bedeuten.
4. Will der Nutzer die Trades: `rebalance.py --write`, dann `/trader-skill-deutsch:trader-risiko`,
   dann `/trader-skill-deutsch:trader-broker`, ein Ja pro Auftrag.

## Beispiel: das Depot aus dem Reel

Das Kopie-Depot, gekauft am 13.10.2026 (AAPL 1,250 USD gedeckelt, AXP 1,119 USD,
GOOGL 823 USD, KO 708 USD, BAC 600 USD, 500 USD Bargeld), zu den Schlusskursen dieses Tages
bewertet, liegt auf seinen Zielen: jede Abweichung ist unter 0.1 Punkt, also
sagt `rebalance.py` "Nichts liegt außerhalb des Bands" und endet mit Exit 1.
Zahlt der Nutzer 2,000 USD Bargeld ein, ist das Depot 7,000 USD wert und der Deckel
wird 1,750 USD. AAPL liegt dann 7.1 Punkte unter seinem Ziel und AXP 6.4, beide
außerhalb des 5-Punkte-Bands, also schlägt er vor, 499 USD AAPL und 446 USD AXP zu
kaufen. GOOGL, KO und BAC liegen auch unter dem Ziel, aber innerhalb des Bands,
also warten sie. Zahlt er stattdessen 5,000 USD ein, liegen alle fünf außerhalb des
Bands: fünf Käufe, die den Puffer von 1,000 USD als Bargeld stehen lassen.

## Was er nie tut

Er gibt nie einen Auftrag auf, handelt nie innerhalb des Bands, um kleinen
Unterschieden hinterherzulaufen, verkauft nie Positionen außerhalb der Ziele,
außer er wird darum gebeten, und sagt nie, dass ein Rebalancing die Rendite verbessert.

## Sicherheit

- Bildung und Werkzeug. Keine Anlageberatung. Ein Rebalancing folgt deinen eigenen Zielen, keiner Prognose.
- Standardmäßig Paper Trading, also Spielgeld (`"paper": true`). Scharf zu gehen ist deine eigene Entscheidung, in deiner eigenen Konfiguration.
- Dieses Skript schreibt Vorschläge. trader-broker bereitet Tickets vor, führt sie selbst aber nicht aus. Für einen separat verbundenen Broker sind Risikoprüfung, ein ausdrückliches Ja je Order und eine zusätzlich konfigurierte Tool-Genehmigung vorgesehen. Das sind Projektregeln und Berechtigungen, keine technisch unfehlbare Sperre aller Ausführungswege. Kein Order-Tool auf "Always allow" setzen.
- Keine Optionen, kein Hebel (Margin), kein Leerverkauf (Short Selling) und keine Hebelprodukte, außer die Konfiguration erlaubt es und du bestätigst es.
- Broker-Schlüssel liegen in Umgebungsvariablen oder in der Claude-Desktop-Konfiguration, nie im Chat.
