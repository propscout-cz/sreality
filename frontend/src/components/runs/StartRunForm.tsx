import { useId, useMemo, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import ErrorBanner from '@/components/ErrorBanner';
import { Field } from '@/components/controls';
import { startAgentRun } from '@/lib/api';
import {
  agentRunKeys, buildInput, fieldsOf, QUEUE_HOLD, type AgentOption, type FormField, type FormValues, type QueueState,
} from '@/lib/agentRuns';
import { ROUTES } from '@/lib/routes';

const BOX = 'w-full min-w-0 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] px-2 py-1.5 text-sm text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] aria-[invalid=true]:border-[var(--color-brick)]';

const captionOf = (field: FormField): string => `${field.name.replace(/_/g, ' ')}${field.required ? ' *' : ''}`;

function FieldControl({ field, value, errorId, onChange }: {
  field: FormField;
  value: string | boolean | undefined;
  errorId?: string;
  onChange: (next: string | boolean) => void;
}) {
  const caption = captionOf(field);
  const named = {
    'aria-required': field.required || undefined,
    'aria-invalid': errorId ? true : undefined,
    'aria-describedby': errorId,
  };
  const text = typeof value === 'string' ? value : '';
  if (field.kind === 'boolean') {
    return <input type="checkbox" aria-label={caption} {...named} checked={value === true} onChange={(e) => onChange(e.target.checked)} />;
  }
  if (field.kind === 'choice') {
    return (
      <select className={BOX} aria-label={caption} {...named} value={text} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {field.options?.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }
  if (field.kind === 'json') {
    return (
      <textarea className={`${BOX} font-mono`} rows={3} aria-label={caption} {...named} value={text} placeholder="JSON"
        onChange={(e) => onChange(e.target.value)} />
    );
  }
  const numeric = field.kind === 'integer' || field.kind === 'number';
  return (
    <input className={`${BOX} ${numeric ? 'font-mono tabular-nums' : ''}`} aria-label={caption} {...named} value={text}
      inputMode={field.kind === 'integer' ? 'numeric' : field.kind === 'number' ? 'decimal' : undefined}
      placeholder={field.kind === 'list' ? 'Separated by commas' : undefined}
      onChange={(e) => onChange(e.target.value)} />
  );
}

function FormFieldBlock({ field, value, error, onChange }: {
  field: FormField;
  value: string | boolean | undefined;
  error?: string;
  onChange: (next: string | boolean) => void;
}) {
  const errorId = useId();
  return (
    <div className="min-w-0">
      <Field label={captionOf(field)} help={field.description} as="control">
        <FieldControl field={field} value={value} errorId={error ? errorId : undefined} onChange={onChange} />
      </Field>
      {error && <p id={errorId} className="mt-1 text-[0.78rem] text-[var(--color-brick)]">{error}</p>}
    </div>
  );
}

/* Start a run of one of the agents the API offered. One idempotency key per input: a double click or a retry after a lost
 * answer is the same run, and a changed input is a new one. */
export default function StartRunForm({ agents, queue }: { agents: AgentOption[]; queue: QueueState | null }) {
  const [agentName, setAgentName] = useState(agents[0].name);
  const agent = agents.find((a) => a.name === agentName) ?? agents[0];
  const fields = useMemo(() => fieldsOf(agent.input_schema), [agent]);
  const [values, setValues] = useState<FormValues>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const key = useRef(crypto.randomUUID());
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const start = useMutation({
    mutationFn: startAgentRun,
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: agentRunKeys.list });
      navigate(ROUTES.runDetail.build({ id: run.id }));
    },
  });

  const change = (name: string, next: string | boolean) => {
    key.current = crypto.randomUUID();
    start.reset();
    setValues((v) => ({ ...v, [name]: next }));
    setErrors(({ [name]: _gone, ...rest }) => rest);
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const built = buildInput(fields, values);
    setErrors(built.errors);
    if (Object.keys(built.errors).length === 0) {
      start.mutate({ agent: agent.name, input: built.input, idempotency_key: key.current });
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      {agents.length > 1 && (
        <Field label="Agent" as="control" className="max-w-sm">
          <select className={BOX} value={agent.name} onChange={(e) => { setAgentName(e.target.value); setValues({}); setErrors({}); key.current = crypto.randomUUID(); }}>
            {agents.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
          </select>
        </Field>
      )}
      <p className="text-sm text-[var(--color-ink-2)] max-w-[52rem]">
        {agents.length === 1 && <span className="font-mono text-[0.78rem]">{agent.name}: </span>}
        {agent.description}
      </p>
      {fields.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {fields.map((field) => (
            <FormFieldBlock key={field.name} field={field} value={values[field.name]} error={errors[field.name]}
              onChange={(next) => change(field.name, next)} />
          ))}
        </div>
      )}
      {start.isError && <ErrorBanner flush title="Not started:" message={start.error.message} />}
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" disabled={start.isPending}
          className="rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-3 py-1.5 text-sm text-[var(--color-on-copper)] transition-colors hover:bg-[var(--color-copper-2)] disabled:opacity-50">
          Start run
        </button>
        {start.isPending && <span className="text-sm text-[var(--color-ink-3)]">Starting…</span>}
        {queue && QUEUE_HOLD[queue] && <span className="text-[0.78rem] text-[var(--color-ink-3)]">{QUEUE_HOLD[queue]}</span>}
      </div>
    </form>
  );
}
