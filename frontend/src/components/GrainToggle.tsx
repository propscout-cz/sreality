import { SegmentedBar } from '@/components/controls';

export type Grain = 'hour' | 'day';

const GRAINS = [
  { value: 'hour' as const, label: 'Hour' },
  { value: 'day' as const, label: 'Day' },
];

/* The Hour/Day granularity switch used by the Costs spend chart (and any
 * future time-series panel, so they read identically): the compact joined bar. */
export default function GrainToggle({
  grain,
  onChange,
}: {
  grain: Grain;
  onChange: (g: Grain) => void;
}) {
  return <SegmentedBar label="Granularity" options={GRAINS} value={grain} onChange={onChange} />;
}
