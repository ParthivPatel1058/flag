"""End-to-end check of every PLAG core function, safely.

Runs the real API in dry-run mode (PLAG_DRY_RUN=1): commands are understood, permission-checked, audited and
answered, but nothing opens and no WhatsApp message is sent (WhatsApp isn't even brought forward).

    cd core && uv run python tests/check_all.py
"""

import os
import sys
import tempfile
import time as time_mod
from datetime import datetime, timedelta
from pathlib import Path

os.environ["PLAG_DRY_RUN"] = "1"
os.environ["PLAG_TOKEN"] = "check-all-token"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from plag_core import agent as agent_mod_early  # noqa: E402
from plag_core import memory, wake, whatsapp  # noqa: E402

memory.DB_PATH = Path(tempfile.mkdtemp()) / "plag.db"  # never touch the real memories and reminders
whatsapp.ALIASES = Path(tempfile.mkdtemp()) / "whatsapp_names.json"  # nor the names you've used ("which Rahul?" saves one)

from plag_core import elevenlabs as _eleven_mod  # noqa: E402
from plag_core import settings as _settings_mod  # noqa: E402

_settings_mod.PATH = Path(tempfile.mkdtemp()) / "settings.json"  # the Voice switch tests change settings: not yours
_settings_mod._cache = None
_eleven_mod.STATE = Path(tempfile.mkdtemp()) / "elevenlabs_state.json"

from plag_core.api import app  # noqa: E402
from plag_core.edgevoice import edgevoice  # noqa: E402
from plag_core.nvasr import nvhearing  # noqa: E402
from plag_core.nvspeech import nvspeech  # noqa: E402
from plag_core.sarvam import sarvam  # noqa: E402

# no network voices or hearing unless a test brings in a stand-in (the real ones would make every run slow and flaky)
for _cloud in (nvhearing, nvspeech, edgevoice, sarvam):
    _cloud.usable = lambda: False

H = {"X-PLAG-Token": "check-all-token"}
passed, failed = 0, []


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed
    if ok:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name}  {detail}")


def turn(c: TestClient, text: str, **extra) -> dict:
    r = c.post("/v1/turn/text", headers=H, json={"text": text, "lang": extra.pop("lang", "auto"), **extra})
    return r.json()


def acts(res: dict) -> str:
    return res.get("action", {}).get("type", "?")


with TestClient(app) as c:
    print("security")
    check("no token is refused", c.get("/v1/health").status_code == 401)
    check("foreign website is refused", c.get("/v1/health", headers={**H, "Origin": "https://evil.example"}).status_code == 403)
    check("health", c.get("/v1/health", headers=H).json().get("ok") is True)

    print("open and search (fast path, English / Hinglish / Hindi)")
    cases = [
        ("open notepad", "open_app"), ("notepad kholo", "open_app"), ("यूट्यूब खोलो", "open_url"),
        ("open github.com", "open_url"), ("open whatsapp", "open_app"), ("open calculator", "open_app"),
        ("search NVIDIA news", "web_search"), ("search NVIDIA news in chrome", "web_search"),
        ("chrome mein cricket score search karo", "web_search"), ("search youtube for cat videos", "web_search"),
        ("play arijit singh songs", "play_youtube"), ("kesariya bajao", "play_youtube"),
        ("youtube pe lo-fi chalao", "play_youtube"), ("what time is it", "time"), ("कितने बजे हैं", "time"),
        ("why is my laptop slow", "system_status"), ("mera laptop slow kyun hai", "system_status"),
    ]
    for text, want in cases:
        res = turn(c, text)
        check(f"{text!r} -> {want}", acts(res) == want and res.get("model") == "fast path" and bool(res.get("reply")),
              f"got {acts(res)} / {res.get('model')} / {res.get('reply')!r}")
    res = turn(c, "search NVIDIA news in chrome")
    check("chrome search really targets Chrome", res["action"].get("browser") == "chrome", str(res["action"]))

    print("compound commands")
    res = turn(c, "open Google, open YouTube and open WhatsApp")
    check("three opens in one go", acts(res) == "multi" and "YouTube" in res["reply"] and "WhatsApp" in res["reply"], res.get("reply"))
    res = turn(c, "open google and search anything")
    check("open Google + search = one search", acts(res) == "web_search", acts(res))
    res = turn(c, "google kholo aur nvidia news search karo")
    check("Hinglish open + search = one search", acts(res) == "web_search" and res["model"] == "fast path", acts(res))
    res = turn(c, "youtube kholo aur kesariya chalao")
    check("open YouTube + play = one play", acts(res) == "play_youtube", acts(res))

    print("small talk (local, works when Gemini is down)")
    for text in ["hi", "hello", "namaste", "how are you", "kaise ho", "thank you", "who are you", "what can you do", "bye"]:
        res = turn(c, text)
        check(f"{text!r} answered locally", res.get("model") == "fast path" and bool(res.get("reply")) and "error" not in res,
              f"{res.get('model')} {res.get('reply')!r} {res.get('error')}")

    print("dashboard and camera by voice")
    for text, cmd in [("mute", "mute"), ("hindi mein bolo", "lang_hi"), ("speak english", "lang_en"), ("camera kholo", "camera_on"),
                      ("close camera", "camera_off"), ("stop", "stop")]:
        res = turn(c, text)
        check(f"{text!r} -> ui {cmd}", (res.get("client") or {}).get("command") == cmd, str(res.get("client")))
    res = turn(c, "yeh kya hai")
    check("'yeh kya hai' -> camera look", (res.get("client") or {}).get("type") == "camera_look", str(res.get("client")))

    print("plans: compound commands, the app you're in, Gmail vs Google (dry run)")
    plan_cases = [
        ("Open Google and search for AI news", ["web_search"], {"site": "google", "query": "ai news"}),
        ("Open YouTube and search for Honey Singh", ["web_search"], {"site": "youtube", "query": "honey singh"}),
        ("Open WhatsApp and search Priyans", ["whatsapp_open"], {"contact": "Priyans"}),
        ("Open WhatsApp and open the chat with Priyans", ["whatsapp_open"], {"contact": "Priyans"}),
        ("Open WhatsApp and message Priyans saying I'll call you later", ["whatsapp_send"], {"contact": "Priyans"}),
        ("Search Gmail for BhoomiX", ["gmail_search"], {"query": "BhoomiX"}),
        ("Open Google and search AI, then open YouTube", ["web_search", "open_url"], None),
        ("Google kholo aur AI ki latest news search karo", ["web_search"], {"site": "google"}),
    ]
    for text, want, args in plan_cases:
        res = turn(c, text)
        got = [s.strip() for s in res["action"].get("label", "").split(",")] if acts(res) == "multi" else [acts(res)]
        a = res.get("action", {})
        ok = (acts(res) == "multi" and len(want) > 1) or (acts(res) == want[0] and all(a.get(k) == v for k, v in (args or {}).items()))
        check(f"plan: {text!r}", ok and res["model"] == "fast path", f"{acts(res)} {a} {got}")
    turn(c, "open youtube")
    res = turn(c, "search honey singh")
    check("follow-up: after YouTube, 'search X' searches YouTube", res["action"].get("site") == "youtube", str(res["action"]))
    turn(c, "open whatsapp")
    res = turn(c, "search NVIDIA news")
    check("WhatsApp doesn't carry over: a later search is a web search", res["action"].get("site") == "google", str(res["action"]))

    print("WhatsApp (anyone, no saved contacts, no confirmation; dry run sends nothing)")
    wa_cases = [
        ("send hi to Rahul from WhatsApp", "Rahul", "hi"),
        ("send a message to Test Person saying hello from PLAG", "Test Person", "hello from PLAG"),
        ("send msg to Priya on whatsapp that the meeting is at 5", "Priya", "the meeting is at 5"),
        ("send good morning message to my mom on whatsapp", "mom", "good morning"),
        ("Rahul ko whatsapp pe message bhejo ki main aa raha hoon", "Rahul", "main aa raha hoon"),
        ("send hi to 98765 43210 on whatsapp", "98765 43210", "hi"),
    ]
    for text, who, what in wa_cases:
        res = turn(c, text)
        a = res.get("action", {})
        check(f"{text!r} sends straight away", acts(res) == "whatsapp_send" and a.get("contact") == who and a.get("message") == what
              and not res.get("approval") and (res.get("result") or {}).get("ok") is True
              and (f"Sent to {who}" in res.get("reply", "") or f"{who} ko bhej diya" in res.get("reply", "")),
              f"{a} / {res.get('reply')!r}")
    for text, who, video in [("call Rahul on WhatsApp", "Rahul", False), ("mummy ko video call karo", "mummy", True),
                             ("video call Priya", "Priya", True)]:
        res = turn(c, text)
        a = res.get("action", {})
        check(f"{text!r} calls straight away", acts(res) == "whatsapp_call" and a.get("contact") == who and a.get("video") is video
              and (res.get("result") or {}).get("ok") is True and not res.get("approval"), f"{a} / {res.get('reply')!r}")
    from plag_core.agent import agent as the_agent
    import time as _time
    for said, want in [("Rahul Verma", "Rahul Verma"), ("the second one", "Rahul Verma"), ("dusra", "Rahul Verma"), ("Sharma", "Rahul Sharma")]:
        the_agent.pending_choice = {"action": "whatsapp_send", "args": {"contact": "Rahul", "message": "hi"},
                                    "options": ["Rahul Sharma", "Rahul Verma"], "at": _time.monotonic()}
        res = turn(c, said)
        check(f"'which Rahul?' answered with {said!r}", (res.get("action") or {}).get("contact") == want, str(res.get("action")))
    the_agent.pending_choice = {"action": "whatsapp_send", "args": {"contact": "Rahul", "message": "hi"},
                                "options": ["Rahul Sharma", "Rahul Verma"], "at": _time.monotonic()}
    check("a new request isn't taken as the answer", acts(turn(c, "open notepad")) == "open_app")
    check("whole names are exact", whatsapp.exact("rahul sharma", "Rahul Sharma") and not whatsapp.exact("rahul", "Rahul Sharma")
          and whatsapp.exact("rahul", "Rahul Bhai"))
    res = turn(c, "send Test Person a whatsapp saying hi, and open youtube")
    check("WhatsApp + open in one sentence", acts(res) == "multi" and "Test Person" in res.get("reply", ""), res.get("reply"))
    check("contacts API is gone", c.get("/v1/contacts", headers=H).status_code == 404)
    check("name matching", whatsapp.score("rahul", "Rahul Sharma") == 1.0 and whatsapp.score("my mom", "Mom") == 1.0
          and whatsapp.score("priya", "Priyanka") < whatsapp.MATCH and whatsapp.score("rahul", "Rohit") < whatsapp.MATCH)
    check("phone numbers", whatsapp._as_phone("98765 43210") == "919876543210" and whatsapp._as_phone("Rahul") is None)

    print("memory and reminders (a throwaway database)")
    res = turn(c, "remember that my bike service is on Friday")
    check("remember", "remember" in res.get("reply", "").lower() and res["model"] == "fast path", res.get("reply"))
    res = turn(c, "remember my wifi password is abc123")
    check("passwords are refused", (res.get("result") or {}).get("ok") is False and "password" in res.get("reply", "").lower(), res.get("reply"))
    check("only the real memory was kept", [m["text"] for m in c.get("/v1/memory", headers=H).json()["memories"]] == ["my bike service is on Friday"])
    res = turn(c, "what do you remember")
    check("recall", "bike service" in res.get("reply", ""), res.get("reply"))
    res = turn(c, "forget that")
    check("forget that", "Forgotten" in res.get("reply", "") and c.get("/v1/memory", headers=H).json()["memories"] == [], res.get("reply"))
    res = turn(c, "remind me in 10 minutes to drink water")
    check("set a reminder", "drink water" in res.get("reply", "") and "10 minutes" in res.get("reply", ""), res.get("reply"))
    res = turn(c, "kal subah 8 baje test yaad dilana")
    check("Hinglish reminder", "test" in res.get("reply", "") and "kal" in res.get("reply", "").lower(), res.get("reply"))
    check("reminders listed", len(c.get("/v1/reminders", headers=H).json()["reminders"]) == 2)
    res = turn(c, "cancel the reminder about water")
    check("cancel a reminder", "drink water" in res.get("reply", "") and len(c.get("/v1/reminders", headers=H).json()["reminders"]) == 1, res.get("reply"))
    rid = c.get("/v1/reminders", headers=H).json()["reminders"][0]["id"]
    check("cancel from the dashboard", c.delete(f"/v1/reminders/{rid}", headers=H).json()["ok"] is True)
    now = datetime.now()
    memory.add_reminder("due one", now - timedelta(minutes=1))
    memory.add_reminder("stale one", now - timedelta(hours=13))
    due = memory.take_due()
    check("due reminders go off, stale ones don't", [d["text"] for d in due] == ["due one"], str(due))
    check("nothing goes off twice", memory.take_due() == [])

    print("Gmail + Calendar (read-only; nothing is saved to Credential Manager here)")
    from plag_core.google import google as g
    if not g.connected():  # on this laptop Google isn't connected yet: PLAG must say so, not pretend
        for text, want in [("check my important emails", "gmail_check"), ("kal ka schedule batao", "calendar_check")]:
            res = turn(c, text)
            check(f"{text!r} -> {want}, honest when not connected", acts(res) == want and (res.get("result") or {}).get("ok") is False
                  and "Connect" in res.get("reply", ""), res.get("reply"))
    r = c.post("/v1/google/client", headers=H, json={"file": '{"web": {"client_id": "x.apps.googleusercontent.com"}}'})
    check("a Web client file is refused", r.status_code == 422 and "Desktop" in r.json().get("message", ""), r.text[:100])
    r = c.post("/v1/google/client", headers=H, json={"file": "this is not the client file at all"})
    check("a non-JSON file is refused", r.status_code == 422)
    r = c.get("/oauth/google?state=forged&code=abc")
    check("sign-in with a forged state is refused", r.status_code == 400 and "expired" in r.text)
    real_client = g.client
    g.client = lambda: {"client_id": "test.apps.googleusercontent.com", "client_secret": "test"}  # in memory only
    try:
        url = g.begin("http://127.0.0.1:1234/")
        check("sign-in uses PKCE and read-only scopes", "code_challenge_method=S256" in url and "gmail.readonly" in url
              and "calendar.readonly" in url and "gmail.send" not in url and "oauth%2Fgoogle" in url, url[:80])
    finally:
        g.client = real_client
    rows = {x["id"]: x for x in c.get("/v1/connectors", headers=H).json()}
    check("Google row with a Connect button", rows.get("google", {}).get("action") in ("connect", "disconnect"), str(rows.get("google")))

    print("image generation (dry run: no NVIDIA call)")
    for text, prompt, aspect in [("gen an image of a cat astronaut floating in space", "a cat astronaut floating in space", "1:1"),
                                 ("make a wallpaper of a neon Tokyo street at night", "a wallpaper of a neon Tokyo street at night", "16:9"),
                                 ("draw a tiger in the snow", "a tiger in the snow", "1:1")]:
        res = turn(c, text)
        a = res.get("action", {})
        check(f"{text!r} draws", acts(res) == "generate_image" and a.get("prompt") == prompt and a.get("aspect") == aspect
              and (res.get("result") or {}).get("ok") is True and res["model"] == "fast path", f"{a} / {res.get('reply')!r}")
    res = turn(c, "gen an image of this in anime style")
    check("'an image of this' uses the camera", (res.get("client") or {}) == {"type": "camera_imagine", "style": "in anime style"},
          str(res.get("client")))
    check("unknown image id", c.get("/v1/images/0123456789", headers=H).status_code == 404
          and c.get("/v1/images/..%2F..%2Fsecret", headers=H).status_code == 404)
    check("imagine needs a real JPEG", c.post("/v1/imagine", headers=H, json={"image": "x" * 200}).status_code == 415)
    check("Image generation row", any(x["id"] == "images" for x in c.get("/v1/connectors", headers=H).json()))

    print("settings, ElevenLabs (nothing saved to Credential Manager here), research")
    from plag_core import settings as app_settings
    app_settings.PATH = Path(tempfile.mkdtemp()) / "settings.json"
    app_settings._cache = None  # the throwaway file, not your real settings
    r = c.post("/v1/settings", headers=H, json={"changes": {"turn": "patient", "wake_sensitivity": "loud", "speed": 5, "eleven_model": "x"}}).json()
    s = r["settings"]
    check("settings: valid changes kept, invalid ones ignored", s["turn"] == "patient" and s["wake_sensitivity"] == "normal"
          and s["speed"] == 1.2 and s["eleven_model"] == "eleven_flash_v2_5", str(s))
    check("the listener follows the pause setting", wake.wake.pause_s == 1.2)
    c.post("/v1/settings", headers=H, json={"changes": {"turn": "normal"}})
    from plag_core.elevenlabs import eleven as el
    if not el.configured():
        check("ElevenLabs shows as not configured", r["eleven"]["configured"] is False
              and next(x for x in c.get("/v1/connectors", headers=H).json() if x["id"] == "elevenlabs")["state"] == "off")
        tts = c.post("/v1/tts", headers=H, json={"text": "On it.", "mood": "calm"})
        check("without ElevenLabs, PLAG's voice is the local one", tts.status_code == 200 and tts.headers.get("x-plag-voice") == "kokoro",
              f"{tts.status_code} {tts.headers.get('x-plag-voice')}")
        bad = c.post("/v1/elevenlabs/key", headers=H, json={"key": "sk_" + "0" * 40})
        check("a wrong ElevenLabs key is refused and not kept", bad.status_code == 422 and not el.configured(), bad.text[:120])
    res = turn(c, "research the latest AI agriculture news, compare the developments and save the report")
    check("research is its own step", acts(res) == "research" and res["action"].get("topic") == "the latest AI agriculture news",
          str(res["action"]))
    check("wake look-alikes need a real command", wake.is_stop("stop") and wake.split_wake("Plug in the charger")[0])

    print("conversation without \"PLAG\": answers and follow-ups (dry run)")
    res = turn(c, "send a message to Rahul on WhatsApp")
    check("who but not what: PLAG asks, and listens for the answer", acts(res) == "whatsapp_send" and res.get("expects_reply") is True
          and "What should I send to Rahul" in res.get("reply", "") and (res.get("result") or {}).get("state") == "user_required",
          f"{res.get('action')} / {res.get('reply')!r}")
    res = turn(c, "tell him I'll be late", heard_by="ElevenLabs realtime", followup="answer")
    a = res.get("action", {})
    check("the answer is the message (never the word 'a')", acts(res) == "whatsapp_send" and a.get("contact") == "Rahul"
          and a.get("message") == "I'll be late" and "Sent to Rahul" in res.get("reply", ""), f"{a} / {res.get('reply')!r}")
    a = turn(c, "send msg to omi bro bangalore hi").get("action", {})
    check("a greeting at the end is the message, not the name", a.get("contact") == "omi bro bangalore" and a.get("message") == "hi", str(a))
    res = turn(c, "send hi message to omi bro shotu bangalore")  # 2026-09-24: the AI said "bhej raha hoon" and sent nothing
    a = res.get("action", {})
    check("'send hi message to X' sends without saying WhatsApp", acts(res) == "whatsapp_send" and a.get("contact") == "omi bro shotu bangalore"
          and a.get("message") == "hi" and res["model"] == "fast path" and "Sent to" in res.get("reply", ""), f"{a} / {res.get('reply')!r}")
    a = turn(c, "send good morning to mom").get("action", {})
    check("'send good morning to mom'", a.get("contact") == "mom" and a.get("message") == "good morning", str(a))
    check("'send the report to Rahul' isn't taken as a greeting", turn(c, "send the report to Rahul").get("model") != "fast path")
    turn(c, "send a message to Rahul")
    a = turn(c, "omi ko bol do main aa raha hoon", followup="answer").get("action", {})
    check("after 'What should I send to Rahul?', a full send to Omi goes to Omi", a.get("contact") == "omi", str(a))
    for said, who, what in [("omi ko bol do main 10 min mein aa raha hoon", "omi", "main 10 min mein aa raha hoon"),
                            ("mummy ko good night bhej do", "mummy", "good night"), ("tell omi that I'm coming", "omi", "I'm coming")]:
        a = turn(c, said).get("action", {})
        check(f"Hinglish/English without 'WhatsApp': {said!r}", a.get("contact") == who and a.get("message") == what, str(a))
    check("'Rahul ko photo bhej do' isn't sent as the text 'photo'", turn(c, "Rahul ko photo bhej do").get("action", {}).get("message") != "photo")
    nested = agent_mod_early._intents_from_model({"language": "mixed", "reply": "Omi ko message bhej raha hoon.", "actions": [
        {"type": "whatsapp_send", "action": {"contact": "Omi", "message": "Main 10 min mein aa raha hoon"}}]})
    check("Gemma's nested answer is read", nested[0].action == "whatsapp_send" and nested[0].args.get("contact") == "Omi", str(nested[0]))
    from plag_core.agent import agent as _agent  # noqa: E402

    async def _claims_but_does_nothing(*_a, **_k):
        return "stub", {"transcript": "do the thing for omi", "language": "mixed", "actions": [{"type": "none"}],
                        "reply": "Omi bro shotu bangalore ko hi bhej raha hoon.", "mood": "calm"}, 5

    async def _answers_a_fact(*_a, **_k):
        return "stub", {"transcript": "who sent the first email", "language": "en", "actions": [{"type": "none"}],
                        "reply": "Ray Tomlinson sent the first email in 1971.", "mood": "calm"}, 5

    real_think = _agent._think
    _agent.pending_ask = None  # an earlier AI question ("What should I send to Rahul?") would take the next words
    try:
        _agent._think = _claims_but_does_nothing
        res = turn(c, "do the thing for omi")
        check("the AI can't claim a send it didn't do", "bhej raha" not in res.get("reply", "") and "kuch kiya nahi" in res.get("reply", "").lower()
              and acts(res) != "whatsapp_send", repr(res.get("reply")))
        _agent._think = _answers_a_fact
        check("a factual answer isn't mistaken for a claim", "Ray Tomlinson" in turn(c, "who sent the first email").get("reply", ""))
    finally:
        _agent._think = real_think

    print("voices (NVIDIA, Sarvam, Edge stand-ins: no network)")
    from plag_core import hinglish  # noqa: E402
    from plag_core import settings as app_settings  # noqa: E402
    from plag_core.fastpath import parse_many  # noqa: E402
    from plag_core.sarvam import SarvamError  # noqa: E402
    check("Hinglish is spotted, English isn't", hinglish.is_hindi("Theek hai, cancel kar diya.") and hinglish.is_hindi("Main PLAG hoon")
          and not hinglish.is_hindi("Sent to Rahul.") and not hinglish.is_hindi("What do you want to do?"))
    v = hinglish.for_voice("Haan bro, Omi ko hi bhej diya. Aur kuch karna hai?")
    check("Hinglish -> Devanagari for the voice, English words and names kept", v == "हाँ bro, Omi को hi भेज दिया. और कुछ करना है?", v)
    check("'to Rahul' stays English, 'kar do' is Hindi", hinglish.for_voice("Sent to Rahul") == "Sent to Rahul"
          and hinglish.for_voice("band kar do") == "बंद कर दो", hinglish.for_voice("band kar do"))
    heard_back = hinglish.to_latin("ओमी को मैसेज भेज दो कि मैं दस मिनट में आ रहा हूं।")
    check("what NVIDIA hears in Devanagari comes back as Hinglish", heard_back == "omi ko message bhej do ki main das minute mein aa raha hoon.",
          heard_back)
    check("...and runs as a command", [(i.action, i.args.get("contact")) for i in parse_many(heard_back)] == [("whatsapp_send", "omi")])
    check("English passes through untouched", hinglish.to_latin("What is the weather like?") == "What is the weather like?")
    saved = (nvspeech._synth, nvspeech._synth_stream, edgevoice.speak, sarvam.speak)
    heard: list[str] = []
    try:
        nvspeech.usable = lambda: True
        nvspeech._synth = lambda text, *_a: heard.append(text) or b"\x00\x00" * 2205
        nvspeech._synth_stream = lambda text, *_a: (heard.append(text) or piece for piece in [b"\x00\x00" * 2205] * 3)
        r = c.post("/v1/tts", headers=H, json={"text": "Rahul ko kya bhejun, test " + str(time_mod.time()), "mood": "calm"})
        check("Hinglish is spoken by Leo on NVIDIA, in Devanagari", r.status_code == 200
              and r.headers.get("x-plag-voice") == "nvidia-magpie" and heard and "को क्या भेजूँ" in heard[-1], f"{r.headers.get('x-plag-voice')} {heard[-1:]}")
        r = c.post("/v1/tts/stream", headers=H, json={"text": "Theek hai, ho gaya " + str(time_mod.time()), "mood": "calm"})
        check("...and streamed (22 kHz PCM, the first sound in ~0.3 s)", r.status_code == 200 and "rate=22050" in r.headers.get("content-type", "")
              and len(r.content) == 3 * 4410, f"{r.status_code} {r.headers.get('content-type')} {len(r.content)}")
        r = c.post("/v1/tts", headers=H, json={"text": "On it, test " + str(time_mod.time()), "mood": "calm"})
        check("English: the same Leo, in English", r.headers.get("x-plag-voice") == "nvidia-magpie" and not hinglish.is_hindi(heard[-1]),
              f"{r.headers.get('x-plag-voice')} {heard[-1:]}")

        async def _edge(text, mood="calm"):
            return b"ID3edge-mp3"

        async def _sarvam(text, mood="calm"):
            return b"RIFFsarvam-wav"
        edgevoice.usable, edgevoice.speak = (lambda: True), _edge
        sarvam.usable, sarvam.speak = (lambda: True), _sarvam
        app_settings.update({"voice_engine": "edge"})
        r = c.post("/v1/tts", headers=H, json={"text": "Haan bro", "mood": "calm"})
        check("Settings → Edge: Edge speaks (MP3)", r.headers.get("x-plag-voice") == "edge" and "mpeg" in r.headers.get("content-type", ""),
              f"{r.headers.get('x-plag-voice')} {r.headers.get('content-type')}")
        check("Edge doesn't stream: the dashboard voices it phrase by phrase", c.post("/v1/tts/stream", headers=H,
                                                                                      json={"text": "Haan bro", "mood": "calm"}).status_code == 409)
        app_settings.update({"voice_engine": "sarvam"})
        r = c.post("/v1/tts", headers=H, json={"text": "Haan bro", "mood": "calm"})
        check("Settings → Sarvam: Sarvam speaks", r.headers.get("x-plag-voice") == "sarvam", str(r.headers.get("x-plag-voice")))

        async def _sarvam_down(text, mood="calm"):
            raise SarvamError("Sarvam voice error (no_credits).", "no_credits")
        sarvam.speak = _sarvam_down
        r = c.post("/v1/tts", headers=H, json={"text": "Haan bro " + str(time_mod.time()), "mood": "calm"})
        check("Sarvam failing: the next voice speaks at once", r.status_code == 200 and r.headers.get("x-plag-voice") == "nvidia-magpie",
              str(r.headers.get("x-plag-voice")))
        app_settings.update({"voice_engine": "nonsense"})
        check("an unknown voice choice is ignored", app_settings.get()["voice_engine"] == "sarvam")
        app_settings.update({"voice_engine": "auto"})
        check("Auto: Sarvam first once its key works", c.get("/v1/settings", headers=H).json()["live"]["voice"] == "sarvam")

        def _broken(*_a):
            raise RuntimeError("UNAVAILABLE")
        edgevoice.usable = sarvam.usable = (lambda: False)
        nvspeech._synth, nvspeech._rest_until = _broken, 0
        r = c.post("/v1/tts", headers=H, json={"text": "Abhi kuch naya nahi hai " + str(time_mod.time()), "mood": "calm"})
        check("NVIDIA failing: the next voice speaks", r.status_code == 200 and r.headers.get("x-plag-voice") != "nvidia-magpie",
              f"{r.status_code} {r.headers.get('x-plag-voice')}")
    finally:
        nvspeech._synth, nvspeech._synth_stream, edgevoice.speak, sarvam.speak = saved
        for _voice in (nvspeech, edgevoice, sarvam):
            _voice.usable = lambda: False
        nvspeech._rest_until = 0
        app_settings.update({"voice_engine": "auto"})

    print("drafts: PDFs and 3D models aren't saved until you say \"save\"")
    from plag_core import drafts as drafts_mod  # noqa: E402
    drafts_mod.TEMP = Path(tempfile.mkdtemp()) / "PLAG-drafts"
    folder = Path(tempfile.mkdtemp()) / "3D"
    d3 = drafts_mod.add("3d", "a wooden chair", b"glTF-fake", folder, "chair.glb")
    check("a new 3D model is only in memory: nothing on the laptop", not folder.exists() and drafts_mod.latest("3d") is d3)
    r = c.get(f"/v1/models3d/{d3.id}", headers=H)
    check("the dashboard's 3D viewer gets it from memory", r.status_code == 200 and r.content == b"glTF-fake")
    check("'Show in folder' before saving says to save first", c.post(f"/v1/models3d/{d3.id}/reveal", headers=H).status_code == 409)
    res = turn(c, "save the 3d model")
    check("'save the 3D model' (dry run: nothing written)", acts(res) == "save_draft" and not folder.exists()
          and "Saved" in res.get("reply", "") and (res.get("client") or {}).get("id") == d3.id, f"{acts(res)} {res.get('reply')!r}")
    r = c.post(f"/v1/drafts/{d3.id}/save", headers=H)  # the Save button
    check("the Save button writes it to its folder", r.status_code == 200 and (folder / "chair.glb").read_bytes() == b"glTF-fake", r.text[:120])
    check("saving twice keeps one file", drafts_mod.save(d3) == folder / "chair.glb" and len(list(folder.iterdir())) == 1)
    pdf = drafts_mod.add("pdf", "An essay", b"%PDF-fake", folder, "essay.pdf")
    tmp_pdf = drafts_mod.temp_copy(pdf)
    check("opening an unsaved PDF uses a temporary copy", tmp_pdf.parent == drafts_mod.TEMP and tmp_pdf.read_bytes() == b"%PDF-fake"
          and not (folder / "essay.pdf").exists())
    drafts_mod.clear_temp()
    check("...deleted when PLAG starts and quits", not drafts_mod.TEMP.exists())
    check("'ise save karo' / 'report save kar do' mean save", [i.action for i in parse_many("ise save karo")] == ["save_draft"]
          and parse_many("report save kar do")[0].args == {"kind": "pdf"})
    with drafts_mod._lock:
        drafts_mod._drafts.clear()
    res = turn(c, "save it")
    check("'save' with nothing made yet says so", acts(res) == "save_draft" and "no" in res.get("reply", "").lower(), res.get("reply"))

    print("\"sir\", your language, and feeling")
    from plag_core import agent as agent_p  # noqa: E402
    from plag_core import edgevoice as edgevoice_mod  # noqa: E402
    sp = " ".join(agent_p.system_prompt("auto").split())
    check('PLAG calls you "sir", never "bro"', 'Always call the user "sir" (never "bro"' in sp and "Haan bro" not in sp)
    check("it answers in the language you used (English -> English, Hindi -> Hindi)",
          "SAME language as the user's latest message" in sp and "English -> reply only in English" in sp)
    check("good news gets real joy ('Wow sir, that's amazing!', mood excited)", "react with real joy" in sp
          and "Wow sir, that's amazing!" in sp and 'mood "excited"' in sp)
    check("the camera answers call you sir too", 'Call the user "sir"' in agent_p.vision_prompt("mixed"))
    joy, sad = sarvam._body("Wow sir, amazing!", "excited"), sarvam._body("Oh no sir", "sorry")
    check("Sarvam: joy is livelier (faster, more expressive), sad is softer", joy["temperature"] > 0.6 > sad["temperature"]
          and joy["pace"] > sad["pace"] and joy["language_code"] == "en-IN", f"{joy} {sad}")
    check("Sarvam: Hinglish is spoken in Hindi", sarvam._body("Arre waah sir, kya baat hai!", "excited")["language_code"] == "hi-IN")
    import edge_tts as _edge_tts  # noqa: E402
    calls: list[tuple] = []

    class _FakeCommunicate:
        def __init__(self, text, voice, rate="+0%", pitch="+0Hz", **_k):
            calls.append((text, voice, rate, pitch))

        async def stream(self):
            yield {"type": "audio", "data": b"mp3"}
    saved_edge = (_edge_tts.Communicate, edgevoice.usable, edgevoice_mod.CACHE)
    try:
        _edge_tts.Communicate, edgevoice.usable, edgevoice_mod.CACHE = _FakeCommunicate, (lambda: True), Path(tempfile.mkdtemp())
        import asyncio as _a_e  # noqa: E402
        _a_e.run(edgevoice.speak("Wow sir, that's amazing!", "excited"))
        check("Edge: you spoke English, the English voice answers, brighter for joy",
              calls[-1][1] == "en-IN-PrabhatNeural" and calls[-1][3] == "+9Hz" and calls[-1][2].startswith("+"), str(calls[-1:]))
        _a_e.run(edgevoice.speak("Arre waah sir, kya baat hai!", "excited"))
        check("Edge: Hindi is spoken by the Hindi voice", calls[-1][1] == "hi-IN-MadhurNeural" and "क्या" in calls[-1][0], str(calls[-1:]))
    finally:
        _edge_tts.Communicate, edgevoice.usable, edgevoice_mod.CACHE = saved_edge

    print("messages only when you ask for one")
    real_think2 = _agent._think

    async def _guess_send(*_a, **_k):
        return "stub", {"transcript": "Land", "language": "en", "reply": "", "mood": "calm",
                        "actions": [{"type": "whatsapp_send", "contact": "Omi", "message": "Land"}]}, 5
    try:
        _agent._think = _guess_send
        _agent.pending_ask = None
        res = turn(c, "Land", followup="window", heard_by="NVIDIA Parakeet")
        check("a stray word after a reply is never sent as a message (2026-09-25: 'Land' went to Omi)", res.get("silent") is True,
              str(res)[:160])
        res = turn(c, "Land")
        check("...and said to PLAG, it asks instead of guessing", acts(res) != "whatsapp_send" and "?" in res.get("reply", ""),
              f"{acts(res)} {res.get('reply')!r}")
    finally:
        _agent._think = real_think2
    turn(c, "send a message to Rahul")
    res = turn(c, "go ahead", followup="answer")
    check("'go ahead' is said to PLAG, not sent: it asks again", acts(res) == "chat" and "What should I send" in res.get("reply", ""),
          f"{acts(res)} {res.get('reply')!r}")
    res = turn(c, "I'll be late", followup="answer")
    check("...then your real message goes", acts(res) == "whatsapp_send" and res["action"].get("contact") == "Rahul"
          and res["action"].get("message") == "I'll be late", str(res.get("action")))
    check("'mausam kholo aur Omi ko message bhejo' is two requests, not a contact called 'mausam kholo aur Omi'",
          parse_many("mausam kholo aur obi bro Bangalore ko message bhejo") is None)

    print("WhatsApp: one search, results read off the screen")

    class _FakeComposer:
        def __init__(self, name):
            self.CurrentName = whatsapp.COMPOSER + name

    class _FakePage:
        def __init__(self, titles):
            self.titles, self.clicked = titles, []

        def result_rows(self, box):
            return [(t, t) for t in self.titles]  # the "row" is its title here

        def click(self, row):
            self.clicked.append(row)

        def composer(self):
            return _FakeComposer(self.clicked[-1]) if self.clicked else None

        def _wait(self, get, timeout, every=0.1):
            return get()
    typed_names: list[str] = []
    saved_ts = whatsapp._type_search
    try:
        whatsapp._type_search = lambda page, box, q: typed_names.append(q)
        p = _FakePage(["Omi Bro Shotu Bangalore", "Bhoomix", "🌿 bhoomiX 🌿"])
        got = whatsapp._search_once(p, None, "omi", "omi", [])
        check("'omi': typed once, 'Bhoomix' skipped, only Omi's chat opened", typed_names == ["omi"]
              and p.clicked == ["Omi Bro Shotu Bangalore"] and got and got[1] == "Omi Bro Shotu Bangalore", f"{typed_names} {p.clicked}")
        p = _FakePage(["Rahul Sharma", "Rahul Verma"])
        try:
            whatsapp._search_once(p, None, "rahul", "rahul", [])
            options = None
        except whatsapp._Ambiguous as e:
            options = e.options
        check("two Rahuls: PLAG asks which, before opening either", options == ["Rahul Sharma", "Rahul Verma"] and p.clicked == [],
              f"{options} {p.clicked}")
        p = _FakePage(["Rahul Verma", "Rahul"])
        whatsapp._search_once(p, None, "rahul", "rahul", [])
        check("the exact name wins over a longer one", p.clicked == ["Rahul"], str(p.clicked))
        p = _FakePage(["Priyanka", "Bhoomix"])
        check("nobody fits: nothing is opened", whatsapp._search_once(p, None, "priya", "priya", []) is None and p.clicked == [])
    finally:
        whatsapp._type_search = saved_ts
    check("a row's chat name, without its time and last message",
          whatsapp._ROW_END.sub("", "Omi Bro Shotu Bangalore 2:56 pm You deleted this message").strip() == "Omi Bro Shotu Bangalore"
          and whatsapp._ROW_END.sub("", "Rahul Sharma Yesterday Ok bhai").strip() == "Rahul Sharma")

    print("directions and saved places (map services stubbed: no network)")
    from plag_core import location as loc_mod  # noqa: E402
    from plag_core import navigation as nav  # noqa: E402
    for said, want in [("take me to India Gate", ("navigate", "India Gate")), ("India Gate kaise jaun", ("navigate", "India Gate")),
                       ("airport ka rasta batao", ("navigate", "airport")), ("take me home", ("navigate", "home")),
                       ("how far is Noida", ("navigate", "Noida")), ("save this location as home", ("save_place", "home")),
                       ("is jagah ko office naam se save karo", ("save_place", "office")),
                       ("look at my screen", ("screen_look", "")), ("meri screen dekho", ("screen_look", "")),
                       ("what does this error say", ("screen_look", "what does this error say"))]:
        got = parse_many(said)
        arg = (got[0].args.get("place") if got and got[0].action == "navigate" else got[0].args.get("label")
               if got and got[0].action == "save_place" else got[0].args.get("question") if got else None)
        check(f"{said!r} -> {want[0]}", bool(got) and got[0].action == want[0] and arg == want[1], str([(i.action, i.args) for i in got or []]))
    for said in ("I want to go to sleep", "take me through it", "YouTube chalo", "save the pdf", "what is this"):
        got = parse_many(said)
        check(f"{said!r} isn't directions", not got or got[0].action not in ("navigate", "save_place"), str([(i.action, i.args) for i in got or []]))
    check("a polyline decodes (Google's own example)",
          nav.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]])
    check("the spoken words decide the arrow ('Turn left' sent as a roundabout)", nav._turn("roundabout", "Turn left onto Pant Marg") == "left"
          and nav._turn("depart", "Head west on Kartavya Path") == "depart" and nav._turn("", "Make a slight right") == "slight-right")
    steps = [{"text": "Head west", "turn": "depart", "distance_m": 400, "lat": 28.6139, "lng": 77.2088},
             {"text": "Turn left", "turn": "left", "distance_m": 200, "lat": 28.6141, "lng": 77.2040},
             {"text": "You have arrived", "turn": "arrive", "distance_m": 0, "lat": 28.6160, "lng": 77.2040}]
    i, m = nav.next_step(steps, {"lat": 28.6140, "lng": 77.2060})
    check("halfway down the first road, the next turn is the left", i == 1 and 150 < m < 250, f"{i} {m:.0f}")
    saved_places, saved_pos, saved_place, saved_search, saved_route = nav.PLACES, loc_mod.position, loc_mod.place, nav.search, nav.route
    try:
        nav.PLACES = Path(tempfile.mkdtemp()) / "places.json"

        async def _pos(fresh=False):
            return {"lat": 28.6139, "lon": 77.2090, "accuracy_m": 30, "at": 1.0}

        async def _place(fresh=False):
            return {"lat": 28.6139, "lon": 77.2090, "accuracy_m": 30, "area": "Rajpath", "city": "New Delhi", "address": "Rajpath, New Delhi"}

        async def _search(q, near=None):
            return {"name": "India Gate", "address": "India Gate, New Delhi", "lat": 28.6129, "lng": 77.2295}

        async def _route(o, d):
            return {"distance_m": 5800, "duration_s": 660, "steps": steps, "path": [[28.6139, 77.2088], [28.6129, 77.2295]],
                    "traffic": True, "by": "Ola Maps"}
        loc_mod.position, loc_mod.place, nav.search, nav.route = _pos, _place, _search, _route
        res = turn(c, "take me to India Gate")
        cl = res.get("client") or {}
        check("directions: the live map gets the route, PLAG tells the trip like Jarvis", cl.get("type") == "route"
              and cl.get("dest", {}).get("name") == "India Gate" and len(cl.get("steps", [])) == 3
              and "5.8 km" in res.get("reply", "") and "11 min" in res.get("reply", "") and "sir" in res.get("reply", "").lower(),
              f"{res.get('reply')!r} {str(cl)[:120]}")
        res = turn(c, "save this location as home")
        check("'save this location as home' saves where you are", acts(res) == "save_place" and nav.find_saved("home")
              and nav.find_saved("home")["address"] == "Rajpath, New Delhi", f"{res.get('reply')!r}")
        check("'ghar' and 'my home' find the saved home", bool(nav.find_saved("ghar") and nav.find_saved("my home")))
        res = turn(c, "take me home")
        check("'take me home' goes to the saved home, no search", (res.get("client") or {}).get("dest", {}).get("name") == "home",
              str((res.get("client") or {}).get("dest")))
        check("'stop navigation' closes the map", (turn(c, "stop navigation").get("client") or {}).get("command") == "stop_nav")
    finally:
        nav.PLACES, loc_mod.position, loc_mod.place, nav.search, nav.route = saved_places, saved_pos, saved_place, saved_search, saved_route
    res = turn(c, "look at my screen")
    check("'look at my screen': the dashboard takes the screenshot", (res.get("client") or {}).get("type") == "screen_look")
    check("the screen reader translates, reads errors, and never reads out passwords", all(
        w in agent_p.vision_prompt("en", screen=True) for w in ("translate it", "next step", "passwords")))
    check("a screenshot is accepted by /v1/vision (not refused as an unknown source)",
          c.post("/v1/vision", headers=H, json={"image": "x" * 200, "source": "screen"}).status_code == 415)

    print("more Hinglish, straight from the fast path")
    for said, want in [("YouTube pe Arijit Singh ke gaane chala do", ("play_youtube", "query", "arijit singh ke gaane")),
                       ("kal delhi mein baarish hogi kya", ("weather", "city", "delhi")),
                       ("WhatsApp par Rahul ko call karo", ("whatsapp_call", "contact", "Rahul")),
                       ("generate an 3d model of iron man trianlge shape arc reacter ok", ("generate_3d", "prompt", "iron man trianlge shape arc reacter"))]:
        got = parse_many(said)
        check(f"{said!r}", bool(got) and got[0].action == want[0] and got[0].args.get(want[1]) == want[2],
              str([(i.action, i.args) for i in got]))

    print("WhatsApp names")
    check("'omi shotu' is 'Omi Bro Shotu Bangalore'", whatsapp.fits("omi shotu", "Omi Bro Shotu Bangalore"))
    check("'omi bro shotu bangalore' is 'Omi Shotu'", whatsapp.fits("omi bro shotu bangalore", "Omi Shotu"))
    check("'omi shotu' isn't just 'Omi'", not whatsapp.fits("omi shotu", "Omi"))
    check("'priya' isn't 'Priyanka'", not whatsapp.fits("priya", "Priyanka Verma"))
    check("search 'omi shotu', then 'shotu', then 'omi'", whatsapp.search_terms("omi shotu") == ["omi shotu", "shotu", "omi"],
          str(whatsapp.search_terms("omi shotu")))
    saved_aliases = whatsapp.ALIASES
    try:
        whatsapp.ALIASES = Path(tempfile.mkdtemp()) / "names.json"
        whatsapp.remember("Omi  Shotu", "Omi Bro Shotu Bangalore")
        whatsapp.remember("Rahul", "rahul")  # the same name: nothing to remember
        check("a name that worked is remembered", whatsapp.aliases() == {"omi shotu": "Omi Bro Shotu Bangalore"}, str(whatsapp.aliases()))
        whatsapp.forget("omi shotu")
        check("and forgotten when the chat is gone", whatsapp.aliases() == {})
    finally:
        whatsapp.ALIASES = saved_aliases

    print("3D models in the background (stubbed TRELLIS)")
    import asyncio as _a3  # noqa: E402

    from plag_core import agent as agent_3d  # noqa: E402
    from plag_core.bus import bus as _bus  # noqa: E402
    published: list[tuple[str, dict]] = []
    saved_pub, saved_gen, saved_retry = _bus.publish, agent_3d.model3d.generate, agent_3d.RETRY_3D_S
    try:
        _bus.publish = lambda topic, data=None, *a, **k: published.append((topic, data or {}))
        agent_3d.RETRY_3D_S = 0
        tries = {"n": 0}

        async def _flaky(prompt):
            tries["n"] += 1
            if tries["n"] < 3:
                raise agent_3d.model3d.ModelError("NVIDIA's 3D service returned an error (500).", "500")
            return {"id": "abc123def0", "prompt": prompt, "path": "x.glb", "ms": 1000, "bytes": 10}
        agent_3d.model3d.generate = _flaky
        _a3.run(_agent._build_3d("a wooden chair", "mixed"))
        ready = [d for t, d in published if t == "model3d.ready"]
        check("TRELLIS failing twice, then working: the model still arrives", tries["n"] == 3 and ready and ready[0]["id"] == "abc123def0"
              and "ready hai" in ready[0]["text"], f"{tries} {published[-1:]}")

        async def _down(prompt):
            raise agent_3d.model3d.ModelError("NVIDIA's 3D service returned an error (500).", "500")
        agent_3d.model3d.generate = _down
        published.clear()
        _a3.run(_agent._build_3d("a red cup", "en"))
        said = [d for t, d in published if t == "plag.say"]
        check("TRELLIS down for good: PLAG says it's NVIDIA's side", said and "NVIDIA's TRELLIS server kept failing" in said[0]["text"],
              str(published[-1:]))

        # NVIDIA's filter blocks names ("iron man"): PLAG describes the look instead and builds that
        prompts: list[str] = []

        async def _filtered_then_ok(prompt):
            prompts.append(prompt)
            if "iron man" in prompt.lower():
                raise agent_3d.model3d.ModelError("NVIDIA's safety filter blocked that model.", "filtered")
            return {"id": "abc123def1", "prompt": prompt, "path": "", "ms": 900, "bytes": 10, "draft": True}

        async def _rewrite(prompt, limit=300, timeout=8.0):
            return "a glowing blue triangular energy reactor, polished metal, sci-fi"
        saved_safe = agent_3d.promptfix.safe
        agent_3d.model3d.generate, agent_3d.promptfix.safe = _filtered_then_ok, _rewrite
        published.clear()
        _a3.run(_agent._build_3d("iron man triangle arc reactor", "en"))
        ready = [d for t, d in published if t == "model3d.ready"]
        check("'iron man ...' blocked: built from its look instead, and PLAG says why", len(prompts) == 2
              and "iron man" not in prompts[1].lower() and ready and "NVIDIA blocks character names" in ready[0]["text"]
              and "iron man triangle arc reactor" in ready[0]["text"] and ready[0]["saved"] is False, f"{prompts} {published[-1:]}")
        agent_3d.promptfix.safe = saved_safe
    finally:
        _bus.publish, agent_3d.model3d.generate, agent_3d.RETRY_3D_S = saved_pub, saved_gen, saved_retry

    print("the AI race (stubbed brains)")
    import asyncio  # noqa: E402
    import time  # noqa: E402

    from plag_core import agent as agent_mod  # noqa: E402

    def _walk(s, bad):
        if isinstance(s, dict):
            bad += ["enum"] if "" in (s.get("enum") or []) else []
            for v in s.values():
                _walk(v, bad)
        elif isinstance(s, list):
            for v in s:
                _walk(v, bad)
        return bad
    check("no empty value in the command schema (Gemini refuses it: every turn failed on 2026-09-24)", not _walk(agent_mod.SCHEMA, []))
    its = agent_mod._intents_from_model({"language": "en", "reply": "", "actions": [{"type": "whatsapp_send", "contact": "Omi", "message": "none"}]})
    check("a WhatsApp message of 'none' is never sent: PLAG asks instead", its[0].action == "whatsapp_send" and its[0].args["message"] == "",
          str(its[0].args))

    async def _fast_wrong(**_k):
        await asyncio.sleep(0.05)
        return "fast", {"transcript": "x", "language": "mixed", "actions": [{"type": "none"}], "reply": "Omi ko message bhej raha hoon.",
                        "mood": "calm"}, 50

    async def _slow_right(**_k):
        await asyncio.sleep(0.4)
        return "slow", {"transcript": "x", "language": "mixed", "reply": "", "mood": "calm",
                        "actions": [{"type": "whatsapp_send", "contact": "Omi", "message": "aa raha hoon"}]}, 400

    async def _never(**_k):
        await asyncio.sleep(30)

    saved = (agent_mod.gemini.turn, agent_mod.nvidia.models, agent_mod.nvidia.turn, agent_mod.groq.ready)
    try:
        agent_mod.gemini.turn, agent_mod.nvidia.models, agent_mod.groq.ready = _fast_wrong, (lambda: ["stub"]), (lambda: False)
        agent_mod.nvidia.turn = _slow_right
        won = asyncio.run(_agent._think("auto", None, "omi ko bol do aa raha hoon"))[0]
        check("a fast wrong answer loses to a slower right one", won == "slow", won)
        agent_mod.nvidia.turn = _never
        t0 = time.monotonic()
        won = asyncio.run(_agent._think("auto", None, "omi ko bol do aa raha hoon"))[0]
        check("only wrong answers: the race still ends within ~2.5 s", won == "fast" and time.monotonic() - t0 < 4, f"{won} {time.monotonic() - t0:.1f}s")
    finally:
        agent_mod.gemini.turn, agent_mod.nvidia.models, agent_mod.nvidia.turn, agent_mod.groq.ready = saved
    turn(c, "send a message to Yoyo")
    res = turn(c, "whatsapp sending function is not working properly", followup="answer")
    check("talk about PLAG isn't sent as the message", not (acts(res) == "whatsapp_send" and res["action"].get("contact") == "Yoyo"),
          str(res.get("action")))
    check("Hindi in -> Hinglish out (Latin letters)", not any("ऀ" <= ch <= "ॿ" for ch in turn(c, "यूट्यूब खोलो").get("reply", "")))
    turn(c, "message mom on whatsapp")
    res = turn(c, "never mind", followup="answer")
    check("'never mind' drops the question", acts(res) == "chat" and "cancel" in res.get("reply", "").lower(), res.get("reply"))
    turn(c, "Rahul ko message bhejo")
    res = turn(c, "open youtube", followup="answer")
    check("a clear new command isn't sent as the message", acts(res) == "open_url", str(res.get("action")))
    res = turn(c, "Thank you.", heard_by="ElevenLabs realtime", followup="window")
    check("a follow-up window ignores what's only noise", res.get("silent") is True and res.get("reply") == "", str(res)[:160])
    res = turn(c, "what time is it", followup="window")
    check("a real follow-up is answered", acts(res) == "time" and not res.get("silent") and res.get("expects_reply") is False)
    res = turn(c, "PLAG", heard_by="ElevenLabs realtime")
    check("just the name, heard in realtime: 'Yes sir?' and listening", "sir" in res.get("reply", "").lower()
          and res.get("expects_reply") is True, res.get("reply"))
    res = turn(c, "PLAG open notepad", heard_by="ElevenLabs realtime")
    check("the name is dropped from a realtime command", acts(res) == "open_app" and res.get("transcript") == "open notepad"
          and res.get("model") == "elevenlabs realtime", f"{res.get('transcript')!r} / {res.get('model')}")
    res = c.post("/v1/turn/text", headers=H, json={"text": "hi", "followup": "sometimes"})
    check("an unknown follow-up kind is refused", res.status_code == 422)

    print("weather and 3D models (the forecast is stubbed: no network; the rest is dry run)")
    from plag_core import model3d, weather as weather_mod

    async def fake_forecast(city: str = "", *, lat=None, lon=None, name: str = "") -> dict:
        return {"place": name or city.strip().title(), "region": "", "country": "India",
                "now": {"temp": 31, "feels": 35, "humidity": 50, "wind": 5, "code": 2},
                "days": [{"date": "d0", "code": 2, "high": 34, "low": 26, "rain": 10},
                         {"date": "d1", "code": 61, "high": 30, "low": 24, "rain": 80}]}
    real_forecast, weather_mod.forecast = weather_mod.forecast, fake_forecast
    c.post("/v1/settings", headers=H, json={"changes": {"use_location": False}})  # no real location lookups in tests
    try:
        res = turn(c, "what's the weather")
        check("no city yet: PLAG asks which, and listens", "Which city" in res.get("reply", "") and res.get("expects_reply") is True,
              res.get("reply"))
        res = turn(c, "Delhi", followup="answer")
        check("the answer is the city, and it's remembered", "Delhi" in res.get("reply", "") and "31 degrees" in res.get("reply", "")
              and app_settings.get()["home_city"] == "Delhi", f"{res.get('reply')!r} / {app_settings.get()['home_city']!r}")
        res = turn(c, "will it rain tomorrow")
        check("tomorrow, in your city", "Tomorrow in Delhi" in res.get("reply", "") and "80%" in res.get("reply", ""), res.get("reply"))
        res = turn(c, "delhi mein kal ka mausam kaisa rahega")
        check("Hinglish weather, Hinglish answer", acts(res) == "weather" and res.get("reply", "").startswith("Kal Delhi mein"),
              res.get("reply"))
        from plag_core import location as location_mod

        async def fake_place(fresh: bool = False) -> dict:
            return {"lat": 12.5, "lon": 77.5, "accuracy_m": 70, "at": 1.0, "road": "MG Road", "area": "Test Nagar",
                    "city": "Testpur", "district": "Testpur", "state": "Test State", "postcode": "123456",
                    "country": "India", "address": "MG Road, Test Nagar, Testpur, Test State, 123456, India"}
        real_place, location_mod.place = location_mod.place, fake_place
        c.post("/v1/settings", headers=H, json={"changes": {"use_location": True}})
        try:
            res = turn(c, "where am i")
            check("'where am I' (Windows Location, stubbed)", acts(res) == "where" and "Test Nagar, Testpur" in res.get("reply", ""),
                  res.get("reply"))
            res = turn(c, "what's my address")
            check("'what's my address'", "MG Road" in res.get("reply", "") and "70 metres" in res.get("reply", ""), res.get("reply"))
            check("Hinglish: 'main kahan hoon'", acts(turn(c, "main kahan hoon")) == "where")
            res = turn(c, "what's the weather")
            check("no city named: the weather where you are", acts(res) == "weather" and "Testpur" in res.get("reply", ""),
                  res.get("reply"))
            check("no coordinates in the result log", "12.5" not in str(turn(c, "where am i").get("result")))
        finally:
            location_mod.place = real_place
            c.post("/v1/settings", headers=H, json={"changes": {"use_location": False}})
    finally:
        weather_mod.forecast = real_forecast
    check("every reply in ElevenLabs by default", app_settings.DEFAULTS["eleven_max_chars"] == 1000)
    for said, want in [("tell me the news", "research"), ("what's the latest news about ISRO", "research"), ("news batao", "research"),
                       ("cricket ki news batao", "research"), ("make a report on the latest AI news", "research"),
                       ("write an essay on climate change", "write"), ("write a leave letter to my principal", "write"),
                       ("pollution par essay likho", "write"), ("write a short story about a robot", "write")]:
        res = turn(c, said)
        check(f"{said!r} -> {want}, no browser", acts(res) == want and (res.get("result") or {}).get("ok") is True
              and res.get("model") == "fast path", f"{res.get('action')} / {res.get('reply')!r}")
    check("news is told, never a Google search", acts(turn(c, "tell me today's headlines")) == "research")
    w = turn(c, "write a short story about a robot")["action"]
    check("the kind and length of writing are understood", w.get("kind") == "story" and w.get("topic") == "a robot", str(w))
    check("a PDF from writing's Markdown can't carry code", "<script>" not in __import__("plag_core.documents", fromlist=["x"])
          .markdown_html("## Hi\n<script>alert(1)</script> **bold**") and "<strong>bold</strong>" in
          __import__("plag_core.documents", fromlist=["x"]).markdown_html("**bold**"))
    for said, want in [("open downloads", "open_folder"), ("what's on my desktop", "list_files"),
                       ("open my resume from the desktop", "open_file"), ("desktop se resume kholo", "open_file"),
                       ("search wikipedia for Sundar Pichai", "lookup"), ("wikipedia ISRO", "lookup")]:
        res = turn(c, said)
        check(f"{said!r} -> {want}", acts(res) == want and res.get("model") == "fast path", f"{res.get('action')} / {res.get('reply')!r}")
    from plag_core.fastpath import parse_many as _pm_q  # noqa: E402
    for said in ("who is Modi ji", "Modi ji kaun hai", "ISRO kya hai", "tell me about the Taj Mahal"):
        check(f"{said!r}: the AI answers from what it knows, no web search first", not any(
            i.action == "lookup" for i in (_pm_q(said) or [])), str([(i.action, i.args) for i in (_pm_q(said) or [])]))
    import plag_core.agent as _agent_q  # noqa: E402
    check("the AI is told to answer ordinary questions directly, lookup only for latest news",
          "do NOT use lookup" in _agent_q.system_prompt("auto") and "latest news" in _agent_q.system_prompt("auto"))
    check("'open youtube' is still the website, not a file", acts(turn(c, "open youtube")) == "open_url")
    check("questions about you aren't Wikipedia lookups", acts(turn(c, "what is my name")) != "lookup")
    from plag_core import files as files_mod
    check("scripts are never run by voice", ".ps1" in files_mod.NEVER_RUN and ".bat" in files_mod.NEVER_RUN and ".pdf" not in files_mod.NEVER_RUN)
    s = c.post("/v1/settings", headers=H, json={"changes": {"always_listen": True}}).json()["settings"]
    check("the ear button: always listening", s["always_listen"] is True and wake.wake.always is True)
    c.post("/v1/settings", headers=H, json={"changes": {"always_listen": False}})
    check("ear off: only after 'PLAG'", wake.wake.always is False)
    check("'flag' wakes PLAG too", wake.split_wake("Flag, open YouTube") == (True, "open YouTube") and wake.exact_wake("Flag open YouTube")
          and not wake.exact_wake("Plug in the charger"))
    res = turn(c, "run a weather simulation")
    check("weather simulation: FourCastNet, honest about its sample start", acts(res) == "weather_sim"
          and "sample" in res.get("reply", "") and (res.get("result") or {}).get("ok") is True, res.get("reply"))
    check("'simulate the wind' shows wind speed", turn(c, "simulate the wind")["action"].get("variable") == "w10m")
    res = turn(c, "make a 3d model of a wooden chair")
    check("3D model (dry run)", acts(res) == "generate_3d" and res["action"].get("prompt") == "a wooden chair"
          and (res.get("result") or {}).get("ok") is True, str(res.get("action")))
    check("3D descriptions fit NVIDIA's 77 characters", len(model3d.short_prompt("a " + "very " * 30 + "big chair")) <= 77
          and model3d.short_prompt("a 3D model of a red cup") == "a red cup")
    check("unknown 3D model and map ids", c.get("/v1/models3d/0123456789", headers=H).status_code == 404
          and c.get("/v1/weather/0123456789/0", headers=H).status_code == 404)
    check("3D and weather rows", {"model3d", "weather"} <= {x["id"] for x in c.get("/v1/connectors", headers=H).json()})
    s = c.post("/v1/settings", headers=H, json={"changes": {"follow_up": "questions", "home_city": "  Pune  "}}).json()["settings"]
    check("follow-up and city settings", s["follow_up"] == "questions" and s["home_city"] == "Pune", str(s))
    c.post("/v1/settings", headers=H, json={"changes": {"follow_up": "sometimes"}})
    check("an invalid follow-up setting is ignored", app_settings.get()["follow_up"] == "questions")
    if not el.configured():
        r = c.post("/v1/tts/stream", headers=H, json={"text": "On it.", "mood": "calm"})
        check("no ElevenLabs: the streamed voice says so at once (409)", r.status_code == 409 and r.json().get("error") == "no_stream")
        with c.websocket_connect("/ws/listen", subprotocols=["plag.v1", "token.check-all-token"]) as ws:
            check("no ElevenLabs: realtime hearing says so at once", ws.receive_json() == {"type": "ready", "realtime": False})
    try:
        with c.websocket_connect("/ws/listen", subprotocols=["plag.v1", "token.wrong"]) as ws:
            ws.receive_json()
        check("realtime hearing needs the token", False)
    except Exception:  # noqa: BLE001 - closed with 4401
        check("realtime hearing needs the token", True)

    print("interrupting PLAG by voice")
    check("stop phrases", all(wake.is_stop(t) for t in ["stop", "PLAG, stop", "ruko", "bas karo", "the main reason is stop"]))
    check("PLAG's own words don't stop it", not any(wake.is_stop(t) for t in ["Stopped.", "I stopped the task.", "Opening YouTube."]))
    check("speaking signal", c.post("/v1/voice/speaking", headers=H, json={"speaking": True}).json()["ok"] is True and wake.wake.speaking)
    c.post("/v1/voice/speaking", headers=H, json={"speaking": False})

    print("stop, halt, resume")
    check("cancel", c.post("/v1/cancel", headers=H).json().get("ok") is True)
    check("halt", c.post("/v1/killswitch", headers=H).json().get("halted") is True)
    check("commands refused while halted", c.post("/v1/turn/text", headers=H, json={"text": "open notepad"}).status_code == 423)
    check("resume", c.post("/v1/resume", headers=H).json().get("halted") is False)
    check("works again after resume", acts(turn(c, "open notepad")) == "open_app")

    print("status")
    names = [x["name"] for x in c.get("/v1/connectors", headers=H).json()]
    check("connections list", {"Wake word", "Gemini", "WhatsApp"} <= set(names), str(names))
    check("wake status", "state" in c.get("/v1/wake", headers=H).json())
    check("bad image rejected", c.post("/v1/vision", headers=H, json={"image": "x" * 200}).status_code == 415)

print(f"\n{passed} passed, {len(failed)} failed" + (f": {failed}" if failed else ""))
sys.exit(1 if failed else 0)
