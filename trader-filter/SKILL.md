---
name: trader-filter
description: >-
  Der Aktienfilter. Prüft eine Liste von Firmen gegen deine eigenen Regeln
  (Umsatzwachstum, Margen, Gewinn je Aktie, KGV, Gewinnrendite, Branche, alles
  aus der Kennzahlen-Datei), sagt genau, welche Regel jede Firma besteht oder
  verfehlt und um wie viel, und sortiert die Firmen, die bestehen, in eine
  Shortlist. Liest die Kennzahlen, die /trader-skill-deutsch:trader-analyst aus SEC-Berichten
  schreibt, oder jede beliebige CSV. Nutze ihn, wenn der Nutzer sagt: "filtere
  Aktien", "finde mir Firmen, die", "filter meine Watchlist", "welche davon
  erfüllen meine Regeln", "Shortlist", "rank diese Aktien", "wachsende Firmen
  mit KGV unter 30", "screen for stocks", "shortlist candidates" oder "rank
  these stocks".
argument-hint: "[Kennzahlen-Datei] [--rules rules.json]"
---

# trader-filter

**Befehle:** Die vollständigen Aufrufe unten gelten für die Plugininstallation. Bei einzeln nach `.claude/skills/` kopierten Ordnern entfällt der Präfix `trader-skill-deutsch:`. Verwende beim Wechsel zu einem anderen Skill den in deiner Sitzung tatsächlich angebotenen Namen.


Ein Filter macht aus "Ich will profitable Firmen, die noch wachsen und nicht
zu teuer sind" Regeln, die ein Computer prüfen kann. Und er zeigt seine Arbeit:
Jedes Bestanden und jedes Durchgefallen kommt mit der Zahl und der Schwelle.

Das Werkzeug in diesem Ordner (Python-Standardbibliothek, kein Netzwerk):

```bash
python3 "${CLAUDE_SKILL_DIR}/screen.py" --help
```

## Was er liest und schreibt

- Kennzahlen: `trader/metrics.json` von `/trader-skill-deutsch:trader-analyst` (`facts.py --metrics`)
  oder jede JSON-Liste oder CSV mit einer Spalte `ticker`. Zahlen in der CSV
  dürfen `$`, `%` und Kommas enthalten.
- Kurse (optional): `trader/prices.json` (`{"prices": {"AAPL": 341.27}}`,
  aus beliebiger Quelle; `/trader-skill-deutsch:trader-alarm` schreibt sie). Mit Kursen ergänzt er
  `price`, `pe` (Kurs / Gewinn je Aktie der letzten zwölf Monate, nur wenn der
  Gewinn positiv ist) und `earnings_yield` (Gewinnrendite).
- Regeln: eine JSON-Datei. Als Startpunkt gibt es `rules.example.json` in
  diesem Ordner.
- Mit `--out` schreibt er `trader/shortlist.json`: wer bestanden hat, mit
  Punktzahl und Begründung, und wer durchgefallen ist und warum.

## Die Regeldatei

```json
{"name": "Wachsend, profitabel, nicht auf Perfektion bewertet",
 "rules": [{"metric": "revenue_growth_yoy", "op": ">=", "value": 10},
           {"metric": "operating_margin", "op": ">=", "value": 25},
           {"metric": "pe", "op": "<=", "value": 35}],
 "rank":  [{"metric": "revenue_growth_yoy", "order": "desc", "weight": 2},
           {"metric": "pe", "order": "asc", "weight": 1}],
 "missing": "fail"}
```

Operatoren: `>`, `>=`, `<`, `<=`, `==`, `!=`, `between` ([niedrig, hoch]), `in`
und `not in` (eine Liste, zum Beispiel Ticker oder Branchen, die du
ausschließen willst). `"missing": "fail"` lässt eine Firma durchfallen, der
eine Kennzahl fehlt, die eine Regel braucht (Banken haben in den SEC-Daten
keine operative Marge). `"skip"` überspringt diese Regel für die Firma und
sagt das dazu. Die Rangliste gibt jeder Firma, die besteht, pro Rang-Kennzahl
eine Note von 0 bis 1 und nimmt den gewichteten Durchschnitt. NaN und unendliche Kennzahlen gelten als fehlend und erhalten keinen Rangbeitrag. Grenzwerte müssen endliche Zahlen sein; Ranggewichte müssen positiv und endlich sein.

## Schritte

1. Übersetze die Worte des Nutzers in Regeln. Frag nach, wenn eine Schwelle
   unklar ist ("billig" heißt welches KGV?). Schreib die Regeln nach
   `trader/rules.json`.
2. Hol die Kennzahlen für die Kandidaten: Starte `/trader-skill-deutsch:trader-analyst` dafür mit
   `--metrics trader/metrics.json` (also `facts.py AAPL AXP GOOGL KO BAC
   --metrics trader/metrics.json`) oder nimm die CSV des Nutzers. Kurse aus
   `trader/prices.json`, wenn das KGV eine Rolle spielt.
3. Starte den Filter:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/screen.py" trader/metrics.json --rules trader/rules.json \
     --prices trader/prices.json --out trader/shortlist.json
   ```

   Exit-Code 1 heißt: Niemand hat bestanden. Sag das und zeig die knappsten
   Fehlschläge.
4. Berichte die Shortlist mit den Gründen, die Durchgefallenen mit den Zahlen,
   an denen sie gescheitert sind, und dem Datum der Zahlen (das letzte
   Quartalsende je Firma, das Kursdatum). Empfiehl `/trader-skill-deutsch:trader-analyst` für die
   Spitzenreiter, bevor irgendetwas anderes passiert.

## Beispiel: die fünf aus dem Reel

Kennzahlen aus der SEC für AAPL, AXP, GOOGL, KO und BAC, Kurse vom Schlusskurs
am 2026-10-13 und `rules.example.json`:

```
Wachsend, profitabel, nicht auf Perfektion bewertet: 2 von 5 Firmen bestehen.
 1. PASS GOOGL  Alphabet Inc.  score 0.75   (growth 24.2, P/E 17.8)
 2. PASS KO     COCA COLA CO   score 0.25   (growth 12.1, P/E 28.3)
    FAIL AAPL   pe 39.2, braucht <= 35
    FAIL BAC    operating_margin fehlt
    FAIL AXP    revenue_growth_yoy 8.6, braucht >= 10; operating_margin fehlt
```

Dann die Vorbehalte, die ein sorgfältiger Analyst ergänzt: Alphabets letztes
Quartal hat einen Nettogewinn über dem operativen Gewinn (Gewinne außerhalb
des Kerngeschäfts), und Coca-Colas Zahlen enden mit dem Quartal zum
2026-04-03, weil die SEC-Daten dem letzten 10-Q hinterherhinken.

## Was er nie tut

- Nennt nie eine Firma, die besteht, einen Kauf. Ein Filter sortiert gemeldete
  Zahlen, er ist keine Empfehlung.
- Füllt nie eine fehlende Zahl auf und rät nie eine Kennzahl: Fehlt sie, fehlt
  sie.
- Schickt nie von sich aus etwas an `/trader-skill-deutsch:trader-budget` oder an einen Weg zum
  Auftrag.

## Sicherheit

Bildung und Werkzeug, keine Anlageberatung. Dieser Skill filtert nur Zahlen.
Nichts löst einen Auftrag aus außer `/trader-skill-deutsch:trader-broker`, und das erst, nachdem
`/trader-skill-deutsch:trader-risiko` bestanden ist und der Nutzer für genau diesen Auftrag ein
ausdrückliches Ja gegeben hat. Ohne dein Ja passiert nichts.
