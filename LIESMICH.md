# Mail-Assistant

Liest das Postfach per IMAP **nur lesend** (EXAMINE + BODY.PEEK) und teilt die Mails in
**Wichtig / Nicht wichtig / Vorsicht** ein. Nichts wird gelöscht, verschoben, als gelesen
markiert oder beantwortet. Thunderbird merkt davon nichts.

## Build (auf einem Windows-PC)

1. Python 3.10+ von python.org installieren (Häkchen „Add to PATH“).
2. `build.bat` doppelklicken → ergibt `dist\Mail-Assistant.exe`.
3. Die exe auf den PC des Vaters kopieren, Verknüpfung auf den Desktop (ggf. in Autostart).

Test ohne Server und API: `python main.py --demo`

## Ersteinrichtung

Beim ersten Start öffnen sich die Einstellungen (später: Zahnrad oder Strg+E):

| Feld | Wert |
|---|---|
| Mailserver | `securemail.a1.net` (Port 993, SSL), wie in Thunderbird |
| Benutzer | E-Mail-Adresse |
| Passwort | Mail-Passwort |
| Claude API-Key | von console.anthropic.com |
| Zeitraum | Standard 7 Tage, höchstens 80 Mails |
| Bekannte Absender | z. B. `anna@gmx.at, @ordination-huber.at` |

Passwort und API-Key liegen in der Windows-Anmeldeinformationsverwaltung, nicht im Klartext.
Einstellungen und Cache: `%APPDATA%\MailAssistant\`.

## Bedienung

| Taste | Funktion |
|---|---|
| 1 / 2 / 3 | Wichtig / Nicht wichtig / Vorsicht |
| ↑ ↓ | Nächste / vorherige Mail (wird automatisch vorgelesen) |
| Leertaste / Enter | Vorlesen |
| Esc | Vorlesen stoppen |
| T | Mailtext statt Zusammenfassung |
| Strg + / Strg – | Schrift größer / kleiner |
| F5 | Neu laden (sonst automatisch alle 10 Minuten) |

## Wie die Einstufung funktioniert

1. **Lokale Regeln**: SPF/DKIM/DMARC-Ergebnis, Markenname im Absendernamen mit fremder Domain
   (PayPal, A1, Post, Banken, FinanzOnline …), abweichendes Reply-To, Links auf IP-Adressen oder mit
   gefälschtem Anzeigetext, gefährliche Anhänge, typische Betrugsformulierungen.
2. **Claude** bewertet Absender, Betreff, Links und die ersten 4000 Zeichen Text und schreibt eine
   kurze Zusammenfassung mit Empfehlung.
3. Starke Regel-Treffer erzwingen immer „Vorsicht“, auch bei bekannten Absendern (Spoofing).

Jede Mail wird nur einmal an die API geschickt (Cache nach Message-ID). Ohne API-Key oder bei
API-Fehlern gibt es eine einfache Regel-Einstufung; diese Mails werden beim nächsten Laden erneut geprüft.

**Datenschutz:** Mailinhalte gehen zur Bewertung an die Anthropic-API. Das sollte dein Vater wissen.

## Updates über GitHub

1. Öffentliches Repo anlegen (der Code enthält keine Zugangsdaten), in `update.py` `GITHUB_REPO = "user/repo"` setzen, pushen.
2. Neue Version ausliefern: `git tag v1.0.1 && git push --tags`.
   GitHub Actions baut die exe unter Windows und veröffentlicht sie samt SHA256-Prüfsumme als Release.
3. Die installierte exe prüft beim Start, ob es ein neueres Release gibt, lädt es, prüft die Prüfsumme,
   tauscht sich aus und startet neu (abschaltbar: Einstellung „Updates automatisch installieren“ = 0).

Die exe muss in einem Ordner liegen, in dem der Benutzer schreiben darf, z. B.
`%LOCALAPPDATA%\Programs\Mail-Assistant\`, nicht unter `C:\Program Files`.
Die allererste Version (v1.0.0) einmal aus den Releases herunterladen und hinkopieren; lokale Builds
(`build.bat`) haben die Version `0.0.0-dev` und aktualisieren sich nicht selbst.

## Anpassen

- `analyse.py` → `BRANDS` (Markennamen und echte Domains), `SYSTEM_PROMPT` (Kategorie-Definitionen)
- `config.py` → `DEFAULTS` (z. B. Modell, Schriftgröße, Zeitraum)
