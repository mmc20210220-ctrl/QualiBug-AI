import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./session', () => ({
  API_V1_BASE: '/api/v1',
  fetchJSON: vi.fn(),
  resolveProjectId: vi.fn(),
}));

import { fetchJSON, resolveProjectId } from './session';
import { getTestIntelligence } from './test-intelligence';

describe('getTestIntelligence direct read', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('starts the Test Intelligence request without a serial project-list lookup', async () => {
    vi.mocked(fetchJSON).mockRejectedValue(new Error('stop after request'));

    await expect(getTestIntelligence(' acme ')).rejects.toThrow('stop after request');

    expect(resolveProjectId).not.toHaveBeenCalled();
    expect(fetchJSON).toHaveBeenCalledTimes(1);
    expect(fetchJSON).toHaveBeenCalledWith('/api/v1/projects/acme/test-intelligence');
  });

  it('does not issue a request when the project id is blank', async () => {
    await expect(getTestIntelligence('   ')).resolves.toBeNull();

    expect(resolveProjectId).not.toHaveBeenCalled();
    expect(fetchJSON).not.toHaveBeenCalled();
  });
});
