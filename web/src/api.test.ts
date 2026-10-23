import { describe, expect, it } from 'vitest';
import { params, intervalLabel } from './api';
describe('filter and interval contract', () => {
 it('uses repeated query keys for station and variable selection', () => {
  const query = params({ start: '2026-09-01', end: '2026-09-07', stationIds: [3, 8], variableIds: [2, 4], status: 'inactive', categories: ['weather'], interval: 86400, sort: 'asc' });
  expect(query.getAll('station_ids')).toEqual(['3', '8']);
  expect(query.getAll('variable_ids')).toEqual(['2', '4']);
  expect(query.get('status')).toBe('inactive');
  expect(query.get('interval_seconds')).toBe('86400');
 });
 it('distinguishes instantaneous and aggregated observations', () => {
  expect(intervalLabel(0)).toBe('Instantaneous'); expect(intervalLabel(300)).toBe('5 minutes'); expect(intervalLabel(3600)).toBe('1 hour'); expect(intervalLabel(86400)).toBe('Daily');
 });
});
