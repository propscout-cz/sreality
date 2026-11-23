import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ALL_TICKETS, isBuilt, MILESTONES, PROPOSALS, RUN_NODES, SERVICES, TOOL_GROUPS } from '@/lib/agentPlatformMap';
import AgentPlatform from './AgentPlatform';

describe('AgentPlatform', () => {
  it('derives the closed count from the ticket table', () => {
    render(<AgentPlatform />);
    const closed = ALL_TICKETS.filter((t) => t.done).length;
    expect(screen.getByText(`${closed} of ${ALL_TICKETS.length}`)).toBeInTheDocument();
  });

  it('links every ticket to its issue', () => {
    render(<AgentPlatform />);
    for (const t of ALL_TICKETS) {
      const links = screen.getAllByRole('link', { name: `#${t.n}` });
      expect(links[0]).toHaveAttribute('href', expect.stringContaining(`/issues/${t.n}`));
    }
  });

  it('names no ticket outside the table', () => {
    const known = new Set(MILESTONES.flatMap((m) => m.tickets.map((t) => t.n)));
    const named = [
      ...RUN_NODES.flatMap((n) => n.builtBy ?? []),
      ...SERVICES.flatMap((s) => s.builtBy ?? []),
      ...TOOL_GROUPS.flatMap((g) => g.tools.flatMap((t) => (t.ticket ? [t.ticket] : []))),
      ...PROPOSALS.map((p) => p.ticket),
    ];
    expect(named.filter((n) => !known.has(n))).toEqual([]);
  });

  it('counts a thing as built only when every ticket it names is done', () => {
    expect(isBuilt()).toBe(true);
    expect(isBuilt([2058])).toBe(true);
    expect(isBuilt([2058, 2079])).toBe(false);
  });
});
