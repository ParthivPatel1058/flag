import type { SVGProps } from 'react';

type P = SVGProps<SVGSVGElement>;
const base = { width: 22, height: 22, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' } as const;

/** The PLAG mark: a P whose bowl stays open, with the core at its centre. */
export const Mark = (p: P) => (
  <svg viewBox="0 0 48 48" width={26} height={26} fill="none" aria-hidden="true" {...p}>
    <path d="M14 41 V19 A12 12 0 1 1 26 31 H20.5" stroke="currentColor" strokeWidth={3.6} strokeLinecap="round" strokeLinejoin="round" />
    <circle cx="26" cy="19" r="3.6" fill="var(--lime)" />
  </svg>
);

export const MicIcon = (p: P) => (
  <svg {...base} {...p}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
  </svg>
);

export const StopSquare = (p: P) => (
  <svg {...base} {...p}>
    <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none" />
  </svg>
);

export const KeyboardIcon = (p: P) => (
  <svg {...base} {...p}>
    <rect x="3" y="6" width="18" height="12" rx="2.5" />
    <path d="M7 10h.01M11 10h.01M15 10h.01M17 10h.01M7 14h10" />
  </svg>
);

export const CloseIcon = (p: P) => (
  <svg {...base} {...p}>
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
);

export const VolumeIcon = (p: P) => (
  <svg {...base} width={18} height={18} {...p}>
    <path d="M4 10v4h4l5 4V6L8 10H4z" />
    <path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a7.5 7.5 0 0 1 0 11" />
  </svg>
);

export const VolumeOffIcon = (p: P) => (
  <svg {...base} width={18} height={18} {...p}>
    <path d="M4 10v4h4l5 4V6L8 10H4z" />
    <path d="M17 10l4 4M21 10l-4 4" />
  </svg>
);

export const SendIcon = (p: P) => (
  <svg {...base} width={18} height={18} {...p}>
    <path d="M5 12h13M13 6l6 6-6 6" />
  </svg>
);

export const CameraIcon = (p: P) => (
  <svg {...base} {...p}>
    <path d="M4 8h3l1.6-2.2h6.8L17 8h3v11H4z" />
    <circle cx="12" cy="13.2" r="3.4" />
  </svg>
);

export const WhatsAppIcon = (p: P) => (
  <svg {...base} width={18} height={18} {...p}>
    <path d="M4.5 19.5l1.2-3.6A8 8 0 1 1 8.2 18.4z" />
    <path d="M9.2 9.2c.2 2.6 2.4 4.9 5.2 5.4l1-1.1-1.6-.9-.8.7c-.9-.4-1.6-1.1-2.1-2l.7-.8-.8-1.6z" fill="currentColor" stroke="none" />
  </svg>
);

export const GearIcon = (p: P) => (
  <svg {...base} width={18} height={18} {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M5.6 18.4l1.6-1.6M16.8 7.2l1.6-1.6" />
  </svg>
);

export const OpenIcon = (p: P) => (
  <svg {...base} width={16} height={16} {...p}>
    <path d="M14 5h5v5M19 5l-8 8M17 14v5H5V7h5" />
  </svg>
);

export const FolderIcon = (p: P) => (
  <svg {...base} width={16} height={16} {...p}>
    <path d="M3.5 7.5V18h17V9.5h-8.5L10 7.5z" />
  </svg>
);

export const TrashIcon = (p: P) => (
  <svg {...base} width={15} height={15} {...p}>
    <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" />
  </svg>
);

export const EarIcon = (p: P) => (
  <svg {...base} width={15} height={15} strokeWidth={2} {...p}>
    <path d="M7 10a5 5 0 0 1 10 0c0 3-3 3.6-3 6a3 3 0 0 1-5.4 1.8" />
    <path d="M10 10a2 2 0 0 1 4 0" />
  </svg>
);

/** The ear button: always listening (a big ear, the dock size). */
export const ListenIcon = (p: P) => (
  <svg {...base} {...p}>
    <path d="M6 10a6 6 0 0 1 12 0c0 3.6-3.6 4.3-3.6 7.2a3.6 3.6 0 0 1-6.5 2.1" />
    <path d="M9.6 10a2.4 2.4 0 0 1 4.8 0" />
    <path d="M20.5 7.5a9 9 0 0 1 0 5M3.5 7.5a9 9 0 0 0 0 5" opacity="0.55" />
  </svg>
);

/** Attach a picture to the chat. */
export const AttachIcon = (p: P) => (
  <svg {...base} {...p}>
    <rect x="3.5" y="5" width="17" height="14" rx="2.5" />
    <circle cx="9" cy="10" r="1.6" />
    <path d="M20.5 15.5l-4.6-4.6L7 19" />
  </svg>
);

/** A document PLAG wrote (PDF). */
export const DocIcon = (p: P) => (
  <svg {...base} {...p}>
    <path d="M7 3h7l5 5v13H7z" />
    <path d="M14 3v5h5M10 13h6M10 17h6" />
  </svg>
);

export const PlayIcon = (p: P) => (
  <svg {...base} width={15} height={15} {...p}>
    <path d="M8 5.5v13l10-6.5z" fill="currentColor" />
  </svg>
);

export const PauseIcon = (p: P) => (
  <svg {...base} width={15} height={15} strokeWidth={2.4} {...p}>
    <path d="M9 6v12M15 6v12" />
  </svg>
);

export const PowerIcon = (p: P) => (
  <svg {...base} width={15} height={15} strokeWidth={2.2} {...p}>
    <path d="M12 3v8M6.3 6.8a8 8 0 1 0 11.4 0" />
  </svg>
);
