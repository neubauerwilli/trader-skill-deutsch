---
name: trader-nachrichten
description: >-
  Der Nachrichten-Wächter. Beobachtet aktuelle Marktnachrichten und
  SEC-Meldungen zu den Aktien, die du hältst (oder gleich kopierst):
  Yahoo-Finance-Schlagzeilen pro Ticker und die 8-K-Meldungen jedes
  Unternehmens bei SEC EDGAR (Quartalszahlen, Wechsel an der Spitze,
  Übernahmen, Korrekturen, Insolvenz). Zeigt nur, was seit der letzten
  Prüfung neu ist, neueste zuerst, markiert, was deine Positionen berührt
  und warum es wichtig ist, und wiederholt nie einen Eintrag. Nutze es, wenn
  der Nutzer sagt: "Gibt es Neuigkeiten zu meinen Aktien", "was ist bei Apple
  los", "überwache die Nachrichten", "beobachte mein Depot", "ist etwas
  passiert", "prüf die 8-Ks", "Morgen-Briefing", oder auf Englisch "any news
  on my stocks", "what's happening with Apple", "monitor the news", "watch my
  portfolio", "did anything break", "check the 8-Ks" oder "morning
  briefing".
argument-hint: "[Ticker]"
---

# trader-nachrichten

**Befehle:** Die vollständigen Aufrufe unten gelten für die Plugininstallation. Bei einzeln nach `.claude/skills/` kopierten Ordnern entfällt der Präfix `trader-skill-deutsch:`. Verwende beim Wechsel zu einem anderen Skill den in deiner Sitzung tatsächlich angebotenen Namen.


Eine Position, die du hältst, kann sich in einer Minute ändern: verfehlte
Quartalszahlen, ein Chef, der geht, eine Klage, eine Korrektur. Dieser Skill
liest die beiden Quellen, die das als Erste melden, für jede deiner Aktien
und sagt dir nur, was neu ist.

Das Werkzeug in diesem Ordner (Python-Standardbibliothek, keine Schlüssel):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py" --help
```

## Was er liest und schreibt

- Deine Ticker: `--tickers`, sonst die Positionen aus `trader/portfolio.json`,
  sonst die Gewichte (`weights`) aus `trader/targets.json` (so läuft er direkt
  nach `/trader-skill-deutsch:trader-kopie`).
- Schlagzeilen: der öffentliche Schlagzeilen-RSS von Yahoo Finance, ein Feed
  pro Ticker
  (`https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US`).
- Meldungen: der Atom-Feed der SEC EDGAR pro Unternehmen, standardmäßig 8-K
  (`--forms 8-K,10-Q` für mehr). Braucht den SEC-User-Agent aus
  `trader/config.json` (`sec_user_agent`) oder `SEC_USER_AGENT`. Ohne ihn
  werden die Meldungen mit einem Hinweis übersprungen, die Schlagzeilen
  laufen trotzdem.
- Schreibt `trader/state/news.json`: was schon gezeigt wurde (30 Tage lang
  aufbewahrt) und die CIK je Ticker, damit jeder Eintrag nur einmal erscheint.

## Die Regeln

- **Schlagzeilen sind Hinweise, keine Fakten.** Bevor du dem Nutzer sagst,
  dass etwas passiert ist, öffne die Meldung oder die Einreichung und lies
  sie. Nenne die Quelle und verlinke sie. Fasse nie eine Meldung hinter einer
  Bezahlschranke zusammen, die du nicht lesen konntest: Sag, dass sie hinter
  einer Bezahlschranke liegt.
- **Meldungen schlagen Schlagzeilen.** Widersprechen sich ein 8-K und eine
  Schlagzeile, ist das 8-K das, was das Unternehmen der SEC gesagt hat.
- **Sag, was es berührt.** Jeder Eintrag nennt die Ticker, die er berührt
  (der Feed, aus dem er kam, plus deine Ticker oder Firmennamen, die er
  erwähnt), und Tags (earnings = Quartalszahlen, analyst = Analysten,
  legal = Rechtliches, deal = Übernahmen, capital = Kapital, people = Personen,
  risk = Risiko). Mit `!` markierte Meldungen tragen einen wichtigen
  8-K-Punkt: Quartalszahlen (2.02), einen Wechsel bei Vorstand oder
  Aufsichtsrat (5.02), eine Wertminderung (2.06), eine Delisting-Mitteilung
  (3.01), frühere Abschlüsse, die nicht mehr verlässlich sind (4.02),
  Insolvenz (1.03) oder einen Cybervorfall (1.05).
- **Keine Vorhersagen.** Berichte, was veröffentlicht wurde. Sag nie, was die
  Aktie als Nächstes tun wird.

## Schritte

1. Starte die Prüfung:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/news.py"
   python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers AAPL AXP GOOGL KO BAC --hours 24
   ```

   Exit-Code 1 heißt: nichts Neues. `--all` zeigt schon gesehene Einträge,
   `--json` gibt alles maschinenlesbar aus, `--dry-run` lässt den Zustand in
   Ruhe.
2. Lies die Einträge, auf die es ankommt: jede Meldung mit `!`, jeden
   Eintrag mit dem Tag earnings, legal oder risk, und alles, was die größten
   Positionen berührt, zuerst.
3. Schreib ein kurzes Briefing: pro Ticker, was passiert ist, die Quelle mit
   Link und eine Zeile, warum es für dieses Depot wichtig ist (sein Gewicht,
   aus `trader/targets.json` oder `trader/portfolio.json`).
4. Könnte eine Meldung den Plan ändern (eine 8-K-Korrektur nach 4.02, eine
   Delisting-Mitteilung), sag das und schlage `/trader-skill-deutsch:trader-analyst` für die
   Zahlen oder `/trader-skill-deutsch:trader-risiko` vor, um offene Aufträge neu zu prüfen. Handle
   nie allein wegen einer Schlagzeile.

Für den Dauerbetrieb nutze `/loop 30m /trader-skill-deutsch:trader-nachrichten` während der
Börsenzeiten oder eine geplante Aufgabe jeden Morgen.

## Beispiel: das Depot aus dem Reel

Der Nutzer hat Berkshires Top 5 kopiert (AAPL, AXP, GOOGL, KO, BAC). Am
Dienstag, 13.10.2026, zeigt die Prüfung zuerst Apple-Schlagzeilen ("Apple
Introduces HomeView Smart Home Hub, Other Gadgets", "Judge orders OpenAI to
turn over hiring records in Apple trade secrets case", getaggt mit legal),
Coca-Cola-Dividendenartikel und von der SEC: Apples 8-K vom 30.07.2026
(Punkt 2.02, Quartalszahlen) und das von Coca-Cola vom 28.07.2026 (2.02).
Briefing: "Apple (deine größte Position, 30,7 %) hat einen Smart-Home-Hub für
299 USD vorgestellt; dazu ein Gerichtsbeschluss in seinem Verfahren um
Geschäftsgeheimnisse gegen OpenAI. Keine neuen Meldungen deiner fünf
Unternehmen in dieser Woche." Hat ein Ticker keine Nachrichten, sag das,
statt das Briefing aufzufüllen.

## Was er nie tut

- Erfindet oder schmückt nie eine Meldung, ein Zitat oder eine Zahl aus.
- Handelt nie wegen Nachrichten und sagt dem Nutzer nie, er solle wegen einer
  Schlagzeile kaufen oder verkaufen.
- Hämmert nie auf einen Server: eine Anfrage pro Feed und Ticker, eine Pause
  zwischen den SEC-Aufrufen, und das Limit der SEC von 10 Anfragen pro
  Sekunde bleibt weit entfernt.

## Sicherheit

Bildung und Werkzeug, **Keine Anlageberatung**. Dieser Skill liest und
berichtet nur. Nichts gibt einen Auftrag auf außer `/trader-skill-deutsch:trader-broker`, nachdem
`/trader-skill-deutsch:trader-risiko` besteht und du für genau diesen Auftrag ausdrücklich "Ja"
schreibst. Ohne dein "Ja" passiert nichts.
