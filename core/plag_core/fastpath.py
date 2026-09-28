"""Deterministic fast path for typed commands in English, Hinglish and Hindi (no model call).

Anything it doesn't recognise returns None and goes to Gemini.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .catalog import lookup

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
HINGLISH = {
    "kholo", "karo", "kardo", "kyun", "kyon", "hai", "hain", "kya", "mera", "meri", "mujhe", "chalao", "bajao",
    "lagao", "dhundo", "dhoondo", "batao", "dikhao", "abhi", "kitne", "baje", "raha", "rahi", "khol", "pe",
    "mausam", "kaisa", "kaisi", "rahega", "mein", "bhejo", "banao",
}


@dataclass
class Intent:
    action: str                      # open_url | open_app | web_search | system_status | time | none
    args: dict = field(default_factory=dict)
    lang: str = "en"                 # how the user spoke: en | hi | mixed
    reply: str = ""                  # model-written reply, if any
    label: str = ""                  # display name of the target ("YouTube")
    mood: str = "calm"               # how the reply should sound


def detect_lang(text: str) -> str:
    if DEVANAGARI.search(text):
        return "hi"
    words = set(re.findall(r"[a-z']+", text.lower()))
    return "mixed" if words & HINGLISH else "en"


_WAKE = re.compile(r"^(?:hey\s+|ok\s+|हे\s+)?(?:plag|flag|प्लैग|प्लाग|फ्लैग)[\s,।!.]*", re.I)
_TAIL = re.compile(r"\s+(?:app|website|site|ko|को|ऐप|वेबसाइट)$")

_OPEN_EN = re.compile(r"^(?:please\s+)?(?:open|launch|start|go to|take me to)\s+(?P<t>.+?)(?:\s+please)?$")
_OPEN_HI = re.compile(
    r"^(?P<t>.+?)\s+(?:kholo|khol do|khol de|khol|open karo|open kar do|open kardo|chalu karo|start karo|launch karo"
    r"|खोलो|खोल दो|खोल दे|खोलिए|चालू करो|ओपन करो|ओपन कर दो)$")
_SEARCH_SITE_EN = re.compile(r"^search\s+(?P<s>youtube|google)\s+for\s+(?P<q>.+)$")
_SEARCH_EN = re.compile(r"^(?:search|google|look up)\s+(?:for\s+)?(?P<q>.+?)(?:\s+on\s+(?P<s>youtube|google))?$")
_PLAY_EN = re.compile(r"^play\s+(?P<q>.+?)(?:\s+on\s+youtube)?$")
# "open Gender Religion Caste class 10th lecture on YouTube" means watch it; "find X on YouTube" means show results
_ON_YOUTUBE = re.compile(r"^(?P<v>open|watch|show me|start|find|search(?:\s+for)?|look\s+for)\s+(?P<q>.+?)\s+(?:on|in)\s+youtube$")
_SEARCH_HI_SITE = re.compile(
    r"^(?P<s>youtube|google|यूट्यूब|गूगल)\s+(?:par|pe|पर|पे)\s+(?P<q>.+?)\s+"
    r"(?:search karo|search kar do|search kardo|dhundo|dhoondo|chalao|chala do|chala de|lagao|laga do|bajao|baja do"
    r"|play karo|play kar do|sunao|suna do|dikhao|dikha do|सर्च करो|खोजो|ढूंढो|चलाओ|चला दो|लगाओ|बजाओ|दिखाओ)$")
_SEARCH_HI = re.compile(r"^(?P<q>.+?)\s+(?:search karo|search kar do|search kardo|dhundo|dhoondo|google karo"
                        r"|सर्च करो|खोजो|ढूंढो|गूगल करो)$")
_PLAY_HI = re.compile(r"^(?P<q>.+?)\s+(?:chalao|chala do|chala de|bajao|baja do|lagao|laga do|play karo|play kar do|sunao"
                      r"|suna do|चलाओ|चला दो|बजाओ|लगाओ|सुनाओ)$")
_PLAY_VERB = re.compile(r"(chalao|chala do|chala de|bajao|baja do|lagao|laga do|play karo|play kar do|sunao|suna do|चलाओ|चला दो"
                        r"|बजाओ|लगाओ|सुनाओ)$")
_CHROME = [
    re.compile(r"^(?:search|google)\s+(?:for\s+)?(?P<q>.+?)\s+(?:in|on)\s+(?:google\s+)?chrome$"),
    re.compile(r"^(?:google\s+)?chrome\s+(?:mein|me|pe|par|में|पर)\s+(?P<q>.+?)\s+(?:search karo|search kar do|dhundo|khojo|सर्च करो|खोजो)$"),
]
_TIME = re.compile(r"(what(?:'s| is) the time|what time is it|\btime now\b|kitne baje|कितने बजे|टाइम क्या|समय क्या)")
_SYSTEM = re.compile(
    r"(\blag|\bslow|\bhang|performance|\bcpu\b|\bram\b|memory usage|battery|what'?s running|what is running"
    r"|system status|kya chal raha|धीमा|स्लो|हैंग|लैग|बैटरी|परफॉर्मेंस)")
_DOMAIN = re.compile(r"(?:https?://)?(?P<d>[a-z0-9-]+(?:\.[a-z0-9-]+)+)(?P<p>/\S*)?")

_CAMERA_LOOK = re.compile(
    r"(what is this|what's this|whats this|what am i holding|what do you see|identify this|what is in my hand"
    r"|ye kya hai|yeh kya hai|yah kya hai|ise pehchano|यह क्या है|ये क्या है|इसे पहचानो|क्या है ये)")
# "look at my screen", "my screen dekho", "screen pe kya likha hai", "what does this error say", "read my screen",
# "translate my screen", "kya karna hai isme": one screenshot, read by the AI (plain = no specific question)
_SCREEN = r"(?:my\s+|the\s+|this\s+|meri\s+|mera\s+)?(?:screen|display|monitor|स्क्रीन)"
_SCREEN_LOOK = re.compile(
    rf"^(?:(?:jarvis|plag)[,\s]+)?(?P<plain>(?:please\s+)?(?:look at|check|see|read|scan|analy[sz]e|dekho|dekh lo|check karo|padho)\s+{_SCREEN}"
    rf"|{_SCREEN}\s+(?:dekho|dekh lo|check karo|padho|dekhna|dekh)|what'?s on {_SCREEN}|what is on {_SCREEN}"
    rf"|what do you see on {_SCREEN}|{_SCREEN}\s+pe\s+kya\s+(?:hai|chal raha hai))"
    rf"|(?:.*\b(?:on|in)\s+{_SCREEN}.*|.*\b{_SCREEN}\s+(?:pe|par|mein|me)\b.*"
    r"|what does (?:this|the|that) (?:error|message|popup|pop-up|warning|dialog) (?:say|mean)\??"
    r"|translate (?:this|the|my) (?:screen|page|text)|any work to do\??|kya karna hai (?:isme|ismein|yahan)\??)$", re.I)
_CAMERA_ON = re.compile(r"^((open|start|turn on|switch on)\s+(the\s+)?camera|camera\s+(on|kholo|chalu karo|on karo|start karo)"
                        r"|कैमरा\s+(खोलो|चालू करो|ऑन करो))$")
_CAMERA_OFF = re.compile(r"^((close|stop|turn off|switch off)\s+(the\s+)?camera|camera\s+(off|band karo|band|off karo)"
                         r"|कैमरा\s+(बंद करो|ऑफ करो))$")
_UI = [
    ("stop", re.compile(r"^(stop|stop it|ruko|ruk jao|bas|bas karo|cancel|रुको|रुक जाओ|बस)$")),
    ("mute", re.compile(r"^(mute|be quiet|stop talking|chup|chup raho|awaaz band karo|आवाज़ बंद करो|चुप रहो)$")),
    ("unmute", re.compile(r"^(unmute|voice on|talk to me|awaaz chalu karo|आवाज़ चालू करो)$")),
    ("lang_hi", re.compile(r"(speak (in )?hindi|talk in hindi|hindi (mein|me|main) (bolo|baat karo|jawab do)|हिंदी में (बोलो|बात करो|जवाब दो))")),
    ("lang_en", re.compile(r"(speak (in )?english|talk in english|english (mein|me|main) (bolo|baat karo|jawab do)|इंग्लिश में (बोलो|बात करो))")),
]
# WhatsApp: "send hi to Rahul from WhatsApp", "send a message to Rahul saying I'm late", "message mom on WhatsApp that
# I'm coming", "Rahul ko message bhejo ki kal milte hain". Anything freer ("wish Priya happy birthday") goes to the AI.
_WA = r"(?:whatsapp|whats app|व्हाट्सएप|वॉट्सऐप)"
_VIA = rf"(?:on|from|via|through|in|over|pe|par)\s+{_WA}"
_SAYING = r"(?:saying|that says|that|ki|:)"
_WHATSAPP = [
    re.compile(rf"^send\s+(?:a\s+)?(?:whatsapp\s+)?(?:message|msg|text)\s+to\s+(?P<c>.+?)(?:\s+{_VIA})?\s+{_SAYING}\s*"
               rf"(?P<m>.+?)(?:\s+{_VIA})?$", re.I),
    re.compile(rf"^send\s+(?P<m>.+?)\s+to\s+(?P<c>.+?)\s+{_VIA}$", re.I),
    re.compile(rf"^(?:send|message|text|whatsapp)\s+(?P<c>.+?)\s+(?:(?:a\s+)?(?:whatsapp|message|msg|text)(?:\s+message)?|{_VIA})"
               rf"\s+{_SAYING}\s*(?P<m>.+)$", re.I),
    re.compile(rf"^whatsapp\s+(?P<c>.+?)\s+{_SAYING}\s*(?P<m>.+)$", re.I),
    re.compile(rf"^tell\s+(?P<c>.+?)\s+{_VIA}\s+(?:that\s+)?(?P<m>.+)$", re.I),
    re.compile(r"^(?P<c>.+?)\s+ko\s+(?:whatsapp\s+(?:pe|par)\s+)?(?:message|msg|whatsapp)\s+(?:bhejo|bhej do|karo|kar do|kardo)"
               r"\s+(?:ki\s+)?(?P<m>.+)$", re.I),
    # "Aman ko bolo whatsapp pe ki kal cricket khelenge" / "Aman ko whatsapp pe bata do ki ..."
    re.compile(r"^(?P<c>.+?)\s+ko\s+(?:whatsapp\s+(?:pe|par)\s+(?:bolo|bol do|batao|bata do|keh do|kaho)"
               r"|(?:bolo|bol do|batao|bata do|keh do|kaho)\s+whatsapp\s+(?:pe|par))\s+(?:ki\s+)?(?P<m>.+)$", re.I),
    re.compile(r"^(?P<c>.+?)\s+को\s+(?:व्हाट्सएप\s+(?:पर|पे)\s+)?(?:मैसेज|संदेश|व्हाट्सएप)\s+(?:भेजो|भेज दो|करो|कर दो)"
               r"\s+(?:कि\s+)?(?P<m>.+)$"),
]
# "send msg to Rahul from WhatsApp" says who but not what: PLAG asks what to send, and hears the answer without "PLAG"
_NO_TEXT = re.compile(r"^(?:a\s+)?(?:whatsapp\s+)?(?:message|msg|text|whatsapp)$", re.I)
_GREETING_TAIL = re.compile(r"[\s,]+(?P<g>hi+|hello|hey|hii+|namaste|good morning|good night|good evening|gm|gn|hi bro|hello bro)$", re.I)
_WA_ASK = [
    re.compile(rf"^send\s+(?:a\s+)?(?:whatsapp\s+)?(?:message|msg|text)\s+to\s+(?P<c>.+?)(?:\s+{_VIA})?$", re.I),
    re.compile(rf"^(?:message|text)\s+(?P<c>.+?)\s+{_VIA}$", re.I),
    re.compile(r"^(?P<c>.+?)\s+ko\s+(?:whatsapp\s+(?:pe|par)\s+)?(?:message|msg)\s+(?:bhejo|bhej do|karo|kar do|kardo)$", re.I),
    re.compile(r"^(?P<c>.+?)\s+को\s+(?:व्हाट्सएप\s+(?:पर|पे)\s+)?(?:मैसेज|संदेश)\s+(?:भेजो|भेज दो|करो|कर दो)$"),
]
_WHATSAPP.append(re.compile(rf"^(?:message|text)\s+(?P<c>.+?)\s+{_SAYING}\s*(?P<m>.+)$", re.I))  # "message Priyans saying hi"
# WhatsApp is PLAG's only way to message, so it needn't be named (2026-09-24: "send hi message to omi bro shotu
# bangalore" wasn't understood, and the AI claimed to send it without doing anything)
_WHATSAPP.append(re.compile(rf"^send\s+(?:a\s+)?(?P<m>.+?)\s+(?:message|msg|text)\s+to\s+(?P<c>.+?)(?:\s+{_VIA})?$", re.I))
_WHATSAPP.append(re.compile(rf"^send\s+(?P<m>hi+|hello|hey|hii+|good morning|good night|good evening|namaste|gm|gn|bye)\s+to\s+"
                            rf"(?P<c>.+?)(?:\s+{_VIA})?$", re.I))
# Hinglish without naming WhatsApp: "omi ko bol do main 10 min mein aa raha hoon", "omi ko hi bhej do"; English "tell omi
# that I'm coming". Not people PLAG can't message (me, him...) and not files ("Rahul ko photo bhej do" goes to the AI).
_NOT_PERSON = r"(?!(?:me|mujhe|us|hume|him|her|them|usko|use|isko|everyone|sabko)\s)"
_WHATSAPP.append(re.compile(rf"^{_NOT_PERSON}(?P<c>.+?)\s+ko\s+(?:bolo|bol do|bol de|batao|bata do|bata de|keh do|keh de|kaho)"
                            r"\s+(?:ki\s+)?(?P<m>.+)$", re.I))
_WHATSAPP.append(re.compile(rf"^{_NOT_PERSON}(?P<c>.+?)\s+ko\s+(?P<m>(?!.*\b(?:photo|pic|image|file|pdf|video|document|doc|location)\b).+?)"
                            r"\s+(?:bhejo|bhej do|bhej de)$", re.I))
_WHATSAPP.append(re.compile(rf"^tell\s+{_NOT_PERSON}(?P<c>.+?)\s+that\s+(?P<m>.+)$", re.I))
# Open or find a chat without sending: "find Priyans on WhatsApp", "open the chat with Priyans", "Priyans ki chat kholo"
_WA_OPEN = [
    re.compile(rf"^(?:open|find|search(?:\s+for)?|show|go to)\s+(?:the\s+|my\s+)?(?:chat\s+(?:with|of)\s+)?(?P<c>.+?)"
               rf"(?:'s\s+chat)?\s+(?:on|in)\s+{_WA}$", re.I),
    re.compile(rf"^(?:open|show)\s+(?:the\s+|my\s+)?(?:{_WA}\s+)?chat\s+(?:with|of)\s+(?P<c>.+)$", re.I),
    re.compile(r"^(?P<c>.+?)\s+(?:ki|ka|की|का)\s+(?:chat|whatsapp)\s+(?:kholo|khol do|dikhao|खोलो|दिखाओ)$", re.I),
]
# Gmail search is not Google search: "search Gmail for BhoomiX", "find BhoomiX in my inbox", "emails from Rahul"
_GMAIL_SEARCH = [
    re.compile(r"^(?:search|find|check|look\s+(?:in|through))\s+(?:in\s+|through\s+)?(?:my\s+)?(?:gmail|e-?mails?|mails?|inbox)"
               r"\s+(?:for|about|from)\s+(?P<q>.+)$", re.I),
    re.compile(r"^(?:search|find|look)\s+(?:for\s+)?(?P<q>.+?)\s+in\s+(?:my\s+)?(?:gmail|e-?mails?|mails?|inbox)$", re.I),
    re.compile(r"^(?:any\s+|show\s+(?:me\s+)?)?(?:e-?mails?|mails?)\s+(?:from|about)\s+(?P<q>.+?)\??$", re.I),
    re.compile(r"^(?:gmail|email|mail)\s+(?:mein|me|pe|par)\s+(?P<q>.+?)\s+(?:search karo|dhundo|khojo)$", re.I),
]
# Research: news from several sources, summarised, saved as a report. The rest of the sentence ("compare…,
# summarise…, save…") describes what the research step already does.
_RESEARCH = re.compile(r"^(?:please\s+)?(?:research|do (?:some\s+)?research (?:on|about)|find out about|look into|investigate)"
                       r"\s+(?P<t>.+)$", re.I)
_RESEARCH_TAIL = re.compile(r"(?:,|\s+and|\s+then)\s+(?:compare|summari[sz]e|save|make|write|create|give)\b.*$", re.I)
# News is told, not opened: "tell me the news", "latest news about ISRO", "aaj ki khabar", "cricket ki news batao"
_NEWS = [
    re.compile(r"^(?:please\s+)?(?:tell me|give me|read me|what(?:'s| is| are)|any|show me|get me)?\s*(?:the\s+)?"
               r"(?:latest|today'?s|top|current|recent|breaking)?\s*(?:news|headlines)"
               r"(?:\s+(?:about|on|for|in|of|regarding)\s+(?P<t>.+?))?(?:\s+today)?\??$", re.I),
    re.compile(r"^(?:aaj\s+ki\s+|aaj\s+ke\s+)?(?:news|khabar|khabre|khabrein|samachar|headlines)(?:\s+(?:batao|sunao|dikhao|do))?$", re.I),
    re.compile(r"^(?P<t>.+?)\s+(?:ki|ke|से\s+जुड़ी|की)\s+(?:news|khabar|khabre|खबर|खबरें)\s*(?:batao|sunao|बताओ|सुनाओ)?$", re.I),
    re.compile(r"^(?:make|create|prepare|give me|write)\s+(?:me\s+)?(?:a\s+)?(?:news\s+)?report\s+(?:on|about)\s+(?P<t>(?:the\s+)?"
               r"(?:latest|today'?s|recent|current|this week'?s)\b.+)$", re.I),
]
# Writing, saved as a PDF: "write an essay on climate change", "write a leave letter to my principal", "X par essay likho"
_WRITE_KIND = (r"(?P<k>essay|article|report|letter|leave letter|application|story|poem|blog(?:\s+post)?|speech|summary|notes"
               r"|document|email|paper|assignment)")
_WRITE = [
    re.compile(rf"^(?:please\s+)?(?:write|draft|compose|create|make|prepare|generate)\s+(?:me\s+)?(?:an?\s+|the\s+|my\s+)?"
               rf"(?:(?P<len>short|long|detailed|full|brief)\s+)?{_WRITE_KIND}\s+(?:on|about|for|to|regarding|of|explaining)\s+(?P<t>.+)$", re.I),
    re.compile(r"^(?P<t>.+?)\s+(?:par|pe|ke\s+upar|पर)\s+(?:ek\s+)?(?P<k>essay|nibandh|article|report|letter|kahani|story|poem|kavita"
               r"|निबंध|लेख|रिपोर्ट|पत्र|कहानी|कविता)\s+(?:likho|likh do|banao|लिखो|लिख दो|बनाओ)$", re.I),
]
_KIND_OF = {"nibandh": "essay", "निबंध": "essay", "लेख": "article", "रिपोर्ट": "report", "पत्र": "letter", "kahani": "story",
            "कहानी": "story", "kavita": "poem", "कविता": "poem", "leave letter": "letter", "blog post": "blog"}
_CURRENT = re.compile(r"\b(?:latest|news|today'?s?|recent|current|this week|breaking)\b", re.I)
# WhatsApp calls: "call Rahul on WhatsApp", "WhatsApp call mom", "video call Priya", "Rahul ko whatsapp pe call karo".
# A bare "call Rahul" goes to the AI, which knows WhatsApp is the only way PLAG calls.
_CALL = [
    re.compile(rf"^(?:please\s+)?(?P<v>video\s+)?call\s+(?P<c>.+?)\s+{_VIA}$", re.I),
    re.compile(rf"^{_WA}\s+(?P<v>video\s+)?call\s+(?P<c>.+)$", re.I),
    re.compile(r"^(?:please\s+)?(?P<v>video)\s+call\s+(?P<c>.+)$", re.I),
    re.compile(r"^(?P<c>.+?)\s+ko\s+(?:whatsapp\s+(?:pe|par)\s+)?(?P<v>video\s+)?call\s+(?:karo|kar do|kardo|lagao|laga do|milao)$", re.I),
    re.compile(r"^(?P<c>.+?)\s+को\s+(?:व्हाट्सएप\s+(?:पर|पे)\s+)?(?P<v>वीडियो\s+)?कॉल\s+(?:करो|कर दो|लगाओ|मिलाओ)$"),
]

# Memory: "remember that my bike service is on Friday", "yaad rakhna ki kal test hai", "forget that"
_REMEMBER = re.compile(r"^(?:please\s+)?(?:remember|note down|make a note|yaad rakhna|yaad rakho|yaad rakh lo|याद रखना|याद रखो)"
                       r"\s*(?:that|ki|कि|:)?\s+(?P<t>.+)$", re.I)
_REMEMBER_TAIL = re.compile(r"^(?P<t>.+?)\s+(?:yaad rakhna|yaad rakho|याद रखना|याद रखो)$", re.I)
_RECALL = re.compile(r"^(?:what do you remember(?: about me)?|what do you know about me|what have i told you"
                     r"|show (?:my )?memor(?:y|ies)|tumhe kya yaad hai|kya yaad hai|तुम्हें क्या याद है|क्या याद है)\??$", re.I)
# New messages on connected accounts (LinkedIn, Instagram, Gmail…): never "message Rahul", which is a WhatsApp send
_INBOX = re.compile(r"^(?:any (?:new |unread )?messages|(?:do i have |i have )?(?:any )?new messages|check (?:my )?(?:messages|inbox)"
                    r"|read (?:my )?(?:new )?messages|what'?s (?:new )?in my inbox|(?:show |open )?(?:my )?inbox"
                    r"|koi naya message(?: aaya)?(?: hai)?|naye messages(?: batao| dikhao)?|mere messages(?: batao| dikhao| check karo)?"
                    r"|messages check karo|inbox check karo|कोई नया मैसेज(?: आया)?(?: है)?)\??$", re.I)
_FORGET = re.compile(r"^(?:forget|bhool jao|bhul jao|भूल जाओ)(?:\s+about)?\s*(?P<q>.*)$", re.I)
# Reminders: "remind me in 10 minutes to drink water", "remind me to call mom at 7 pm", "kal subah 8 baje test yaad dilana"
_REMINDERS = re.compile(r"^(?:what are my reminders|show (?:my )?reminders|list (?:my )?reminders|any reminders"
                        r"|mere reminders(?: dikhao)?|reminders dikhao|मेरे रिमाइंडर)\??$", re.I)
_CANCEL_REMINDER = re.compile(r"^(?:cancel|delete|remove)\s+(?:the\s+|my\s+|that\s+)?reminder\s*(?:for|about|to)?\s*(?P<q>.*)$", re.I)
_REMIND = re.compile(r"^(?:please\s+)?(?:remind me|set a reminder|reminder)\b\s*(?P<r>.*)$", re.I)
_REMIND_HI = re.compile(r"^(?P<r>.+?)\s+(?:yaad dilana|yaad dila dena|remind karna|remind kar dena|याद दिलाना|याद दिला देना)$", re.I)

# Images (FLUX.1-dev): "generate an image of a cat astronaut", "draw a tiger in the snow", "red car ki image banao",
# "gen an image of this" (the camera). Non-English descriptions go to the AI, which writes an English prompt.
_IMAGE_KIND = r"(?:ai\s+)?(?:image|picture|pic|photo|art|artwork|drawing|painting|illustration|wallpaper|poster|logo|sketch)"
_IMAGE = [
    re.compile(rf"^(?:please\s+)?(?:gen|generate|create|make|draw|paint|design|render)\s+(?:me\s+)?(?:an?\s+|one\s+)?"
               rf"(?P<k>{_IMAGE_KIND})\s+(?P<prep>of|showing|with|for|where)\s+(?P<p>.+)$", re.I),
    re.compile(r"^(?:please\s+)?(?:draw|paint|sketch|imagine)\s+(?:me\s+)?(?P<p>.+)$", re.I),
]
# "sher ki tasveer banao": the words are Hindi even in Latin letters, so the AI writes the English prompt
_IMAGE_HI = re.compile(r"^(?P<p>.+?)\s+(?:ki|ka|की|का)\s+(?:image|photo|picture|tasveer|drawing|wallpaper|तस्वीर|फोटो|इमेज)\s+"
                       r"(?:banao|bana do|banaiye|generate karo|बनाओ|बना दो)$", re.I)
_THIS = re.compile(r"^(?:this|that|it|this one|this thing|this object|what i'?m (?:holding|showing)|what you see|yeh|ye|ise|isko|iski"
                   r"|iska|यह|ये|इसकी|इसका)\b\s*(?P<style>.*)$", re.I)

# Gmail and Calendar (read-only): "check my important emails", "any new mail?", "what's my schedule tomorrow", "aaj ka schedule"
_MAIL = r"(?:e-?mails?|mails?|inbox|gmail)"
_EMAIL = [
    re.compile(rf"^(?:check|read|show|summari[sz]e)\s+(?:me\s+)?(?:my\s+)?(?:(?P<k>important|unread|new|today'?s)\s+)?{_MAIL}"
               r"(?:\s+(?:for\s+)?(?P<t>today))?$", re.I),
    re.compile(rf"^(?:any|do i have any|did i get any)\s+(?:(?P<k>important|new|unread)\s+)?{_MAIL}(?:\s+(?P<t>today))?\??$", re.I),
    re.compile(rf"^what\s+(?:(?P<k>important)\s+)?{_MAIL}\s+did i (?:get|receive)(?:\s+(?P<t>today))?\??$", re.I),
    re.compile(rf"^(?:mere\s+)?(?:(?P<k>important|naye|new)\s+)?{_MAIL}\s+(?:check|dikhao|batao|padho)\s*(?:karo|kar do)?$", re.I),
    re.compile(rf"^koi\s+(?:(?P<k>naya|naye|important|new)\s+)?{_MAIL}\s+(?:aaya|aya|hai)\??$", re.I),
]
_CALENDAR = [
    re.compile(r"^(?:what'?s|what is|show|check|read)\s+(?:on\s+)?(?:me\s+)?my\s+(?:schedule|calendar|agenda|day)"
               r"(?:\s+(?:for\s+)?(?P<d>today|tomorrow))?\??$", re.I),
    re.compile(r"^(?:what do i have|what have i got|what'?s happening|am i free)\s+(?P<d>today|tomorrow)\??$", re.I),
    re.compile(r"^(?P<d>aaj|kal)\s+(?:ka|mera)\s+(?:schedule|plan|calendar)(?:\s+(?:kya hai|batao|dikhao))?\??$", re.I),
    re.compile(r"^(?:mera\s+)?(?:schedule|calendar)\s+(?:batao|dikhao)$", re.I),
    re.compile(r"^(?P<d>आज|कल)\s+का\s+(?:शेड्यूल|कैलेंडर)(?:\s+(?:क्या है|बताओ))?$"),
]

# 3D models (TRELLIS): "make a 3D model of a wooden chair", "generate a 3d robot", "chair ka 3d model banao"
_MODEL3D = [
    re.compile(r"^(?:please\s+)?(?:gen|generate|create|make|build|design|render)\s+(?:me\s+)?(?:an?\s+)?3d\s+"
               r"(?:(?:model|object|asset|version|figure)\s+(?:of|for)\s+)?(?P<p>.+)$", re.I),
    re.compile(r"^(?:an?\s+)?3d\s+(?:model|object)\s+of\s+(?P<p>.+)$", re.I),
]
# Keep what PLAG just made (PDFs and 3D models are drafts until then): "save", "save it", "save the PDF",
# "ise save karo", "report save kar do"
_SAVE_WHAT = r"pdf|report|file|document|doc|essay|letter|story|model|3d\s+model|3d"
_SAVE = re.compile(rf"^(?:please\s+)?(?:save|keep)(?:\s+(?P<w>it|this|that|them|(?:the|this|that|my)\s+(?:{_SAVE_WHAT})))?"
                   r"(?:\s+(?:please|now|for me))?"
                   rf"|(?:ise|isko|ye|yeh|use|usko)?\s*(?:(?P<w2>{_SAVE_WHAT})\s+(?:ko\s+)?)?save\s+(?:karo|kar do|kardo|kar lo|kar de|kijiye)",
                   re.I)
# Directions: "take me to India Gate", "navigate to the airport", "how do I get to CP", "I want to go to Connaught
# Place", "India Gate kaise jaun", "airport ka rasta batao", "mujhe office jana hai", "ghar le chalo", "take me home"
_NAV = [
    re.compile(r"^(?:please\s+)?(?:take me|navigate|directions|get me|drive me|guide me|show me the way|show me the route"
               r"|route|how do i get|how can i get|how to go|how to get|i want to go|i wanna go|let'?s go|lets go"
               r"|i need to go|i have to go)\s+to\s+(?P<p>.+?)(?:\s+please)?$", re.I),
    re.compile(r"^(?:please\s+)?(?:take me|get me|drive me|navigate|let'?s go)\s+(?:back\s+)?(?P<p>home)(?:\s+please)?$", re.I),
    re.compile(r"^(?:how far is|how long to|how much time to|how to reach|how do i reach|how long will it take to (?:go to|reach|get to))"
               r"\s+(?P<p>.+?)\??$", re.I),
    re.compile(r"^(?:mujhe\s+|hume\s+|humein\s+)?(?P<p>.+?)\s+(?:kaise\s+(?:jaun|jaaun|jaye|jayen|jaana hai|jana hai|jau|pahunchu|pahuchu)"
               r"|ka\s+rasta\s+(?:batao|bata do|dikhao|dikha do|bataiye)|ka\s+route\s+(?:batao|dikhao)|le\s+chalo"
               r"|jana\s+hai|jaana\s+hai|kitni\s+door\s+hai|kitna\s+door\s+hai)\??$", re.I),
    re.compile(r"^(?P<p>.+?)\s+(?:कैसे\s+(?:जाऊं|जाऊँ|जाएं)|का\s+रास्ता\s+(?:बताओ|दिखाओ)|ले\s+चलो|जाना\s+है)$"),
]
_NAV_TARGET_JUNK = re.compile(r"^(?:the\s+|a\s+)?|\s+(?:by car|on the map|now|abhi|jaldi|please)$", re.I)
# Saving a place: "save this location as home", "save this place as office", "save my location as gym",
# "save India Gate as favourite", "is jagah ko home naam se save karo", "yeh location office ke naam se save karo"
_SAVE_HERE = re.compile(r"^(?:please\s+)?(?:save|mark|remember)\s+(?:this|my|the current|current|my current)\s+"
                        r"(?:location|place|spot|address|position)\s+as\s+(?:my\s+)?(?P<l>.+?)$", re.I)
_SAVE_HERE_HI = re.compile(r"^(?:is|iss|ye|yeh|meri|meri\s+abhi\s+ki)\s+(?:jagah|location|place)\s+(?:ko\s+)?(?P<l>.+?)\s+"
                           r"(?:ke\s+|ki\s+)?(?:naam\s+se\s+)?save\s+(?:karo|kar do|kardo|kar lo)$", re.I)
_SAVE_PLACE = re.compile(r"^(?:please\s+)?(?:save|mark)\s+(?P<p>.+?)\s+as\s+(?:my\s+)?(?P<l>.+?)$", re.I)
_STOP_NAV = re.compile(r"^(?:stop|end|cancel|close|exit)\s+(?:the\s+)?(?:navigation|directions|route|map)$"
                       r"|^(?:navigation|directions|route|map)\s+(?:band karo|band kar do|stop karo|hatao)$", re.I)


def _nav_intent(raw: str, lang: str) -> Intent | None:
    if _STOP_NAV.fullmatch(raw.strip(" .!")):
        return Intent("ui", {"command": "stop_nav"}, lang)
    for rx in (_SAVE_HERE, _SAVE_HERE_HI):
        if m := rx.fullmatch(raw):
            label = m["l"].strip(" .!?\"'")
            if label and len(label) <= 40:
                return Intent("save_place", {"label": label, "place": ""}, lang, label="Places")
    if (m := _SAVE_PLACE.fullmatch(raw)) and not re.search(r"\b(?:pdf|report|file|document|model|draft|image|photo)\b", m["p"], re.I):
        return Intent("save_place", {"label": m["l"].strip(" .!?\"'"), "place": m["p"].strip()}, lang, label="Places")
    for rx in _NAV:
        if m := rx.fullmatch(raw):
            place = _NAV_TARGET_JUNK.sub("", m["p"].strip(" .!?")).strip()
            # "take me through it", "let's go", "chalo": not a place
            if not place or len(place) > 80 or re.fullmatch(
                    r"(?:it|this|that|there|here|back|ahead|now|chalo|chale|yahan|wahan|sleep|bed|so|sona|settings|the next\s+\w+"
                    r"|next\s+\w+|previous\s+\w+|(?:the\s+)?(?:top|bottom|start|end)(?:\s+of\s+.+)?)", place, re.I):
                return None
            return Intent("navigate", {"place": place}, lang, label="Maps")
    return None


# "... arc reactor ok", "... please bro": words said to PLAG, not part of what to draw
_FILLER_TAIL = re.compile(r"(?:[\s,]+(?:ok|okay|okk|please|pls|plz|bro|yaar|jaldi|now|na))+[\s.!?]*$", re.I)
_MODEL3D_HI = re.compile(r"^(?P<p>.+?)\s+(?:ka|ki|का|की)\s+3d\s+(?:model|मॉडल)\s+(?:banao|bana do|banaiye|बनाओ|बना दो)$", re.I)

# Weather. The forecast for a city: "what's the weather in Delhi", "will it rain tomorrow", "aaj ka mausam kaisa hai".
# A global AI simulation (NVIDIA FourCastNet): "run a weather simulation", "simulate the wind", "mausam ka simulation dikhao".
_SIM_WHAT = r"(?P<v>weather|climate|temperature|wind|winds|rain|moisture|humidity|pressure|storms?|atmosphere)"
_WEATHER_SIM = [
    re.compile(rf"^(?:please\s+)?(?:run|show|start|do|play|create|make)?\s*(?:me\s+)?(?:an?\s+|the\s+)?"
               rf"(?:global\s+|world\s+|earth\s+|ai\s+)?{_SIM_WHAT}\s+simulation(?:\s+(?:with|using|on)\s+.+)?$", re.I),
    re.compile(rf"^(?:please\s+)?simulate\s+(?:the\s+)?(?:global\s+|world\s+|earth'?s?\s+|planet'?s?\s+)?{_SIM_WHAT}\b.*$", re.I),
    re.compile(r"^(?:run\s+|open\s+|start\s+|show\s+)?(?:nvidia\s+)?(?:fourcastnet|four cast net|earth-?2)(?:\s+(?P<v>.+))?$", re.I),
    re.compile(r"^(?P<v>mausam|weather|hawa|baarish|garmi)\s+(?:ka|ki)\s+simulation\s+(?:dikhao|chalao|karo|banao)$", re.I),
]
_W_DAY = r"(?:today|tomorrow|tonight|right now|now|outside)"
_WEATHER = [
    re.compile(rf"^(?:what(?:'s| is| will be)\s+the\s+|how(?:'s| is| will be)\s+the\s+|tell me the\s+|check the\s+"
               rf"|show (?:me )?the\s+)?(?:weather(?:\s+forecast)?|forecast|temperature)(?:\s+like)?"
               rf"(?:\s+(?:in|at|for)\s+(?P<c>[a-z .'-]+?))?(?:\s+(?P<d>{_W_DAY}))?(?:\s+(?:in|at|for)\s+(?P<c2>[a-z .'-]+?))?\??$", re.I),
    re.compile(rf"^(?:will|is) it (?:going to )?(?:rain|be hot|be cold|snow|be sunny)(?:\s+(?P<d>{_W_DAY}))?"
               rf"(?:\s+in\s+(?P<c>[a-z .'-]+?))?(?:\s+(?P<d2>{_W_DAY}))?\??$", re.I),
    re.compile(r"^(?:(?P<c>[a-z .'-]+?)\s+(?:mein|me|ka|ki)\s+)?(?:(?P<d>aaj|kal)\s+(?:ka\s+)?)?(?:mausam|weather)"
               r"(?:\s+(?:kaisa|kya|kaise|kaisi))?(?:\s+(?:hai|hoga|rahega|rahegi))?(?:\s+(?:batao|bata do|bataiye))?$", re.I),
    re.compile(r"^(?:(?P<c>\S+?)\s+(?:में|का|की)\s+)?(?:(?P<d>आज|कल)\s+(?:का\s+)?)?मौसम(?:\s+(?:कैसा|क्या))?"
               r"(?:\s+(?:है|होगा|रहेगा))?(?:\s+बताओ)?$"),
    # "kal dilli mein baarish hogi kya", "aaj delhi me barish hogi", "kya kal baarish hogi"
    re.compile(r"^(?:kya\s+)?(?:(?P<d>aaj|kal)\s+)?(?:(?P<c>[a-z .'-]+?)\s+(?:mein|me)\s+)?(?:(?P<d2>aaj|kal)\s+)?"
               r"(?:baarish|barish|rain|thand|garmi)\s+(?:hogi|hoga|ho rahi hai|aayegi|aaegi|padegi|pad rahi hai)(?:\s+kya)?\??$", re.I),
]
_TOMORROW = {"tomorrow", "kal", "कल"}

# Your files and folders: "open Downloads", "what's on my desktop", "open my resume from the desktop",
# "open the physics notes pdf", "desktop se resume kholo"
_FOLDER = r"(?P<f>desktop|downloads?|documents?|docs|pictures|photos|videos?|music)"
_FILE_KIND = r"(?P<k>file|pdf|document|doc|photo|picture|image|video|ppt|presentation|excel|sheet|spreadsheet|notes|song|zip)"
_OPEN_FOLDER = [re.compile(rf"^(?:open|show|go to)\s+(?:my\s+|the\s+)?{_FOLDER}(?:\s+folder)?$", re.I),
                re.compile(rf"^(?:my\s+|mera\s+)?{_FOLDER}(?:\s+folder)?\s+(?:kholo|khol do|dikhao)$", re.I)]
_LIST_FOLDER = re.compile(rf"^(?:what(?:'s| is)\s+(?:on|in)\s+(?:my\s+|the\s+)?|show\s+(?:me\s+)?(?:my\s+|the\s+)?(?:files\s+(?:on|in)\s+(?:my\s+)?)?"
                          rf"|list\s+(?:my\s+|the\s+)?(?:files\s+(?:on|in)\s+(?:my\s+)?)?){_FOLDER}(?:\s+files)?\??$", re.I)
_OPEN_FILE = [
    re.compile(rf"^(?:open|show|play|find)\s+(?:my\s+|the\s+)?(?P<q>.+?)\s+(?:from|on|in)\s+(?:my\s+|the\s+)?{_FOLDER}(?:\s+folder)?$", re.I),
    re.compile(rf"^(?:open|find|show)\s+(?:the\s+|my\s+)?{_FILE_KIND}\s+(?:called|named|of|about|on)?\s*(?P<q>.+)$", re.I),
    re.compile(rf"^(?:open|find|show)\s+(?:the\s+|my\s+)?(?P<q>.+?)\s+{_FILE_KIND}$", re.I),
    re.compile(rf"^{_FOLDER}\s+(?:se|me|mein|में|से)\s+(?P<q>.+?)\s+(?:kholo|khol do|खोलो)$", re.I),
    re.compile(rf"^(?P<q>.+?)\s+{_FILE_KIND}\s+(?:kholo|khol do|खोलो)$", re.I),
]
_FOLDER_OF = {"download": "downloads", "document": "documents", "docs": "documents", "photos": "pictures", "video": "videos"}

# Knowledge from Wikipedia and the news: "who is Sundar Pichai", "what is ISRO", "tell me about the Taj Mahal",
# "ISRO kya hai", "Virat Kohli kaun hai". Questions about you, PLAG or right now go elsewhere.
_LOOKUP = [
    re.compile(r"^(?:who\s+(?:is|was|are|were)|what\s+(?:is|are|was|were)|what's|tell me (?:about|more about)|explain|define"
               r"|look up|search wikipedia(?:\s+for)?|wikipedia(?:\s+search)?(?:\s+for)?)\s+(?P<q>.+?)\??$", re.I),
    re.compile(r"^(?P<q>.+?)\s+(?:kaun\s+(?:hai|tha|thi|hain|the)|kya\s+(?:hai|hota\s+hai|hoti\s+hai|hain)"
               r"|ke\s+baare\s+(?:mein|me)\s+(?:batao|bataiye|bata do))\??$", re.I),
    re.compile(r"^(?P<q>.+?)\s+(?:कौन\s+(?:है|था|थी)|क्या\s+(?:है|होता\s+है)|के\s+बारे\s+में\s+बताओ)\??$"),
]
_EXPLICIT_LOOKUP = re.compile(r"^(?:look up|search wikipedia|wikipedia)\b", re.I)
_NOT_LOOKUP = re.compile(r"\b(?:my|me|mine|i|you|your|this|that|it|time|weather|date|day|today|battery|news|mera|meri|mujhe"
                         r"|tum|tumhara|aap|aapka|ye|yeh|plag|mausam|samay|baje)\b|मेरा|मेरी|तुम|आप|यह|ये", re.I)


def _files_intent(raw: str, t: str, lang: str) -> Intent | None:
    for rx in _OPEN_FOLDER:
        if m := rx.fullmatch(t):
            f = _FOLDER_OF.get(m["f"].casefold(), m["f"].casefold())
            return Intent("open_folder", {"folder": f}, lang, label=f.capitalize())
    if m := _LIST_FOLDER.fullmatch(t):
        f = _FOLDER_OF.get(m["f"].casefold(), m["f"].casefold())
        return Intent("list_files", {"folder": f}, lang, label=f.capitalize())
    for rx in _OPEN_FILE:
        if m := rx.fullmatch(raw):
            g = m.groupdict()
            q = (g.get("q") or "").strip(" .?")
            if not q or q.casefold() in ("a", "the", "my"):
                return None
            f = (g.get("f") or "").casefold()
            return Intent("open_file", {"query": q, "folder": _FOLDER_OF.get(f, f), "kind": (g.get("k") or "").casefold()},
                          lang, label="Files")
    return None


def _lookup_intent(raw: str, lang: str) -> Intent | None:
    # "who is Modi ji" / "ISRO kya hai": the AI knows, so it answers straight away (2026-09-25: going to Wikipedia and
    # the news first made every question slow). Only an explicit "look up X" / "wikipedia X" searches.
    if not _EXPLICIT_LOOKUP.match(raw):
        return None
    for rx in _LOOKUP:
        if m := rx.fullmatch(raw):
            q = re.sub(r"^(?:a|an|the)\s+", "", m["q"].strip(" ?.!"), flags=re.I)
            if not q or len(q) > 80 or _NOT_LOOKUP.search(q):
                return None
            return Intent("lookup", {"query": q, "question": raw}, lang, label="Wikipedia")
    return None
# Where you are (Windows Location + the address): "where am I", "what's my address", "main kahan hoon"
_WHERE = re.compile(r"^(?:where am i(?: right now| now)?|where are we|what(?:'s| is) my (?:current |exact )?(?:location|address|position)"
                    r"|(?:show|tell me|give me) my (?:current |exact )?(?:location|address)|my (?:current )?(?:location|address)"
                    r"|which (?:city|area|place) am i in|mera (?:location|address|pata) (?:kya hai|batao)|main kaha(?:n|an)? hoon"
                    r"|hum kaha(?:n)? hain|मैं कहाँ हूँ|मेरा (?:पता|लोकेशन) (?:क्या है|बताओ))\??$", re.I)


def _weather_intent(raw: str, t: str, lang: str) -> Intent | None:
    if _WHERE.fullmatch(t):
        return Intent("where", {"detail": "address" if re.search(r"address|pata|पता", t) else "place"}, lang, label="Location")
    for rx in _WEATHER_SIM:
        if m := rx.fullmatch(t):
            from .weather import variable_for  # the maps' variable from the words ("wind" -> wind speed)
            return Intent("weather_sim", {"variable": variable_for(m["v"] or "")}, lang, label="FourCastNet")
    for rx in _WEATHER:
        if m := rx.fullmatch(t):
            g = m.groupdict()
            city = (g.get("c") or g.get("c2") or "").strip(" .'-")
            day = (g.get("d") or g.get("d2") or "").strip()
            if city in ("aaj", "kal", "today", "tomorrow", "आज", "कल"):  # "aaj ka mausam": a day, not a city
                city, day = "", city
            return Intent("weather", {"city": city, "day": "tomorrow" if day in _TOMORROW else "today"}, lang, label="Weather")
    for rx in _MODEL3D:
        if m := rx.fullmatch(raw):
            prompt = _FILLER_TAIL.sub("", m["p"]).strip(" .!?,")
            if prompt.casefold() in ("model", "object", "asset", "version", "figure", "") or detect_lang(prompt) != "en":
                return None  # nothing to build yet, or a Hindi description: the AI asks or writes the English prompt
            return Intent("generate_3d", {"prompt": prompt}, lang, label="3D model")
    if _MODEL3D_HI.fullmatch(raw):
        return None  # "kursi ka 3d model banao": the AI writes the English description
    return None


_COUNT = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20,
          "thirty": 30, "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "das": 10, "half an": 0.5, "half a": 0.5}
_IN = re.compile(r"\b(?:in|after)\s+(?P<n>\d+(?:\.\d+)?|half an?|an?|one|two|three|four|five|ten|fifteen|twenty|thirty)\s*"
                 r"(?P<u>seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b", re.I)
_IN_HI = re.compile(r"\b(?P<n>\d+|ek|do|teen|char|paanch|das)\s*(?P<u>second|minute|minat|min|ghante|ghanta|hour|din)\s*"
                    r"(?:mein|me|baad|में|बाद)\b", re.I)
_AT = re.compile(r"\b(?:at\s+)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?=\W|$)"
                 r"|\bat\s+(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\b", re.I)
_BAJE = re.compile(r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*baje\b", re.I)
_DAY = re.compile(r"\b(tomorrow|kal|today|aaj|tonight)\b", re.I)
_PART = re.compile(r"\b(?:in the\s+|this\s+)?(morning|afternoon|evening|night|subah|dopahar|shaam|sham|raat)\b", re.I)


def _count(word: str) -> float:
    w = word.lower().strip()
    return float(w) if re.fullmatch(r"\d+(?:\.\d+)?", w) else _COUNT.get(w, 1)


def parse_reminder(text: str, now: datetime | None = None) -> tuple[str, datetime] | None:
    """ "in 10 minutes to drink water" -> ("drink water", now + 10 min). None when there's no clear time: the AI
    asks or works it out."""
    now = now or datetime.now()
    t = f" {text.strip()} "
    spans: list[tuple[int, int]] = []
    due = None
    if m := (_IN.search(t) or _IN_HI.search(t)):
        n, unit = _count(m["n"]), m["u"].lower()
        seconds = 1 if unit.startswith("s") else 3600 if unit.startswith(("h", "ghant")) else 86400 if unit.startswith(("d", "din")) else 60
        due = now + timedelta(seconds=n * seconds)
        spans.append(m.span())
    else:
        day, part = _DAY.search(t), _PART.search(t)
        m = _AT.search(t) or _BAJE.search(t)
        if not m and not (day and day[1].lower() in ("tomorrow", "kal")):
            return None
        date = now.date() + timedelta(days=1 if day and day[1].lower() in ("tomorrow", "kal") else 0)
        if m:
            hour = int(m["h"] or m.groupdict().get("h2") or 0)
            minute = int(m["m"] or m.groupdict().get("m2") or 0)
            ap = (m.groupdict().get("ap") or "").lower().replace(".", "")
            p = (part[1].lower() if part else "")
            if hour > 23 or minute > 59:
                return None
            if ap == "pm" or (not ap and p in ("afternoon", "evening", "night", "dopahar", "shaam", "sham", "raat") and hour < 12):
                hour = hour % 12 + 12
            elif ap == "am" or p in ("morning", "subah"):
                hour = hour % 12
            elif not ap and hour <= 12 and not (day and day[1].lower() in ("tomorrow", "kal")):
                # "at 7" with no am/pm: the next 7 o'clock from now
                first = datetime.combine(date, datetime.min.time()).replace(hour=hour % 12, minute=minute)
                hour = hour % 12 if first > now else hour % 12 + 12
            spans.append(m.span())
        else:
            hour, minute = 9, 0  # "tomorrow", no time: 9 in the morning
        due = datetime.combine(date, datetime.min.time()).replace(hour=hour, minute=minute)
        if due <= now and not day:
            due += timedelta(days=1)  # "at 7 am" said at 8 am means tomorrow
        spans += [x.span() for x in (day, part) if x]
    for a, b in sorted(spans, reverse=True):
        t = t[:a] + " " + t[b:]
    task = re.sub(r"\s+", " ", t).strip(" ,.")
    task = re.sub(r"^(?:me\s+)?(?:to|that|about|ki|कि|ke\s+liye)\s+", "", task, flags=re.I).strip()
    task = re.sub(r"\s+(?:ko|को)$", "", task).strip()
    if not task or due <= now:
        return None
    return task, due

# Small talk answered locally: instant, and still works when Gemini is overloaded.
_CHAT = [
    ("hello", re.compile(r"(hi+|hello+|hey+|hlo|helo|yo|namaste|namaskar|नमस्ते|नमस्कार|hey there|hello there"
                         r"|good (morning|afternoon|evening))( sir)?")),
    ("how", re.compile(r"(how are you|how r u|how are you doing|how's it going|kaise ho|kaisa hai|kya haal hai|kya hal hai"
                       r"|कैसे हो|क्या हाल है)( sir)?")),
    ("thanks", re.compile(r"(thanks|thank you|thank u|thx|ty|shukriya|dhanyavaad|dhanyawad|धन्यवाद|शुक्रिया)( so much| a lot| sir)?")),
    ("who", re.compile(r"(who are you|what are you|what is your name|what's your name|tum kaun ho|aap kaun ho|tumhara naam kya hai"
                       r"|तुम कौन हो|आप कौन हो|तुम्हारा नाम क्या है)")),
    ("help", re.compile(r"(help|what can you do|what all can you do|tum kya kar sakte ho|aap kya kar sakte ho|kya kya kar sakte ho"
                        r"|तुम क्या कर सकते हो|आप क्या कर सकते हो)")),
    ("bye", re.compile(r"(bye|goodbye|good night|see you|chalo bye|अलविदा|शुभ रात्रि)( sir)?")),
]

# Answers to "Send it?". "No" wins over "yes" so "don't send" never sends.
_NO = re.compile(r"(\b(no|nope|cancel|don'?t|stop|nahi|nahin|mat|ruko|rehne do)\b|नहीं|मत|रुको|रहने दो|कैंसल)", re.I)
_YES = re.compile(r"(\b(yes|yeah|yep|yup|send|go ahead|confirm|sure|ok|okay|haan|han|haa|ha|bhej|bhejo|bhej do|kar do"
                  r"|theek hai|thik hai)\b|हाँ|हां|भेज|ठीक है|कर दो)", re.I)


def yes_no(text: str) -> bool | None:
    """True for a clear yes, False for a clear no, None when the answer is something else."""
    if _NO.search(text or ""):
        return False
    if _YES.search(text or ""):
        return True
    return None


_SPLIT = re.compile(r"\s*(?:,|;|\band then\b|\bthen\b|\band\b|\baur\b|\bphir\b|और|फिर)\s*", re.I)
_OPENERS = ("open_url", "open_app")
_SEARCHES = ("web_search", "play_youtube")


# The app a step leaves you in, so the next step (or your next command) can build on it.
_APP_OF_LABEL = {"Google": "google", "YouTube": "youtube", "Gmail": "gmail", "WhatsApp": "whatsapp"}
# Steps that open their own app: "open X" right before one of these in the same app is redundant.
_SELF_OPENING = {"web_search", "play_youtube", "whatsapp_send", "whatsapp_call", "whatsapp_open", "gmail_search"}


def context_of(intent: Intent) -> str | None:
    a = intent.action
    if a in _OPENERS:
        return _APP_OF_LABEL.get(intent.label)
    if a == "web_search":
        return intent.args.get("site") or "google"
    if a == "play_youtube":
        return "youtube"
    if a.startswith("whatsapp_"):
        return "whatsapp"
    if a in ("gmail_check", "gmail_search"):
        return "gmail"
    return None


def parse_many(text: str, ctx: str | None = None) -> list[Intent] | None:
    """A sentence as an ordered plan: "open Google, YouTube and WhatsApp" (three steps), "open YouTube and search
    Honey Singh" (one YouTube search), "open WhatsApp and find Priyans" (one WhatsApp chat).

    Each step is read in the context of the app the previous step left you in, so "search X" after "open YouTube"
    searches YouTube and after "open WhatsApp" finds a contact. `ctx` carries that over from your last command.
    Every piece must be understood locally, otherwise None (the whole sentence goes to the AI).
    """
    raw = _strip(text)
    parts = [p for p in _SPLIT.split(raw) if p and p.strip()]
    if len(parts) >= 2:
        lang = detect_lang(text)
        out: list[Intent] = []
        here = ctx
        for part in parts[:5]:
            it = parse(part, here)
            if it is None and out and out[-1].action in _OPENERS:
                it = _open_target(_normalize(part), lang)  # "open google, youtube and whatsapp"
            if it is None:
                out = []
                break
            if (out and out[-1].action in _OPENERS and it.action in _SELF_OPENING
                    and context_of(out[-1]) == context_of(it)):
                out.pop()  # "open YouTube and search X": the search opens YouTube itself
            out.append(it)
            here = context_of(it) or here
        if out:
            return out
    whole = parse(text, ctx)
    if whole and whole.action.startswith("whatsapp_") and _JOINER.search(whole.args.get("contact", "")):
        # "mausam kholo aur obi bro ko message bhejo" is two requests, not one person called "mausam kholo aur obi bro"
        # (2026-09-25): the AI sorts it out
        return None
    return [whole] if whole else None


_JOINER = re.compile(r"\b(?:and|and then|then|aur|phir|fir)\b|और|फिर", re.I)


def _strip(text: str) -> str:
    """Remove the wake word and closing punctuation, keeping the user's capitalisation."""
    t = _WAKE.sub("", text.strip())
    t = re.sub(r"[।.!?]+$", "", t).strip()
    return re.sub(r"\s+", " ", t)


def _normalize(text: str) -> str:
    t = text.strip().lower()
    t = _WAKE.sub("", t)
    t = re.sub(r"[।.!?,]+$", "", t).strip()
    return re.sub(r"\s+", " ", t)


def _site_key(s: str) -> str:
    return "youtube" if s in ("youtube", "यूट्यूब") else "google"


def _open_target(raw: str, lang: str) -> Intent | None:
    t = _TAIL.sub("", raw.strip()).removeprefix("the ").strip()
    hit = lookup(t)
    if hit:
        kind, key, e = hit
        if kind == "app":
            return Intent("open_app", {"app": key}, lang, label=e.name)
        return Intent("open_url", {"url": e.target}, lang, label=e.name)
    m = _DOMAIN.fullmatch(t)
    if m:
        return Intent("open_url", {"url": f"https://{m['d']}{m['p'] or ''}"}, lang, label=m["d"])
    return None


_THIS_HI = re.compile(r"^(?:iski|iska|इसकी|इसका)\s+(?:image|photo|picture|tasveer|तस्वीर|फोटो|इमेज)\s+"
                      r"(?:banao|bana do|banaiye|बनाओ|बना दो)$", re.I)


def _image_intent(raw: str, lang: str) -> Intent | None:
    if _THIS_HI.fullmatch(raw):
        return Intent("imagine_camera", {"style": ""}, lang, label="Camera")
    if m := _IMAGE_HI.fullmatch(raw):
        if _THIS.fullmatch(m["p"].strip()):  # "iski image banao": what the camera sees
            return Intent("imagine_camera", {"style": ""}, lang, label="Camera")
        return None
    for rx in _IMAGE:
        if m := rx.fullmatch(raw):
            prompt = _FILLER_TAIL.sub("", m["p"]).strip(" .!?,")
            kind = (m.groupdict().get("k") or "").lower().removeprefix("ai ").strip()
            if this := _THIS.fullmatch(prompt):  # "an image of this": what the camera sees
                return Intent("imagine_camera", {"style": this["style"].strip()}, lang, label="Camera")
            if not prompt or detect_lang(prompt) != "en":
                return None  # the AI writes an English prompt from a Hindi description
            if kind and kind not in ("image", "picture", "pic", "photo"):
                prompt = f"a {kind} {m['prep']} {prompt}"  # "a logo for my startup", "a wallpaper of a neon street"
            aspect = "16:9" if kind == "wallpaper" else "9:16" if kind == "poster" else "1:1"
            return Intent("generate_image", {"prompt": prompt, "aspect": aspect}, lang, label="Image")
    return None


def _memory_intent(raw: str, t: str, lang: str) -> Intent | None:
    if intent := _weather_intent(raw, t, lang):
        return intent
    if intent := _image_intent(raw, lang):
        return intent
    for rx in _GMAIL_SEARCH:
        if m := rx.fullmatch(raw):
            return Intent("gmail_search", {"query": m["q"].strip(" ?.")}, lang, label="Gmail")
    for rx in _EMAIL:
        if m := rx.fullmatch(t):
            k = (m.groupdict().get("k") or "").lower()
            kind = "unread" if k in ("unread", "new", "naya", "naye") else "today" if (m.groupdict().get("t") or k.startswith("today")) and not k else "important"
            return Intent("gmail_check", {"kind": kind, "summarize": t.startswith("summar")}, lang, label="Gmail")
    for rx in _CALENDAR:
        if m := rx.fullmatch(t):
            d = (m.groupdict().get("d") or "").lower()
            return Intent("calendar_check", {"day": "tomorrow" if d in ("tomorrow", "kal", "कल") else "today"}, lang, label="Calendar")
    if _INBOX.fullmatch(t):
        return Intent("inbox_check", {}, lang, label="Inbox")
    if _RECALL.fullmatch(t):
        return Intent("recall", {}, lang, label="Memory")
    if _REMINDERS.fullmatch(t):
        return Intent("reminders", {}, lang, label="Reminders")
    if m := _CANCEL_REMINDER.fullmatch(raw):
        return Intent("reminder_cancel", {"query": m["q"].strip()}, lang, label="Reminders")
    if m := (_REMIND.fullmatch(raw) or _REMIND_HI.fullmatch(raw)):
        if parsed := parse_reminder(m["r"]):
            what, due = parsed
            return Intent("remind", {"text": what, "when": due.isoformat(timespec="seconds")}, lang, label="Reminder")
        return None  # no clear time: the AI asks when
    if m := (_REMEMBER.fullmatch(raw) or _REMEMBER_TAIL.fullmatch(raw)):
        return Intent("remember", {"text": m["t"].strip()}, lang, label="Memory")
    if m := _FORGET.fullmatch(raw):
        q = m["q"].strip()
        if q.lower() in ("it", "about it"):  # "forget it" usually means "never mind"
            return None
        return Intent("forget", {"query": q}, lang, label="Memory")
    return None


def _contact(name: str) -> str:
    name = re.sub(r"^(?:the\s+)?(?:chat\s+(?:with|of)\s+)?", "", name.strip(), flags=re.I)
    name = re.sub(rf"^{_WA}\s+(?:pe|par|se|on|पर|पे)\s+", "", name, flags=re.I)  # "WhatsApp par Rahul ko call karo"
    return re.sub(r"^(?:my|mera|meri|mere)\s+", "", re.sub(r"'s\s+chat$", "", name, flags=re.I), flags=re.I).strip()


def _search_in(ctx: str | None, query: str, lang: str) -> Intent:
    """A search that didn't name a site happens in the app you're in: YouTube, WhatsApp (a contact), Gmail, or Google."""
    if ctx == "youtube":
        return Intent("web_search", {"site": "youtube", "query": query}, lang, label="YouTube")
    if ctx == "whatsapp":
        return Intent("whatsapp_open", {"contact": _contact(query)}, lang, label="WhatsApp")
    if ctx == "gmail":
        return Intent("gmail_search", {"query": query}, lang, label="Gmail")
    return Intent("web_search", {"site": "google", "query": query}, lang, label="Google")


def parse(text: str, ctx: str | None = None) -> Intent | None:
    """One command. `ctx` is the app the user is in ("youtube" after "open YouTube"), for commands that build on it."""
    lang = detect_lang(text)
    t = _normalize(text)
    if not t:
        return None
    raw = _strip(text)
    if intent := _nav_intent(raw, lang):  # directions and saved places, before "save" (a PDF) gets it
        return intent
    if _EXPLICIT_LOOKUP.match(raw) and (intent := _lookup_intent(raw, lang)):  # you named Wikipedia: not the app you're in
        return intent
    if m := _SAVE.fullmatch(raw):  # "save", "save the PDF", "ise save karo": keep the latest draft
        what = (m["w"] or m["w2"] or "").casefold()
        kind = "3d" if re.search(r"3d|model", what) else "pdf" if what and re.search(r"pdf|report|essay|document|doc|file|letter|story", what) else ""
        return Intent("save_draft", {"kind": kind}, lang, label="Save")
    for rx in _WA_OPEN:
        if m := rx.fullmatch(raw):
            if contact := _contact(m["c"]):
                return Intent("whatsapp_open", {"contact": contact}, lang, label="WhatsApp")
    for rx in _WHATSAPP:
        if m := rx.fullmatch(raw):
            message = re.sub(r"\s+(?:message|msg)$", "", m["m"].strip(), flags=re.I)  # "send good morning message to mom"
            contact = _contact(m["c"])
            if not contact:
                return None
            if _NO_TEXT.fullmatch(message) or _NO_TEXT.fullmatch(m["m"].strip()) or message.casefold() in ("a", "an", "the", "one"):
                message = ""  # ("send a message to Rahul": "a message" is not the text, and never "a")
            return Intent("whatsapp_send", {"contact": contact, "message": message}, lang, label="WhatsApp")
    for rx in _WA_ASK:
        if (m := rx.fullmatch(raw)) and (contact := _contact(m["c"])):
            # "send msg to omi bro bangalore hi": a greeting at the end is the message, not part of the name
            if (g := _GREETING_TAIL.search(contact)) and g.start() > 0:
                return Intent("whatsapp_send", {"contact": contact[:g.start()].strip(" ,"), "message": g["g"].strip()}, lang,
                              label="WhatsApp")
            return Intent("whatsapp_send", {"contact": contact, "message": ""}, lang, label="WhatsApp")
    for rx in _CALL:
        if m := rx.fullmatch(raw):
            contact = _contact(m["c"])
            if contact:
                return Intent("whatsapp_call", {"contact": contact, "video": bool(m["v"])}, lang, label="WhatsApp")
    if intent := _files_intent(raw, t, lang):
        return intent
    if intent := _memory_intent(raw, t, lang):  # before the dashboard and lag checks: "remind me to charge the battery"
        return intent
    if _CAMERA_ON.fullmatch(t):
        return Intent("ui", {"command": "camera_on"}, lang)
    if _CAMERA_OFF.fullmatch(t):
        return Intent("ui", {"command": "camera_off"}, lang)
    if m := _SCREEN_LOOK.fullmatch(t):
        return Intent("screen_look", {"question": "" if m["plain"] else raw}, lang, label="Screen")
    if _CAMERA_LOOK.search(t):
        return Intent("camera_look", {}, lang)
    for command, rx in _UI:
        if rx.search(t):
            return Intent("ui", {"command": command}, lang)
    for key, rx in _CHAT:
        if rx.fullmatch(t):
            return Intent("chat", {"key": key}, lang, mood="cheerful")
    if m := _ON_YOUTUBE.fullmatch(t):
        watch = m["v"] in ("open", "watch", "show me", "start")
        return Intent("play_youtube" if watch else "web_search", {"query": m["q"]} if watch else {"site": "youtube", "query": m["q"]},
                      lang, label="YouTube")
    for rx in (_OPEN_EN, _OPEN_HI):
        m = rx.fullmatch(t)
        if m and (intent := _open_target(m["t"], lang)):
            return intent
        if m and ctx == "whatsapp":  # "open WhatsApp and open Priyans": a chat, not an app
            return Intent("whatsapp_open", {"contact": _contact(m["t"])}, lang, label="WhatsApp")
    if (m := _RESEARCH.fullmatch(raw)) and not _SYSTEM.search(t):
        topic = _RESEARCH_TAIL.sub("", m["t"]).strip(" ,.")
        if topic:
            return Intent("research", {"topic": topic}, lang, label="Research")
    for rx in _NEWS:
        if m := rx.fullmatch(raw):
            topic = ((m.groupdict().get("t") or "").strip(" ,.?") or "top news in India")
            if detect_lang(topic) == "en":  # a Hindi topic goes to the AI, which writes the English search words
                return Intent("research", {"topic": topic}, lang, label="News")
    for rx in _WRITE:
        if m := rx.fullmatch(raw):
            kind = _KIND_OF.get(m["k"].casefold(), m["k"].casefold())
            kind = "blog" if kind.startswith("blog") else kind
            topic = m["t"].strip(" ,.?")
            if kind == "report" and _CURRENT.search(topic):
                return Intent("research", {"topic": topic}, lang, label="Research")  # a report on the news
            return Intent("write", {"kind": kind, "topic": topic, "length": (m.groupdict().get("len") or "").lower()},
                          lang, label="Writing")
    for rx in _CHROME:  # an explicit "in Chrome" wins over the app you're in
        if m := rx.fullmatch(t):
            return Intent("web_search", {"site": "google", "query": m["q"], "browser": "chrome"}, lang, label="Chrome")
    if ctx == "whatsapp" and (m := re.fullmatch(r"(?:find|search(?:\s+for)?|look\s+for|go\s+to)\s+(?P<c>.+)", raw, re.I)):
        return Intent("whatsapp_open", {"contact": _contact(m["c"])}, lang, label="WhatsApp")
    if m := _SEARCH_SITE_EN.fullmatch(t):
        site = m["s"]
        return Intent("web_search", {"site": site, "query": m["q"]}, lang, label="YouTube" if site == "youtube" else "Google")
    if m := _SEARCH_HI_SITE.fullmatch(t):
        site = _site_key(m["s"])
        if site == "youtube" and _PLAY_VERB.search(t):  # "youtube pe X chalao" means play it
            return Intent("play_youtube", {"query": m["q"]}, lang, label="YouTube")
        return Intent("web_search", {"site": site, "query": m["q"]}, lang, label="YouTube" if site == "youtube" else "Google")
    if m := _PLAY_HI.fullmatch(t):
        if intent := _open_target(m["q"], lang):  # "youtube chalao" means open YouTube
            return intent
        return Intent("play_youtube", {"query": m["q"]}, lang, label="YouTube")
    if m := _SEARCH_HI.fullmatch(t):
        return _search_in(ctx, m["q"], lang)
    if m := _PLAY_EN.fullmatch(t):
        return Intent("play_youtube", {"query": m["q"]}, lang, label="YouTube")
    if m := _SEARCH_EN.fullmatch(t):
        if m["s"]:
            return Intent("web_search", {"site": m["s"], "query": m["q"]}, lang, label="YouTube" if m["s"] == "youtube" else "Google")
        return _search_in(ctx, m["q"], lang)
    if _TIME.search(t):
        return Intent("time", {}, lang)
    if _SYSTEM.search(t) and len(t.split()) <= 12:
        return Intent("system_status", {}, lang)
    return _lookup_intent(raw, lang)
