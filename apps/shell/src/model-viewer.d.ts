// <model-viewer> (Google's web component for .glb files), used to show the 3D models PLAG builds with TRELLIS.
import 'react';

declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'model-viewer': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        src?: string;
        alt?: string;
        'camera-controls'?: string;
        'auto-rotate'?: string;
        'shadow-intensity'?: string;
        exposure?: string;
        'interaction-prompt'?: string;
        'touch-action'?: string;
      };
    }
  }
}
