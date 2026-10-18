---
name: trader-pelosi
description: >-
  Der Pelosi-Tracker. Beobachtet die Aktiengeschäfte, die Mitglieder des
  US-Kongresses nach dem STOCK Act offenlegen müssen: liest die öffentlichen
  Periodic Transaction Reports (PTRs) des House Clerk, sobald sie
  veröffentlicht sind, meldet jede neue Meldung genau einmal und macht aus
  jeder PDF saubere Zeilen (wer, Eigentümer, Aktie, Ticker, Kauf oder
  Verkauf, Strike und Verfall bei Optionen, Handelsdatum, Betragsspanne, wie
  viele Tage bis zur Meldung vergingen). Markiert Geschäfte in Aktien, die du
  hältst. Die Senatsmeldungen deckt ein dokumentierter manueller Weg ab.
  Nutze es, wenn der Nutzer sagt: "Was kauft Pelosi", "Was hat Pelosi
  gekauft", "verfolge Nancy Pelosi", "Kongress-Trades", "Aktiengeschäfte von
  Politikern", "hat ein Abgeordneter X gekauft", "melde dich, wenn Pelosi
  etwas einreicht", "starte den Pelosi-Skill", oder auf Englisch "what is
  Pelosi buying", "track Nancy Pelosi", "Congress trades", "politician stock
  trades", "did any congressman buy X", "alert me when Pelosi files" oder
  "run the Pelosi skill".
argument-hint: "[Name des Mitglieds] [--read]"
---

# trader-pelosi

**Befehle:** Die vollständigen Aufrufe unten gelten für die Plugininstallation. Bei einzeln nach `.claude/skills/` kopierten Ordnern entfällt der Präfix `trader-skill-deutsch:`. Verwende beim Wechsel zu einem anderen Skill den in deiner Sitzung tatsächlich angebotenen Namen.


Mitglieder des Kongresses müssen ihre Geschäfte melden, ebenso die ihres
Ehepartners und ihrer abhängigen Kinder. Der House Clerk veröffentlicht jede
Meldung als PDF und listet alle in einer Indexdatei pro Jahr. Dieser Skill
liest den Index, merkt, sobald eine neue Meldung erscheint, und liest die
Meldung für dich.

Er ist schnell. Er ist nicht früh. Eine PTR ist fällig 30 Tage, nachdem das
Mitglied vom Geschäft erfahren hat, und nie später als 45 Tage danach. Bis
sie jemand lesen kann, ist das Geschäft also Tage oder Wochen alt. Es gibt
hier keine Insiderinformation: nur öffentliche Meldungen, schnell gelesen.
Sag das jedes Mal.

Das Werkzeug in diesem Ordner (Python-Standardbibliothek; `pdftotext` aus
poppler ist optional, um die PDFs lokal zu lesen):

```bash
python3 "${CLAUDE_SKILL_DIR}/ptr.py" --help
```

## Was er liest und schreibt

- Liest `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/<YEAR>FD.zip`
  (ein `<Member>` pro Meldung: Name, Wahlbezirk, FilingType, FilingDate,
  DocID) und jede PTR unter `.../public_disc/ptr-pdfs/<YEAR>/<DocID>.pdf`.
- Schreibt `trader/state/pelosi.json` (die DocIDs, die schon gezeigt wurden,
  damit jede PTR nur einmal meldet) und legt heruntergeladene PDFs in
  `trader/state/ptr-pdfs/` ab.
- Liest `trader/portfolio.json` (falls vorhanden), um Geschäfte in dem zu
  markieren, was du hältst.

## Die Regeln

- **Zitiere die Meldung, nicht einen Tracker.** Jede Zeile kommt aus der
  PTR-PDF auf der Seite des Clerk, mit DocID und Link. Fremde Tracker sind
  zum Stöbern in Ordnung, können aber danebenliegen (Capitol Trades zeigte
  Pelosis Handelsdaten einen Tag früher als ihre PTRs). Die PTR gewinnt.
- **Beträge sind Spannen.** "1,000,001 USD - 5,000,000 USD", nie "3 Millionen USD".
- **Der Eigentümer zählt.** SP ist der Ehepartner, JT gemeinsam, DC ein
  abhängiges Kind, leer das Mitglied selbst. Lies die Angabe je Zeile; ordne sie nicht pauschal Nancy oder Paul Pelosi zu.
- **Nenne die Verzögerung.** Jede Zeile zeigt, wie viele Tage zwischen
  Geschäft und Meldung vergingen. Das frühere Datum aus 30 Tagen nach der gemeldeten Kenntnisnahme und 45 Tagen nach dem Handel ist die Frist; eine spätere Meldung wird markiert.
- **Optionen sind Optionen.** Ein gekaufter Call mit Strike und Verfall ist
  kein Aktienkauf. Das Werkzeug liest Kontrakte, Strike und Verfall aus der
  Beschreibung und markiert Ausübungen.

## Schritt 1: Was wurde eingereicht

```bash
python3 "${CLAUDE_SKILL_DIR}/ptr.py" index --member pelosi
python3 "${CLAUDE_SKILL_DIR}/ptr.py" index --since 2026-10-01        # jedes Mitglied
python3 "${CLAUDE_SKILL_DIR}/ptr.py" index --member "Josh Gottheimer" --year 2025
```

`--member` nimmt einen Nachnamen, "Vorname Nachname" oder "Nachname, Vorname"
und lässt sich wiederholen. `--type ''` listet jeden Meldungstyp (Jahresberichte
sind Typ O).

## Schritt 2: Auf neue Meldungen warten

```bash
python3 "${CLAUDE_SKILL_DIR}/ptr.py" new --member pelosi --read
```

Zeigt nur PTRs, die bei der letzten Prüfung noch nicht da waren (beim ersten
Lauf die letzten 45 Tage), lädt sie mit `--read` herunter, liest sie und
trägt sie in die Zustandsdatei ein. Exit-Code 1 heißt: nichts Neues. Im
Januar prüft er auch den Index des Vorjahres. Der Clerk baut die Indexdatei
etwa einmal am Tag neu (die Kopie vom 13.10.2026 trug den Stempel 09:00 Uhr
am selben Morgen), also reicht eine Prüfung pro Stunde an Werktagen:
Starte sie mit `/loop 1h` oder als geplante Aufgabe.

## Schritt 3: Eine Meldung lesen

```bash
python3 "${CLAUDE_SKILL_DIR}/ptr.py" fetch 20035143
python3 "${CLAUDE_SKILL_DIR}/ptr.py" parse ptr.txt --filed 2026-08-21 --json
```

`fetch` speichert die PDF und gibt die Zeilen aus, wenn `pdftotext`
installiert ist. Ist es nicht installiert, öffne die gespeicherte PDF und
lies sie direkt: Du kannst PDFs lesen. `parse` macht aus
`pdftotext -layout`-Text Zeilen. Vergleiche die Zeilenzahl mit der PDF, wenn
eine Meldung über mehrere Seiten geht.

## Beispiel: der Alarm aus dem Reel

```
NEU Periodic Transaction Report: Hon. Nancy Pelosi (CA11) eingereicht am 2026-08-21, doc 20035143
  - Ehepartner Kauf, Bloom Energy Corporation Class A Common Stock (BE): 100 call-Optionen,
    100 USD Strike, Verfall 2027-06-17, 1,000,001 USD - 5,000,000 USD, gehandelt am
    2026-07-24, 28 Tage später gemeldet
```

Dieselbe PTR zeigt außerdem 10.000 und 5.000 BE-Aktien, einen zweiten Block
mit 100 BE-Calls am 28.07., Intel-Aktien und 50 Intel-Calls (50 USD Strike,
17.06.2027) und eine private Immobilien-LLC ohne Ticker. Ihre anderen PTRs
2026: 20033725 (23.01.), 20034836 (23.06., Intel- und Uber-Calls) und
20035553 (02.10., eine Immobilien-LLC). Dann sagst du: "Das ist eine
öffentliche Offenlegung, 28 Tage nach dem Geschäft eingereicht. Sie sagt, was
Paul Pelosi im Juli gekauft hat, nicht, was jemand als Nächstes kaufen wird."

Wenn der Nutzer danach handeln will, geht es den langen Weg: nur Aktien (die
Konfiguration hat `allow_options: false`), Größe durch `/trader-skill-deutsch:trader-budget`,
Prüfung durch `/trader-skill-deutsch:trader-risiko`, Auftrag durch `/trader-skill-deutsch:trader-broker` erst nach
seinem "Ja".

## Der Senat: der manuelle Weg

Senatsmeldungen liegen auf `https://efdsearch.senate.gov`. Vor der Suche
verlangt die Seite ein Häkchen, mit dem man den rechtlichen Grenzen für die
Nutzung der Meldungen zustimmt. Diese Vereinbarung muss die Person selbst
annehmen, deshalb klickt dieser Skill sie nicht und automatisiert sie nicht.
Der Weg:

1. Der Nutzer öffnet `https://efdsearch.senate.gov/search/` in seinem
   Browser und nimmt die Vereinbarung selbst an.
2. Er wählt den Berichtstyp "Periodic Transactions", tippt den Nachnamen
   des Senators, setzt den Zeitraum und sucht.
3. Er öffnet die Meldung: Elektronische Meldungen zeigen eine Tabelle der
   Geschäfte, Papiermeldungen sind gescannte Bilder.
4. Er fügt die Tabelle (oder einen Screenshot, oder die gespeicherte Seite)
   in den Chat ein, und du schreibst sie in derselben Form wie die
   House-Zeilen auf, mit dem Link zur Meldung und demselben 45-Tage-Hinweis.

## Was er nie tut

- Sagt oder deutet nie an, ein Mitglied habe Insiderinformationen oder
  einen Politiker zu kopieren schlage den Markt. Keine Renditeversprechen.
- Stellt nie eine Spanne als genauen Dollarbetrag dar und nie das Datum
  eines Trackers als Handelsdatum.
- Gibt nie einen Auftrag auf und kopiert nie Optionsgeschäfte.
- Automatisiert nie die Senatsvereinbarung oder irgendeinen Login.

## Sicherheit

- Bildung und Werkzeug, **Keine Anlageberatung**. Nichts in diesem Paket ist
  eine Empfehlung, etwas zu kaufen oder zu verkaufen.
- Standardmäßig Paper Trading, also Spielgeld (`"paper": true` in
  `trader/config.json`).
- Nichts gibt einen Auftrag auf außer `/trader-skill-deutsch:trader-broker`, und das erst, wenn
  `/trader-skill-deutsch:trader-risiko` besteht UND du im Chat für genau diesen Auftrag
  ausdrücklich "Ja" schreibst, UND das Broker-Werkzeug in Claude auf "Needs
  approval" steht (Customize > Connectors > tool permissions), sodass auch
  Claudes eigene Karte "Allow once / Deny" erscheint. Nie "Always allow" für
  ein Auftragswerkzeug. Ohne dein "Ja" passiert nichts.
- Keine Optionen, kein Hebel auf Kredit (Margin), kein Leerverkauf, keine
  Hebelprodukte, außer die Konfiguration erlaubt es und du bestätigst es.
- Kongress-Geschäfte sind ÖFFENTLICHE Offenlegungen, bis zu 45 Tage nach dem
  Geschäft eingereicht (STOCK Act). Dieser Skill liest sie schnell; er hat
  keine Insiderinformation und sagt das auch.
