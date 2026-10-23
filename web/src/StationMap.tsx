import { useEffect, useRef } from 'react';
import L from 'leaflet';
import coastline from './coastline.json';
import type { Station } from './api';
import { stationColor, typeLabel } from './api';
import { LocateFixed, Minus, Plus } from 'lucide-react';

export default function StationMap({ stations, allStations, onSelect }: { stations: Station[]; allStations: Station[]; onSelect: (id: number | null) => void }) {
 const container = useRef<HTMLDivElement>(null), map = useRef<L.Map | null>(null), layer = useRef<L.LayerGroup | null>(null);
 const select = useRef(onSelect); select.current = onSelect;
 const bounds = useRef<L.LatLngBounds | null>(null);
 useEffect(() => {
  const instance = L.map(container.current!, { zoomControl: false, scrollWheelZoom: false, minZoom: 8, maxZoom: 18 }); map.current = instance;
  instance.on('click', event => {
   const target = event.originalEvent?.target;
   if (target instanceof Element && target.closest('[data-station-marker]')) return;
   select.current(null);
  });
  const coordinates = allStations.filter(s => s.latitude != null && s.longitude != null).map(s => [s.latitude!, s.longitude!] as [number, number]);
  bounds.current = coordinates.length ? L.latLngBounds(coordinates) : L.latLngBounds([[-14.36, -170.85], [-14.23, -170.52]]);
  const fit = () => instance.fitBounds(bounds.current!, { padding: [28, 35], maxZoom: 12, animate: false });
  L.geoJSON(coastline as Parameters<typeof L.geoJSON>[0], { interactive: false, style: { color: '#9bbab1', weight: 8, opacity: .12, fill: false } }).addTo(instance);
  L.geoJSON(coastline as Parameters<typeof L.geoJSON>[0], { interactive: false, style: { color: '#a4b9a4', weight: 1.2, fillColor: '#eff1e3', fillOpacity: 1 } }).addTo(instance);
  instance.attributionControl.addAttribution('<a href="https://www.geoboundaries.org/">geoBoundaries</a> · <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>');
  const labels: [number, number, string, string][] = [[-14.31, -170.725, 'TUTUILA', 'island'], [-14.294, -170.548, 'AUNUʻU', 'island'], [-14.285, -170.69, 'Pago Pago', 'place'], [-14.385, -170.655, 'SOUTH PACIFIC OCEAN', 'ocean']];
  labels.forEach(([lat, lon, text, type]) => L.marker([lat, lon], { interactive: false, keyboard: false, icon: L.divIcon({ className: `geographic-label ${type}`, html: text, iconSize: [150, 20], iconAnchor: [75, 10] }) }).addTo(instance));
  layer.current = L.layerGroup().addTo(instance); fit();
  const resize = new ResizeObserver(() => instance.invalidateSize({ pan: false })); resize.observe(container.current!);
  return () => { resize.disconnect(); instance.remove(); map.current = null; layer.current = null; };
 }, [allStations]);
 useEffect(() => {
  if (!layer.current) return;
  layer.current.clearLayers();
  stations.filter(s => s.latitude != null && s.longitude != null).forEach(s => {
   const color = s.status === 'active' ? stationColor(s.category) : '#a6aeaa';
   L.circleMarker([s.latitude!, s.longitude!], { radius: 11, color, weight: 1, opacity: .28, fillColor: '#fff', fillOpacity: .65, interactive: false }).addTo(layer.current!);
   const marker = L.circleMarker([s.latitude!, s.longitude!], { radius: 5, color: '#fff', weight: 2, fillColor: color, fillOpacity: 1, bubblingMouseEvents: false }).addTo(layer.current!);
   const tip = document.createElement('div'); tip.className = 'station-map-tooltip';
   const strong = document.createElement('strong'); strong.textContent = s.name;
   const type = document.createElement('span'); type.textContent = `${typeLabel(s.category)} station · ${s.status}`;
   const details = document.createElement('span'); details.textContent = `${s.elevation_m == null ? 'Elevation unavailable' : `${s.elevation_m.toFixed(1)} m elevation`} · ${s.network || 'Network unavailable'}`;
   const hint = document.createElement('small'); hint.textContent = 'Select to view observations →'; tip.append(strong, type, details, hint);
   marker.bindTooltip(tip, { direction: 'top', offset: [0, -8], className: 'station-leaflet-tooltip' });
   marker.on('click', event => { if (event.originalEvent) L.DomEvent.stopPropagation(event.originalEvent); select.current(s.id); });
   const element = marker.getElement()!; element.setAttribute('data-station-marker', String(s.id)); element.setAttribute('tabindex', '0'); element.setAttribute('role', 'button'); element.setAttribute('aria-label', `View observations for ${s.name}`);
   element.addEventListener('focus', () => marker.openTooltip()); element.addEventListener('blur', () => marker.closeTooltip());
   element.addEventListener('keydown', event => { const e = event as KeyboardEvent; if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select.current(s.id); } if (e.key === 'Escape') marker.closeTooltip(); });
  });
 }, [stations, allStations]);
 return <div className="map"><div id="leaflet-map" ref={container} aria-label="American Samoa station map" /><div className="map-caption"><span className="live-dot" />{stations.length} station{stations.length === 1 ? '' : 's'} shown · Tutuila & Aunuʻu</div><div className="map-tools"><button aria-label="Zoom in" onClick={() => map.current?.zoomIn()}><Plus size={15} /></button><button aria-label="Zoom out" onClick={() => map.current?.zoomOut()}><Minus size={15} /></button><button aria-label="Reset map" onClick={() => bounds.current && map.current?.fitBounds(bounds.current, { padding: [28, 35], maxZoom: 12 })}><LocateFixed size={15} /></button></div></div>;
}
