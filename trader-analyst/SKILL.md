---
name: trader-analyst
description: >-
  Der Quartalszahlen-Analyst. Liest die gemeldeten Zahlen eines Unternehmens
  direkt bei SEC EDGAR (XBRL company facts aus seinen 10-Qs und 10-Ks, dazu
  das neueste 8-K zu den Zahlen und seine Pressemitteilung) und fasst
  zusammen, was sich geändert hat: Umsatz, Brutto-, Betriebs- und
  Nettomarge, Nettogewinn und verwässertes EPS, Quartal für Quartal, jeweils
  gegen dasselbe Quartal des Vorjahres, dazu die letzten zwölf Monate (TTM).
  Schreibt eine Kennzahlendatei, die der Filter liest. Nutze es, wenn der
  Nutzer sagt: "Wie waren Apples Zahlen", "analysiere die Quartalszahlen",
  "was hat sich dieses Quartal geändert", "wächst der Umsatz", "prüf das
  10-Q", "vergleiche die Margen", "lass den Analysten über mein Depot laufen",
  oder auf Englisch "how did Apple's earnings look", "analyze the earnings",
  "what changed this quarter", "is revenue growing", "check the 10-Q",
  "compare margins" oder "run the analyst on my holdings".
argument-hint: "[Ticker]"
---

# trader-analyst

**Befehle:** Die vollständigen Aufrufe unten gelten für die Plugininstallation. Bei einzeln nach `.claude/skills/` kopierten Ordnern entfällt der Präfix `trader-skill-deutsch:`. Verwende beim Wechsel zu einem anderen Skill den in deiner Sitzung tatsächlich angebotenen Namen.


Die Berichtssaison bewegt Aktien, und die Pressemitteilung ist so
geschrieben, dass die beste Zahl zuerst steht. Dieser Skill liest die Zahlen,
die das Unternehmen tatsächlich bei der SEC eingereicht hat, stellt sie
Quartal für Quartal nebeneinander und sagt klar, was sich geändert hat.

Das Werkzeug in diesem Ordner (Python-Standardbibliothek, keine Schlüssel):

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" --help
```

Es braucht den SEC-User-Agent (`sec_user_agent` in `trader/config.json` oder
`SEC_USER_AGENT`): "Dein Name you@example.com" mit dem echten Namen und der
echten E-Mail des Nutzers. Ohne ihn weigert es sich, die SEC aufzurufen.

## Was er liest und schreibt

- Liest `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` (jede
  Zahl, die das Unternehmen in seinen Meldungen ausgezeichnet hat), die
  Meldungsliste des Unternehmens (für das neueste 10-Q oder 10-K und das
  neueste 8-K mit Punkt 2.02, "Results of Operations") und den Ordner dieses
  8-K, um die Pressemitteilung zu finden (die Anlage EX-99.1).
- Schreibt mit `--metrics` die Datei `trader/metrics.json`: eine Zeile pro
  Unternehmen (TTM-Umsatz, -Nettogewinn und verwässertes EPS, die neuesten
  Margen und das Wachstum) für `/trader-skill-deutsch:trader-filter`. `metrics.example.json` in
  diesem Ordner ist ein echter Lauf für AAPL, AXP, GOOGL, KO und BAC am
  13.10.2026.

## Die Regeln

- **Zuerst die eingereichten Zahlen.** Die Tabelle kommt aus XBRL-Fakten, die
  das Unternehmen eingereicht hat. Nutze die Pressemitteilung für den
  Zusammenhang (Ausblick, Segmentfarbe) und sag, welche Zahlen woher kamen.
- **Währung und Lücken erhalten.** USD-Zahlen werden nur aus der Einheit USD gelesen, EPS aus USD/shares. Eine andere Währung ist kein Ersatz; fehlende, NaN- oder unendliche Werte bleiben fehlend.
- **Vergleiche Gleiches mit Gleichem.** Wachstum gilt gegenüber demselben
  Quartal des Vorjahres. Veränderungen zum Vorquartal werden gezeigt, aber
  viele Geschäfte sind saisonal (Apples Dezemberquartal ist immer sein
  größtes).
- **Sag, was abgeleitet ist.** Unternehmen reichen kein 10-Q für das vierte
  Quartal ein. Das vierte Quartal ist das 10-K-Jahr minus die drei Quartale
  darin und wird als "abgeleitet" (derived) markiert; abgeleitetes EPS ist
  ungefähr. Die drei abgezogenen Quartale müssen lückenlos anschließen; fehlt ein passender Zeitraum, bleibt Q4 leer. TTM wird nur aus vier zusammenhängenden Quartalen bis zum neuesten Berichtsende gebildet. Alte EPS-Jahre werden nicht mit einem aktuellen Kurs als aktuelles TTM vermischt.
- **Sag, wenn die Daten hinterherhinken.** Die Fakten-Schnittstelle der SEC
  kann einer Meldung hinterherhinken. Ist das neueste 10-Q später als das
  neueste Quartal in den Fakten, druckt das Werkzeug einen HINWEIS mit dem
  Link: Lies das 10-Q selbst, bevor du zusammenfasst.
- **Markiere Einmaleffekte.** Liegt der Nettogewinn über dem Betriebsergebnis,
  stecken Gewinne außerhalb des Kerngeschäfts darin (Bewertungsgewinne aus
  Geldanlagen, Steuerposten). Das Werkzeug sagt es. Banken und Versicherer
  haben keine Bruttomarge: leer, nicht null.

## Schritte

1. Starte es für die betreffenden Unternehmen (oder jeden Ticker in
   `trader/targets.json`):

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/facts.py" AAPL
   python3 "${CLAUDE_SKILL_DIR}/facts.py" AAPL AXP GOOGL KO BAC --metrics trader/metrics.json
   ```

   `--quarters 8` zeigt zwei Jahre, `--json` alles maschinenlesbar, `--cik`
   ein Unternehmen ohne Ticker.
2. Öffne die neueste Pressemitteilung zu den Zahlen (den EX-99-Link) und das
   10-Q, wenn die Zahlen Zusammenhang brauchen, und lies sie.
3. Schreib die Zusammenfassung: eine Zeile zum Quartal (Umsatz, EPS,
   Wachstum), eine zu den Margen, eine zu allem Ungewöhnlichen (ein
   Einmaleffekt, ein abgeleitetes Quartal, Daten, die hinterherhinken) und die
   Links. Einfache Worte, echte Zahlen, keine Adjektive, die die Zahlen nicht
   hergeben.

## Beispiel: die größte Position aus dem Reel

`facts.py AAPL` (13.10.2026): Quartal bis 27.06.2026 (Geschäftsjahr Q3 2026),
Umsatz 109,42 Mrd. USD, +16,4 % gegenüber dem Vorjahr, Nettogewinn 29,79 Mrd. USD
(+27,1 %), verwässertes EPS 2,02 USD (+28,7 %); Bruttomarge 50,1 % (+0,8 Punkte
gegenüber dem Quartal davor); die letzten vier Quartale 466,8 Mrd. USD Umsatz und
8,71 USD EPS. Pressemitteilung: die Anlage EX-99.1 des 8-K, eingereicht am
30.07.2026. Bei Coca-Cola vermerkt das Werkzeug, dass die Fakten der SEC am
03.04.2026 enden, während das 10-Q für den 03.07.2026 schon eingereicht ist,
also liest die Zusammenfassung dieses 10-Q direkt.

## Was er nie tut

- Sagt nie, dass eine Aktie billig, teuer, ein Kauf oder ein Verkauf ist. Er
  berichtet, was das Unternehmen eingereicht hat.
- Erfindet nie eine Zahl, die die Meldungen nicht haben, und schätzt nie ein
  Quartal, das er nicht ableiten kann.
- Ruft die SEC nie ohne den User-Agent auf und bleibt weit unter 10 Anfragen
  pro Sekunde.

## Sicherheit

Bildung und Werkzeug, **Keine Anlageberatung**. Dieser Skill liest nur
Meldungen. Nichts gibt einen Auftrag auf außer `/trader-skill-deutsch:trader-broker`, nachdem
`/trader-skill-deutsch:trader-risiko` besteht und du für genau diesen Auftrag ausdrücklich "Ja"
schreibst. Ohne dein "Ja" passiert nichts.
