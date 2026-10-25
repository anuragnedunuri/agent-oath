/**
 * Mode 2: Agent demo (task 4.4, SPEC.md section 6).
 *
 * The user asks a question, sees every candidate source's Trust Summary, and
 * approves or rejects each one. The agent can only answer from approved
 * sources; with none approved it refuses visibly. In dev mode a payload
 * panel shows the exact request body sent to the LLM, so the isolation
 * proof is on screen rather than only in the server console.
 */

import { useEffect, useState } from 'react';
import TrustSummaryCard from '../components/TrustSummaryCard';
import type {
  AgentAnswerResponse,
  AgentAskResponse,
  AgentTopicsResponse,
  LlmPayloadDebugResponse,
} from '../types';

const GATHER_TIMEOUT_MS = 180_000;
const ANSWER_TIMEOUT_MS = 120_000;

type Mode = 'seed' | 'byo';

async function postJson<T>(url: string, body: unknown, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const detail = await response
        .json()
        .then((payload: { detail?: unknown }) =>
          typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`,
        )
        .catch(() => `HTTP ${response.status}`);
      throw new Error(detail);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}

export default function AgentDemo() {
  const [topics, setTopics] = useState<AgentTopicsResponse | null>(null);
  const [devMode, setDevMode] = useState(false);
  const [mode, setMode] = useState<Mode>('seed');
  const [topicId, setTopicId] = useState<string>('');
  const [question, setQuestion] = useState('');
  const [byoUrls, setByoUrls] = useState('');
  const [gathering, setGathering] = useState(false);
  const [answering, setAnswering] = useState(false);
  const [session, setSession] = useState<AgentAskResponse | null>(null);
  const [answer, setAnswer] = useState<AgentAnswerResponse | null>(null);
  const [payload, setPayload] = useState<LlmPayloadDebugResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/agent/topics')
      .then((response) => response.json() as Promise<AgentTopicsResponse>)
      .then(setTopics)
      .catch(() => setTopics(null));
    fetch('/health')
      .then((response) => response.json() as Promise<{ app_env: string }>)
      .then((health) => setDevMode(health.app_env === 'dev'))
      .catch(() => setDevMode(false));
  }, []);

  const approvedCount = session
    ? session.sources.filter((source) => source.approved === true).length
    : 0;

  const canGather =
    !gathering &&
    question.trim() !== '' &&
    (mode === 'seed' ? topicId !== '' : byoUrls.trim() !== '');

  async function gather() {
    setGathering(true);
    setError(null);
    setSession(null);
    setAnswer(null);
    setPayload(null);
    try {
      const body =
        mode === 'seed'
          ? { question: question.trim(), topic_id: topicId }
          : {
              question: question.trim(),
              urls: byoUrls
                .split('\n')
                .map((line) => line.trim())
                .filter(Boolean),
            };
      setSession(await postJson<AgentAskResponse>('/api/agent/ask', body, GATHER_TIMEOUT_MS));
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : 'The gathering request failed.');
    } finally {
      setGathering(false);
    }
  }

  async function decide(sourceId: string, approved: boolean) {
    if (!session) return;
    setError(null);
    try {
      await postJson(
        '/api/agent/approve',
        {
          session_id: session.session_id,
          source_id: sourceId,
          approved,
        },
        30_000,
      );
      setSession({
        ...session,
        sources: session.sources.map((source) =>
          source.source_id === sourceId ? { ...source, approved } : source,
        ),
      });
      setAnswer(null);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : 'The approval request failed.');
    }
  }

  async function requestAnswer() {
    if (!session) return;
    setAnswering(true);
    setError(null);
    setAnswer(null);
    try {
      const result = await postJson<AgentAnswerResponse>(
        '/api/agent/answer',
        { session_id: session.session_id },
        ANSWER_TIMEOUT_MS,
      );
      setAnswer(result);
      if (devMode) {
        const debug = await fetch('/api/agent/debug/payload');
        if (debug.ok) setPayload((await debug.json()) as LlmPayloadDebugResponse);
      }
    } catch (caught: unknown) {
      const aborted = caught instanceof DOMException && caught.name === 'AbortError';
      setError(
        aborted
          ? 'The answer did not arrive within 2 minutes, so the request was stopped. Try again.'
          : caught instanceof Error
            ? caught.message
            : 'The answer request failed.',
      );
    } finally {
      setAnswering(false);
    }
  }

  return (
    <section className="panel">
      <h2>Mode 2: Agent demo</h2>
      <p>
        Ask a research question. The agent gathers candidate sources and shows you a Trust Summary
        for each one. It cannot use a source in its answer until you approve that source.
      </p>

      <form
        className="analyze-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (canGather) void gather();
        }}
      >
        <input
          type="text"
          className="url-input"
          placeholder="Your research question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          aria-label="Research question"
        />

        <div className="fixture-switch" role="tablist" aria-label="Source mode">
          <button
            type="button"
            className={mode === 'seed' ? 'active' : ''}
            onClick={() => setMode('seed')}
          >
            Demo topics
          </button>
          <button
            type="button"
            className={mode === 'byo' ? 'active' : ''}
            onClick={() => setMode('byo')}
          >
            Your own URLs
          </button>
        </div>

        {mode === 'seed' ? (
          <>
            {topics && <p className="placeholder-note">{topics.disclosure}</p>}
            <div className="topic-grid" role="radiogroup" aria-label="Demo topic">
              {(topics?.topics ?? []).map((topic) => (
                <button
                  key={topic.topic_id}
                  type="button"
                  className={`topic-chip ${topicId === topic.topic_id ? 'active' : ''}`}
                  onClick={() => setTopicId(topic.topic_id)}
                >
                  {topic.title}
                </button>
              ))}
            </div>
          </>
        ) : (
          <textarea
            className="text-input"
            rows={5}
            placeholder={'One web address per line.\nProvide between 3 and 5.'}
            value={byoUrls}
            onChange={(event) => setByoUrls(event.target.value)}
            aria-label="Your source URLs"
          />
        )}

        <button type="submit" className="submit" disabled={!canGather}>
          {gathering ? 'Gathering sources...' : 'Gather sources'}
        </button>
      </form>

      {gathering && (
        <p className="progress" role="status">
          Gathering sources and building a Trust Summary for each one. This fetches real pages and
          can take up to a minute.
        </p>
      )}

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {session && (
        <div className="agent-sources">
          <h3>
            Sources for review ({session.sources.length}, gathered by{' '}
            {session.gathering_mode === 'seed' ? 'curated demo topic' : 'your URL list'})
          </h3>
          {session.sources.map((source) => (
            <div key={source.source_id} className="agent-source">
              <div className="agent-source-bar">
                <span
                  className={`approval-badge ${
                    source.approved === true
                      ? 'approved'
                      : source.approved === false
                        ? 'rejected'
                        : 'undecided'
                  }`}
                >
                  {source.approved === true
                    ? 'Approved'
                    : source.approved === false
                      ? 'Rejected'
                      : 'Awaiting your decision'}
                </span>
                <span className="agent-source-url">{source.url}</span>
                <span className="agent-source-actions">
                  <button
                    type="button"
                    className="approve"
                    disabled={source.approved === true}
                    onClick={() => void decide(source.source_id, true)}
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    className="reject"
                    disabled={source.approved === false}
                    onClick={() => void decide(source.source_id, false)}
                  >
                    Reject
                  </button>
                </span>
              </div>
              <TrustSummaryCard summary={source.trust_summary} />
            </div>
          ))}

          <div className="answer-controls">
            <button
              type="button"
              className="submit"
              disabled={answering}
              onClick={() => void requestAnswer()}
            >
              {answering ? 'Asking the agent...' : 'Ask the agent to answer'}
            </button>
            <span className="coverage">
              {approvedCount} of {session.sources.length} sources approved. The agent can only use
              approved sources.
            </span>
          </div>
        </div>
      )}

      {answering && (
        <p className="progress" role="status">
          Building the answer from your approved sources only.
        </p>
      )}

      {answer && answer.status === 'ok' && (
        <div className="answer-panel ok">
          <h3>Answer</h3>
          <p className="answer-text">{answer.answer}</p>
          <h4>Sources used</h4>
          <ul className="attribution">
            {answer.sources_used.map((used) => (
              <li key={used.source_id}>
                <span className={`tier tier-${used.trust_tier ?? 'unrated'}`}>
                  {used.trust_tier ?? 'unrated'}
                </span>{' '}
                {used.url}
              </li>
            ))}
          </ul>
          <p className="model-line">Model: {answer.model_id}.</p>
        </div>
      )}

      {answer && answer.status === 'no_approved_sources' && (
        <div className="answer-panel refusal" role="status">
          <h3>No answer: nothing is approved</h3>
          <p>{answer.detail}</p>
        </div>
      )}

      {answer && answer.status === 'llm_unavailable' && (
        <div className="answer-panel unavailable" role="alert">
          <h3>No answer: service problem</h3>
          <p>{answer.detail}</p>
        </div>
      )}

      {devMode && payload && (
        <details className="payload-panel" open>
          <summary>Payload proof: the exact request sent to the language model (dev mode)</summary>
          <p className="placeholder-note">
            {payload.detail} Rejected and undecided source text is not filtered out of this request;
            it was never put in.
          </p>
          <pre className="payload-json">
            {payload.payload ? JSON.stringify(payload.payload, null, 2) : 'No call made yet.'}
          </pre>
        </details>
      )}
    </section>
  );
}
