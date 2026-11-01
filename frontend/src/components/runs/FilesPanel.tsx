import { useState } from 'react';
import Panel, { PanelEmpty } from '@/components/Panel';
import { fetchAgentRunFile } from '@/lib/api';
import { isLive, type AgentRun, type AgentRunFile } from '@/lib/agentRuns';
import { fmtBytes } from '@/lib/attachments';
import { saveBlob } from '@/lib/shareFiles';

/* A run's files: names, sizes, a download. A name is the model's words, so it is React text and nothing here opens a file:
 * the bytes only ever leave as a saved download, never as an object URL a tab could render on this origin (this app has no
 * Content-Security-Policy to stop a page that ran here). The API has already typed them as inert (api/agents/agent_files.py). */

function FileRow({ runId, file }: { runId: number; file: AgentRunFile }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const download = async () => {
    setBusy(true);
    setError(null);
    try {
      saveBlob(await fetchAgentRunFile(runId, file.id), file.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The download failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
      <span className="min-w-0 break-all font-mono text-[0.78rem] text-[var(--color-ink-2)]">{file.name}</span>
      <span className="font-mono text-[0.78rem] tabular-nums text-[var(--color-ink-3)]">{fmtBytes(file.size_bytes)}</span>
      {file.stored ? (
        <button type="button" disabled={busy} onClick={() => void download()}
          className="text-[0.78rem] text-[var(--color-ink-2)] underline underline-offset-2 disabled:opacity-60">
          {busy ? 'Downloading…' : 'Download'}
        </button>
      ) : (
        <span className="text-[0.78rem] text-[var(--color-brick)]">Not stored</span>
      )}
      {error && <span role="alert" className="w-full text-[0.78rem] text-[var(--color-brick)]">Not downloaded: {error}</span>}
    </li>
  );
}

export default function FilesPanel({ run }: { run: Pick<AgentRun, 'id' | 'status' | 'files'> }) {
  const lost = run.files.filter((file) => !file.stored).length;
  return (
    <Panel title="Files" count={run.files.length}
      note={lost > 0 ? `${lost} couldn’t be stored when the run ended, so only ${lost === 1 ? 'its name remains' : 'their names remain'}` : undefined}>
      {run.files.length ? (
        <ul className="space-y-1">
          {run.files.map((file) => <FileRow key={file.id} runId={run.id} file={file} />)}
        </ul>
      ) : (
        <PanelEmpty>{isLive(run.status) ? 'Files appear when the run ends.' : 'No files.'}</PanelEmpty>
      )}
    </Panel>
  );
}
