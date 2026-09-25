// Local speech: Windows voices through Chromium. Instant, free, offline, and it reports word boundaries,
// so the reply lights up word by word. Hindi needs a Windows Hindi voice; without one PLAG falls back to
// Gemini's natural voice (10 free requests a day, cached per phrase).
import { live } from './live';

let voices: SpeechSynthesisVoice[] = [];
const load = () => {
  voices = window.speechSynthesis?.getVoices() ?? [];
};
if (typeof window !== 'undefined' && window.speechSynthesis) {
  load();
  window.speechSynthesis.onvoiceschanged = load;
}

export function localVoice(lang: 'en' | 'hi'): SpeechSynthesisVoice | null {
  if (!voices.length) load();
  if (lang === 'hi') return voices.find((v) => v.lang.toLowerCase().startsWith('hi')) ?? null;
  for (const name of ['Microsoft Mark', 'Microsoft David', 'Microsoft Guy', 'Microsoft Zira']) {
    const v = voices.find((x) => x.name.startsWith(name) && x.lang.toLowerCase().startsWith('en'));
    if (v) return v;
  }
  return voices.find((v) => v.lang.toLowerCase().startsWith('en')) ?? null;
}

let pulse: number | undefined;

// Local voices can't act, but speed and pitch carry a lot of feeling.
const PROSODY: Record<string, { rate: number; pitch: number }> = {
  calm: { rate: 1.04, pitch: 0.92 },
  cheerful: { rate: 1.1, pitch: 1.08 },
  excited: { rate: 1.16, pitch: 1.16 },
  serious: { rate: 0.98, pitch: 0.84 },
  sorry: { rate: 0.94, pitch: 0.88 },
  curious: { rate: 1.06, pitch: 1.04 },
};

export function speakLocal(text: string, voice: SpeechSynthesisVoice, mood = 'calm'): Promise<void> {
  const synth = window.speechSynthesis;
  return new Promise<void>((resolve) => {
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    const p = PROSODY[mood] ?? PROSODY.calm;
    u.voice = voice;
    u.lang = voice.lang;
    u.rate = p.rate;
    u.pitch = p.pitch;
    let t = 0;
    const finish = () => {
      window.clearInterval(pulse);
      live.speak = 0;
      live.speakProgress = 1;
      resolve();
    };
    u.onstart = () => {
      live.speakProgress = 0;
      // local voices can't be metered, so the orb follows a speech-like envelope instead
      pulse = window.setInterval(() => {
        t += 0.08;
        live.speak = 0.28 + 0.42 * Math.abs(Math.sin(t * 7.3)) * (0.6 + 0.4 * Math.sin(t * 2.1));
      }, 60);
    };
    u.onboundary = (e) => {
      live.speakProgress = Math.min(1, (e.charIndex + (e.charLength || 1)) / Math.max(1, text.length));
    };
    u.onend = finish;
    u.onerror = finish;
    synth.speak(u);
  });
}

export function stopLocal() {
  window.clearInterval(pulse);
  window.speechSynthesis?.cancel();
  live.speak = 0;
}
