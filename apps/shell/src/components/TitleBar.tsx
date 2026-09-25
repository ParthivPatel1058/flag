import { useState } from 'react';
import { haltPlag, setWake } from '../lib/voice';
import { useStore, type Lang } from '../state/store';
import { EarIcon, GearIcon, Mark, PowerIcon, VolumeIcon, VolumeOffIcon } from './icons';
import SettingsSheet from './SettingsSheet';

const LANGS: { id: Lang; label: string; title: string }[] = [
  { id: 'auto', label: 'Auto', title: 'Reply in the language you speak' },
  { id: 'en', label: 'EN', title: 'Always reply in English' },
  { id: 'hi', label: 'हिं', title: 'हमेशा हिंदी में जवाब दें' },
];

function Indicator({ state, label, hint }: { state: 'off' | 'armed' | 'on'; label: string; hint: string }) {
  return (
    <span className={`ind ${state}`} title={hint}>
      <i />
      {label}
    </span>
  );
}

export default function TitleBar() {
  const listening = useStore((s) => s.listening);
  const wake = useStore((s) => s.wake);
  const wakeOn = useStore((s) => s.wakeOn);
  const cloud = useStore((s) => s.cloud);
  const cameraOn = useStore((s) => s.cameraOn);
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const voiceOn = useStore((s) => s.voiceOn);
  const setVoice = useStore((s) => s.setVoice);
  const halted = useStore((s) => s.halted);
  const armed = wake.state === 'listening';
  const [settingsOpen, setSettingsOpen] = useState(false);

  const wakeLabel = !wakeOn ? 'Wake off' : armed ? 'Say “PLAG”' : wake.state === 'error' ? 'Wake error' : 'Wake…';
  return (
    <header className="titlebar">
      <div className="brand">
        <Mark />
        <span className="wordmark">PLAG</span>
      </div>
      <div className="privacy" role="status" aria-label="Privacy indicators">
        <Indicator state={listening ? 'on' : armed ? 'armed' : 'off'} label="Mic"
          hint={listening ? 'Recording your command' : armed ? 'Listening only for “PLAG”, offline on this laptop' : 'Microphone is off'} />
        <Indicator state={cameraOn ? 'on' : 'off'} label="Camera" hint={cameraOn ? 'Camera is on' : 'Camera is off'} />
        <Indicator state={cloud ? 'on' : 'off'} label="Cloud" hint={cloud ? 'Talking to Gemini right now' : 'Nothing is being sent'} />
      </div>
      <div className="tb-actions">
        <button className={`wake-pill ${wakeOn ? wake.state : 'off'}`} aria-pressed={wakeOn}
          onClick={() => void setWake(!wakeOn)} title={wake.error ?? (wakeOn ? 'Turn off the wake word' : 'Turn on the wake word')}>
          <EarIcon />
          {wakeLabel}
        </button>
        <div className="seg" role="radiogroup" aria-label="Reply language">
          {LANGS.map((l) => (
            <button key={l.id} role="radio" aria-checked={lang === l.id} className={lang === l.id ? 'on' : ''}
              title={l.title} onClick={() => setLang(l.id)}>
              {l.label}
            </button>
          ))}
        </div>
        <button className="icon-btn" aria-pressed={voiceOn} onClick={() => setVoice(!voiceOn)}
          aria-label={voiceOn ? 'Turn off spoken replies' : 'Turn on spoken replies'}
          title={voiceOn ? 'Spoken replies on' : 'Spoken replies off'}>
          {voiceOn ? <VolumeIcon /> : <VolumeOffIcon />}
        </button>
        <button className="icon-btn" onClick={() => setSettingsOpen(true)} aria-label="Settings" title="Settings · voice, ElevenLabs, listening">
          <GearIcon />
        </button>
        <button className="halt-btn" onClick={() => void haltPlag()} disabled={halted} title="Halt PLAG · Ctrl+Alt+Shift+X">
          <PowerIcon />
          Halt
        </button>
      </div>
      {settingsOpen ? <SettingsSheet onClose={() => setSettingsOpen(false)} /> : null}
    </header>
  );
}
