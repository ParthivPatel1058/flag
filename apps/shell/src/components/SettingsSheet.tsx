import { useEffect, useState } from 'react';
import {
  clearCaches, elevenVoices, loadKeyFile, loadKeys, loadSettings, removeKey, saveKey, removeElevenKey, removeFishKey, removeSarvamKey, saveElevenKey, saveFishKey, saveSarvamKey,
  saveSettings,
  type ElevenStatus, type KeyFile, type KeyService, type Live, type Settings, type VoiceEngine,
} from '../lib/voice';
import { CloseIcon } from './icons';

type Voice = { id: string; name: string; category: string };

const KEY_ROWS: { id: KeyService; name: string; what: string; where: string; url: string }[] = [
  { id: 'tinyfish', name: 'TinyFish', what: 'Web search, page reading and a web agent that works on websites for you', where: 'agent.tinyfish.ai → API keys', url: 'https://agent.tinyfish.ai/api-keys' },
  { id: 'calcom', name: 'Cal.com', what: 'Your meetings, free slots, booking link, and booking by voice (asks you first)', where: 'Cal.com → Settings → Security', url: 'https://app.cal.com/settings/developer/api-keys' },
  { id: 'groq', name: 'Groq', what: 'The fastest answers (Llama 3.3 70B), free', where: 'console.groq.com → API keys', url: 'https://console.groq.com/keys' },
  { id: 'tavily', name: 'Tavily', what: 'Backup web search when TinyFish isn’t set up', where: 'tavily.com → API keys', url: 'https://app.tavily.com' },
  { id: 'github', name: 'GitHub', what: 'The CodeRabbit agent: reads pull requests and posts its reviews', where: 'github.com → Settings → Developer settings → Personal access tokens', url: 'https://github.com/settings/tokens' },
];

/** Optional keys: each is checked with its service, then kept in Windows Credential Manager (never shown again). */
function KeysSection() {
  const [keys, setKeys] = useState<Record<KeyService, boolean> | null>(null);
  const [open, setOpen] = useState<KeyService | null>(null);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [file, setFile] = useState<KeyFile | null>(null);
  useEffect(() => { void loadKeys().then(setKeys); void loadKeyFile().then(setFile); }, []);
  const save = async (id: KeyService) => {
    setBusy(true);
    const err = await saveKey(id, value);
    setBusy(false);
    setValue('');
    setMsg(err ? { ok: false, text: err } : { ok: true, text: 'Saved in Windows Credential Manager.' });
    if (!err) { setOpen(null); setKeys(await loadKeys()); }
  };
  return (
    <section className="set-sec">
      <h3>Keys</h3>
      <p className="set-note">Optional connections. Each key is checked with its service, then kept in Windows Credential Manager; PLAG never shows it again.</p>
      {file ? (
        <p className={`set-note ${file.found ? 'ok' : ''}`}>
          {file.found
            ? `Your keys file loaded ${file.keys.length} key${file.keys.length === 1 ? '' : 's'} at startup, so you don’t have to type any of these: ${file.path}`
            : `Tip: run setup-keys.ps1 once and PLAG loads every key by itself from ${file.path} — no typing here again.`}
        </p>
      ) : null}
      {KEY_ROWS.map((k) => (
        <div key={k.id} className="key-row">
          <div className="set-row">
            <span><b>{k.name}</b> · {k.what}</span>
            {keys?.[k.id] ? (
              <span className="key-actions">
                <span className="set-status ok">Connected</span>
                <button className="link-btn" onClick={() => void removeKey(k.id).then(loadKeys).then(setKeys)}>Remove</button>
              </span>
            ) : (
              <button className="link-btn" onClick={() => { setOpen(open === k.id ? null : k.id); setMsg(null); setValue(''); }}>
                {open === k.id ? 'Cancel' : 'Add key'}
              </button>
            )}
          </div>
          {open === k.id ? (
            <form className="key-form" onSubmit={(e) => { e.preventDefault(); void save(k.id); }}>
              <input type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder={`${k.name} API key`}
                aria-label={`${k.name} API key`} autoComplete="off" spellCheck={false} autoFocus />
              <button type="submit" disabled={busy || value.trim().length < 10}>{busy ? 'Checking…' : 'Connect'}</button>
            </form>
          ) : null}
          {open === k.id ? <p className="set-note">Get it at <a href={k.url} target="_blank" rel="noreferrer">{k.where}</a>.</p> : null}
        </div>
      ))}
      {msg ? <p className={`set-status ${msg.ok ? 'ok' : 'warn'}`}>{msg.text}</p> : null}
    </section>
  );
}

const ENGINES: { id: VoiceEngine; label: string; title: string }[] = [
  { id: 'auto', label: 'Auto', title: 'The best voice available: Jarvis on Fish Audio or Sarvam once a key is saved, else Leo on NVIDIA, else Edge' },
  { id: 'fish', label: 'Jarvis', title: 'Fish Audio: a Jarvis voice from its voice library (your key)' },
  { id: 'sarvam', label: 'Sarvam', title: 'Sarvam AI: Indian voices for Hindi, English and Hinglish (your key)' },
  { id: 'edge', label: 'Edge', title: 'Microsoft Edge voices: free, no key or account' },
  { id: 'nvidia', label: 'NVIDIA', title: 'Leo on NVIDIA: streamed, starts talking in ~0.3 s' },
  { id: 'elevenlabs', label: 'ElevenLabs', title: 'Your ElevenLabs voice (uses your credits)' },
  { id: 'local', label: 'Offline', title: 'On this laptop, no internet (~350 MB of memory while it talks)' },
];
const ENGINE_NAMES: Record<string, string> = {
  fish: 'Jarvis on Fish Audio', sarvam: 'Sarvam AI', nvidia: 'Leo on NVIDIA', edge: 'Microsoft Edge', elevenlabs: 'ElevenLabs', local: 'the offline voice', none: 'nobody (text only)',
};
const EDGE_NAMES: Record<string, string> = {
  'hi-IN-MadhurNeural': 'Madhur · Hindi · male',
  'en-IN-PrabhatNeural': 'Prabhat · Indian English · male',
  'hi-IN-SwaraNeural': 'Swara · Hindi · female',
  'en-IN-NeerjaNeural': 'Neerja · Indian English · female',
  'en-IN-NeerjaExpressiveNeural': 'Neerja, expressive · Indian English · female',
};
// Sarvam lists its men first (see core settings.SARVAM_SPEAKERS): the first 23 are men
const SARVAM_MEN = 23;
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function Seg<T extends string>({ value, options, onChange, label }: {
  value: T; options: { id: T; label: string; title?: string }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} role="radio" aria-checked={value === o.id} className={value === o.id ? 'on' : ''} title={o.title}
          onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function credits(e: ElevenStatus): string {
  const u = e.usage;
  if (!u || !u.limit) return '';
  const left = Math.max(0, u.limit - u.used);
  const reset = u.reset ? ` · resets ${new Date(u.reset * 1000).toLocaleDateString([], { day: 'numeric', month: 'short' })}` : '';
  return `${left.toLocaleString()} of ${u.limit.toLocaleString()} credits left${reset}`;
}

/** Settings: which voice speaks (Sarvam, Edge, NVIDIA, ElevenLabs, offline), and how PLAG listens. */
export default function SettingsSheet({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [eleven, setEleven] = useState<ElevenStatus | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [key, setKey] = useState('');
  const [sarvamKey, setSarvamKey] = useState('');
  const [fishKey, setFishKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cleared, setCleared] = useState<string | null>(null);

  const refresh = async () => {
    const r = await loadSettings();
    if (!r) return;
    setSettings(r.settings);
    setEleven(r.eleven);
    setLive(r.live ?? null);
    if (r.eleven.configured) setVoices(await elevenVoices());
  };

  const connectFish = async () => {
    setBusy(true);
    setError(await saveFishKey(fishKey));
    setBusy(false);
    setFishKey('');
    await refresh();
  };

  const connectSarvam = async () => {
    setBusy(true);
    setError(await saveSarvamKey(sarvamKey));
    setBusy(false);
    setSarvamKey('');
    await refresh();
  };

  useEffect(() => {
    void refresh();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const change = async (changes: Partial<Settings>) => {
    setSettings((s) => (s ? { ...s, ...changes } : s));
    try {
      const r = await saveSettings(changes);
      setSettings(r.settings);
      setEleven(r.eleven);
      setLive(r.live ?? null);
    } catch {
      setError("Couldn't save that. Is PLAG's core running?");
    }
  };

  const connect = async () => {
    setBusy(true);
    setError(await saveElevenKey(key));
    setBusy(false);
    setKey('');
    await refresh();
  };

  return (
    <div className="sheet-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="sheet" role="dialog" aria-modal="true" aria-labelledby="settings-h">
        <header className="sheet-h">
          <h2 id="settings-h">Settings</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close settings"><CloseIcon /></button>
        </header>

        <section className="set-sec">
          <h3>Voice</h3>
          {!settings || !live ? (
            <p className="empty">Loading…</p>
          ) : (
            <>
              <div className="set-row">
                <span>Who speaks PLAG's replies</span>
                <Seg label="Voice" value={settings.voice_engine} onChange={(v) => void change({ voice_engine: v })} options={ENGINES} />
              </div>
              <p className={`set-status ${settings.voice_engine === 'auto' || settings.voice_engine === live.voice ? 'ok' : 'warn'}`}>
                Speaking now: {ENGINE_NAMES[live.voice]}
                {settings.voice_engine !== 'auto' && settings.voice_engine !== live.voice
                  ? ` · ${ENGINE_NAMES[settings.voice_engine]} isn't ready, so the next voice speaks` : ''}
              </p>

              {settings.voice_engine === 'sarvam' || (settings.voice_engine === 'auto' && !live.sarvam.configured) ? (
                !live.sarvam.configured ? (
                  <>
                    <p className="set-note">
                      Sarvam AI's Indian voices speak Hindi, English and Hinglish naturally. Paste your Sarvam API key
                      (<a href="https://dashboard.sarvam.ai" target="_blank" rel="noreferrer">dashboard.sarvam.ai → API keys</a>).
                      It's checked with Sarvam and kept in Windows Credential Manager; PLAG never shows it again.
                    </p>
                    <form className="key-form" onSubmit={(e) => { e.preventDefault(); void connectSarvam(); }}>
                      <input type="password" value={sarvamKey} onChange={(e) => setSarvamKey(e.target.value)} placeholder="Sarvam API key"
                        aria-label="Sarvam API key" autoComplete="off" spellCheck={false} />
                      <button type="submit" disabled={busy || sarvamKey.trim().length < 10}>{busy ? 'Checking…' : 'Connect'}</button>
                    </form>
                  </>
                ) : (
                  <>
                    <p className={`set-status ${live.sarvam.usable ? 'ok' : 'warn'}`}>
                      Sarvam {live.sarvam.usable ? 'connected' : `paused (${live.sarvam.error ?? 'error'}) · the next voice speaks for now`}
                    </p>
                    <label className="set-row">
                      <span>Sarvam voice</span>
                      <select value={settings.sarvam_speaker} onChange={(e) => void change({ sarvam_speaker: e.target.value })}>
                        <optgroup label="Male">
                          {live.sarvam.speakers.slice(0, SARVAM_MEN).map((s) => <option key={s} value={s}>{cap(s)}</option>)}
                        </optgroup>
                        <optgroup label="Female">
                          {live.sarvam.speakers.slice(SARVAM_MEN).map((s) => <option key={s} value={s}>{cap(s)}</option>)}
                        </optgroup>
                      </select>
                    </label>
                    <button className="link-btn" onClick={() => void removeSarvamKey().then(refresh)}>Remove Sarvam key</button>
                  </>
                )
              ) : null}

              {settings.voice_engine === 'fish' || (settings.voice_engine === 'auto' && !live.fish?.configured) ? (
                !live.fish?.configured ? (
                  <>
                    <p className="set-note">
                      Jarvis on Fish Audio: a composed, cinematic assistant voice. Paste your Fish Audio API key
                      (<a href="https://fish.audio/app/api-keys" target="_blank" rel="noreferrer">fish.audio → API keys</a>). It's
                      checked with Fish Audio and kept in Windows Credential Manager; PLAG never shows it again.
                    </p>
                    <form className="key-form" onSubmit={(e) => { e.preventDefault(); void connectFish(); }}>
                      <input type="password" value={fishKey} onChange={(e) => setFishKey(e.target.value)} placeholder="Fish Audio API key"
                        aria-label="Fish Audio API key" autoComplete="off" spellCheck={false} />
                      <button type="submit" disabled={busy || fishKey.trim().length < 10}>{busy ? 'Checking…' : 'Connect'}</button>
                    </form>
                  </>
                ) : (
                  <>
                    <p className={`set-status ${live.fish.usable ? 'ok' : 'warn'}`}>
                      Fish Audio {live.fish.usable ? `connected · ${live.fish.voice_name || 'Jarvis voice'}` : `paused (${live.fish.error ?? 'error'}) · the next voice speaks for now`}
                    </p>
                    <form className="set-row" onSubmit={(e) => {
                      e.preventDefault();
                      const v = (new FormData(e.currentTarget).get('fishvoice') as string) ?? '';
                      void change({ fish_voice_id: v.trim() });
                    }}>
                      <span>Voice ID (from a fish.audio voice page's address; empty = the most used Jarvis voice)</span>
                      <input className="city" name="fishvoice" key={settings.fish_voice_id} defaultValue={settings.fish_voice_id}
                        placeholder="auto: Jarvis" aria-label="Fish Audio voice ID" autoComplete="off" spellCheck={false}
                        onBlur={(e) => e.target.value.trim() !== settings.fish_voice_id && void change({ fish_voice_id: e.target.value.trim() })} />
                    </form>
                    <button className="link-btn" onClick={() => void removeFishKey().then(refresh)}>Remove Fish Audio key</button>
                  </>
                )
              ) : null}

              {settings.voice_engine === 'edge' ? (
                <label className="set-row">
                  <span>Edge voice (free, from Microsoft's online voices)</span>
                  <select value={settings.edge_voice} onChange={(e) => void change({ edge_voice: e.target.value })}>
                    {live.edge.voices.map((v) => <option key={v} value={v}>{EDGE_NAMES[v] ?? v}</option>)}
                  </select>
                </label>
              ) : null}

              {settings.voice_engine === 'nvidia' ? (
                <p className="set-note">
                  {live.nvidia.configured
                    ? 'Leo on NVIDIA: the same voice in Hindi and English, streamed, so PLAG starts talking in about 0.3 s.'
                    : 'No NVIDIA speech key saved (Credential Manager: PLAG / nvidia_speech_api_key).'}
                </p>
              ) : null}

              {settings.voice_engine === 'local' ? (
                <p className="set-note">The offline voice works without internet. It loads into memory (~350 MB) only while it's in use.</p>
              ) : null}

              <label className="set-row">
                <span>Speed</span>
                <input type="range" min={0.8} max={1.2} step={0.05} value={settings.speed}
                  onChange={(e) => void change({ speed: Number(e.target.value) })} aria-valuetext={`${settings.speed}x`} />
              </label>
              <label className="set-row">
                <span>Interrupt PLAG by talking over it (it stops and listens, like a person)</span>
                <input type="checkbox" checked={settings.barge_in ?? true} onChange={(e) => void change({ barge_in: e.target.checked })} />
              </label>
            </>
          )}
          {error ? <p className="line-error">{error}</p> : null}
        </section>

        {settings?.voice_engine === 'elevenlabs' ? (
        <section className="set-sec">
          <h3>ElevenLabs</h3>
          {!eleven ? (
            <p className="empty">Loading…</p>
          ) : !eleven.configured ? (
            <>
              <p className="set-note">
                Not configured, so PLAG uses its local voice and hearing. Paste your ElevenLabs API key
                (<a href="https://elevenlabs.io/app/settings/api-keys" target="_blank" rel="noreferrer">elevenlabs.io → API keys</a>).
                It's checked with ElevenLabs and kept in Windows Credential Manager; PLAG never shows it again.
              </p>
              <form className="key-form" onSubmit={(e) => { e.preventDefault(); void connect(); }}>
                <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="ElevenLabs API key"
                  aria-label="ElevenLabs API key" autoComplete="off" spellCheck={false} />
                <button type="submit" disabled={busy || key.trim().length < 20}>{busy ? 'Checking…' : 'Connect'}</button>
              </form>
            </>
          ) : (
            <>
              <p className={`set-status ${eleven.usable ? 'ok' : 'warn'}`}>
                {eleven.usable ? 'Connected' : eleven.error ? `Paused (${eleven.error}) · local voice for now` : 'Credits nearly used · local voice until reset'}
                {credits(eleven) ? ` · ${credits(eleven)}` : ''}
              </p>
              {settings ? (
                <>
                  <label className="set-row">
                    <span>Voice</span>
                    <select value={settings.eleven_voice_id} onChange={(e) => void change({ eleven_voice_id: e.target.value })}>
                      <option value="">{eleven.voice ? `${eleven.voice} (default)` : 'First voice on your account'}</option>
                      {voices.map((v) => <option key={v.id} value={v.id}>{v.name}{v.category === 'cloned' ? ' · your clone' : ''}</option>)}
                    </select>
                  </label>
                  <div className="set-row">
                    <span>Model</span>
                    <Seg label="Voice model" value={settings.eleven_model} onChange={(v) => void change({ eleven_model: v })}
                      options={[
                        { id: 'eleven_flash_v2_5', label: 'Fast', title: 'Flash v2.5: ~75 ms, half the credits per character' },
                        { id: 'eleven_v3_conversational', label: 'Expressive', title: 'v3 Conversational: more emotion, ~280 ms, full credits' },
                      ]} />
                  </div>
                  <label className="set-row">
                    <span>Talk through my ElevenLabs agent (its voice and personality; PLAG does the actions). Off: PLAG's own voice</span>
                    <input type="checkbox" checked={settings.voice_agent} onChange={(e) => void change({ voice_agent: e.target.checked })} />
                  </label>
                  <label className="set-row">
                    <span>Speak with ElevenLabs</span>
                    <input type="checkbox" checked={settings.eleven_speak} onChange={(e) => void change({ eleven_speak: e.target.checked })} />
                  </label>
                  <label className="set-row">
                    <span>Hear longer commands with ElevenLabs</span>
                    <input type="checkbox" checked={settings.eleven_hear} onChange={(e) => void change({ eleven_hear: e.target.checked })} />
                  </label>
                  <label className="set-row">
                    <span>Realtime hearing: words appear while you speak, the answer starts sooner</span>
                    <input type="checkbox" checked={settings.eleven_realtime} disabled={!settings.eleven_hear}
                      onChange={(e) => void change({ eleven_realtime: e.target.checked })} />
                  </label>
                  <div className="set-row">
                    <span>Which replies ElevenLabs speaks (the rest use the local voice)</span>
                    <Seg label="Longest reply for ElevenLabs" value={String(settings.eleven_max_chars)}
                      onChange={(v) => void change({ eleven_max_chars: Number(v) })}
                      options={[{ id: '120', label: 'Short', title: 'Saves the most credits' }, { id: '220', label: 'Most' },
                        { id: '1000', label: 'All', title: 'Every reply in your ElevenLabs voice' }]} />
                  </div>
                  <p className="set-note">
                    Credits are spent only on what's needed: “PLAG” is always heard on this laptop for free, only the command you're
                    saying is streamed (never the always-on listening), every spoken reply is cached so repeats cost nothing, and below
                    {' '}{settings.eleven_reserve_pct}% of your monthly credits PLAG switches to the local voice on its own.
                  </p>
                  <button className="link-btn" onClick={() => void removeElevenKey().then(refresh)}>Remove ElevenLabs key</button>
                </>
              ) : null}
            </>
          )}
        </section>
        ) : null}

        {settings ? (
          <section className="set-sec">
            <h3>Listening</h3>
            <div className="set-row">
              <span>How long a pause ends what you say</span>
              <Seg label="Turn taking" value={settings.turn} onChange={(v) => void change({ turn: v })}
                options={[
                  { id: 'fast', label: 'Fast', title: 'Answers quickly after short commands' },
                  { id: 'normal', label: 'Normal', title: 'About a second of pause' },
                  { id: 'patient', label: 'Patient', title: 'Waits longer, for long sentences with pauses' },
                ]} />
            </div>
            <div className="set-row">
              <span>Keep listening after PLAG answers (no “PLAG” needed)</span>
              <Seg label="Follow-up listening" value={settings.follow_up} onChange={(v) => void change({ follow_up: v })}
                options={[
                  { id: 'off', label: 'Off', title: 'Always say “PLAG” first' },
                  { id: 'questions', label: 'Questions', title: 'Only when PLAG asks you something, like “What should I send?”' },
                  { id: 'always', label: 'Always', title: 'Also 5 seconds after a spoken answer; never after opening or playing something' },
                ]} />
            </div>
            <form className="set-row" onSubmit={(e) => {
              e.preventDefault();
              const v = (new FormData(e.currentTarget).get('city') as string) ?? '';
              void change({ home_city: v.trim() });
            }}>
              <span>Your city, for “what's the weather”</span>
              <input className="city" name="city" key={settings.home_city} defaultValue={settings.home_city} placeholder="e.g. Delhi"
                aria-label="Your city" autoComplete="off" spellCheck={false}
                onBlur={(e) => e.target.value.trim() !== settings.home_city && void change({ home_city: e.target.value.trim() })} />
            </form>
            <label className="set-row">
              <span>Use my location (Windows Location) for “where am I” and the weather</span>
              <input type="checkbox" checked={settings.use_location} onChange={(e) => void change({ use_location: e.target.checked })} />
            </label>
            <div className="set-row">
              <span>Wake word sensitivity</span>
              <Seg label="Wake sensitivity" value={settings.wake_sensitivity} onChange={(v) => void change({ wake_sensitivity: v })}
                options={[
                  { id: 'low', label: 'Low', title: 'Fewer false wakes in a noisy room' },
                  { id: 'normal', label: 'Normal' },
                  { id: 'high', label: 'High', title: 'Hears a soft “PLAG” from further away' },
                ]} />
            </div>
          </section>
        ) : null}

        {settings ? (
          <section className="set-sec">
            <h3>Inbox agent</h3>
            <p className="set-note">
              New messages on the accounts you connect (Connections → Your accounts) and in Gmail show up in the Inbox tab,
              each with a reply PLAG drafted. PLAG never sends them: you copy the reply and send it yourself. To draft, the
              message goes to the AI that writes the reply.
            </p>
            <label className="set-row">
              <span>Watch my accounts for new messages</span>
              <input type="checkbox" checked={settings.inbox_agent} onChange={(e) => void change({ inbox_agent: e.target.checked })} />
            </label>
            <label className="set-row">
              <span>Draft a reply for each new message</span>
              <input type="checkbox" checked={settings.inbox_draft} onChange={(e) => void change({ inbox_draft: e.target.checked })} />
            </label>
            <label className="set-row">
              <span>Tell me about new messages out loud</span>
              <input type="checkbox" checked={settings.inbox_announce} onChange={(e) => void change({ inbox_announce: e.target.checked })} />
            </label>
            <form className="set-row" onSubmit={(e) => {
              e.preventDefault();
              const v = (new FormData(e.currentTarget).get('owner') as string) ?? '';
              void change({ inbox_owner: v.trim() });
            }}>
              <span>Your name, for replies written as you</span>
              <input className="city" name="owner" key={settings.inbox_owner} defaultValue={settings.inbox_owner} placeholder="e.g. Parthiv"
                aria-label="Your name" autoComplete="off" spellCheck={false}
                onBlur={(e) => e.target.value.trim() !== settings.inbox_owner && void change({ inbox_owner: e.target.value.trim() })} />
            </form>
          </section>
        ) : null}

        {settings ? (
          <section className="set-sec">
            <h3>Computer use</h3>
            <p className="set-note">
              PLAG can finish a job in your own apps by clicking and typing, reading each window through Windows'
              accessibility interface. It stops the moment you move the mouse, switch windows or press Esc, and it asks
              before anything risky (delete, send, pay, uninstall). It never types into a password box, and never drives
              terminals, the registry editor, Windows Settings or sign-in windows.
            </p>
            <label className="set-row">
              <span>Let PLAG click and type in my apps</span>
              <input type="checkbox" checked={settings.computer_use}
                onChange={(e) => void change({ computer_use: e.target.checked })} />
            </label>
            {settings.computer_use ? (
              <form className="set-row" onSubmit={(e) => {
                e.preventDefault();
                const v = (new FormData(e.currentTarget).get('apps') as string) ?? '';
                void change({ computer_apps: v.trim() });
              }}>
                <span>Only these apps (leave empty for any app it's allowed to touch)</span>
                <input className="city" name="apps" key={settings.computer_apps} defaultValue={settings.computer_apps}
                  placeholder="notepad.exe, winword.exe, excel.exe" aria-label="Allowed apps" autoComplete="off" spellCheck={false}
                  onBlur={(e) => e.target.value.trim() !== settings.computer_apps && void change({ computer_apps: e.target.value.trim() })} />
              </form>
            ) : null}
          </section>
        ) : null}

        <KeysSection />

        <section className="set-sec">
          <h3>Memory and storage</h3>
          <p className="set-note">
            PLAG frees speech models it hasn't used for 10 minutes and stops drawing while it's in the tray. Clearing the cache
            removes saved spoken replies (they're made again when needed) and the dashboard's web caches. Your pictures,
            3D models, reports, memories and settings stay.
          </p>
          <button className="link-btn" onClick={() => void clearCaches().then(setCleared)}>Clear cache</button>
          {cleared ? <p className="set-status ok">{cleared}</p> : null}
        </section>
      </aside>
    </div>
  );
}
