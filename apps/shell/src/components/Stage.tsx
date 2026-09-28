import { useEffect, useMemo, useRef, useState } from 'react';
import { camera } from '../lib/camera';
import { greeting } from '../lib/format';
import { live } from '../lib/live';
import {
  askAboutPicture, decideApproval, lookThroughCamera, openDoc, openImage, openModel3d, pictureToJpeg, revealDoc, revealImage,
  revealModel3d, revealSim, saveDraft, sendText, setAlwaysListen, toggleCamera, toggleListening,
} from '../lib/voice';
import { selectStatus, useStore, type Status } from '../state/store';
import HeroOrb from './HeroOrb';
import NavView from './NavView';
import {
  AttachIcon, CameraIcon, CloseIcon, DocIcon, FolderIcon, KeyboardIcon, ListenIcon, MicIcon, OpenIcon, PauseIcon, PlayIcon,
  CalendarIcon, SendIcon, StopSquare, WhatsAppIcon,
} from './icons';

type Picture = { jpeg: string; url: string; name: string };

const STATUS_LABEL: Record<Status, string> = {
  booting: 'Starting',
  idle: 'Online',
  listening: 'Listening',
  thinking: 'Thinking',
  executing: 'Executing',
  speaking: 'Speaking',
  halted: 'Halted',
  offline: 'Offline',
  watching: 'Camera on',
};

const SUGGESTIONS = ['YouTube pe lo-fi chalao', 'Why is my laptop slow?', 'yeh kya hai?', 'search NVIDIA news in chrome'];

function Reply() {
  const reply = useStore((s) => s.reply);
  const replyId = useStore((s) => s.replyId);
  const replyLang = useStore((s) => s.replyLang);
  const pending = useStore((s) => s.pending);
  const speaking = useStore((s) => s.speaking);
  const uiLang = useStore((s) => s.lang);
  const coreUp = useStore((s) => s.coreUp);
  const wakeListening = useStore((s) => s.wake.state === 'listening');
  const started = useStore((s) => s.messages.length > 0 || !!s.error);
  const words = useMemo(() => reply.split(/\s+/).filter(Boolean), [reply]);
  const [lit, setLit] = useState(Infinity);

  // Words light up in step with PLAG's voice, like the reference's faded tail.
  useEffect(() => {
    if (!speaking) {
      setLit(Infinity);
      return;
    }
    let raf = 0;
    let shown = -1;
    const tick = () => {
      const n = Math.ceil(live.speakProgress * words.length + 0.6);
      if (n !== shown) setLit((shown = n));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [speaking, words.length]);

  if (pending) return <h1 className="reply muted">{uiLang === 'hi' ? 'एक सेकंड…' : 'On it.'}</h1>;
  if (!reply) {
    if (started) return null; // after a failure the error line speaks for itself
    return (
      <>
        <h1 className="reply greet">{coreUp ? greeting() : 'Starting PLAG…'}</h1>
        <p className="wake-hint">{wakeListening ? 'Say “PLAG” from anywhere, or try:' : 'Tap the mic, or try:'}</p>
        <div className="suggest" aria-label="Try saying">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="chip" disabled={!coreUp} onClick={() => void sendText(s)}>
              {s}
            </button>
          ))}
        </div>
      </>
    );
  }
  return (
    <h1 className="reply" key={replyId} lang={replyLang === 'hi' ? 'hi' : undefined}>
      {words.map((w, i) => (
        <span key={i} className={i < lit ? 'w lit' : 'w'} style={{ animationDelay: `${i * 26}ms` }}>
          {w}{' '}
        </span>
      ))}
    </h1>
  );
}

function ApprovalCard() {
  const a = useStore((s) => s.approval);
  const listening = useStore((s) => s.listening);
  const [left, setLeft] = useState(1);
  useEffect(() => {
    if (!a) return;
    const total = Math.max(1, a.expiresAt - Date.now());
    const id = window.setInterval(() => {
      const f = Math.max(0, (a.expiresAt - Date.now()) / total);
      setLeft(f);
      if (f <= 0) useStore.getState().setApproval(null);
    }, 250);
    return () => window.clearInterval(id);
  }, [a]);
  if (!a) return null;
  return (
    <div className="approval" role="dialog" aria-label={a.kind === 'whatsapp' ? `Send WhatsApp message to ${a.name}` : a.name}>
      <div className="ap-head">
        {a.kind === 'calendar' ? <CalendarIcon /> : a.kind === 'computer' ? <KeyboardIcon /> : <WhatsAppIcon />}
        {a.kind === 'whatsapp' ? <span>WhatsApp to <b>{a.name}</b></span> : <span><b>{a.name}</b></span>}
        {a.phone_tail ? <span className="ap-tail mono">••{a.phone_tail}</span> : null}
      </div>
      <p className="ap-msg">{a.message}</p>
      <div className="ap-actions">
        <button className="ap-cancel" onClick={() => void decideApproval(false)}>Cancel</button>
        <button className="ap-send" onClick={() => void decideApproval(true)} autoFocus>{a.kind === 'whatsapp' ? 'Send' : 'Confirm'}</button>
      </div>
      <div className="ap-timer" aria-hidden="true"><i style={{ transform: `scaleX(${left})` }} /></div>
      <p className="ap-hint">{listening ? 'Listening… say “yes” or “haan”, or “cancel”' : a.kind === 'whatsapp' ? 'Or say “PLAG, send it”' : 'Or say “PLAG, yes”'}</p>
    </div>
  );
}

function CameraView() {
  const cameraOn = useStore((s) => s.cameraOn);
  const vision = useStore((s) => s.vision);
  const busy = useStore((s) => s.pending);
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    camera.attach(ref.current);
    return () => camera.attach(null);
  }, [cameraOn]);
  if (!cameraOn) return null;
  return (
    <>
      <video ref={ref} className={`cam ${busy ? 'scanning' : ''}`} autoPlay muted playsInline aria-label="Camera preview" />
      {busy ? <span className="cam-scan" aria-hidden="true" /> : null}
      {vision && !busy ? <span className="cam-label">{vision.label}</span> : null}
      <button className="cam-ask" disabled={busy} onClick={() => void lookThroughCamera()}>
        What is this?
      </button>
    </>
  );
}

/** The picture PLAG just drew, in place of the orb until you close it. */
function ImageView() {
  const image = useStore((s) => s.image);
  if (!image) return null;
  return (
    <figure className="gen-img">
      <img src={image.url} alt={image.prompt} />
      <figcaption>
        <span className="gen-prompt" title={image.prompt}>{image.prompt}</span>
        <span className="gen-actions">
          {image.local ? null : (
            <>
              <button onClick={() => void openImage(image.id)} aria-label="Open in Photos" title="Open in Photos"><OpenIcon /></button>
              <button onClick={() => void revealImage(image.id)} aria-label="Show in folder" title="Show in Pictures\PLAG"><FolderIcon /></button>
            </>
          )}
          <button onClick={() => useStore.getState().setImage(null)} aria-label="Close the image" title="Close"><CloseIcon /></button>
        </span>
      </figcaption>
    </figure>
  );
}

/** A PDF PLAG just wrote (an essay, a letter, a news report): nothing opens until you ask. */
function DocView() {
  const doc = useStore((s) => s.doc);
  if (!doc) return null;
  const meta = doc.sources ? `${doc.sources} sources · PDF` : doc.words ? `${doc.words} words · PDF` : 'PDF';
  return (
    <figure className="gen-img gen-doc">
      <div className="doc-body">
        <span className="doc-icon"><DocIcon /></span>
        <span className="gen-tag mono">{doc.kind.toUpperCase()} · {meta}</span>
        <h2 className="doc-title">{doc.title}</h2>
        {doc.summary ? <p className="doc-summary">{doc.summary}</p> : null}
        <div className="doc-actions">
          <button className="doc-open" onClick={() => void openDoc(doc.id)}><OpenIcon /> Open PDF</button>
          {doc.saved ? (
            <button className="doc-folder" onClick={() => void revealDoc(doc.id)}><FolderIcon /> Show in folder</button>
          ) : (
            <button className="doc-folder" onClick={() => void saveDraft(doc.id)} title="Not on your laptop yet: saves it to Documents\PLAG">
              <FolderIcon /> Save
            </button>
          )}
        </div>
      </div>
      <button className="doc-close" onClick={() => useStore.getState().setDoc(null)} aria-label="Close" title="Close"><CloseIcon /></button>
    </figure>
  );
}

let viewerLoad: Promise<unknown> | null = null; // the 3D viewer (three.js inside) loads the first time it's needed

/** The 3D model PLAG just built: drag to turn it, scroll to zoom. */
function ModelView() {
  const model = useStore((s) => s.model3d);
  const [ready, setReady] = useState(() => !!customElements.get('model-viewer'));
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!model || ready) return;
    viewerLoad ??= import('@google/model-viewer');
    viewerLoad.then(() => setReady(true), () => setFailed(true));
  }, [model, ready]);
  if (!model) return null;
  return (
    <figure className="gen-img gen-3d">
      {ready ? (
        <model-viewer src={model.url} alt={`3D model: ${model.prompt}`} camera-controls="" auto-rotate="" shadow-intensity="1"
          exposure="1.05" interaction-prompt="none" touch-action="pan-y" />
      ) : (
        <p className="gen-wait">{failed ? 'The 3D viewer couldn’t load. Open the model in 3D Viewer instead.' : 'Loading the 3D viewer…'}</p>
      )}
      <span className="gen-tag mono">TRELLIS · 3D</span>
      <figcaption>
        <span className="gen-prompt" title={model.prompt}>{model.prompt}</span>
        <span className="gen-actions">
          <button onClick={() => void openModel3d(model.id)} aria-label="Open in 3D Viewer" title="Open in 3D Viewer"><OpenIcon /></button>
          {model.saved ? (
            <button onClick={() => void revealModel3d(model.id)} aria-label="Show in folder" title="Show in Documents\PLAG\3D"><FolderIcon /></button>
          ) : (
            <button className="save-btn" onClick={() => void saveDraft(model.id)} aria-label="Save the 3D model"
              title="Not on your laptop yet: saves it to Documents\PLAG\3D">Save</button>
          )}
          <button onClick={() => useStore.getState().setModel3d(null)} aria-label="Close the 3D model" title="Close"><CloseIcon /></button>
        </span>
      </figcaption>
    </figure>
  );
}

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

/** A FourCastNet simulation: the planet's weather every 6 hours, played like a short film you can scrub. */
function SimView() {
  const sim = useStore((s) => s.sim);
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    setI(0);
    setPlaying(!reducedMotion());
  }, [sim?.id]);
  useEffect(() => {
    if (!sim || !playing || sim.urls.length < 2) return;
    const id = window.setInterval(() => setI((n) => (n + 1) % sim.urls.length), 650);
    return () => window.clearInterval(id);
  }, [sim, playing]);
  if (!sim) return null;
  const hour = sim.hours[i] ?? 0;
  return (
    <figure className="gen-img gen-sim">
      <img src={sim.urls[i]} alt={`Simulated global ${sim.label}, ${hour} hours ahead`} />
      <span className="gen-tag mono">FourCastNet · {sim.label} · +{hour} h</span>
      <figcaption>
        <button className="sim-play" onClick={() => setPlaying((p) => !p)} aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <input className="sim-scrub" type="range" min={0} max={sim.urls.length - 1} value={i} aria-label="Hours ahead"
          aria-valuetext={`${hour} hours ahead`} onChange={(e) => { setPlaying(false); setI(Number(e.target.value)); }} />
        <span className="gen-actions">
          <button onClick={() => void revealSim(sim.id)} aria-label="Show in folder" title="Show in Pictures\PLAG\Weather"><FolderIcon /></button>
          <button onClick={() => useStore.getState().setSim(null)} aria-label="Close the simulation" title="Close"><CloseIcon /></button>
        </span>
      </figcaption>
    </figure>
  );
}

function Dock({ typing, onType, onAttach }: { typing: boolean; onType: () => void; onAttach: () => void }) {
  const status = useStore(selectStatus);
  const halted = useStore((s) => s.halted);
  const coreUp = useStore((s) => s.coreUp);
  const cameraOn = useStore((s) => s.cameraOn);
  const busy = useStore((s) => s.pending || s.speaking);
  const always = useStore((s) => s.alwaysListen);
  const listening = status === 'listening';
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let raf = 0;
    let lvl = 0;
    let shown = '';
    const tick = () => {
      const t = Math.max(live.mic, live.speak, live.ambient * 0.5);
      lvl += (t - lvl) * 0.25;
      const v = lvl < 0.002 ? '0' : lvl.toFixed(3);
      if (v !== shown) wrap.current?.style.setProperty('--lvl', (shown = v)); // no style work while silent
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="dock">
      <button className={`dock-btn ear ${always ? 'on' : ''}`} aria-pressed={always} disabled={halted || !coreUp}
        onClick={() => void setAlwaysListen(!always)}
        aria-label={always ? 'Always listening is on: turn it off to answer only after “PLAG”' : 'Always listening: no “PLAG” needed'}
        title={always ? 'Always listening · click to answer only after “PLAG”' : 'Only after “PLAG” · click to always listen'}>
        <ListenIcon />
      </button>
      <button className={`dock-btn type ${typing ? 'on' : ''}`} aria-pressed={typing} onClick={onType}
        aria-label={typing ? 'Hide the command box' : 'Type a command'} title="Type a command">
        <KeyboardIcon />
      </button>
      <div className={`mic-wrap ${listening ? 'live' : ''}`} ref={wrap}>
        <span className="ring r3" />
        <span className="ring r2" />
        <span className="ring r1" />
        <button className="mic" onClick={() => void toggleListening()} disabled={halted || !coreUp}
          aria-label={listening ? 'Stop listening and send' : busy ? 'Interrupt PLAG' : 'Talk to PLAG'}>
          {listening || busy ? <StopSquare /> : <MicIcon />}
        </button>
      </div>
      <button className={`dock-btn cam-btn ${cameraOn ? 'on' : ''}`} onClick={() => void toggleCamera()} disabled={halted}
        aria-pressed={cameraOn} aria-label={cameraOn ? 'Turn the camera off' : 'Turn the camera on'} title="Camera">
        {cameraOn ? <CloseIcon /> : <CameraIcon />}
      </button>
      <button className="dock-btn cam-btn" onClick={onAttach} disabled={halted || !coreUp}
        aria-label="Attach a picture and ask about it" title="Attach a picture">
        <AttachIcon />
      </button>
    </div>
  );
}

function CommandBox({ onClose, picture, onAttach, onDrop }: {
  onClose: () => void; picture: Picture | null; onAttach: () => void; onDrop: () => void;
}) {
  const [value, setValue] = useState('');
  return (
    <form className="command" onSubmit={(e) => {
      e.preventDefault();
      const v = value;
      setValue('');
      if (picture) {
        onDrop();
        void askAboutPicture(picture.jpeg, picture.url, v.trim());
      } else void sendText(v);
    }}>
      {picture ? (
        <span className="attached" title={picture.name}>
          <img src={picture.url} alt="" />
          <button type="button" onClick={() => { URL.revokeObjectURL(picture.url); onDrop(); }} aria-label="Remove the picture"><CloseIcon /></button>
        </span>
      ) : (
        <button type="button" className="attach" onClick={onAttach} aria-label="Attach a picture" title="Attach a picture"><AttachIcon /></button>
      )}
      <input id="command-input" autoFocus value={value} onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === 'Escape' && onClose()}
        placeholder={picture ? 'Ask about this picture (or just press Enter)' : 'Type a command in English या हिंदी'}
        aria-label="Command" autoComplete="off" spellCheck={false} />
      <button type="submit" className="send" disabled={!value.trim() && !picture} aria-label="Send">
        <SendIcon />
      </button>
    </form>
  );
}

export default function Stage() {
  const status = useStore(selectStatus);
  const heard = useStore((s) => s.heard);
  const heardLang = useStore((s) => s.heardLang);
  const error = useStore((s) => s.error);
  const notice = useStore((s) => s.notice);
  const woke = useStore((s) => s.woke);
  const cameraOn = useStore((s) => s.cameraOn);
  const hasImage = useStore((s) => !!(s.image || s.model3d || s.sim || s.doc || s.route));
  const asking = useStore((s) => !!s.approval);
  const followUp = useStore((s) => s.followUp);
  const always = useStore((s) => s.alwaysListen);
  const [typing, setTyping] = useState(false);
  const [flash, setFlash] = useState(false);
  const [picture, setPicture] = useState<Picture | null>(null);
  const filePick = useRef<HTMLInputElement>(null);
  const attach = () => filePick.current?.click();
  const picked = async (file: File | undefined) => {
    if (!file) return;
    try {
      const { jpeg, url } = await pictureToJpeg(file);
      setPicture((old) => {
        if (old) URL.revokeObjectURL(old.url);
        return { jpeg, url, name: file.name };
      });
      setTyping(true);
    } catch {
      useStore.getState().setError('That file isn’t a picture PLAG can read. Try a JPG or PNG.');
    }
  };

  useEffect(() => {
    if (!woke) return;
    setFlash(true);
    const id = window.setTimeout(() => setFlash(false), 900);
    return () => window.clearTimeout(id);
  }, [woke]);

  return (
    <section className={`stage st-${status} ${flash ? 'woke' : ''} ${asking ? 'has-approval' : ''}`} aria-label="PLAG">
      <div className="stage-head">
        <span className="pill">PLAG</span>
        <span className="status-line"><i />{STATUS_LABEL[status]}</span>
      </div>
      <div className={`orb-wrap ${cameraOn ? 'with-cam' : ''} ${hasImage ? 'with-image' : ''}`}>
        <div className="orb-glow" />
        <CameraView />
        <ImageView />
        <ModelView />
        <SimView />
        <DocView />
        <NavView />
        <HeroOrb />
      </div>
      <div className="speech" aria-live="polite">
        {heard ? (
          <p className="heard">
            <span className="who">You</span>
            <span lang={heardLang === 'hi' ? 'hi' : undefined}>{heard}</span>
          </p>
        ) : null}
        <Reply />
        {followUp ? (
          <p className="follow-hint">
            <i aria-hidden="true" />
            {followUp === 'answer' ? 'Listening for your answer · no “PLAG” needed' : 'Still listening · just say it, no “PLAG” needed'}
          </p>
        ) : null}
        {error ? <p className="line-error" role="alert">{error}</p> : notice ? <p className="line-notice">{notice}</p> : null}
        <ApprovalCard />
      </div>
      <Dock typing={typing} onType={() => setTyping((t) => !t)} onAttach={attach} />
      <input ref={filePick} type="file" accept="image/*" hidden
        onChange={(e) => { void picked(e.target.files?.[0]); e.target.value = ''; }} />
      {typing ? (
        <CommandBox onClose={() => setTyping(false)} picture={picture} onAttach={attach} onDrop={() => setPicture(null)} />
      ) : (
        <p className="hint">
          <span>{always ? <>Always listening · just talk</> : <>Say <kbd>PLAG</kbd></>}</span>
          <span><kbd>Space</kbd> hold to talk</span>
          <span><kbd>Ctrl</kbd> <kbd>Space</kbd> from any app</span>
          <span><kbd>Esc</kbd> stop</span>
        </p>
      )}
    </section>
  );
}
