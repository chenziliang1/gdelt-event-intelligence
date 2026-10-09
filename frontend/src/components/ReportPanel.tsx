import { FileText, Lightbulb, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { ReportChecks, ReportResult } from '../types';

const CHECK_NAMES: Record<string, string> = {
  ungrounded_numbers: 'numbers not in the data',
  sample_as_total: 'totals from a sample',
  qualitative_trend: 'a trend from a sample',
  dates_outside_window: 'dates outside the query',
  count_overclaims: 'a wrong count of records',
  comparison_direction: 'the wrong direction of change',
};

const names = (failed: Record<string, string[]>) =>
  Object.keys(failed).map(k => CHECK_NAMES[k] ?? k).join(', ');

const CHECKED_NAMES: Record<string, string> = {
  ungrounded_numbers: 'numbers',
  sample_as_total: 'totals',
  qualitative_trend: 'trends',
  dates_outside_window: 'dates',
  count_overclaims: 'record counts',
  comparison_direction: 'direction of change',
};

/** "numbers, totals and record counts": only the checks that actually ran. */
const checkedList = (checked?: string[]) => {
  const items = (checked ?? []).map(k => CHECKED_NAMES[k] ?? k);
  return items.length > 1 ? `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}` : items.join('');
};

/** One line under the report: what the deterministic checks found. */
export function ChecksLine({ checks }: { checks: ReportChecks }) {
  const warn = checks.fallback;
  const text = checks.fallback
    ? `The AI summary failed the checks twice (${names(checks.failed)}); the records are shown instead.`
    : checks.attempts > 1
      ? `Passed the checks after one rewrite (first draft had ${names(checks.failed_first)}).`
      : checks.checked?.length
        ? `Passed the checks against the data: ${checkedList(checks.checked)}.`
        : 'Passed the checks against the data.';
  const Icon = warn ? ShieldAlert : ShieldCheck;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 12, fontSize: 12, color: warn ? '#b45309' : '#047857' }}>
      <Icon size={14} />
      {text}
    </div>
  );
}

interface Props {
  report: ReportResult;
  title?: string;
}

export default function ReportPanel({ report, title = 'AI Report' }: Props) {
  if (!report) return null;

  return (
    <div className="panel" style={{ background: '#fafafa' }}>
      <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <FileText size={18} color="#2563eb" />
        {title}
      </h3>

      {report.summary && (
        <div style={{ lineHeight: 1.7, color: '#374151', fontSize: 14, marginBottom: 16 }}>
          {report.summary.split('\n').map((para, i) => (
            <p key={i} style={{ marginBottom: 8 }}>{para}</p>
          ))}
        </div>
      )}

      {report.key_findings && report.key_findings.length > 0 && (
        <div>
          <h4 style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#555', marginBottom: 8 }}>
            <Lightbulb size={14} color="#f59e0b" />
            Key Findings
          </h4>
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            {report.key_findings.map((finding, i) => (
              <li key={i} style={{ marginBottom: 6, fontSize: 13, color: '#4b5563', lineHeight: 1.5 }}>
                {finding}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.checks && <ChecksLine checks={report.checks} />}
    </div>
  );
}
