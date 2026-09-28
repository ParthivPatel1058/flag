# PLAG

A voice-first personal AI assistant for Windows. It understands English, हिंदी and Hinglish, answers out loud, and acts on your laptop through a permission-checked tool layer.

Architecture and roadmap: https://claude.ai/artifact/8rgS1sVvRMv6xbuHP8N5Rw

```
apps/shell   Electron desktop dashboard (React + TypeScript + Vite)
core         plag-core: agent, tools, policy, audit log, local API (Python 3.12, FastAPI)
```

## First-time setup

1. Store the Gemini API key in Windows Credential Manager (never in a file):

   ```
   python -c "import keyring,getpass; keyring.set_password('PLAG','gemini_api_key', getpass.getpass('Gemini key: '))"
   ```

   NVIDIA keys, the same way (each prompt asks for one key; paste it and press Enter):

   ```
   python -c "import keyring,getpass; keyring.set_password('PLAG','nvidia_hearing_api_key', getpass.getpass('Hearing (Parakeet) key: '))"
   python -c "import keyring,getpass; keyring.set_password('PLAG','nvidia_muse_api_key', getpass.getpass('Muse key: '))"
   python -c "import keyring,getpass; keyring.set_password('PLAG','nvidia_glm_api_key', getpass.getpass('GLM 5.3 Flash key: '))"
   ```

   *Hearing* is NVIDIA Parakeet, which understands what you say in English, Hindi and Hinglish (it falls back to
   `nvidia_speech_api_key`). *Muse* (Meta) and *GLM 5.3 Flash* replace Gemma: they race Gemini on every command,
   answer, news brief and piece of writing, and the first good answer wins.

2. Install both halves:

   ```
   cd core && uv sync
   cd ../apps/shell && npm install
   ```

## Run

```
plag             # from any terminal (bin/ is on PATH): builds, then opens the app
```

For development: `cd apps/shell && npm run dev` (live-reloading dashboard).

The dashboard starts `plag-core` itself on a random loopback port with a one-time token, and stops it on exit.
If the dashboard says the core isn't responding, read `%LOCALAPPDATA%\PLAG\logs\core.log`.

The Kokoro voice models live in `models\kokoro\` next to this README, and the core's Python in `core\.python\`.
Neither is kept in AppData: when a packaged app (such as the Claude desktop app) downloads into AppData, Windows
redirects the files into that app's private folder, and PLAG started from your terminal can't see them.

## Using it

- **Talk:** click the lime mic, hold <kbd>Space</kbd>, or press <kbd>Ctrl</kbd>+<kbd>Space</kbd> from any app. Speaking stops automatically after a pause.
- **Type:** the keyboard button opens a command box. Typed commands such as `youtube kholo`, `यूट्यूब खोलो`, `play lo-fi beats` or `why is my laptop slow` run on the fast path, with no cloud call.
- **Language:** Auto replies in the language you spoke. EN and हिं force one language.
- **Stop:** <kbd>Esc</kbd> or the ✕ button stops the current task. **Halt** (or <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>X</kbd>) is the kill switch: everything stops until you resume.

## What it can do today

| Action | Level | Confirmation |
|---|---|---|
| Open websites and apps, search Google or YouTube, play videos | L1 | none |
| Send a WhatsApp message to anyone in your WhatsApp, by name or number | L1 | none (your choice) |
| Diagnose lag (CPU, memory, disk, battery, heaviest apps) | L0 | none |
| Write essays, letters and reports as PDFs; news told aloud with a PDF report | L0 | none |
| 3D models, weather forecast and simulation, where you are | L0 | none |
| Answer questions, tell the time | L0 | none |
| Email, delete, install | not built yet | declined |

**Always on:** closing the window hides PLAG to the tray (the lime ring near the clock); it keeps listening for
“PLAG”, answers by voice, and fires reminders. Right-click the tray icon for *Listen for “PLAG”*, *Start with Windows*
and *Quit PLAG*. While PLAG is talking, say “stop”, “ruko”, “bas” or “PLAG, stop” to cut it off.

**Memory and reminders** (stored in `%LOCALAPPDATA%\PLAG\plag.db`): “remember that my bike service is on Friday”,
“what do you remember”, “forget that”, “remind me at 7 pm to call mom”, “10 minute baad paani peena yaad dilana”,
“what are my reminders”. See or delete them in the Memory and Reminders tabs. Passwords, PINs and keys are refused.

**Plans and verification:** one sentence becomes ordered steps, each read in the app the previous step left you in
(“open YouTube and search Honey Singh” = one YouTube search; “open WhatsApp and find Priyans” = open that chat;
“search Gmail for BhoomiX” is Gmail, never Google). A follow-up “search X” uses the app you were in. Every browser
and app step is checked against the real window title (the results page, not just “a tab opened”); the Now panel
shows the plan as a live checklist, and PLAG says “On it.” before slower plans. “Research …” gathers ~10 news
sources plus Wikipedia's article for background, writes a cited brief and saves it to `Documents\PLAG\Reports`.
“Who is …” / “look up …” reads the top Wikipedia articles (English, plus Hindi Wikipedia when you speak Hindi) together
with the latest news.

**No second “PLAG”:** after PLAG asks you something (“What should I send to Rahul?”, “Which city?”) the mic opens
for your answer, and after a spoken answer it keeps listening ~5 s (never after opening or playing something). The
**ear button** in the dock is *Always listening*: every phrase is for PLAG (it ignores its own voice and room noise);
off, it answers only after “PLAG” (or “flag”). ⚙ Settings → *Keep listening after PLAG answers* changes the rest.

**Writing, news and reports, as PDFs (no browser):** “write an essay on climate change”, “write a leave letter to my
principal”, “pollution par essay likho” → PLAG writes it in full and saves a PDF in `Documents\PLAG\Writing`.
“Tell me the news”, “latest news about ISRO”, “aaj ki khabar” → PLAG *says* a short cited brief and saves a PDF report
in `Documents\PLAG\Reports`. Nothing opens by itself: the dashboard shows a card with *Open PDF* / *Show in folder*.
PDFs are printed by Microsoft Edge in headless mode. If the AI is busy, reports list the real headlines instead.

**Pictures in the chat:** the picture button (dock, or inside the command box) attaches an image; ask about it (“what's
written here?”) or just press Enter. Gemini answers aloud, and PLAG remembers it for the next request.

**3D models (TRELLIS on NVIDIA):** “make a 3D model of a wooden chair” → a model you can turn with the mouse, saved to
`Documents\PLAG\3D`. NVIDIA's hosted TRELLIS is flaky (Sept 2026: over half the requests hung ~90 s and failed), so PLAG
calls it directly, runs two attempts at once (a third after 30 s) and keeps the first finished model. Text only:
NVIDIA's image mode accepts only its own sample pictures. Key: `PLAG / nvidia_trellis_api_key`.

**Weather:** “what's the weather” (where you are), “will it rain tomorrow in Pune”, “delhi mein kal ka mausam” →
Open-Meteo, free. “Run a weather simulation” / “simulate the wind” → NVIDIA FourCastNet maps of the whole planet for 48
hours, played on the dashboard (it starts from NVIDIA's sample atmosphere, not today's live weather). Key:
`PLAG / nvidia_weather_api_key`.

**Location:** “where am I”, “what's my address”, “main kahan hoon” → Windows Location (~70 m) and the address from
OpenStreetMap. Kept in memory only. ⚙ Settings → *Use my location*.

**Memory use:** measured on this laptop, committed memory went from ~3.1 GB to ~1.6 GB. numpy's per-CPU buffers are
off, the always-on listener uses Whisper *tiny* (0.4 s per phrase), the more accurate *base* loads only when a command
needs it and is freed after 10 idle minutes, the local voice isn't loaded while ElevenLabs speaks, and the dashboard
stops drawing while it's in the tray. ⚙ Settings → *Clear cache* removes saved spoken replies and web caches.

**ElevenLabs (your account):** ⚙ Settings → paste your API key (it's checked, then kept in Credential Manager). The key
starts with `sk_` and is shown only once when you create it; the key's *ID* won't work. With it: realtime hearing
(words appear while you speak), streamed voice (it starts talking in ~0.1 s), and every reply in your voice.
PLAG then speaks with your ElevenLabs voice (Flash v2.5 by default: fastest, half the credits) and hears longer
commands with Scribe. Credits are protected: “PLAG” is always detected on this laptop, short commands stay
on-device, every spoken phrase is cached, long replies use the local voice, and below 10% of the monthly quota PLAG
falls back to the local voice until it resets. Without a key everything works with the local voice and hearing.

**Images** (FLUX.1-dev on NVIDIA, about 6–8 s): “generate an image of a cat astronaut”, “draw a tiger in the snow”,
“make a wallpaper of a neon Tokyo street” (16:9), “ek sher ki tasveer banao”. “Gen an image of this” (or “iski image
banao”) uses the camera: Gemini describes what you're showing and FLUX draws it, so it's a re-creation, not an edit
(NVIDIA's hosted editing models only accept its own sample images). Every image is saved to `Pictures\PLAG` and shown
on the dashboard with Open / Show in folder. Key: Credential Manager, `PLAG / nvidia_image_api_key`.

**The left column is four sections**, picked with the buttons at its top (PLAG remembers which one you last used):
*Usage* (CPU, memory, disk, network and the heaviest apps), *Climate* (now and the next two days where you are, from
Open-Meteo), *Time* (a live clock and your next reminder) and *News* (today's headlines, the same sources PLAG's
reports use). Each has a button that hands the topic to PLAG out loud.

**Computer use: PLAG working in your apps.** ⚙ Settings → *Computer use* (off until you turn it on). Then jobs that
need an app's own screen are done by PLAG itself: “in Word, change the phone number in my resume to …”, “total column C
in Excel”, “rename these files”, “fill this form with my details”. It reads each window through Windows' accessibility
interface (the same one a screen reader uses), so it clicks the *real* “Save” button by name instead of guessing pixel
positions, then looks again after every step. Each step shows live in the Now panel.

It stops instantly when you move the mouse to another window, press Esc, or hit Halt. It **asks first** on an approval
card before anything risky (delete, send, pay, uninstall, shut down, discard) and only acts on your “yes”. It **never**
types into a password box, and **never** drives terminals, PowerShell, the registry editor, Windows Settings,
Credential Manager, password managers, or any sign-in or payment window. Text on screen is data it reads, never
instructions it follows. You can limit it to named apps (e.g. `notepad.exe, winword.exe, excel.exe`) in Settings.

**Autopilot (agent mode):** give PLAG a goal and it works through it on its own, one step at a time: the AI picks a
tool, PLAG runs it (same checks and audit log as any command), reads the result and decides the next step, up to 8
steps. The Now panel shows each step live. “Brief me” / “plan my day” → weather, calendar, important mail, new
messages, reminders and top news, then the 2–3 things that need you first. Any goal: “autopilot: find the best
laptop under 60k and write me a report”, “check my mail and remind me about anything urgent”, “agent, research
the monsoon forecast and draw a poster about it”. PLAG also switches to autopilot by itself when a request needs
several steps that depend on each other. It never sends messages, calls, deletes, installs or pays on its own: when
a goal needs a message sent, it drafts it and asks you.

**TinyFish (web search + web agent):** answers to “who is…” / “look up…”, news reports and the autopilot use
TinyFish's search and read the top pages in full (free). For things only a website can tell you, the **web agent**
browses the site in TinyFish's cloud browser and reports back: “check the price of iPhone 16 on Flipkart”, “train
timings from Ahmedabad to Mumbai on IRCTC”, “today's show times at PVR”. The Now panel shows what it's doing. It only
reads: PLAG tells it never to buy, book, sign in, post or send anything. It uses TinyFish credits, so PLAG only sends it
when a plain search can't answer.

**Cal.com (meetings):** “what meetings do I have tomorrow”, “when am I free tomorrow for a 30 min call”, “share my
booking link” (copied for you), “book a 30 min call with Ananya at ananya@example.com tomorrow 5 pm”, “cancel my
meeting with Rahul”, “move my call with Rahul to Friday 4 pm”. Booking, cancelling and moving email the other person,
so PLAG shows a confirmation card first and waits for your “yes”. The briefing includes today's Cal.com meetings, and
when someone in your inbox asks to meet, PLAG's drafted reply can offer your booking link.

Add both keys in ⚙ Settings → **Keys** (each is checked with its service before it's saved).

**Keys that make PLAG better** (all optional except Gemini; each one saved the same way as step 1 of setup, name
after `PLAG /`):

| Key name | What it adds | Where to get it |
|---|---|---|
| `gemini_api_key` | The main brain, camera and screen reading (required) | aistudio.google.com |
| `nvidia_glm_api_key`, `nvidia_muse_api_key` | GLM 5.3 Flash and Muse race Gemini (commands, answers, drafts, autopilot) | build.nvidia.com |
| `nvidia_llm_api_key` | gpt-oss-20b and mistral-nemotron in the race | build.nvidia.com |
| `nvidia_hearing_api_key` | Parakeet: hears English, Hindi and Hinglish in ~0.3 s | build.nvidia.com |
| `nvidia_speech_api_key` | Leo, the streamed voice | build.nvidia.com |
| `nvidia_image_api_key`, `nvidia_trellis_api_key`, `nvidia_weather_api_key` | Images, 3D models, the weather simulation | build.nvidia.com |
| `groq_api_key` | Llama 3.3 70B on Groq: usually the fastest answer of all | console.groq.com (free) |
| `tinyfish_api_key` | Web search and page reading (free), plus a web agent that does tasks on real websites (uses TinyFish credits) | agent.tinyfish.ai → API keys |
| `calcom_api_key` | Cal.com: your meetings, free slots, booking link, and booking / cancelling / moving by voice | Cal.com → Settings → Security |
| `tavily_api_key` | Backup web search when TinyFish isn't set up | tavily.com (1,000 free a month) |
| `fishaudio_api_key` | The **Jarvis voice** on Fish Audio, PLAG's first voice once saved (Settings → Voice → Jarvis) | fish.audio → API keys |
| `sarvam_api_key` | Indian voices (Settings → Voice) | dashboard.sarvam.ai |
| `elevenlabs_api_key` | Your own voice and realtime hearing (Settings → ElevenLabs) | elevenlabs.io |
| `olamaps_api_key` | Directions with live traffic in India | maps.olakrutrim.com |
| Google OAuth client (a file, not a key) | Gmail + Calendar, read-only (Connections → Connect) | console.cloud.google.com |

**Inbox agent: your accounts (Gmail, LinkedIn, Instagram, X, anything):** Connections → *Your accounts* → **+ Connect**,
type the site's address (`linkedin.com`) or pick one of the chips. PLAG opens the real sign-in page in its own window;
you sign in there (PLAG never sees your password) and close it. Each account keeps its own private, signed-in session.
From then on PLAG keeps that site's messages page open in the background and catches its "new message" notifications
and unread count. Every new message lands in the **Inbox** tab with a one-line summary and a **reply PLAG drafted**
(Gemini, GLM and Muse race to write it, in the message's own language). Edit it, **Copy reply**, and **Open** the chat
to paste and send it yourself: PLAG drafts, it never sends on these accounts. Tell it how to answer (“yes, 5 pm works”,
“decline politely”) and it redrafts. By voice: “any new messages?”, “koi naya message?”, “reply to Ananya's LinkedIn
message saying I'm interested”. Security codes and sign-in alerts are never drafted or read out. Up to 6 accounts are
watched at once (each is a browser page, ~100–200 MB); pause or disconnect one with its buttons (disconnecting signs
it out and wipes its cookies). Messages are kept 14 days in `plag.db`. ⚙ Settings → *Inbox agent* turns watching,
drafting and announcing on or off, and sets your name for replies written as you. **Google refuses to sign in inside an app window** (“this browser or app may not be secure”), so for Gmail use
*Gmail + Calendar* below instead: PLAG checks it for new mail every two minutes and it feeds the same Inbox tab.
Other sites (LinkedIn, Instagram, X, Slack…) sign in normally with your email and password.
If a site won't connect, its row in Connections shows why, and
`%LOCALAPPDATA%\PLAG\logs\accounts.log` has the details.

**Gmail + Calendar** (read-only): “check my important emails”, “any new mail?”, “what's my schedule tomorrow”,
“aaj ka schedule”. One-time setup, done by you because it's tied to your Google account:
1. [console.cloud.google.com](https://console.cloud.google.com) → create a project → *APIs & Services* → enable the
   **Gmail API** and the **Google Calendar API**.
2. *OAuth consent screen* → External → add your own Gmail address as a test user.
3. *Credentials* → *Create credentials* → *OAuth client ID* → **Desktop app** → download the JSON.
4. In PLAG → Connections → *Gmail + Calendar* → **Connect** → pick that JSON → sign in in the browser.
While the Google app is in *Testing*, Google ends the sign-in after 7 days; PLAG then shows *Connect* again.

**WhatsApp:** say "send hi to Rahul from WhatsApp", "call Rahul on WhatsApp", "mummy ko video call karo", "Rahul ko whatsapp pe bolo ki kal milte hain" or "wish Priya happy
birthday on WhatsApp" (the AI writes the wish). There's no contact list in PLAG: it drives WhatsApp Desktop's own search,
so everyone in your WhatsApp works. Before pressing Enter it checks that the chat that opened matches the name you
said and that the message box holds your message, and it stops the moment you switch to another window. Halt stops it too.
If you say only a first name and two chats fit ("Rahul Sharma", "Rahul Verma"), PLAG asks which one; answer with the
name, "the second one" or "dusra".

Every tool call is written to a hash-chained audit log in `%LOCALAPPDATA%\PLAG\logs\audit.jsonl`.
