export type Station = { id: number; code: string; name: string; network: string | null; latitude: number | null; longitude: number | null; elevation_m: number | null; timezone: string; category: string; status: string };
export type Variable = { id: number; code: string; name: string; unit: string; description: string; synoptic_code?: string | null; aggregation?: string | null };
export type Catalog = { stations: Station[]; variables: Variable[]; intervals: number[]; first_date: string | null; last_date: string | null; default_start: string; default_end: string };
export type Filters = { start: string; end: string; stationIds: number[]; variableIds: number[]; status: string; categories: string[]; interval: number; sort: 'asc' | 'desc' };
export type Observation = { date: string; station_id: number; observed_at: string; interval_seconds: number; values: Record<string, number>; quality: Record<string, string> };
export type Page = { items: Observation[]; next_cursor: string | null; has_more: boolean };
export function params(f: Filters) {
 const q = new URLSearchParams({ start: f.start, end: f.end, status: f.status, interval_seconds: String(f.interval), sort: f.sort });
 for (const id of f.stationIds) q.append('station_ids', String(id));
 for (const id of f.variableIds) q.append('variable_ids', String(id));
 for (const c of f.categories) q.append('categories', c);
 return q;
}
export async function get<T>(url: string, signal?: AbortSignal): Promise<T> {
 let response: Response;
 try { response = await fetch(url, { signal }); }
 catch (error) { if (signal?.aborted) throw error; throw new Error('Unable to reach the archive. Check your connection and try again.'); }
 if (!response.ok) {
  let message = 'Unable to load data. Please try again.';
  try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch { /* proxy may return non-JSON */ }
  throw new Error(message);
 }
 return response.json();
}
export const intervalLabel = (seconds: number) => seconds === 0 ? 'Instantaneous' : seconds === 86400 ? 'Daily' : seconds % 86400 === 0 ? `${seconds / 86400} days` : seconds % 3600 === 0 ? `${seconds / 3600} hour${seconds === 3600 ? '' : 's'}` : seconds % 60 === 0 ? `${seconds / 60} minutes` : `${seconds} seconds`;
export const typeLabel = (type: string) => type.charAt(0).toUpperCase() + type.slice(1);
export const stationColor = (type: string) => type === 'weather' ? '#d8933e' : type === 'stream' ? '#468fba' : '#9675ad';
