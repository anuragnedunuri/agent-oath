import { useEffect, useState } from 'react';
import Inspector from './pages/Inspector';
import AgentDemo from './pages/AgentDemo';

type Mode = 'inspector' | 'agent';

type BackendState =
  | { status: 'checking' }
  | { status: 'up'; version: string; env: string }
  | { status: 'down'; detail: string };

/**
 * Application shell. Two modes, plus an always-visible backend status line so
 * a failed backend is stated plainly rather than shown as a stuck spinner.
 */
export default function App() {
  const [mode, setMode] = useState<Mode>('inspector');
  const [backend, setBackend] = useState<BackendState>({ status: 'checking' });

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);

    fetch('/health', { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Backend responded with HTTP ${response.status}`);
        }
        const body = (await response.json()) as { version: string; app_env: string };
        setBackend({ status: 'up', version: body.version, env: body.app_env });
      })
      .catch((error: unknown) => {
        const detail =
          error instanceof DOMException && error.name === 'AbortError'
            ? 'The backend did not respond within 5 seconds.'
            : 'The backend could not be reached. Start it with: make dev';
        setBackend({ status: 'down', detail });
      })
      .finally(() => clearTimeout(timeout));

    return () => {
      clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Agent Oath</h1>
        <p className="tagline">Trust Verification Layer</p>
        <nav className="mode-switch">
          <button
            type="button"
            className={mode === 'inspector' ? 'active' : ''}
            onClick={() => setMode('inspector')}
          >
            Mode 1: Inspector
          </button>
          <button
            type="button"
            className={mode === 'agent' ? 'active' : ''}
            onClick={() => setMode('agent')}
          >
            Mode 2: Agent demo
          </button>
        </nav>
      </header>

      <main>{mode === 'inspector' ? <Inspector /> : <AgentDemo />}</main>

      <footer className="app-footer">
        {backend.status === 'checking' && <span>Checking backend...</span>}
        {backend.status === 'up' && (
          <span className="ok">
            Backend online. Version {backend.version}, environment {backend.env}.
          </span>
        )}
        {backend.status === 'down' && <span className="warn">{backend.detail}</span>}
      </footer>
    </div>
  );
}
