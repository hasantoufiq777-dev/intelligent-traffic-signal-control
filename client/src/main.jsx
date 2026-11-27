import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App.jsx';
import { AuthProvider } from './hooks/useAuth.js';
import { registerServiceWorker } from './hooks/useInstallPrompt.js';
import './styles.css';

// Installable app (PWA): only in the built dashboard, not on the dev server
if (import.meta.env.PROD) registerServiceWorker();

try {
  const theme = localStorage.getItem('itsc-theme') || 'dark';
  if (theme !== 'auto') document.documentElement.setAttribute('data-theme', theme);
} catch {
  document.documentElement.setAttribute('data-theme', 'dark');
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
