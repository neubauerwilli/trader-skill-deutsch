---
name: trader-kopie
description: >-
  Der Nachbauer (Reverse Engineer). Liest die öffentlichen SEC-Meldungen eines
  bekannten Investors (die vierteljährliche 13F-HR, auf Wunsch dazu Form 4 für
  Insider-Geschäfte) und baut sein Depot als Gewichte nach, die du kopieren
  kannst: jede Position, ihr Anteil am Fonds, was er seit dem letzten Quartal
  gekauft, aufgestockt, reduziert und verkauft hat, mit Tickern zu jeder CUSIP.
  Danach schreibt er trader/targets.json, damit der Budget-Skill sie auf dein
  Geld zuschneiden kann. Nutze ihn, wenn der Nutzer sagt: "kopier Buffett",
  "kopier Berkshire", "copy Buffett", "copy Berkshire", "was besitzt Ackman",
  "what does Ackman own", "bau diesen Investor nach", "reverse engineer this
  investor", "zeig mir seine 13F", "show me their 13F", "was hat er letztes
  Quartal gekauft", "what did they buy last quarter", "kopier die Top 5",
  "copy the top 5", oder einen Fonds oder bekannten Investor nennt.
argument-hint: "[Investor, Fonds oder CIK] [--top N]"
---

# trader-kopie

Einen Investor zu kopieren beginnt mit dem, was er gesetzlich offenlegen muss:
eine 13F-HR, die jeder Vermögensverwalter mit über 100 Millionen Dollar in
US-börsennotierten Aktien und anderen 13(f)-Wertpapieren jedes Quartal bei der
SEC einreicht. Dieser Skill liest sie direkt von SEC EDGAR, baut das Depot
nach und macht daraus Zielgewichte für dein eigenes Konto.

Das Werkzeug in diesem Ordner (Standardbibliothek von Python, keine Schlüssel):

```bash
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" --help
```

Die SEC verlangt von jedem Programm, dass es sich mit Name und E-Mail
ausweist. Trage das einmal in `trader/config.json` ein (`"sec_user_agent":
"Dein Name you@example.com"`, mit echtem Namen und echter E-Mail des Nutzers)
oder setze `export SEC_USER_AGENT=...`. Das Werkzeug ruft die SEC ohne diese
Angabe nicht auf und sagt, wie man es behebt. Frag den Nutzer danach und
erfinde nie etwas.

## Was er schreibt

- `trader/targets.json`: die Gewichte zum Kopieren, in der Form, die jeder
  andere Trader-Skill liest: `{ "source", "as_of", "weights": [ {"ticker",
  "name", "weight"} ] }`, Gewichte in % mit 2 Nachkommastellen, die über den
  Aktienanteil zusammen 100 ergeben (die 2 Nachkommastellen behalten, die
  Budget-Berechnung braucht sie), dazu der 13F-Anteil, der Wert, die CUSIPs,
  was übersprungen wurde und warum, und die Vorbehalte.
- `trader/tickers.json` (optional, deine eigene): Korrekturen von CUSIP zu
  Ticker, die das Werkzeug immer zuerst nutzt, etwa `{"650111107": "NYT"}`.

`targets.example.json` in diesem Ordner ist das echte Ergebnis des
Reel-Laufs (Berkshire, Top 5).

## Die Regeln

- **Sag jedes Mal, wie alt die Daten sind.** Eine 13F ist eine Momentaufnahme
  vom letzten Tag des Quartals, bis zu 45 Tage später eingereicht. Schreib in
  jede Antwort, die eine nutzt: "Bestand zum 2026-06-30, eingereicht
  2026-08-14".
- **Sag, was sie nicht zeigen kann.** Nur Long-Positionen in US-börsennotierten
  Aktien: keine Shorts, kein Bargeld, keine Anleihen, keine ausländischen
  Aktien, keine privaten Firmen, nichts, was innerhalb des Quartals gekauft und
  wieder verkauft wurde, und keine gezahlten Preise.
- **Die Firma, nicht die Person.** Berkshire Hathaway reicht die 13F ein, nicht
  Warren Buffett. Greg Abel ist seit 2026-01-01 CEO und Buffett wurde am
  2026-09-18 Chairman Emeritus. Sag nie "Buffett hat gekauft", wenn in der
  Meldung nur steht "Berkshire hielt".
- **Jede Zahl kommt aus der Meldung.** Nenne die Accession-Nummer und verlinke
  die Informationstabelle. Ist ein Ticker geraten (mit `*` markiert), sag das
  und korrigiere ihn mit `--figi` oder `trader/tickers.json`, bevor irgendetwas
  kopiert wird.
- **Optionen und Anleihen werden nie kopiert.** PUT-, CALL- und PRN-Zeilen
  werden als ausgeschlossen aufgeführt. Dieses Paket handelt nur Aktien, außer
  die Konfiguration erlaubt mehr.

## Schritt 1: den Investor finden

```bash
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" find buffett
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" find "pershing square" --sec
```

`filers.json` listet 23 bekannte Einreicher mit CIKs, die am 2026-10-13 gegen
die SEC geprüft wurden (Berkshire, Pershing Square, Scion, Bridgewater, ARK,
Duquesne, Appaloosa, Baupost und mehr). `--sec` durchsucht alle
EDGAR-Einreicher nach Namen. Hat ein Einreicher aufgehört einzureichen (Scions
letzte 13F-HR war am 2025-11-03), sag das.

## Schritt 2: das letzte Quartal lesen

```bash
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" filings 1067983
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" holdings 1067983 --combine --top 15
```

Er lädt die Liste der Meldungen, nimmt die neueste ursprüngliche 13F-HR, findet
die XML der Informationstabelle im Ordner der Meldung, addiert die Zeilen, in
die eine Position aufgeteilt ist (Berkshire meldet eine Aktie über mehrere
Tochterfirmen), prüft die Summe gegen das Deckblatt und ordnet Ticker zu:
zuerst deine `trader/tickers.json`, dann `share_classes.json` (geprüfte CUSIPs
für Firmen mit mehreren börsennotierten Gattungen), dann die
`company_tickers.json` der SEC nach Namen und mit `--figi` die kostenlose
CUSIP-Suche von OpenFIGI. `--combine` addiert Aktiengattungen einer Firma
(Alphabet A + C). Gibt es für das Quartal eine Änderungsmeldung, sagt das
Werkzeug es; lies sie mit `--accession`.

## Schritt 3: was sich geändert hat

```bash
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" diff 1067983
```

Neue Positionen, ganz verkaufte, aufgestockte und reduzierte, nach
Aktienzahl gegenüber dem Quartal davor. Das sind Aktienzahlen zu zwei
Zeitpunkten, keine Geschäfte: Sag "hielt am 30. Juni mehr Aktien als am
31. März", nie "kaufte am" einem bestimmten Tag.

## Schritt 4: die Ziele schreiben

```bash
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" copy 1067983 --top 5 --out trader/targets.json
```

Aktiengattungen werden standardmäßig zusammengefasst (`--separate` hält sie
getrennt). `--exclude TICKER` lässt weg, was der Nutzer nicht besitzen will,
`--min-pct 1` streicht Kleinstpositionen. Die Gewichte sind der %-Anteil jeder
Position an der 13F, so skaliert, dass die gewählten zusammen 100 ergeben. Zeig
die Tabelle und übergib dann an `/trader-budget`.

Form 4 (optional): Insider und Besitzer von mehr als 10 % melden ihre Geschäfte
innerhalb von zwei Börsentagen per Form 4. Um sie zu sehen, öffne
`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<CIK>&type=4&owner=include`
(oder die Meldungsliste des Einreichers) und lies die Meldungen, nach denen der
Nutzer fragt. Sie sind schneller als eine 13F, decken aber nur eine Aktie auf
einmal ab.

## Beispiel: das Reel

"Kopier Berkshire Hathaway, Top 5." Die neueste 13F-HR gilt für das Quartal bis
2026-06-30, eingereicht 2026-08-14, Accession 0001193125-26-352200, Summe
$299,253,556,246 (stimmt mit dem Deckblatt überein).

| # | Position | % der 13F | Kopiergewicht |
| --- | --- | --- | --- |
| 1 | Apple (AAPL) | 22.04 | 30.67 |
| 2 | American Express (AXP) | 17.14 | 23.85 |
| 3 | Alphabet (GOOGL + GOOG zusammen) | 12.62 | 17.56 |
| 4 | Coca-Cola (KO) | 10.86 | 15.11 |
| 5 | Bank of America (BAC) | 9.20 | 12.80 |

Gerundet sind das 30.7 / 23.9 / 17.6 / 15.1 / 12.8. Seit dem Quartal davor: neu
D.R. Horton (3,564 Aktien), ganz verkauft Constellation Brands, aufgestockt
Alphabet, Delta, Lennar, Macy's und die New York Times, reduziert Bank of
America, Kroger, Capital One, Nucor, Ally und DaVita. Dann: "Das ist der
Bestand von Berkshire zum 30. Juni 2026. Als Nächstes schneidet
`/trader-budget` ihn auf deine $5,000 zu."

## Was er nie tut

- Er behauptet nie, der Investor halte heute noch, und nie, warum er gekauft hat.
- Er behauptet nie Renditen und nie, dass das Kopieren eines Fonds Geld
  bringt. Eine 13F-Kopie kauft Monate nach dem Fonds, zum heutigen Preis.
- Er schickt nie einen Auftrag ab. Er schreibt Zielgewichte; Berechnung,
  Risikoprüfung und dein Ja kommen danach.
- Er überschreitet nie die Fair-Access-Grenzen der SEC (10 Anfragen pro
  Sekunde; das Werkzeug bleibt weit darunter).

## Sicherheit

- Bildung und Werkzeug. Keine Anlageberatung. Nichts in diesem Paket ist eine
  Empfehlung, irgendetwas zu kaufen oder zu verkaufen.
- Standardmäßig Paper Trading, also Spielgeld (`"paper": true` in
  `trader/config.json`).
- Nichts schickt einen Auftrag ab außer `/trader-broker`, und das nur, wenn
  `/trader-risiko` bestanden hat UND der Nutzer im Chat für genau diesen
  Auftrag ausdrücklich "Ja" schreibt (nichts ohne dein "Ja"), UND das
  Broker-Werkzeug in Claude auf "Needs approval" steht (Customize >
  Connectors > tool permissions), sodass auch Claudes eigene Karte "Allow
  once / Deny" erscheint. Setze ein Auftragswerkzeug nie auf "Always allow".
- Keine Optionen, kein Margin (geliehenes Geld), keine Leerverkäufe, kein
  Hebel, außer die Konfiguration erlaubt es und der Nutzer bestätigt es.
- 13F-Daten sind vierteljährlich und bei der Einreichung bis zu 45 Tage alt.
  Sag das.
