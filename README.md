# Der Trader-Skill

Elf Claude-Skills auf Deutsch, die die Investoren kopieren, denen du vertraust, die Nachrichten und den US-Kongress im Blick behalten, jeden Trade an dein Budget anpassen und nie einen Auftrag ohne dein Ja abschicken.

Kostenlos, MIT, keine Anmeldung, kein API-Schlüssel. Die Werkzeuge sind einfaches Python und lesen
öffentliche Daten: SEC EDGAR, das Melderegister des US-Repräsentantenhauses, Nachrichten-Feeds.

Einer nimmt den Investor auseinander, den du kopieren willst, direkt aus seiner 13F-Meldung. Einer
beobachtet die neuesten Nachrichten zu jeder Aktie, die du hältst. Einer verwaltet dein Budget: jeder
Dollar verteilt, ein Bargeld-Puffer bleibt, keine Aktie über deiner Obergrenze. Und der krasseste ist
der Pelosi-Skill: Er liest die Aktiengeschäfte, die Mitglieder des US-Kongresses offenlegen, sobald die
Meldung erscheint.

Und kein Trade geht durch, bevor du Ja sagst. Nichts in diesem Paket kann von selbst einen Auftrag
abschicken. Standard ist Paper Trading, also Spielgeld.

## Einrichten

Füge das in Claude ein:

```
https://github.com/neubauerwilli/trader-skill-deutsch

install skill
```

Oder als Plugin in Claude Code:

```
/plugin marketplace add neubauerwilli/trader-skill-deutsch
/plugin install trader-skill-deutsch@trader-skill-deutsch
```

Als Plugin bekommen die Skills einen Namensraum, sie heißen dann `/trader-skill-deutsch:trader-kopie` und
so weiter. Wenn du einfach `/trader-kopie` willst, kopiere die Ordner:

```bash
git clone https://github.com/neubauerwilli/trader-skill-deutsch.git
cp -r trader-skill-deutsch/trader-* ~/.claude/skills/
```

Nur für ein Projekt statt global: dieselben Ordner nach `.claude/skills/` in deinem Projekt kopieren.
Gar kein Claude Code? Füge eine einzelne `SKILL.md` oben in einen Chat ein, dann läuft sie als Modus.
Die Python-Werkzeuge fallen dann weg, die Methode funktioniert trotzdem.

Die Werkzeuge brauchen Python 3.8 oder neuer. Nichts per pip installieren. Unter Windows heißt der
Aufruf oft `py` statt `python3`. Die SEC verlangt, dass sich jedes Skript ausweist: Trag deinen Namen
und deine E-Mail in `trader/config.json` ein (`"sec_user_agent": "Dein Name you@example.com"`) oder in
die Umgebungsvariable `SEC_USER_AGENT`.

## Die elf

| Befehl | Rolle | Was er macht |
| --- | --- | --- |
| `/trader-kopie` | Investor kopieren | Liest die 13F-HR-Meldung eines Investors bei SEC EDGAR und baut sein Depot als Gewichte nach, die du kopieren kannst, samt allem, was seit dem letzten Quartal gekauft, aufgestockt, reduziert und verkauft wurde. |
| `/trader-nachrichten` | Nachrichten-Wächter | Beobachtet Schlagzeilen und SEC-Meldungen (8-K) zu jeder Aktie, die du hältst, und markiert, was sie betrifft. |
| `/trader-budget` | Budget-Verwalter | Teilt jede Position nach deinem Budget auf, hält einen Bargeld-Puffer und deckelt jede einzelne Aktie. |
| `/trader-pelosi` | Pelosi-Tracker | Liest die Aktienmeldungen des US-Kongresses (House PTRs), sobald sie erscheinen, macht aus jedem PDF saubere Zeilen und meldet dir jede neue Meldung einmal. |
| `/trader-analyst` | Quartalszahlen-Analyst | Holt die gemeldeten Zahlen jeder Firma aus SEC XBRL (Umsatz, Gewinn, verwässerter Gewinn je Aktie, Margen, Wachstum) und ihre letzte Quartalsmitteilung. |
| `/trader-filter` | Aktienfilter | Prüft Firmen gegen deine eigenen Regeln und sortiert, was durchkommt. |
| `/trader-risiko` | Risiko-Wächter | Prüft jeden vorgeschlagenen Auftrag gegen deine Grenzen und blockt alles, was darüber liegt. |
| `/trader-broker` | Auftrags-Ausführer | Macht aus einem freigegebenen Auftrag genau das Ticket, das dein Broker-Connector bekommt, und hält dein Ja fest. Er schickt selbst nie etwas ab. |
| `/trader-steuer` | Steuer-Helfer | Verfolgt Einstandskurse je Kauf, Gewinne und Verluste, Kandidaten für Verlustverrechnung und Wash Sales (US-Steuerrecht). |
| `/trader-alarm` | Kursalarme | Meldet sich, wenn ein Kursziel, ein Stopp oder die Eröffnungsglocke erreicht ist, mit dem echten NYSE-Kalender. |
| `/trader-ausgleich` | Depot-Ausgleich | Bringt dein Depot zurück auf die Zielgewichte, wenn es über deine Bandbreite hinaus abweicht (Rebalancing). |

## So nutzt du es

Alles landet in einem Ordner `trader/` in deinem Projekt, und jeder Skill liest, was der vorige geschrieben hat:

```
kopie -> budget -> risiko -> broker              (einen Investor kopieren)
nachrichten / pelosi / alarm                      (beobachten, nach Zeitplan)
analyst -> filter -> budget -> risiko -> broker   (selbst auswählen)
ausgleich / steuer -> risiko -> broker            (pflegen)
```

Ein Beispiel: Berkshire Hathaway mit 5.000 Dollar kopieren.

1. **`/trader-kopie`** „kopier die Top 5 von Berkshire“. Der Skill findet Berkshire Hathaway
   (CIK 1067983), lädt die neueste 13F-HR-Meldung (Q2 2026, eingereicht am 14.08.2026) und liest jede
   Zeile: Apple 22,04 % des gemeldeten Depots, American Express 17,14 %, Alphabet 12,62 % (beide
   Aktiengattungen), Coca-Cola 10,86 %, Bank of America 9,20 %. Seit dem letzten Quartal: neu D.R. Horton,
   komplett verkauft Constellation Brands. Er schreibt `trader/targets.json`, die Top 5 neu auf 100 %
   gerechnet: 30,67 / 23,85 / 17,56 / 15,11 / 12,80 %.
2. **`/trader-budget`** mit 5.000 Dollar Budget, 10 % Bargeld-Puffer und 25 % Obergrenze: Apple 1.250 Dollar
   (gedeckelt), American Express 1.119 Dollar, Alphabet 823 Dollar, Coca-Cola 708 Dollar, Bank of America
   600 Dollar, 500 Dollar Bargeld, 0 Dollar ungenutzt. Er schreibt `trader/orders.json` als Vorschläge.
3. **`/trader-risiko`** prüft alle fünf gegen deine Grenzen: gesperrte Ticker, Auftragsgröße,
   Positionsgröße nach dem Kauf, den Bargeld-Puffer, keine Optionen und kein Kredit. PASS oder BLOCK,
   mit Begründung.
4. **`/trader-broker`** zeigt dir jedes Ticket: „Buy $1,250 of AAPL, market, day“. Du tippst Ja für genau
   diesen Auftrag. Claude ruft deinen Broker-Connector auf (den MCP-Server von Alpaca, standardmäßig mit
   Spielgeld-Konto), und Claudes eigene Karte „Allow once / Deny“ fragt dich noch einmal.
5. Nach Zeitplan: **`/trader-nachrichten`** markiert Schlagzeilen zu deinen Aktien, **`/trader-pelosi`**
   meldet dir neue Offenlegungen, und **`/trader-alarm`** meldet sich bei deinen Kursmarken.
6. Jedes Quartal: **`/trader-kopie`** vergleicht die neue 13F-Meldung mit der alten, und
   **`/trader-ausgleich`** schlägt die Trades vor, um wieder gleichzuziehen.

## Die Werkzeuge

Alles Python aus der Standardbibliothek. Die Daten-Werkzeuge lesen öffentliche Schnittstellen und
halten sich an die Regeln (SEC: sich ausweisen, höchstens 10 Anfragen pro Sekunde).

```bash
python3 trader-kopie/edgar13f.py find buffett                                     # CIK eines Melders
python3 trader-kopie/edgar13f.py filings 1067983                                  # seine 13F-HR-Meldungen
python3 trader-kopie/edgar13f.py --help                                           # holdings, diff, copy
python3 trader-nachrichten/news.py --tickers AAPL AXP GOOGL KO BAC --hours 24     # Schlagzeilen + 8-Ks
python3 trader-pelosi/ptr.py index --member pelosi                                # ihre PTRs dieses Jahr
python3 trader-pelosi/ptr.py --help                                               # new, fetch, parse
python3 trader-analyst/facts.py AAPL                                              # Quartale, Wachstum, EPS
python3 trader-filter/screen.py trader/metrics.json --rules trader/rules.json
python3 trader-budget/size.py --budget 5000 --write                               # -> trader/orders.json
python3 trader-risiko/check.py                                                    # PASS / BLOCK je Auftrag
python3 trader-broker/ticket.py show                                              # die Tickets, READY oder nicht
python3 trader-steuer/lots.py harvest --min-loss 100                              # mit Wash-Sale-Prüfung
python3 trader-alarm/alerts.py clock                                              # NYSE offen oder zu
python3 trader-ausgleich/rebalance.py --band 5                                    # die Abweichungstabelle
```

```bash
python3 -m unittest discover -s tests -v
```

Die Tests laufen offline, auf kleinen Ausschnitten aus echten Meldungen: Berkshires Tabellen aus Q1 und
Q2 2026, der Index des House Clerk für 2026, echter PTR-Text, echte XBRL-Daten und Feeds.

## Das Kleingedruckte

**Keine Anlageberatung.** Das ist Software, die öffentliche Daten liest und rechnet. Sie kennt deine
Lage nicht, sie kann nicht in die Zukunft sehen, und wer einen Investor kopiert, kopiert nicht seine
Ergebnisse. Du kannst Geld verlieren. Entscheide selbst und sprich mit einer zugelassenen Beratung,
wenn du Rat brauchst.

**Kein Trade geht durch, bevor du Ja sagst.** Nur `/trader-broker` kann zu einem Auftrag führen, und
nur, nachdem `/trader-risiko` genau diesen Auftrag freigegeben hat und du im Chat Ja dazu tippst. Stell
dein Broker-Werkzeug in Claude auf „Needs approval“ (Customize, Connectors, Tool-Berechtigungen), nie
auf „Always allow“, damit Claudes eigene Karte „Allow once / Deny“ eine zweite Prüfung ist. Keine
Optionen, kein Kredit, keine Leerverkäufe und kein Hebel, außer du schaltest sie ein.

**Erst mit Spielgeld.** `"paper": true` ist der Standard in `config.example.json`, und der MCP-Server von
Alpaca nutzt standardmäßig sein Spielgeld-Konto. Schlüssel liegen in Umgebungsvariablen, nie im Chat.

**13F-Daten sind alt und unvollständig.** Fonds melden bis 45 Tage nach Quartalsende, und eine 13F
listet nur gekaufte Positionen in US-gelisteten Aktien und ein paar verwandten Wertpapieren: keine
Leerverkäufe, kein Bargeld, keine im Ausland gelisteten Aktien. Du siehst, was sie hielten, nicht, was
sie heute halten.

**Die Trades des Kongresses sind öffentlich, keine Insider-Informationen.** Abgeordnete müssen Trades
über 1.000 Dollar innerhalb von 45 Tagen offenlegen (STOCK Act). Der Pelosi-Skill liest diese
öffentlichen Meldungen, sobald sie erscheinen, mit den Beträgen als die Spannen, in denen sie gemeldet
werden. Er hat keinen Zugang zu irgendetwas, das nicht öffentlich ist.

**Die Daten kommen aus kostenlosen öffentlichen Quellen.** SEC EDGAR, der House Clerk, Nachrichten-RSS und
die Chart-Schnittstelle von Yahoo Finance (inoffiziell, sie kann sich ändern). Prüf alles Wichtige bei
deinem Broker, bevor du handelst.

**Steuern.** `/trader-steuer` rechnet mit Kaufposten und Wash-Sale-Fristen nach US-Steuerrecht. In
Deutschland gilt die Abgeltungsteuer. Das ist keine Steuerberatung.

## Dateien

```
trader-kopie/        SKILL.md, edgar13f.py, filers.json, share_classes.json, targets.example.json
trader-nachrichten/  SKILL.md, news.py
trader-budget/       SKILL.md, size.py, config.example.json, targets.example.json
trader-pelosi/       SKILL.md, ptr.py
trader-analyst/      SKILL.md, facts.py, metrics.example.json
trader-filter/       SKILL.md, screen.py, rules.example.json
trader-risiko/       SKILL.md, check.py
trader-broker/       SKILL.md, ticket.py, claude_desktop_config.example.json
trader-steuer/       SKILL.md, lots.py, portfolio.example.json
trader-alarm/        SKILL.md, alerts.py, alerts.example.json
trader-ausgleich/    SKILL.md, rebalance.py
tests/               unittest-Suite + Offline-Testdaten
```

## Herkunft

Deutsche Fassung von Willi Neubauer, [willineubauer.com](https://willineubauer.com), Instagram
[@willi_neubauer.ai](https://www.instagram.com/willi_neubauer.ai/).

Beruht auf dem englischen [Trader skill](https://github.com/Jakeschincariol/trader-skill) von Jake
Schincariol ([opusjake.ai](https://opusjake.ai)), veröffentlicht unter MIT. Übersetzt für den
deutschsprachigen Raum. Daten, Zahlen und Rechenwege sind unverändert.

## Lizenz

MIT, siehe `LICENSE`. Nimm es, ändere es, nutze es.
