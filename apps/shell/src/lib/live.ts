// Values that change every frame. Kept outside React so animation never triggers re-renders.
export const live = {
  mic: 0, // 0..1 microphone level
  speak: 0, // 0..1 speaker level
  speakProgress: 0, // 0..1 through the current spoken reply
  ambient: 0, // 0..1 room level from the wake-word listener: the orb breathes with your voice
  orb: { x: 0, y: 0, r: 0 }, // hero orb centre in window pixels
};
