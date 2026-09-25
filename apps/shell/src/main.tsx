import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/anek-devanagari/standard.css';
import '@fontsource-variable/jetbrains-mono';
import './styles/app.css';
import App from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
