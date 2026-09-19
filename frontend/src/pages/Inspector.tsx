/**
 * Mode 1: Inspector. Submit a URL or paste text, get a Trust Summary.
 *
 * INV-2 on the client side: every submission ends in one of three visible
 * states: a rendered Trust Summary, a plain-language error, or an explicit
 * "still working" indicator with a hard timeout. Never a spinner forever,
 * never a blank screen.
 */

import { useRef, useState } from 'react';
import TrustSummaryCard from '../components/TrustSummaryCard';
import type { TrustSummary } from '../types';

type InputMode = 'url' | 'text';

/** Fetch + provenance lookups can legitimately take ~20s on slow sites. */
const REQUEST_TIMEOUT_MS = 45_000;

export default function Inspector() {
  const [mode, setMode] = useState<InputMode>('url');
  const [url, setUrl] = useState('');
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<TrustSummary | null>(null);
  const requestSeq = useRef(0);

  const canSubmit = !loading && (mode === 'url' ? url.trim() !== '' : text.trim() !== '');

  async function analyze() {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    setSummary(null);

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify(
          mode === 'url' ? { url: url.trim(), text: null } : { url: null, text: text },
        ),
      });

      if (seq !== requestSeq.current) return;

      if (response.status === 422) {
        setError('Provide one input: either a web address or some pasted text.');
      } else if (!response.ok) {
        setError(`The analysis service reported a problem (HTTP ${response.status}). Try again.`);
      } else {
        setSummary((await response.json()) as TrustSummary);
      }
    } catch (caught: unknown) {
      if (seq !== requestSeq.current) return;
      const aborted = caught instanceof DOMException && caught.name === 'AbortError';
      setError(
        aborted
          ? 'The analysis did not finish within 45 seconds, so it was stopped. Slow or unresponsive sites can cause this. Try again, or try a different address.'
          : 'The analysis service could not be reached. Check that the backend is running, then try again.',
      );
    } finally {
      clearTimeout(timeout);
      if (seq === requestSeq.current) setLoading(false);
    }
  }

  return (
    <section className="panel">
      <h2>Mode 1: Inspector</h2>
      <p>
        Submit a URL or paste text. The system runs a synthetic-text check, a provenance check, and
        a registry lookup, then renders a plain-language Trust Summary.
      </p>

      <form
        className="analyze-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) void analyze();
        }}
      >
        <div className="fixture-switch" role="tablist" aria-label="Input type">
          <button
            type="button"
            className={mode === 'url' ? 'active' : ''}
            onClick={() => setMode('url')}
          >
            Web address
          </button>
          <button
            type="button"
            className={mode === 'text' ? 'active' : ''}
            onClick={() => setMode('text')}
          >
            Pasted text
          </button>
        </div>

        {mode === 'url' ? (
          <input
            type="text"
            className="url-input"
            placeholder="https://example.com/article"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            aria-label="Web address to analyze"
          />
        ) : (
          <textarea
            className="text-input"
            rows={8}
            placeholder="Paste the text you want to analyze."
            value={text}
            onChange={(event) => setText(event.target.value)}
            aria-label="Text to analyze"
          />
        )}

        <button type="submit" className="submit" disabled={!canSubmit}>
          {loading ? 'Analyzing...' : 'Analyze'}
        </button>
      </form>

      {loading && (
        <p className="progress" role="status">
          Analyzing. Retrieving the page and running checks; slow sites can take up to 30 seconds.
        </p>
      )}

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {summary && <TrustSummaryCard summary={summary} />}
    </section>
  );
}
