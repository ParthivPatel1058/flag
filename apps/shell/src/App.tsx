import { useEffect } from 'react';
import ContourField from './components/ContourField';
import { ConnectionsPanel, ConversationPanel, HaltOverlay, NowPanel, VitalsPanel } from './components/Panels';
import Stage from './components/Stage';
import TitleBar from './components/TitleBar';
import { call, openEvents } from './lib/core';
import { onInboxNew, refreshInbox, watchAccounts } from './lib/inbox';
import {
  finishListening, haltPlag, loadSettings, onAck, onModel3dReady, onPlagSay, onReminder, onVoiceStop, onWake, primeAck,
  refreshMemory, refreshReminders, setWake,
  startListening, stopAll, syncWake, toggleListening,
} from './lib/voice';
import { useStore } from './state/store';

const isTyping = (t: EventTarget | null) =>
  t instanceof HTMLElement && t.closest('input, textarea, [contenteditable="true"]') !== null;

export default function App() {
  const halted = useStore((s) => s.halted);
  const visible = useStore((s) => s.visible);

  // live events from plag-core; "PLAG" from the wake-word listener goes straight to the voice loop
  useEffect(
    () =>
      openEvents(
        (e) => {
          if (e.topic === 'wake.detected') void onWake(e.data);
          else if (e.topic === 'voice.stop') void onVoiceStop();
          else if (e.topic === 'reminder.due') void onReminder(e.data);
          else if (e.topic === 'model3d.ready') void onModel3dReady(e.data);
          else if (e.topic === 'plag.say') void onPlagSay(e.data);
          else if (e.topic === 'task.ack') onAck(e.data);
          else if (e.topic === 'memory.changed') void refreshMemory();
          else if (e.topic === 'reminders.changed') void refreshReminders();
          else if (e.topic === 'inbox.changed') void refreshInbox();
          else if (e.topic === 'inbox.new') void onInboxNew(e.data);
          else useStore.getState().handleEvent(e);
        },
        (up) => {
          useStore.getState().setCoreUp(up);
          if (up) {
            void syncWake();
            void refreshMemory();
            void refreshReminders();
            void refreshInbox();
            void loadSettings(); // listening speed for the mic
            void primeAck(); // "Yes sir?" made and decoded now, so the first "PLAG" answers instantly
          }
        },
      ),
    [],
  );

  // connected accounts live in the desktop shell (their signed-in sessions); the list follows it
  useEffect(() => watchAccounts(), []);

  // signals from the desktop shell: Ctrl+Space and the kill switch work from any app
  useEffect(() => {
    const bridge = window.plag;
    if (!bridge) return;
    const offs = [
      bridge.onPushToTalk(() => void toggleListening()),
      bridge.onKill(() => void haltPlag()),
      bridge.onSetWake((on) => void setWake(on)), // the tray menu's "Listen for PLAG"
      // hidden in the tray: drop the drawing (memory, CPU) and the live system stats; the voice loop keeps running
      bridge.onVisible((on) => {
        useStore.getState().setVisible(on);
        call('/v1/ui/visible', { json: { visible: on } }).catch(() => undefined);
      }),
    ];
    return () => offs.forEach((off) => off());
  }, []);

  // Space: hold to talk, or tap to start and let silence end it. Esc: stop.
  useEffect(() => {
    let downAt = 0;
    const onDown = (e: KeyboardEvent) => {
      if (e.code === 'Escape') {
        void stopAll();
        return;
      }
      if (e.code !== 'Space' || e.repeat || e.ctrlKey || e.altKey || e.metaKey || isTyping(e.target)) return;
      e.preventDefault();
      if (useStore.getState().listening) {
        void finishListening();
        return;
      }
      downAt = performance.now();
      void startListening();
    };
    const onUp = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || !downAt) return;
      const held = performance.now() - downAt;
      downAt = 0;
      if (held > 350) void finishListening();
    };
    window.addEventListener('keydown', onDown);
    window.addEventListener('keyup', onUp);
    return () => {
      window.removeEventListener('keydown', onDown);
      window.removeEventListener('keyup', onUp);
    };
  }, []);

  if (!visible) return <div className="app" aria-hidden="true" />;

  return (
    <div className="app">
      <ContourField />
      <TitleBar />
      <main className="grid">
        <aside className="col col-left" aria-label="System">
          <VitalsPanel />
        </aside>
        <Stage />
        <aside className="col col-right" aria-label="Activity">
          <NowPanel />
          <ConversationPanel />
          <ConnectionsPanel />
        </aside>
      </main>
      {halted ? <HaltOverlay /> : null}
    </div>
  );
}
