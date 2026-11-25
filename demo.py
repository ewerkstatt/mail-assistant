"""Sample data for 'python main.py --demo' (no mail server, no API)."""
from datetime import datetime, timedelta


def demo_records():
    now = datetime.now()
    def rec(i, cat, name, addr, subject, summary, reason, action="", hours=0, attachments=None):
        return {
            "key": f"demo-{i}", "kategorie": cat, "from_name": name, "from_addr": addr,
            "subject": subject, "date": (now - timedelta(hours=hours)).isoformat(),
            "zusammenfassung": summary, "grund": reason, "handlung": action,
            "text": f"Beispieltext für: {subject}", "attachments": attachments or [],
            "signals": [], "checked": True,
        }
    return [
        rec(1, "wichtig", "Anna Duschanek", "anna@example.at", "Besuch am Sonntag",
            "Ihre Tochter fragt, ob sie Sie am Sonntag um 15 Uhr besuchen kann.",
            "Persönliche Nachricht von einem Familienmitglied.", "Bitte kurz zu- oder absagen.", 1),
        rec(2, "verdacht", "A1 Kundenservice", "service@a1-rechnung-info.top", "Ihre Rechnung konnte nicht abgebucht werden",
            "Angeblich ist eine Zahlung an A1 fehlgeschlagen und Sie sollen über einen Link Ihre Kartendaten eingeben.",
            "Die Absenderadresse gehört nicht zu A1. Der Link führt zu einer fremden Seite.",
            "Nichts anklicken, nicht antworten, keine Daten eingeben.", 2),
        rec(3, "unwichtig", "Hofer Newsletter", "newsletter@hofer.at", "Angebote der Woche",
            "Werbung mit den Sonderangeboten dieser Woche.", "Newsletter ohne Handlungsbedarf.", "", 5),
        rec(4, "wichtig", "Ordination Dr. Huber", "ordination@dr-huber.at", "Terminbestätigung 3. Dezember",
            "Ihr Arzttermin am 3. Dezember um 9 Uhr 30 ist bestätigt.", "Echter Termin bei Ihrem Arzt.",
            "Termin im Kalender notieren.", 20),
        rec(5, "verdacht", "PayPal", "konto@secure-paypa1.com", "Ihr Konto wurde eingeschränkt",
            "Angebliche Kontosperre bei PayPal, Sie sollen sich über einen Link anmelden.",
            "Gefälschte Absenderadresse und Drohung mit Kontosperre.", "Löschen oder ignorieren.", 30),
        rec(6, "unwichtig", "Facebook", "notification@facebookmail.com", "Sie haben 3 neue Benachrichtigungen",
            "Automatische Benachrichtigung von Facebook.", "Keine Handlung nötig.", "", 40),
    ]
