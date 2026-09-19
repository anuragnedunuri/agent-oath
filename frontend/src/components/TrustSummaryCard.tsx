/**
 * Renders one Trust Summary.
 *
 * Charter obligations this component carries:
 *   INV-2  every state renders, including the ones where checks could not run
 *   INV-3  a score never appears without model name, version, disclaimer, and
 *          per-section results; no wording implies a binary verdict
 *   INV-4  every provenance and credibility result expands to its raw signals
 *   INV-6  an unrated source is explained, never given an implied score
 */

import type {
  CredibilityResult,
  ProvenanceResult,
  RetrievalResult,
  Signal,
  SyntheticResult,
  TrustSummary,
} from '../types';

const RETRIEVAL_HEADLINE: Record<RetrievalResult['status'], string> = {
  ok: 'Content retrieved',
  paywall_or_blocked: 'Content could not be read: the site blocked the request',
  timeout: 'Content could not be read: the site did not respond in time',
  js_required: 'Content could not be read: the page builds its text in the browser',
  not_html: 'Content could not be read: this is not a web page',
  invalid_url: 'That does not look like a valid web address',
  unreachable: 'Content could not be read: the site could not be reached',
  http_error: 'Content could not be read: the site returned an error',
};

const CHECK_LABEL: Record<string, string> = {
  synthetic: 'AI-text estimate',
  provenance: 'Provenance',
  registry: 'Source registry',
};

function formatProbability(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function SignalTable({ signals }: { signals: Signal[] }) {
  return (
    <table className="signals">
      <thead>
        <tr>
          <th>Signal</th>
          <th>Raw value</th>
          <th>What it means</th>
        </tr>
      </thead>
      <tbody>
        {signals.map((signal) => (
          <tr key={signal.name}>
            <td>
              <code>{signal.name}</code>
            </td>
            <td>{signal.raw_value}</td>
            <td>{signal.interpretation}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RetrievalBlock({ retrieval }: { retrieval: RetrievalResult }) {
  const tone = retrieval.status === 'ok' ? 'ok' : 'partial';

  return (
    <div className={`block retrieval ${tone}`}>
      <h4>{RETRIEVAL_HEADLINE[retrieval.status]}</h4>
      <p>{retrieval.detail}</p>
      <p className="checks-completed">
        {retrieval.checks_completed.length > 0 ? (
          <>Checks that ran: {retrieval.checks_completed.map((c) => CHECK_LABEL[c]).join(', ')}.</>
        ) : (
          <>No checks could be completed for this input.</>
        )}
      </p>
    </div>
  );
}

function SyntheticBlock({ synthetic }: { synthetic: SyntheticResult }) {
  const scored = synthetic.status === 'ok' && synthetic.probability_ai !== null;

  return (
    <div className="block">
      <h4>AI-text estimate</h4>

      {scored ? (
        <>
          <p className="estimate">
            <strong>{formatProbability(synthetic.probability_ai as number)}</strong> estimated
            probability that this text was AI-generated
          </p>
          <p className="coverage">
            Averaged across {synthetic.sections.length} of {synthetic.total_sections} sections of
            the document.
          </p>
        </>
      ) : (
        <p className="no-estimate">
          <strong>No estimate.</strong> {synthetic.detail}
        </p>
      )}

      <p className="model-line">
        Model: {synthetic.model_name}, version {synthetic.model_version}.
      </p>
      <p className="disclaimer">{synthetic.disclaimer}</p>

      {scored && (
        <details>
          <summary>Show the {synthetic.sections.length} sections that were analyzed</summary>
          <table className="signals">
            <thead>
              <tr>
                <th>Section</th>
                <th>Starts with</th>
                <th>Estimate</th>
              </tr>
            </thead>
            <tbody>
              {synthetic.sections.map((section) => (
                <tr key={section.section_index}>
                  <td>
                    {section.section_index + 1} of {synthetic.total_sections}
                  </td>
                  <td>{section.excerpt_start}...</td>
                  <td>{formatProbability(section.probability_ai)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

function ProvenanceBlock({ provenance }: { provenance: ProvenanceResult }) {
  const { domain, domain_age, https, publisher_metadata_found, publication_date } = provenance;

  return (
    <div className="block">
      <h4>Provenance</h4>

      {domain === null ? (
        <p className="no-estimate">
          <strong>Not applicable.</strong> Pasted text has no web address, so there is no domain,
          certificate, or page metadata to check.
        </p>
      ) : (
        <dl className="facts">
          <dt>Domain</dt>
          <dd>{domain}</dd>

          <dt>Domain age</dt>
          <dd>
            {domain_age === null || domain_age.status === 'unavailable'
              ? `Unavailable. ${domain_age?.detail ?? 'This check could not run.'}`
              : `${domain_age.value_years} years, according to ${domain_age.source}`}
          </dd>

          <dt>Encrypted connection</dt>
          <dd>{https === null ? 'Not checked' : https ? 'Yes' : 'No'}</dd>

          <dt>Publisher named in metadata</dt>
          <dd>
            {publisher_metadata_found === null
              ? 'Could not be checked, because the page content was not readable'
              : publisher_metadata_found
                ? 'Yes'
                : 'No'}
          </dd>

          <dt>Publication date</dt>
          <dd>
            {publication_date === null
              ? 'Could not be checked, because the page content was not readable'
              : publication_date.status === 'not_found'
                ? `Not found by ${publication_date.method}`
                : `${publication_date.value}, found by ${publication_date.method}`}
          </dd>
        </dl>
      )}

      <details>
        <summary>Show the raw signals behind this</summary>
        <SignalTable signals={provenance.signals} />
      </details>
    </div>
  );
}

function CredibilityBlock({ credibility }: { credibility: CredibilityResult }) {
  const entry = credibility.matched_entry;

  if (credibility.status === 'unrated' || entry === null) {
    return (
      <div className="block">
        <h4>Source registry</h4>
        <p className="no-estimate">
          <strong>Unrated.</strong> {credibility.unrated_explanation}
        </p>
        <p className="model-line">Registry version {credibility.registry_version}.</p>
      </div>
    );
  }

  const criteria: Array<[string, boolean]> = [
    ['Editorial standards published', entry.editorial_standards_public],
    ['Corrections policy published', entry.corrections_policy_public],
    ['Ownership transparent', entry.ownership_transparent],
  ];
  const met = criteria.filter(([, value]) => value).length;

  return (
    <div className="block">
      <h4>Source registry</h4>
      <p className="estimate">
        <strong className={`tier tier-${entry.trust_tier}`}>{entry.trust_tier}</strong> trust tier
        for {entry.publisher_name}
      </p>
      <p className="coverage">
        {met} of 3 criteria met. Registry version {credibility.registry_version}, entry last
        verified {entry.last_verified}.
      </p>

      <details>
        <summary>Show exactly how this tier was decided</summary>
        <table className="signals">
          <thead>
            <tr>
              <th>Criterion</th>
              <th>Met</th>
            </tr>
          </thead>
          <tbody>
            {criteria.map(([label, value]) => (
              <tr key={label}>
                <td>{label}</td>
                <td>{value ? 'Yes' : 'No'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="rule">
          The tier is mechanical: all 3 criteria met gives high, 2 gives medium, fewer gives low.
          This source met {met}, which gives {entry.trust_tier}.
        </p>
        <dl className="facts">
          <dt>Publisher</dt>
          <dd>{entry.publisher_name}</dd>
          <dt>Source type</dt>
          <dd>{entry.source_type.replace(/_/g, ' ')}</dd>
          <dt>Country</dt>
          <dd>{entry.country}</dd>
        </dl>
        {entry.notes && <p className="rule">{entry.notes}</p>}
      </details>
    </div>
  );
}

export default function TrustSummaryCard({ summary }: { summary: TrustSummary }) {
  const heading = summary.provenance.domain !== null ? summary.provenance.domain : 'Pasted text';

  return (
    <article className="trust-card">
      <header>
        <h3>Trust Summary</h3>
        <p className="subject">{heading}</p>
      </header>

      <RetrievalBlock retrieval={summary.retrieval} />
      <SyntheticBlock synthetic={summary.synthetic} />
      <ProvenanceBlock provenance={summary.provenance} />
      <CredibilityBlock credibility={summary.credibility} />
    </article>
  );
}
