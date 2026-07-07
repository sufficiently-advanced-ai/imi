/**
 * Tests for SeedPickerOverlay's debounced remote seed search (Task C3).
 *
 * - debounce fires searchEntities once after typing settles
 * - remote results render and clicking one selects the seed
 * - Enter selects the first remote result
 * - rejection falls back silently (no error UI, chip heuristic still works)
 */

import React from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';

jest.mock('@/lib/api/domain', () => ({
  searchEntities: jest.fn(),
}));

import { searchEntities, type EntitySearchResult } from '@/lib/api/domain';
import { SeedPickerOverlay } from '../SeedPickerOverlay';

const mockSearchEntities = searchEntities as jest.Mock;

const TOP_ENTITIES = [
  { id: 'acme', name: 'Acme Corp', type: 'client', degree: 12 },
  { id: 'globex', name: 'Globex', type: 'client', degree: 8 },
];

const RESULTS: EntitySearchResult[] = [
  { id: 'acme-remote', name: 'Acme Remote', type: 'project' },
  { id: 'acme-two', name: 'Acme Two', type: 'person' },
];

function renderOverlay(onSelectSeed = jest.fn()) {
  render(
    <SeedPickerOverlay
      topEntities={TOP_ENTITIES}
      onSelectSeed={onSelectSeed}
      onShowFullGraph={jest.fn()}
      domain="acme-domain"
    />,
  );
  return onSelectSeed;
}

beforeEach(() => {
  jest.useFakeTimers();
  mockSearchEntities.mockReset();
});

afterEach(() => {
  jest.runOnlyPendingTimers();
  jest.useRealTimers();
});

function typeQuery(value: string) {
  const input = screen.getByPlaceholderText(/Type an entity name or ID/i);
  fireEvent.change(input, { target: { value } });
  return input;
}

describe('SeedPickerOverlay — remote search', () => {
  it('debounces and fires searchEntities once after typing settles', async () => {
    mockSearchEntities.mockResolvedValue([]);
    renderOverlay();

    typeQuery('a');
    typeQuery('ac');
    typeQuery('acm');
    expect(mockSearchEntities).not.toHaveBeenCalled();

    await act(async () => {
      jest.advanceTimersByTime(250);
    });

    expect(mockSearchEntities).toHaveBeenCalledTimes(1);
    expect(mockSearchEntities).toHaveBeenCalledWith({
      query: 'acm',
      maxResults: 12,
      domain: 'acme-domain',
    });
  });

  it('renders results and clicking one selects the seed', async () => {
    mockSearchEntities.mockResolvedValue(RESULTS);
    const onSelectSeed = renderOverlay();

    typeQuery('acme');
    await act(async () => {
      jest.advanceTimersByTime(250);
    });

    await waitFor(() => expect(screen.getByText('Acme Remote')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Acme Remote'));
    expect(onSelectSeed).toHaveBeenCalledWith('acme-remote');
  });

  it('Enter selects the first remote result', async () => {
    mockSearchEntities.mockResolvedValue(RESULTS);
    const onSelectSeed = renderOverlay();

    const input = typeQuery('acme');
    await act(async () => {
      jest.advanceTimersByTime(250);
    });
    await waitFor(() => expect(screen.getByText('Acme Remote')).toBeInTheDocument());

    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSelectSeed).toHaveBeenCalledWith('acme-remote');
  });

  it('falls back silently on rejection (chip heuristic still works)', async () => {
    mockSearchEntities.mockRejectedValue(new Error('boom'));
    const onSelectSeed = renderOverlay();

    const input = typeQuery('Globex');
    await act(async () => {
      jest.advanceTimersByTime(250);
    });

    // No error UI; the search-results section never appears.
    expect(screen.queryByText('Search results:')).not.toBeInTheDocument();

    // Enter falls back to the local chip match.
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSelectSeed).toHaveBeenCalledWith('globex');
  });

  it('ignores a stale response that resolves after the query is cleared', async () => {
    let resolveSearch: (r: EntitySearchResult[]) => void = () => {};
    mockSearchEntities.mockImplementation(
      () => new Promise<EntitySearchResult[]>((resolve) => { resolveSearch = resolve; }),
    );
    renderOverlay();

    typeQuery('acme');
    await act(async () => {
      jest.advanceTimersByTime(250); // request fires, stays in flight
    });

    typeQuery(''); // user clears the input before the response lands

    await act(async () => {
      resolveSearch(RESULTS); // stale response arrives
    });

    // Results must stay empty — the cleared query invalidated the request.
    expect(screen.queryByText('Acme Remote')).not.toBeInTheDocument();
  });
});
