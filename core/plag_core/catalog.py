"""Known websites and apps, with English, Hinglish and Devanagari aliases."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    name: str
    target: str
    aliases: tuple[str, ...]


SITES: dict[str, Entry] = {
    "youtube": Entry("YouTube", "https://www.youtube.com", ("youtube", "you tube", "yt", "यूट्यूब", "यू ट्यूब")),
    "google": Entry("Google", "https://www.google.com", ("google", "गूगल")),
    "gmail": Entry("Gmail", "https://mail.google.com", ("gmail", "g mail", "जीमेल", "मेल")),
    "maps": Entry("Google Maps", "https://maps.google.com", ("maps", "google maps", "map", "मैप्स", "मैप", "नक्शा")),
    "drive": Entry("Google Drive", "https://drive.google.com", ("drive", "google drive", "ड्राइव")),
    "calendar": Entry("Google Calendar", "https://calendar.google.com", ("calendar", "google calendar", "कैलेंडर")),
    "github": Entry("GitHub", "https://github.com", ("github", "git hub", "गिटहब")),
    "linkedin": Entry("LinkedIn", "https://www.linkedin.com", ("linkedin", "linked in", "लिंक्डइन")),
    "instagram": Entry("Instagram", "https://www.instagram.com", ("instagram", "insta", "इंस्टाग्राम", "इंस्टा")),
    "wikipedia": Entry("Wikipedia", "https://www.wikipedia.org", ("wikipedia", "wiki", "विकिपीडिया")),
    "netflix": Entry("Netflix", "https://www.netflix.com", ("netflix", "नेटफ्लिक्स")),
    "amazon": Entry("Amazon", "https://www.amazon.in", ("amazon", "अमेज़न", "अमेजन")),
}

APPS: dict[str, Entry] = {
    "whatsapp": Entry("WhatsApp", "whatsapp:", ("whatsapp", "whats app", "व्हाट्सएप", "वॉट्सऐप", "व्हाट्सऐप")),
    "spotify": Entry("Spotify", "spotify:", ("spotify", "स्पॉटिफाई")),
    "chrome": Entry("Chrome", "chrome", ("chrome", "google chrome", "क्रोम")),
    "edge": Entry("Edge", "microsoft-edge:", ("edge", "microsoft edge", "एज")),
    "notepad": Entry("Notepad", "notepad.exe", ("notepad", "नोटपैड")),
    "calculator": Entry("Calculator", "calculator:", ("calculator", "calc", "कैलकुलेटर")),
    "paint": Entry("Paint", "mspaint.exe", ("paint", "ms paint", "पेंट")),
    "explorer": Entry("File Explorer", "explorer.exe", ("file explorer", "explorer", "files", "my files", "फाइल", "फ़ाइल")),
    "settings": Entry("Settings", "ms-settings:", ("settings", "windows settings", "सेटिंग्स", "सेटिंग")),
}


def lookup(alias: str) -> tuple[str, str, Entry] | None:
    """Return (kind, key, entry) for an alias; kind is 'app' or 'site'."""
    a = alias.strip().lower()
    for key, e in APPS.items():
        if a in e.aliases:
            return "app", key, e
    for key, e in SITES.items():
        if a in e.aliases:
            return "site", key, e
    return None
