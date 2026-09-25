"""Hinglish for the Hindi voice: PLAG writes Hindi in Latin letters ("Haan bro, Omi ko message bhej diya"), which
reads well on screen but NVIDIA's Hindi voice mangles it ("Khan bro o my co-message badge duya", measured
2026-09-24 through a Whisper round-trip). In Devanagari it's clear and 3x faster to make. So before speaking, the
Hindi words become Devanagari and the rest (English words, names, numbers) stays as written: the voice switches
between the two languages by itself ("हाँ bro, Omi को message भेज दिया").

Hindi words are known from a dictionary of everyday Hinglish (romanisation is ambiguous: "main" is मैं, "mein" is
में); a few more are recognised by their endings and spelled out by rule.

And back again (to_latin): NVIDIA's hearing writes Hindi in Devanagari ("ओमी को मैसेज भेज दो"), but you write
Hinglish, and a WhatsApp message should go out the way you'd type it: "omi ko message bhej do". English words it
wrote in Devanagari (मैसेज, मिनट, यूट्यूब) come back as English.
"""

import re

_WORDS = """
haan:हाँ han:हाँ ha:हाँ ji:जी nahi:नहीं nahin:नहीं nai:नहीं na:ना mat:मत theek:ठीक thik:ठीक acha:अच्छा achha:अच्छा
accha:अच्छा badhiya:बढ़िया badiya:बढ़िया bilkul:बिल्कुल zaroor:ज़रूर jarur:ज़रूर shukriya:शुक्रिया dhanyavaad:धन्यवाद
namaste:नमस्ते namaskar:नमस्कार bhai:भाई yaar:यार yar:यार dost:दोस्त
main:मैं mai:मैं mein:में me:में hum:हम aap:आप tum:तुम tu:तू ye:ये yeh:यह wo:वो woh:वह vo:वो ve:वे
mera:मेरा meri:मेरी mere:मेरे tera:तेरा teri:तेरी tere:तेरे aapka:आपका aapki:आपकी aapke:आपके apna:अपना apni:अपनी
apne:अपने hamara:हमारा hamari:हमारी uska:उसका uski:उसकी uske:उसके iska:इसका iski:इसकी iske:इसके unka:उनका
mujhe:मुझे mujhko:मुझको tumhe:तुम्हें aapko:आपको usko:उसको use:उसे isko:इसको ise:इसे unko:उनको hume:हमें humko:हमको
kisi:किसी kisko:किसको kise:किसे koi:कोई kuch:कुछ kuchh:कुछ sab:सब sabhi:सभी sabko:सबको sabse:सबसे
ko:को ka:का ki:की ke:के se:से pe:पे par:पर tak:तक liye:लिए lie:लिए saath:साथ sath:साथ bina:बिना baad:बाद
pehle:पहले pahle:पहले andar:अंदर bahar:बाहर upar:ऊपर neeche:नीचे niche:नीचे paas:पास door:दूर
aur:और ya:या lekin:लेकिन magar:मगर to:तो toh:तो bhi:भी sirf:सिर्फ़ bas:बस phir:फिर fir:फिर
kyunki:क्योंकि isliye:इसलिए agar:अगर jab:जब tab:तब jaise:जैसे jaisa:जैसा aisa:ऐसा aisi:ऐसी aise:ऐसे waisa:वैसा
kya:क्या kyun:क्यों kyon:क्यों kaise:कैसे kaisa:कैसा kaisi:कैसी kaun:कौन kaunsa:कौनसा kaunsi:कौनसी kab:कब
kahan:कहाँ kaha:कहा kidhar:किधर kitna:कितना kitni:कितनी kitne:कितने
hai:है hain:हैं hoon:हूँ hu:हूँ hun:हूँ ho:हो tha:था thi:थी the:थे hoga:होगा hogi:होगी honge:होंगे hona:होना
hua:हुआ hui:हुई hue:हुए hone:होने
abhi:अभी ab:अब aaj:आज kal:कल parso:परसों raat:रात din:दिन subah:सुबह shaam:शाम dopahar:दोपहर
ghanta:घंटा ghante:घंटे minat:मिनट saal:साल mahina:महीना hafte:हफ़्ते hafta:हफ़्ता baje:बजे samay:समय waqt:वक़्त
jaldi:जल्दी dheere:धीरे thoda:थोड़ा thodi:थोड़ी thode:थोड़े bahut:बहुत bohot:बहुत zyada:ज़्यादा jyada:ज़्यादा
kam:कम kaam:काम aaram:आराम mushkil:मुश्किल aasaan:आसान sahi:सही galat:ग़लत naya:नया nayi:नई naye:नए
purana:पुराना puraani:पुरानी bada:बड़ा badi:बड़ी bade:बड़े chhota:छोटा chhoti:छोटी chote:छोटे
khaali:ख़ाली khali:ख़ाली poora:पूरा poori:पूरी poore:पूरे pura:पूरा puri:पूरी ek:एक do:दो teen:तीन char:चार
paanch:पाँच das:दस
kar:कर karo:करो karna:करना karke:करके karta:करता karti:करती karte:करते karun:करूँ karunga:करूँगा kardo:कर_दो
kiya:किया kijiye:कीजिए kariye:करिए karoge:करोगे karenge:करेंगे kare:करे
raha:रहा rahi:रही rahe:रहे rahega:रहेगा rahegi:रहेगी rakh:रख rakho:रखो rakhta:रखता rakhunga:रखूँगा rakhiye:रखिए
rakhne:रखने rakhna:रखना
de:दे diya:दिया di:दी diye:दिए dena:देना dunga:दूँगा dungi:दूँगी dijiye:दीजिए dega:देगा
le:ले lo:लो liya:लिया li:ली lena:लेना lunga:लूँगा lijiye:लीजिए
ja:जा jao:जाओ jaa:जा gaya:गया gayi:गई gaye:गए jaana:जाना jana:जाना jayega:जाएगा jaayega:जाएगा
aa:आ aao:आओ aaya:आया aayi:आई aaye:आए aana:आना aaunga:आऊँगा
bhej:भेज bhejo:भेजो bhejun:भेजूँ bheja:भेजा bheji:भेजी bhejna:भेजना bhejta:भेजता
khol:खोल kholo:खोलो khola:खोला kholta:खोलता kholna:खोलना khul:खुल khula:खुला khuli:खुली khule:खुले
band:बंद chalu:चालू chal:चल chala:चला chalo:चलो chalao:चलाओ chalata:चलाता chalane:चलाने chalega:चलेगा
bol:बोल bolo:बोलो boliye:बोलिए bolunga:बोलूँगा bolenge:बोलेंगे bola:बोला bolna:बोलना
bata:बता batao:बताओ bataiye:बताइए bataunga:बताऊँगा bataya:बताया batana:बताना
dekh:देख dekho:देखो dekhta:देखता dekhiye:देखिए dekha:देखा dikha:दिखा dikhao:दिखाओ dikhaiye:दिखाइए
sun:सुन suno:सुनो suniye:सुनिए sununga:सुनूँगा suna:सुना sunao:सुनाओ
samajh:समझ samjha:समझा samjho:समझो padh:पढ़ padho:पढ़ो likh:लिख likho:लिखो likha:लिखा
mil:मिल mila:मिला mili:मिली mile:मिले milega:मिलेगा milte:मिलते
paaya:पाया paya:पाया payi:पाई sakta:सकता sakti:सकती sakte:सकते
chahiye:चाहिए chahte:चाहते chahta:चाहता chahti:चाहती
laga:लगा lagta:लगता lagao:लगाओ lagega:लगेगा hata:हटा hatao:हटाओ ruk:रुक ruko:रुको rok:रोक roko:रोको
bajao:बजाओ bajana:बजाना banao:बनाओ banata:बनाता banaya:बनाया dhoondh:ढूँढ dhoondo:ढूँढो dhundo:ढूँढो
dhoondha:ढूँढा bhool:भूल bhoolo:भूलो yaad:याद dila:दिला dilana:दिलाना ghumane:घुमाने daba:दबा dabaiye:दबाइए
padega:पड़ेगा khud:ख़ुद
naam:नाम baat:बात jawab:जवाब sawaal:सवाल awaaz:आवाज़ bhasha:भाषा jagah:जगह ghar:घर shehar:शहर
mausam:मौसम baarish:बारिश garmi:गर्मी sardi:सर्दी dhoop:धूप hawa:हवा paani:पानी khana:खाना chai:चाय
cheez:चीज़ cheezein:चीज़ें jaankari:जानकारी baare:बारे zaroorat:ज़रूरत madad:मदद galti:ग़लती dikkat:दिक्कत
khabar:ख़बर khabrein:ख़बरें duniya:दुनिया desh:देश log:लोग rajdhani:राजधानी
agla:अगला agle:अगले pichla:पिछला lagbhag:लगभग saare:सारे usi:उसी isi:इसी jis:जिस jo:जो wahan:वहाँ
yahan:यहाँ idhar:इधर udhar:उधर aage:आगे peeche:पीछे
shayad:शायद pata:पता matlab:मतलब sach:सच khush:ख़ुश kha:खा khaa:खा khaya:खाया pi:पी piyo:पियो soch:सोच socho:सोचो
chahe:चाहे mauka:मौक़ा kaafi:काफ़ी kafi:काफ़ी zaruri:ज़रूरी jaruri:ज़रूरी taiyaar:तैयार tayyar:तैयार hisaab:हिसाब
tarah:तरह wala:वाला wali:वाली wale:वाले sirji:सर_जी bataya:बताया kahin:कहीं kabhi:कभी hamesha:हमेशा
""".split()

WORDS: dict[str, str] = {k: v.replace("_", " ") for k, v in (w.split(":", 1) for w in _WORDS)}  # "_" is a space
# Also English words ("to Rahul", "for me", "do it"): spoken as Hindi only next to another Hindi word.
# "hi" isn't in the dictionary at all: in "Omi ko hi bhej diya" it's the greeting being sent.
_AMBIGUOUS = {"to", "me", "the", "do", "use", "door", "log", "par", "band", "sun", "tab", "jab", "char"}
# Endings only Hindi words have: "karega", "bataiye", "sunenge"
_HINDI_ENDING = re.compile(r"(?:enge|engi|ega|egi|unga|ungi|iye|iyega|wala|wali|wale|karke|aiye|oge|ogi)$")

# ---- spelling out an unknown Hindi word (longest match first) -----------------------------------------------
_CONS = [("chh", "छ"), ("ksh", "क्ष"), ("kh", "ख"), ("gh", "घ"), ("ch", "च"), ("jh", "झ"), ("th", "थ"), ("dh", "ध"),
         ("ph", "फ"), ("bh", "भ"), ("sh", "श"), ("k", "क"), ("g", "ग"), ("c", "क"), ("j", "ज"), ("t", "त"),
         ("d", "द"), ("n", "न"), ("p", "प"), ("b", "ब"), ("m", "म"), ("y", "य"), ("r", "र"), ("l", "ल"), ("v", "व"),
         ("w", "व"), ("s", "स"), ("h", "ह"), ("f", "फ़"), ("z", "ज़"), ("q", "क़"), ("x", "क्स")]
_VOW = [("aa", "आ", "ा"), ("ai", "ऐ", "ै"), ("au", "औ", "ौ"), ("ee", "ई", "ी"), ("ii", "ई", "ी"), ("oo", "ऊ", "ू"),
        ("uu", "ऊ", "ू"), ("a", "अ", ""), ("i", "इ", "ि"), ("u", "उ", "ु"), ("e", "ए", "े"), ("o", "ओ", "ो")]


def spell(word: str) -> str:
    """Roman Hindi -> Devanagari by sound ("sunenge" -> सुनेन्गे, said the same as सुनेंगे). Retroflex sounds can't
    be told from Latin letters, so the dictionary carries the common words; this only covers the rest."""
    w, out, i, after_cons = word.lower(), [], 0, False
    while i < len(w):
        for lat, dev in _CONS:
            if w.startswith(lat, i):
                if after_cons:
                    out.append("्")  # two consonants in a row join
                out.append(dev)
                i, after_cons = i + len(lat), True
                break
        else:
            for lat, full, matra in _VOW:
                if w.startswith(lat, i):
                    end = i + len(lat) == len(w)
                    if after_cons:  # a final "a"/"i" in Hinglish is long: "raha" रहा, "nahi" नहीं
                        out.append({"a": "ा", "i": "ी"}.get(lat, matra) if end else matra)
                    else:
                        out.append(full)
                    i, after_cons = i + len(lat), False
                    break
            else:
                out.append(w[i])
                i, after_cons = i + 1, False
    if len(out) > 2 and out[-1] == "न" and out[-2] in ("ा", "ी", "ू", "े", "ै", "ो"):
        out[-1] = "ं"  # "hain", "sakein": a nasal vowel, not an n
    return "".join(out)


_TOKEN = re.compile(r"[A-Za-z']+|[^A-Za-z']+")


def _hindi(word: str) -> str | None:
    w = word.lower().strip("'")
    if w in WORDS:
        return WORDS[w]
    if len(w) > 4 and _HINDI_ENDING.search(w):
        return spell(w)
    return None


def is_hindi(text: str) -> bool:
    """Devanagari, or Hinglish: enough everyday Hindi words ("Theek hai", "Main PLAG hoon")."""
    if re.search(r"[ऀ-ॿ]", text):
        return True
    words = re.findall(r"[A-Za-z']+", text)
    hindi = sum(1 for w in words if w.lower() not in _AMBIGUOUS and _hindi(w))
    return hindi >= 2 or (hindi >= 1 and hindi / max(1, len(words)) >= 0.3)


def for_voice(text: str) -> str:
    """The same sentence with its Hindi words in Devanagari, for the Hindi voice. English words and names stay."""
    tokens = _TOKEN.findall(text)
    words = [i for i, t in enumerate(tokens) if t[0].isalpha() or t[0] == "'"]
    known = {i: _hindi(tokens[i]) for i in words}

    def plain_hindi(n: int) -> bool:
        return 0 <= n < len(words) and known[words[n]] is not None and tokens[words[n]].lower() not in _AMBIGUOUS

    for n, i in enumerate(words):
        if known[i] is None:
            continue
        if tokens[i].lower() in _AMBIGUOUS and not (plain_hindi(n - 1) or plain_hindi(n + 1)):
            continue  # "to Rahul" stays English; "kar do", "band kar", "Delhi me baarish" are Hindi
        tokens[i] = known[i]
    return "".join(tokens)


# ---------------------------------------------------------------- Devanagari -> Hinglish (what PLAG hears)

def _norm(dev: str) -> str:
    return dev.replace("ँ", "ं").replace("़", "")  # हूँ/हूं and ज़/ज are written both ways


# the first spelling in the dictionary is the usual one: हाँ -> "haan", नहीं -> "nahi", में -> "mein"
_LATIN: dict[str, str] = {}
for _lat, _dev in WORDS.items():
    if " " not in _dev:
        _LATIN.setdefault(_norm(_dev), _lat)
_LATIN.update({_norm(k): v for k, v in {"कि": "ki", "है": "hai", "हैं": "hain", "हूं": "hoon", "यह": "yeh", "वह": "woh",
                                         "ने": "ne", "पर": "par", "तो": "to", "दो": "do", "थे": "the", "कर": "kar"}.items()})
# English the hearing wrote in Devanagari (the words PLAG's commands use most)
_LOAN = {
    "मैसेज": "message", "मेसेज": "message", "मिनट": "minute", "मिनिट": "minute", "सेकंड": "second", "यूट्यूब": "YouTube",
    "व्हाट्सएप": "WhatsApp", "व्हाट्सऐप": "WhatsApp", "वॉट्सऐप": "WhatsApp", "वाट्सएप": "WhatsApp", "गूगल": "Google",
    "कॉल": "call", "काल": "call", "वीडियो": "video", "विडियो": "video", "फोन": "phone", "फ़ोन": "phone", "लैपटॉप": "laptop",
    "म्यूजिक": "music", "म्यूज़िक": "music", "सॉन्ग": "song", "सॉन्ग्स": "songs", "गाना": "gaana", "गाने": "gaane",
    "ओपन": "open", "क्लोज": "close", "सर्च": "search", "प्ले": "play", "स्टॉप": "stop", "टाइम": "time", "वेदर": "weather",
    "रिपोर्ट": "report", "न्यूज": "news", "न्यूज़": "news", "फोटो": "photo", "फ़ोटो": "photo", "इमेज": "image",
    "पिक्चर": "picture", "फाइल": "file", "फ़ाइल": "file", "फोल्डर": "folder", "फ़ोल्डर": "folder", "डेस्कटॉप": "desktop",
    "डाउनलोड": "download", "डाउनलोड्स": "downloads", "ईमेल": "email", "मेल": "mail", "जीमेल": "Gmail", "कैलेंडर": "calendar",
    "रिमाइंडर": "reminder", "अलार्म": "alarm", "सेटिंग": "setting", "सेटिंग्स": "settings", "ब्राउज़र": "browser",
    "क्रोम": "Chrome", "एप": "app", "ऐप": "app", "एप्प": "app", "नोटपैड": "Notepad", "कैमरा": "camera", "मॉडल": "model",
    "थ्रीडी": "3D", "प्लग": "PLAG", "प्लैग": "PLAG", "फ्लैग": "PLAG", "ब्रो": "bro", "सर": "sir", "ओके": "okay",
    "थैंक": "thank", "थैंक्स": "thanks", "यू": "you", "सॉरी": "sorry", "प्लीज": "please", "प्लीज़": "please", "हाय": "hi",
    "हेलो": "hello", "हैलो": "hello", "गुड": "good", "मॉर्निंग": "morning", "नाइट": "night", "बाय": "bye", "लेट": "late",
    "टेस्ट": "test", "पीडीएफ": "PDF", "एस्से": "essay", "सिस्टम": "system", "स्लो": "slow", "रैम": "RAM", "बैटरी": "battery",
    "इंटरनेट": "internet", "वाईफाई": "WiFi", "नंबर": "number", "कॉन्टैक्ट": "contact", "चैट": "chat", "ग्रुप": "group",
    "न्यू": "new", "नेक्स्ट": "next", "लास्ट": "last", "फर्स्ट": "first", "सेकंड": "second", "स्क्रीन": "screen",
    "वॉल्यूम": "volume", "साउंड": "sound", "मैप": "map", "लोकेशन": "location", "एड्रेस": "address", "ऑफिस": "office",
    "कॉलेज": "college", "स्कूल": "school", "क्लास": "class", "मीटिंग": "meeting", "प्रोजेक्ट": "project",
    "सिंह": "Singh", "दिल्ली": "Delhi", "मुंबई": "Mumbai", "बैंगलोर": "Bangalore", "बेंगलुरु": "Bengaluru",
}
_CONS_L = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
           "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
           "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
           "ष": "sh", "स": "s", "ह": "h", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f"}
_VOW_L = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
          "ऋ": "ri", "ऑ": "o", "ऍ": "e"}
_MATRA_L = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ृ": "ri",
            "ॉ": "o", "ॅ": "e"}


def _spell_latin(word: str) -> str:
    """Devanagari -> Hinglish by sound, dropping the silent "a" the way Hindi speakers do: करना -> "karna",
    समझ -> "samajh", मिलेगा -> "milega"."""
    syl: list[list[str]] = []  # [consonant(s), vowel] pairs; vowel "a?" is an inherent a that may be silent
    i = 0
    while i < len(word):
        ch = word[i]
        nxt = word[i + 1] if i + 1 < len(word) else ""
        if ch in _CONS_L or (ch + nxt) in _CONS_L:
            cons = _CONS_L.get(ch + nxt) if nxt == "़" else None
            if cons:
                i += 1
            else:
                cons = _CONS_L[ch]
            i += 1
            nxt = word[i] if i < len(word) else ""
            if nxt == "्":  # half letter: joins the next consonant
                syl.append([cons, ""])
                i += 1
            elif nxt in _MATRA_L:
                syl.append([cons, _MATRA_L[nxt]])
                i += 1
            else:
                syl.append([cons, "a?"])
        elif ch in _VOW_L:
            syl.append(["", _VOW_L[ch]])
            i += 1
        elif ch in "ंँ":
            if syl:
                syl[-1][1] = (syl[-1][1] if syl[-1][1] != "a?" else "a") + "n"
            i += 1
        elif ch == "ः":
            if syl:
                syl[-1][1] += "h"
            i += 1
        else:
            syl.append([ch, ""])
            i += 1
    # the silent a: at the end of a word, and between two sounded syllables (कर-ना, सम-झ)
    if syl and syl[-1][1] == "a?":
        syl[-1][1] = ""
    for k in range(len(syl) - 2, 0, -1):
        if syl[k][1] == "a?" and syl[k - 1][1] not in ("", "a?") and syl[k + 1][1] not in ("",):
            syl[k][1] = ""
    out = "".join(c + (v if v != "a?" else "a") for c, v in syl)
    # Hinglish spells a long final vowel short: "karnaa" -> "karna", "nahee" -> "nahi", "tu" stays "tu"
    out = re.sub(r"aa$", "a", out)
    out = re.sub(r"ee$", "i", out)
    return out


def to_latin(text: str) -> str:
    """Devanagari (as NVIDIA's hearing writes Hindi) -> Hinglish in Latin letters. Latin text passes through."""
    if not re.search(r"[ऀ-ॿ]", text or ""):
        return text

    def word(m: re.Match) -> str:
        w = m.group(0)
        key = _norm(w)
        return _LOAN.get(w) or _LOAN.get(key) or _LATIN.get(key) or _spell_latin(w)

    out = re.sub(r"[ऀ-ॣ०-९ॱ-ॿ]+", word, text).replace("।", ".").replace("॥", ".")
    return re.sub(r"\s+([.,!?])", r"\1", out).strip()
