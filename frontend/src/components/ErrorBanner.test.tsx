/* The retry word follows the page's language. Every existing caller keeps
 * "Zkusit znovu" (the default); an English admin page (Health) passes its own
 * word instead of a Czech button under an English verdict. The banner is an
 * alert, so a failure is announced as it appears. */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import ErrorBanner from './ErrorBanner';

describe('<ErrorBanner>', () => {
  it('offers the Czech retry by default and calls onRetry', () => {
    const onRetry = vi.fn();
    render(<ErrorBanner message="HTTP 500" onRetry={onRetry} />);
    fireEvent.click(screen.getByRole('button', { name: 'Zkusit znovu' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('names the retry with retryLabel when the page is English', () => {
    render(<ErrorBanner message="HTTP 500" onRetry={() => {}} retryLabel="Retry" />);
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Zkusit znovu' })).toBeNull();
  });

  it('offers no retry without onRetry', () => {
    render(<ErrorBanner message="HTTP 500" retryLabel="Retry" />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('is an alert carrying its title and message', () => {
    render(<ErrorBanner title="Failed to load:" message="HTTP 500" />);
    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load: HTTP 500');
  });
});
