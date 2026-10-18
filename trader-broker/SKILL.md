---
name: trader-broker
description: >-
  Der Auftrags-Ausführer. Platziert FREIGEGEBENE Aufträge über deinen
  Broker-Connector, einen nach dem anderen, und nie ohne dein ausdrückliches
  Ja. Zeigt jeden Auftrag als verständliches Ticket plus das genaue JSON für
  Alpacas place_stock_order, erfasst dein Ja Wort für Wort, und danach fragt
  Claudes eigene Karte Allow once / Deny noch einmal. Deckt die Einrichtung
  von Alpacas offiziellem MCP-Server in Claude Desktop ab (standardmäßig Paper
  Trading, also Spielgeld; Schlüssel in Umgebungsvariablen, nie im Chat), den
  Interactive-Brokers-Connector (Aufträge gehen zur Freigabe an dein
  IBKR-Konto) und das Setzen der Auftragswerkzeuge auf "Needs approval". Keine
  Anlageberatung. Nutze ihn, wenn der Nutzer sagt: "platziere die Aufträge",
  "kauf das", "ausführen", "schick es an meinen Broker", "verbinde Alpaca",
  "verbinde Interactive Brokers", "richte Paper Trading ein", "place the
  orders", "buy it", "execute", "send it to my broker", "connect Alpaca",
  "connect Interactive Brokers", "set up paper trading", oder nachdem
  /trader-skill-deutsch:trader-risiko bestanden ist.
argument-hint: "[Auftragsnummer]"
---

# trader-broker

**Befehle:** Die vollständigen Aufrufe unten gelten für die Plugininstallation. Bei einzeln nach `.claude/skills/` kopierten Ordnern entfällt der Präfix `trader-skill-deutsch:`. Verwende beim Wechsel zu einem anderen Skill den in deiner Sitzung tatsächlich angebotenen Namen.


Keine Anlageberatung. Der Auftrags-Ausführer ist der einzige Skill in diesem
Paket, der zu einem echten Auftrag führen kann, deshalb ist er der
strengste. Ein Auftrag erreicht den Markt erst nach drei getrennten Toren,
jedes Mal, für jeden Auftrag:

1. **trader-risiko hat genau diesen Auftrag heute bestanden** (zugeordnet über
   Fingerabdruck und Ticketnummer). Reihenfolge, Konfiguration, Kontodaten und
   Kurse müssen noch dem Prüfstand entsprechen.
2. **Der Nutzer hat im Chat für genau diesen einen Auftrag ein ausdrückliches
   Ja getippt.**
3. **Der Nutzer hat auf Claudes eigener Berechtigungskarte "Allow once"
   geklickt.** Die Karte erscheint, weil das Auftragswerkzeug auf "Needs
   approval" steht.

Zwei Ja vom Nutzer, ein PASS von den Regeln. Ohne dein Ja gibt es keinen
Auftrag.

**Wichtig zum Ja:** `ticket.py` zählt eine Antwort nur, wenn sie mit **"ja"**
oder **"yes"** (oder "y") beginnt und keine Bedingung enthält. "ja, aber nur
1000", "ja zu allen", "ja, später" oder "ok" zählen nicht. Sag dem Nutzer
vorher, dass er für genau diesen einen Auftrag "ja" tippen muss. Übersetze eine
Antwort nie selbst in ein Ja und gib sie Wort für Wort an `--reply` weiter.
Ein unbekannter Ticker, die falsche Kauf-/Verkaufsseite oder ein zusätzlicher
abweichender Betrag zählt nicht als Freigabe. Bei unklaren Zusätzen das genaue
Ticket erneut zeigen und nur um ein schlichtes "ja" bitten.

`${CLAUDE_SKILL_DIR}` unten ist der Ordner dieses Skills (dort liegt diese
SKILL.md).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # jedes Ticket, BEREIT (READY) oder nicht
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "ja"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol AAPL --side buy --qty 3.6628 --price 341.27
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` spricht nie mit einem Broker. Es zeigt die Tickets, prüft, ob eine
Antwort ein echtes Ja für genau diesen Auftrag ist, und führt das Protokoll in
`trader/state/broker.json`.

## Einrichtung A: Alpaca (offizieller MCP-Server, Claude Desktop)

Alpacas eigener Server liegt unter github.com/alpacahq/alpaca-mcp-server (MIT).
Er läuft auf dem Computer des Nutzers in Claude Desktop. Er steht nicht im
Connector-Verzeichnis von Claude. Er handelt auf einem **Paper**-Konto (Paper
Trading, also Spielgeld), solange man ihm nichts anderes sagt.

1. Der Nutzer legt ein Alpaca-Konto an und erstellt im Alpaca-Dashboard
   API-Schlüssel für **Paper Trading**. Paper-Schlüssel sind von den
   Live-Schlüsseln getrennt.
2. Der Nutzer installiert `uv` (das liefert `uvx`), siehe docs.astral.sh/uv.
3. In Claude Desktop: Settings > Developer > Edit Config. Das öffnet
   `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`,
   Windows: `%APPDATA%\Claude\`). Der Nutzer fügt diesen Block ein (er steht
   auch in `claude_desktop_config.example.json` hier) und tippt seine eigenen
   Schlüssel in die Datei:

```json
{
  "mcpServers": {
    "alpaca": {
      "command": "uvx",
      "args": ["alpaca-mcp-server"],
      "env": {
        "ALPACA_API_KEY": "paste your paper key here, in this file only",
        "ALPACA_SECRET_KEY": "paste your paper secret here, in this file only",
        "ALPACA_PAPER_TRADE": "true",
        "ALPACA_TOOLSETS": "account,trading,stock-data"
      }
    }
  }
}
```

   (Die Platzhalter in den Anführungszeichen heißen: "füge hier deinen
   Paper-Schlüssel ein, nur in dieser Datei" und "füge hier dein Paper-Secret
   ein, nur in dieser Datei".)

   Der `env`-Block gibt die Schlüssel als Umgebungsvariablen an den Server.
   **Schlüssel gehören nie in den Chat.** Frag nie danach, lies diese Datei nie
   zurück ins Gespräch, und wenn der Nutzer doch einen Schlüssel einfügt, sag
   ihm, er soll die Nachricht löschen und im Alpaca-Dashboard neue Schlüssel
   erstellen. `ALPACA_PAPER_TRADE` ist standardmäßig schon true; es hinzuschreiben
   macht es sichtbar. `ALPACA_TOOLSETS` ist optional und blendet die Werkzeuge
   aus, die der Nutzer nicht braucht.
4. Claude Desktop neu starten.
5. **Werkzeugrechte setzen.** Customize > Connectors (in älteren Versionen
   Settings > Connectors), alpaca öffnen und für jedes Werkzeug Always allow,
   Needs approval oder Blocked wählen:
   - `place_stock_order`: **Needs approval**. Nie Always allow.
   - jedes andere Werkzeug, das etwas platziert, ändert, storniert oder
     schließt (`cancel_order_by_id`, `close_position`,
     `update_account_config`): Needs approval.
   - `place_option_order`, `place_crypto_order`, `close_all_positions`,
     `cancel_all_orders`, `exercise_options_position`: **Blocked**, außer der
     Nutzer hat einen Grund und sagt es.
   - Werkzeuge, die nur lesen (`get_account_info`, `get_all_positions`,
     `get_stock_snapshot`, `get_stock_latest_quote`): Always allow ist in
     Ordnung.
6. Prüfen: Bitte Claude um die Alpaca-Kontoinfo (nur lesen) und bestätige im
   Alpaca-Dashboard, dass es das Paper-Konto ist.

In Claude Code statt Claude Desktop bleibt das Auftragswerkzeug in
`.claude/settings.json` auf "ask": `"permissions": {"ask":
["mcp__alpaca__place_stock_order"]}`. Ein Auftragswerkzeug gehört nie in
`allow`.

## Einrichtung B: Interactive Brokers (Connector im Verzeichnis von Claude)

Interactive Brokers veröffentlicht einen geprüften Connector:
claude.ai/directory/interactive-brokers (Connector-URL
`https://api.ibkr.com/v1/api/mcp`). Füge ihn über Customize > Connectors >
Browse connectors hinzu und melde dich dort bei IBKR an, nicht im Chat. Laut
seiner Beschreibung kann Claude Handelsanweisungen entwerfen, die zur Prüfung
und Freigabe an das IBKR-Konto des Nutzers gehen, und Claude schickt nie
Aufträge direkt an den Markt. Bei IBKR fällt das letzte Ja also auch in IBKR.
Setze `"broker": "ibkr"` in `trader/config.json` und rechne mit
`--whole-shares`, wenn IBKR ganze Stückzahlen will. Stell auch seine
Werkzeuge, die Aufträge entwerfen, auf Needs approval.

## Schritte, für jeden Auftrag

1. `ticket.py show`. Nur BEREITE (READY) Tickets gehen an den Nutzer. Ein
   Ticket, das NICHT BEREIT oder GESPERRT ist, geht zurück an
   `/trader-skill-deutsch:trader-risiko`. Es darf nur eine offene Freigabe geben. Frühere PASS-
   Aufträge im Stapel zuerst tatsächlich ausführen lassen, das Depot
   aktualisieren und erneut prüfen. Ein lediglich platzierter Verkauf darf
   keinen vorgezogenen Kauf finanzieren.
2. Zeig EIN Ticket und frag: "Soll dieser Auftrag platziert werden? Antworte
   ja oder nein." Eine Frage pro Auftrag. Biete nie "ja für alle" an.
3. Starte `ticket.py approve --index N --reply "<die Antwort des Nutzers,
   Wort für Wort>"`. Gibt es "Nicht erfasst" aus, war die Antwort kein
   ausdrückliches Ja für diesen Auftrag (zum Beispiel "sure", "ja, aber",
   "yes to all" oder ein Ja, das eine andere Aktie oder einen anderen Betrag
   nennt). Frag noch einmal. Mach aus einer Antwort nie selbst ein Ja.
4. Erst nach "Dein Ja ... ist erfasst" rufst du `place_stock_order` mit genau
   dem JSON auf, das das Ticket ausgegeben hat. Claudes Karte erscheint: Allow
   once oder Deny.
5. Halte fest, was passiert ist: `ticket.py result --index N --status placed
   --broker-id <ID aus der Antwort>`, oder `--status denied`, wenn er Deny
   gedrückt hat, oder `--status failed` mit `--note` bei einem Fehler. Ein
   fehlgeschlagener Auftrag wird nicht ohne neue Prüfung und neues Ja erneut
   versucht.
6. Sobald die Ausführung bekannt ist (die Antwort des Werkzeugs oder später
   `get_all_positions`), trägt `ticket.py fill` sie als Steuer-Lot für
   trader-steuer in `trader/portfolio.json` ein. Nur tatsächliche positive,
   endliche Aktienzahl und Ausführungspreis erfassen; keine Beispielwerte.
   Alternativ kann `result --status placed --broker-id ID` zusammen mit
   `--filled-qty Q --filled-price P` die bestätigte Ausführung erfassen. Beide
   Ausführungsfelder gehören zusammen. Bei einem mit `--portfolio` geprüften
   eigenen Dateipfad muss `result --portfolio` genau dieselbe Depotdatei
   angeben; ein Ja darf keine andere Kontodatei verändern. Ändern sich der
   Broker-Ausführungsstand oder das Tageslimit, ist vor dem nächsten Ja erneut
   zu prüfen. Danach das Depot und die übrigen
   Vorschläge erneut mit trader-risiko prüfen, dann das nächste Ticket. Bei
   failed oder denied vor einem neuen Versuch ebenfalls neu prüfen und ein
   neues Ja einholen. Alte Freigaben nicht wiederverwenden.

## Beispiel: das Reel

So sieht das Ticket aus (die Ausgabe von `ticket.py show`, auf Deutsch):

```
AUFTRAG 1 von 5, PAPER-Konto (Spielgeld), Alpaca
  KAUF 1,250 USD von AAPL, Market-Order, gültig für heute
  BEREIT: trader-risiko PASS, wartet auf dein Ja
  Claude ruft place_stock_order genau so auf:
    {"symbol": "AAPL", "side": "buy", "notional": "1250",
     "type": "market", "time_in_force": "day"}
```

Der Nutzer antwortet "ja". Claude erfasst es, ruft `place_stock_order` auf,
und die Karte lautet "Claude wants to use Place stock order" mit diesem JSON
und zwei Knöpfen: Allow once, Deny. Allow once schickt ihn an das
Paper-Konto.

## Was er nie tut

- Platziert nie einen Auftrag ohne alle drei Tore und nie zwei Aufträge mit
  einem Ja.
- Platziert nie Aufträge aus einer geplanten Aufgabe oder im Hintergrund:
  Niemand ist da, der Ja sagt.
- Fragt nie nach API-Schlüsseln oder Passwörtern, liest, speichert oder
  wiederholt sie nie.
- Stellt `ALPACA_PAPER_TRADE` oder `"paper"` nie auf live. Das macht der Nutzer
  selbst.
- Ruft `close_all_positions`, `cancel_all_orders` und Options- oder
  Krypto-Auftragswerkzeuge nie von sich aus auf.

## Sicherheit

- Bildung und Werkzeug, **Keine Anlageberatung**. Kein Auftrag hier ist eine
  Empfehlung, und nichts verspricht Rendite.
- Standardmäßig Paper Trading, also Spielgeld (`"paper": true`,
  `ALPACA_PAPER_TRADE` true). Der Wechsel auf echtes Geld ist die eigene
  Entscheidung des Nutzers.
- Nichts in diesem Paket platziert einen Auftrag außer trader-broker, und nur,
  wenn alle drei Bedingungen zutreffen: trader-risiko hat genau diesen Auftrag
  heute bestanden, der Nutzer hat im Chat für genau diesen einen Auftrag ein
  ausdrückliches Ja ("ja" oder "yes") getippt, und der Nutzer hat auf Claudes eigener
  Karte "Allow once" geklickt, weil das Auftragswerkzeug auf "Needs approval"
  steht (Customize > Connectors > tool permissions). Stell ein Auftragswerkzeug
  nie auf "Always allow". Ohne dein Ja passiert nichts.
- Keine Optionen, kein Hebel (Margin), kein Leerverkauf (Short Selling), es sei
  denn, die Config erlaubt es und der Nutzer bestätigt es.
- Broker-Schlüssel liegen in Umgebungsvariablen oder in der Claude-Desktop-
  Config, nie im Chat.
