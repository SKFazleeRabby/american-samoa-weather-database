import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { LoaderCircle } from 'lucide-react';
import { init, use, graphic, type ECharts } from 'echarts/core';
import { CustomChart, HeatmapChart } from 'echarts/charts';
import { AriaComponent, DataZoomComponent, GridComponent, TooltipComponent, VisualMapComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import type { Catalog } from './api';
import { get } from './api';
import './analysis.css';

use([CustomChart, HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, DataZoomComponent, AriaComponent, CanvasRenderer]);
type Annual = { year: number; available_days: number; expected_days: number; percentage: number | null };
type AvailabilityStation = { id: number; name: string; network: string | null; status: string; first_date: string | null; last_date: string | null; available_days: number; missing_days: number; ranges: { start: string; end: string }[]; years: Annual[] };
type Availability = { as_of: string; first_date: string | null; years: number[]; stations: AvailabilityStation[] };
const DAY = 86400000;
const stamp = (day: string) => Date.parse(`${day}T00:00:00Z`);
const escape = (text: string) => text.replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]!));
const dateText = (s: string | null) => s ? new Date(stamp(s)).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric' }) : 'No data';

function Chart({ option, height, width = 700, label }: { option: EChartsOption; height: number; width?: number; label: string }) {
 const target = useRef<HTMLDivElement>(null), chart = useRef<ECharts | null>(null);
 useEffect(() => {
  const instance = init(target.current!, undefined, { renderer: 'canvas' }); chart.current = instance;
  const resize = new ResizeObserver(() => instance.resize()); resize.observe(target.current!);
  return () => { resize.disconnect(); instance.dispose(); chart.current = null; };
 }, []);
 useEffect(() => { chart.current?.setOption(option, true); }, [option]);
 useEffect(() => { chart.current?.resize(); }, [height, width]);
 return <div className="availability-chart-scroll" tabIndex={0} aria-label={`${label}. Scroll horizontally to see all years. Equivalent values are in the data table below.`}><div ref={target} role="img" aria-label={label} style={{ height, minWidth: width }} /></div>;
}

export default function Analysis({ catalog }: { catalog: Catalog }) {
 const [variableId, setVariableId] = useState(catalog.variables.find(v => v.code === 'precipitation')?.id ?? catalog.variables[0]?.id);
 const [status, setStatus] = useState('all');
 const variable = catalog.variables.find(v => v.id === variableId);
 const query = useQuery({ queryKey: ['availability', variableId], queryFn: ({ signal }) => get<Availability>(`/api/analysis/availability?variable_id=${variableId}`, signal), enabled: variableId != null, staleTime: 300_000 });
 const data = query.data;
 const stations = useMemo(() => (data?.stations ?? []).filter(s => status === 'all' || s.status === status), [data, status]);
 const reporting = stations.filter(s => s.first_date);
 const option = useMemo(() => {
  if (!data?.first_date) return null;
  const names = stations.map(s => s.name);
  const common = { animation: false, textStyle: { fontFamily: 'DM Sans', fontSize: 13, color: '#526962' }, aria: { enabled: true, label: { description: `Daily ${variable?.name.toLowerCase()} availability by station. Equivalent dates, day counts, and yearly percentages are provided in the expandable data table below.` } }, useUTC: true };
  const axis = { type: 'category' as const, data: names, inverse: true, axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: '#203d3c', fontSize: 13, width: 155, overflow: 'truncate' as const } };
  const renderItem = (params: any, api: any) => {
   const row = api.value(0), start = api.coord([api.value(1), row]), end = api.coord([api.value(2), row]);
   const shape = graphic.clipRectByRect({ x: start[0], y: start[1] - 8, width: Math.max(end[0] - start[0], 1), height: 16 }, params.coordSys);
   return shape ? { type: 'rect' as const, shape, style: { fill: api.value(3) ? '#347e67' : '#d6dcd6' } } : undefined;
  };
  const timelineData = stations.flatMap((s, row) => !s.first_date ? [] : [
   { value: [row, stamp(s.first_date), stamp(s.last_date!) + DAY, 0], name: s.name },
   ...s.ranges.map(r => ({ value: [row, stamp(r.start), stamp(r.end) + DAY, 1], name: s.name })),
  ]);
  const timeline: EChartsOption = { ...common, grid: { left: 180, right: 30, top: 25, bottom: 75 }, xAxis: { type: 'time', min: stamp(data.first_date), max: stamp(data.as_of) + DAY, axisLabel: { formatter: '{yyyy}', hideOverlap: true }, splitLine: { show: true, lineStyle: { color: '#e9eee7' } } }, yAxis: axis,
   dataZoom: [{ type: 'slider', xAxisIndex: 0, filterMode: 'none', bottom: 12, height: 20, showDataShadow: false, borderColor: '#dce5db', fillerColor: '#347e6720', handleStyle: { color: '#347e67' } }],
   tooltip: { trigger: 'item', confine: true, formatter: (p: any) => { const s = stations[p.value[0]]; return `<strong>${escape(s.name)}</strong><br/>${dateText(s.first_date)} – ${dateText(s.last_date)}<br/>${s.available_days.toLocaleString()} days available<br/>${s.missing_days.toLocaleString()} missing days within this range`; } },
   series: [{ type: 'custom', renderItem, encode: { x: [1, 2], y: 0 }, data: timelineData, silent: false }] };
  const heatData = stations.flatMap((s, row) => s.years.map((y, col) => [col, row, y.percentage ?? -1]));
  const heatmap: EChartsOption = { ...common, grid: { left: 0, right: 25, top: 55, bottom: 20 }, xAxis: { type: 'category', data: data.years.map(String), position: 'top', axisTick: { show: false }, axisLine: { show: false }, axisLabel: { rotate: 45, interval: 0, fontSize: 12 } }, yAxis: { ...axis, axisLabel: { show: false } },
   visualMap: { show: false, min: 0, max: 100, dimension: 2, seriesIndex: 0, inRange: { color: ['#d6dcd6', '#e5eddb', '#b6d5bb', '#70ac8c', '#276b57'] } },
   tooltip: { confine: true, formatter: (p: any) => { const s = stations[p.value[1]], year = s.years[p.value[0]]; return `<strong>${escape(s.name)} · ${year.year}</strong><br/>${year.percentage == null ? 'Outside the recorded range (N/A)' : `${year.percentage}% available<br/>${year.available_days} of ${year.expected_days} calendar days`}`; } },
   series: [
    { type: 'heatmap', data: heatData.filter(v => v[2] >= 0), itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 3 }, emphasis: { itemStyle: { borderColor: '#203d3c', borderWidth: 2 } } },
    { type: 'heatmap', data: heatData.filter(v => v[2] < 0), itemStyle: { color: '#f2f4ef', borderColor: '#fff', borderWidth: 2, borderRadius: 3 }, emphasis: { itemStyle: { borderColor: '#203d3c', borderWidth: 2 } } },
   ] };
  return { timeline, heatmap };
 }, [data, stations, variable?.name]);
 return <main className="analysis-page">
  <div className="analysis-heading"><div><h1>Data availability</h1><p>Review the coverage and gaps in daily station observations.</p></div><div className="analysis-controls"><div className="filter"><label htmlFor="analysis-variable">Variable</label><select id="analysis-variable" value={variableId ?? ''} onChange={e => setVariableId(Number(e.target.value))}>{catalog.variables.map(v => <option value={v.id} key={v.id}>{v.name}</option>)}</select></div><div className="filter"><label htmlFor="analysis-status">Station status</label><select id="analysis-status" value={status} onChange={e => setStatus(e.target.value)}><option value="all">All stations</option><option value="active">Active stations</option><option value="inactive">Inactive stations</option></select></div></div></div>
  {query.isPending && <div className="analysis-loading" role="status"><LoaderCircle className="spin" />Loading availability…</div>}
  {query.isError && <div className="error-panel" role="alert"><p>{query.error.message}</p><button className="retry" onClick={() => query.refetch()}>Try again</button></div>}
  {data && <>
   <div className="analysis-summary"><div><span>Selected variable</span><strong>{variable?.name}</strong></div><div><span>Stations with data</span><strong>{reporting.length} <small>of {stations.length}</small></strong></div><div><span>First observation</span><strong>{dateText(reporting.reduce<string | null>((first, s) => !first || s.first_date! < first ? s.first_date : first, null))}</strong></div><div><span>Measured through</span><strong>{dateText(data.as_of)}</strong></div></div>
   {!reporting.length ? <div className="analysis-empty">No daily observations are available for this variable and station status.</div> : option && <>
    <section className="analysis-card" aria-labelledby="range-heading"><div className="analysis-card-header"><div><h2 id="range-heading">Station record coverage</h2><p>Each bar spans the station’s first to last recorded day for {variable?.name.toLowerCase()}.</p></div><div className="analysis-legend"><span><i className="coverage-present" />Available</span><span><i className="coverage-missing" />Missing within record</span></div></div><Chart option={option.timeline} height={stations.length * 32 + 110} label={`${variable?.name} availability timeline by station`} /><p className="analysis-note">Gray sections mark dates with no daily reading. Drag the year slider to inspect shorter gaps. Stations without a record have no bar. Hover over a bar for its dates and day counts.</p></section>
    <section className="analysis-card" aria-labelledby="heatmap-heading"><div className="analysis-card-header"><div><h2 id="heatmap-heading">Annual data availability</h2><p>Percentage of calendar days with a daily reading, by station and year.</p></div><div className="heatmap-key"><span>0%</span><i /><span>100%</span><span className="na-key">N/A</span></div></div><div className="heatmap-layout"><div className="heatmap-station-labels" aria-hidden="true">{stations.map(s => <span key={s.id} title={s.name}>{s.name}</span>)}</div><Chart option={option.heatmap} height={stations.length * 32 + 75} width={Math.max(500, data.years.length * 34 + 25)} label={`${variable?.name} annual availability heatmap; stations are rows and years are columns`} /></div><p className="analysis-note">A year uses 365 or 366 days; the current year includes dates only through {dateText(data.as_of)} in Pacific/Pago_Pago. Partial first and last years use the full calendar-year denominator. N/A means a year is outside the station’s recorded range. Scroll horizontally for all years.</p></section>
    <details className="analysis-card analysis-data"><summary>View availability data table</summary><div className="analysis-data-scroll"><table><caption>Daily {variable?.name.toLowerCase()} coverage. Annual percentages follow the heatmap definition above.</caption><thead><tr><th scope="col">Station</th><th scope="col">First reading</th><th scope="col">Last reading</th><th scope="col">Available days</th><th scope="col">Missing within range</th>{data.years.map(y => <th scope="col" key={y}>{y}</th>)}</tr></thead><tbody>{stations.map(s => <tr key={s.id}><th scope="row">{s.name}</th><td>{dateText(s.first_date)}</td><td>{dateText(s.last_date)}</td><td>{s.available_days.toLocaleString()}</td><td>{s.missing_days.toLocaleString()}</td>{s.years.map(y => <td key={y.year} title={`${y.available_days} of ${y.expected_days} days`}>{y.percentage == null ? 'N/A' : `${y.percentage}%`}</td>)}</tr>)}</tbody></table></div></details>
   </>}
   <p className="analysis-method">Availability counts distinct local dates with a stored daily reading. Zero is available data. Quality-flagged readings count as present; availability does not assess measurement quality. All station statuses are included by default.</p>
  </>}
 </main>;
}
