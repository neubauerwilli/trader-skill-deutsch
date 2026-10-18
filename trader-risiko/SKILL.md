---
name: trader-risiko
description: >-
  Der Risiko-Wächter (Risk Manager). Prüft jeden vorgeschlagenen Auftrag gegen
  deine Limits, bevor er den Broker erreichen kann, und blockt alles, was
  darüber liegt: gesperrte Ticker, Höchstauftrag, Mindestauftrag, maximale
  Positionsgröße in Prozent nach der Ausführung, den Bargeldpuffer, Margin
  (geliehenes Geld), Leerverkäufe, Optionen und Krypto (aus, außer erlaubt),
  fehlerhafte Aufträge, Doppelte und ein Tageslimit. Zeigt für jeden Auftrag
  PASS oder BLOCK mit dem Grund und speichert die Urteile, damit trader-broker
  nur einen Auftrag zeigt, der bestanden hat. Nutze ihn, wenn der Nutzer sagt:
  "prüf meine Aufträge", "check my orders", "ist das sicher", "is this safe",
  "Risikoprüfung", "risk check", "bricht das meine Regeln", "darf ich das
  abschicken", "can I place this", oder immer vor /trader-skill-deutsch:trader-broker.
argument-hint: "[Pfad zur orders.json]"
---

# trader-risiko

**Befehle:** Die vollständigen Aufrufe unten gelten für die Plugininstallation. Bei einzeln nach `.claude/skills/` kopierten Ordnern entfällt der Präfix `trader-skill-deutsch:`. Verwende beim Wechsel zu einem anderen Skill den in deiner Sitzung tatsächlich angebotenen Namen.


Der Risiko-Wächter ist die Schranke zwischen einem Vorschlag und dem Broker.
Jeder Auftrag, den trader-budget, trader-ausgleich oder trader-steuer
schreibt, läuft hier durch. Ein BLOCK ist für diesen Auftrag endgültig: Er
geht weder zum Nutzer für ein Ja noch zum Broker.

`${CLAUDE_SKILL_DIR}` ist unten der Ordner dieses Skills (dort, wo diese
SKILL.md liegt).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"             # trader/-Dateien; schreibt trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write  # ansehen, ohne zu speichern
```

## Was er liest und schreibt

| Datei | Inhalt |
| --- | --- |
| `trader/orders.json` | die vorgeschlagenen Aufträge |
| `trader/config.json` | die Limits (trader-budget liefert `config.example.json`) |
| `trader/portfolio.json` | Bargeld und Positionen; fehlt sie, gilt ein frisches Konto mit dem Budget |
| `trader/prices.json` | nötig, um Aufträge nach Aktienzahl zu bewerten und Positionen zu bewerten |
| `trader/state/broker.json` | was heute abgeschickt wurde, für Doppelte und das Tageslimit |
| `trader/state/risk.json` | die Urteile, die er schreibt, mit einem Fingerabdruck jedes genauen Auftrags |

## Die Prüfungen

Die Aufträge werden in Dateireihenfolge geprüft, als wäre jeder bestandene
schon ausgeführt. So fällt ein Stapel auf, der Auftrag für Auftrag in Ordnung
ist, zusammen aber eine Regel bricht.

| Prüfung | blockt, wenn |
| --- | --- |
| Format | kein Symbol, side nicht buy oder sell, qty und notional beide oder keins, ein notional-Auftrag, der nicht Markt + Tag ist (Alpacas Regel), ein Limit-Auftrag ohne Limitpreis |
| Wertpapier | ein Optionssymbol, solange `allow_options` false ist; Krypto oder alles, was kein US-Aktien- oder ETF-Ticker ist |
| Sperrliste | der Ticker steht auf der `blocked`-Liste |
| Größe | unter `min_order_usd` oder über `max_order_usd` |
| Position | die Aktie wäre nach der Ausführung über `max_position_pct` des Kontos |
| Bargeld | der Kauf ließe weniger als den Bargeldpuffer übrig oder bräuchte Margin (`allow_margin` false) |
| Leerverkauf | ein Verkauf von mehr, als du hältst (`allow_short` false) |
| Doppelt | dasselbe Symbol mit derselben Seite zweimal in einem Stapel, oder heute schon abgeschickt |
| Tageslimit | mehr als `max_orders_per_day` Aufträge heute abgeschickt |

Zahlen müssen endlich sein: NaN, Infinity, Booleans und negative Größen sind
keine gültigen Beträge. Fehlerhafte Konfiguration, Kurse oder Depotdaten
führen zu einem Fehler statt zu einem PASS. Erweiterte Aufträge (etwa Bracket)
werden blockiert, solange nicht alle Teilaufträge geprüft werden können.
Standard-Aktienoptionen werden, falls ausdrücklich erlaubt, mit 100 Aktien je
Kontrakt bewertet; angepasste Kontrakte sind damit nicht abgedeckt.

Warnungen blocken nicht, aber sag sie dem Nutzer: ein LIVE-Konto und eine Zahl,
die als Zahl statt als String geschickt wurde.

## Schritte

1. Sorge dafür, dass `trader/prices.json` frisch ist, wenn ein Auftrag eine
   Aktienzahl ist oder eine Position einen Wert braucht (trader-alarm:
   `alerts.py quote --holdings --write`).
2. Starte `check.py`. Exit-Code 0 heißt, jeder Auftrag hat bestanden, 1 heißt,
   mindestens einer wurde geblockt.
3. Sag dem Nutzer das Ergebnis in einfachen Worten, Auftrag für Auftrag. Gib
   bei einem BLOCK den Grund und die Lösung an (ein kleinerer Betrag, eine
   andere Aktie oder eine Regel, die der Nutzer vielleicht ändern will). Ändere
   nie selbst ein Limit.
4. Ändern sich Aufträge, Reihenfolge, Regeln, Kontodaten oder Kurse, starte die
   Prüfung erneut. Das Urteil speichert die geprüften Eingaben und die
   Reihenfolge. Ein Urteil von gestern zählt heute nicht. Frühere PASS-Aufträge
   wurden im Stapel als ausgeführt gerechnet: Ziehe einen späteren Auftrag
   deshalb nicht vor. Warte auf die tatsächliche Ausführung, aktualisiere das
   Depot und prüfe erneut; ein nur abgeschickter Verkauf ist noch kein Geld.
   Nach Ablehnung oder Fehlschlag den verbleibenden Stapel ebenfalls neu
   prüfen. Fehlende oder beschädigte Eingaben nicht durch erfundene Werte
   ersetzen.

## Beispiel: das Reel

Die fünf Käufe von trader-budget (AAPL 1,250 USD, AXP 1,119 USD, GOOGL 823 USD, KO
708 USD, BAC 600 USD) auf einem frischen 5,000 USD-Paper-Konto mit der Beispiel-
Konfiguration:

```
PASS  #1 buy AAPL 1,250 USD
PASS  #2 buy AXP 1,119 USD
PASS  #3 buy GOOGL 823 USD
PASS  #4 buy KO 708 USD
PASS  #5 buy BAC 600 USD
5 bestanden, 0 geblockt. Bargeld nach den bestandenen Aufträgen: 500 USD (Paper-Konto).
```

AAPL landet genau auf der 25-%-Obergrenze (1,250 USD von 5,000 USD), und das ist
erlaubt. Ein sechster Auftrag, noch einmal 100 USD AAPL, wird auf drei Wegen
geblockt: Er würde in den Bargeldpuffer von 500 USD greifen, AAPL über die
25-%-Obergrenze heben und AAPL im selben Stapel wiederholen.

## Was er nie tut

Er schickt nie einen Auftrag ab, ändert nie einen Auftrag, damit er besteht,
lockert nie ein Limit und behandelt ein PASS nie als das Ja des Nutzers. Ein
PASS heißt nur, dass der Auftrag dem Nutzer gezeigt werden darf.

## Sicherheit

- Bildung und Werkzeug. Keine Anlageberatung. Ein PASS ist eine Regelprüfung,
  keine Empfehlung.
- Standardmäßig Paper Trading, also Spielgeld (`"paper": true`). Live zu gehen
  ist die eigene Entscheidung des Nutzers, in seiner eigenen Konfiguration.
- Nichts in diesem Paket schickt einen Auftrag ab außer trader-broker, und das
  nur, wenn alle drei Dinge stimmen: trader-risiko hat genau diesen Auftrag
  heute bestanden, der Nutzer hat im Chat für genau diesen einen Auftrag
  ausdrücklich "Ja" geschrieben (nichts ohne dein "Ja"), und der Nutzer hat auf
  Claudes eigener Karte "Allow once" geklickt, weil das Auftragswerkzeug auf
  "Needs approval" steht (Customize > Connectors > tool permissions). Setze ein
  Auftragswerkzeug nie auf "Always allow".
- Keine Optionen, kein Margin (geliehenes Geld), keine Leerverkäufe, kein
  Hebel, außer die Konfiguration erlaubt es und der Nutzer bestätigt es.
- Broker-Schlüssel liegen in Umgebungsvariablen oder in der Claude-Desktop-
  Konfiguration, nie im Chat.
