import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, CloudLightning, Database, Loader2, MapPin, Plane, Search, X } from 'lucide-react';
import { api } from '../api';
import type { AirportProfile, Network, Scenario, ScenarioSummary, SourceFlight } from '../types';
import { compact, histTime } from '../lib/format';

const MONTHS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
export const airportCode = (a: unknown) => typeof a === 'string' ? a : (a as {code?: string})?.code ?? '';

export function GlobeOverview({network, summary, routeLimit, onRouteLimit, weatherOnly, onWeatherOnly, onSelect, selected}: {
  network: Network | null; summary: Record<string, unknown> | null; routeLimit: number; onRouteLimit: (n: number) => void;
  weatherOnly: boolean; onWeatherOnly: (b: boolean) => void; onSelect: (code: string) => void; selected: string | null;
}) {
  const [query, setQuery] = useState('');
  const top = useMemo(() => [...(network?.airports ?? [])].sort((a, b) => b.inbound - a.inbound).slice(0, 9), [network]);
  const matches = useMemo(() => {
    const q = query.trim().toUpperCase();
    if (!q || !network) return [];
    return network.airports.filter(a => a.code.startsWith(q) || a.name.toUpperCase().includes(q) || (a.city ?? '').toUpperCase().includes(q)).slice(0, 6);
  }, [query, network]);
  const maxIn = top[0]?.inbound ?? 1;
  return <aside className="globe-panel left glass" aria-label="Network overview">
    <span className="eyebrow">US DOT 2015 · DISRUPTED FLIGHTS</span>
    <h1>Every delay has a <em>route</em>.</h1>
    <p className="lede">Pick an airport to see where its delayed arrivals come from, then open its recovery room. ATC, each captain and ground ops negotiate one checked plan there.</p>
    <div className="kpis">
      <div><b>{summary ? compact(Number(summary.qualifying_rows)) : '—'}</b><span>qualifying delayed flights</span></div>
      <div><b>{network?.airports.length ?? '—'}</b><span>airports</span></div>
      <div><b>{network ? compact(network.route_count) : '—'}</b><span>routes</span></div>
      <div><b>{summary ? compact(Number(summary.weather_gt15)) : '—'}</b><span>weather delay &gt; 15 min</span></div>
    </div>
    <div className="search"><Search size={14}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Find an airport — ATL, Denver, ORD…" aria-label="Find an airport"/>{query && <button onClick={() => setQuery('')} aria-label="Clear search"><X size={13}/></button>}</div>
    {matches.length > 0 && <div className="matches">{matches.map(a => <button key={a.code} onClick={() => { onSelect(a.code); setQuery(''); }}><b>{a.code}</b><span>{a.name}</span></button>)}</div>}
    <div className="controls">
      <label>Route density <span>{routeLimit} busiest</span><input type="range" min={100} max={1400} step={50} value={routeLimit} onChange={e => onRouteLimit(Number(e.target.value))}/></label>
      <label className="switch"><input type="checkbox" checked={weatherOnly} onChange={e => onWeatherOnly(e.target.checked)}/><i/>Only weather-heavy routes (&gt;15% weather)</label>
    </div>
    <div className="legend"><span>Weather share of delays</span><div className="ramp"/><div className="ramp-labels"><span>0%</span><span>17%</span><span>35%+</span></div></div>
    <div className="top-list"><span className="eyebrow">MOST DELAYED ARRIVALS</span>{top.map(a => <button key={a.code} className={selected === a.code ? 'active' : ''} onClick={() => onSelect(a.code)}>
      <b>{a.code}</b><span className="bar"><i style={{width: `${a.inbound / maxIn * 100}%`}}/></span><small>{compact(a.inbound)}</small>
    </button>)}</div>
    <p className="fine">{network?.note ?? 'Loading network…'} October 2015 is absent from the supplied file.</p>
  </aside>;
}

export function AirportPanel({code, network, scenarios, onClose, onOpenScenario, onBuilt}: {
  code: string; network: Network | null; scenarios: ScenarioSummary[]; onClose: () => void;
  onOpenScenario: (id: string) => void; onBuilt: (s: Scenario) => void;
}) {
  const [profile, setProfile] = useState<AirportProfile | null>(null);
  const [error, setError] = useState('');
  const [date, setDate] = useState('');
  const [flights, setFlights] = useState<SourceFlight[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const info = network?.airports.find(a => a.code === code);
  const prepared = scenarios.filter(s => airportCode(s.airport) === code);

  useEffect(() => {
    let live = true;
    setProfile(null); setError(''); setDate(''); setFlights([]); setPicked([]);
    api<AirportProfile>(`/airports/${code}`).then(p => { if (live) setProfile(p); }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [code]);

  useEffect(() => {
    if (!date) return;
    let live = true;
    setBusy(true); setFlights([]); setPicked([]);
    api<{flights: SourceFlight[]}>(`/flights?airport=${code}&date=${date}&limit=200`).then(r => { if (live) setFlights(r.flights); }).catch(e => { if (live) setError(e.message); }).finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [code, date]);

  const toMin = (v: unknown) => { const s = String(v ?? '').padStart(4, '0'); return Number(s.slice(0, 2)) * 60 + Number(s.slice(2)); };
  const landings = picked.map(id => flights.find(f => f.id === id)).filter((f): f is SourceFlight => !!f).map(f => toMin(f.historical.wheels_on));
  const spread = landings.length > 1 ? Math.max(...landings) - Math.min(...landings) : 0;
  const maxMonth = Math.max(1, ...(profile?.months.map(m => m.flights) ?? [1]));

  async function build() {
    setBusy(true); setError('');
    try { onBuilt(await api<Scenario>('/scenarios', {flight_ids: picked, name: `${code} ${date} recovery`})); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  return <aside className="globe-panel right glass" aria-label={`Airport ${code}`}>
    <header className="panel-head">
      <div><span className="eyebrow"><MapPin size={11}/> {[info?.city, info?.state].filter(Boolean).join(', ') || 'Airport'}</span><h2>{code} <small>{info?.name ?? profile?.airport.name}</small></h2></div>
      <button className="icon-btn ghost" onClick={onClose} aria-label="Close airport"><X size={16}/></button>
    </header>
    <div className="panel-scroll">
      <div className="kpis three">
        <div><b>{info ? compact(info.inbound) : '—'}</b><span>delayed arrivals</span></div>
        <div><b>{info ? compact(info.outbound) : '—'}</b><span>delayed departures</span></div>
        <div><b>{info ? `${Math.round(info.weather_inbound / Math.max(1, info.inbound) * 100)}%` : '—'}</b><span>weather-driven</span></div>
      </div>
      {profile && <div className="months" aria-label="Delayed arrivals by month">{MONTHS.map((m, i) => {
        const row = profile.months.find(x => Number(x.month) === i + 1);
        return <div key={i} className={row ? '' : 'missing'} title={row ? `${row.flights} delayed arrivals, ${row.weather} weather` : 'No data in supplied file'}>
          <span style={{height: `${row ? Math.max(4, row.flights / maxMonth * 100) : 100}%`}}><i style={{height: row ? `${row.weather / Math.max(1, row.flights) * 100}%` : 0}}/></span><small>{m}</small>
        </div>;
      })}</div>}
      {prepared.length > 0 && <section className="block">
        <h3><Plane size={14}/> Prepared recovery scenarios</h3>
        {prepared.map(s => <button key={s.id} className="scenario-card" onClick={() => onOpenScenario(s.id)}>
          <div><b>{s.name}</b><p>{s.description}</p><small>{s.flight_count} aircraft{s.date ? ` · ${s.date}` : ''}</small></div><ArrowRight size={16}/>
        </button>)}
      </section>}
      <section className="block">
        <h3><CloudLightning size={14}/> Build from a real disruption day</h3>
        <p className="fine">Most weather-affected arrival days at {code} in the supplied records. Pick 1–8 flights that landed within a 90-minute window.</p>
        {!profile && !error && <div className="working"><Loader2 size={14} className="spin"/> Reading records…</div>}
        <div className="date-chips">{profile?.dates.map(d => <button key={d.date} className={date === d.date ? 'active' : ''} onClick={() => setDate(d.date)}>
          <b>{d.date.slice(5)}</b><span>{d.flights} · {d.weather}<CloudLightning size={9}/></span>
        </button>)}</div>
        <label className="date-input">Or any date<input type="date" min="2015-01-01" max="2015-12-31" value={date} onChange={e => setDate(e.target.value)}/></label>
        {date && <div className="flight-pick">
          {busy && !flights.length ? <div className="working"><Loader2 size={14} className="spin"/> Loading flights…</div> : flights.length === 0 ? <p className="fine">No qualifying delayed arrivals on this date.</p> :
            flights.map(f => <label key={f.id} className={picked.includes(f.id) ? 'on' : ''}>
              <input type="checkbox" checked={picked.includes(f.id)} disabled={!picked.includes(f.id) && picked.length >= 8} onChange={() => setPicked(p => p.includes(f.id) ? p.filter(x => x !== f.id) : [...p, f.id])}/>
              <b>{f.callsign}</b><span>{f.origin.code} → {code}</span><span>landed {histTime(f.historical.wheels_on)}</span>
              <span className={Number(f.historical.weather_delay) > 15 ? 'wx' : ''}>wx {String(f.historical.weather_delay ?? 0)}′</span>
            </label>)}
        </div>}
        {picked.length > 0 && <div className="build-bar">
          <span>{picked.length} selected · landing spread {spread} min {spread > 90 && <em>(over 90 — will be rejected)</em>}</span>
          <button className="btn primary sm" disabled={busy || spread > 90} onClick={() => void build()}>{busy ? <Loader2 size={13} className="spin"/> : <Database size={13}/>} Build & open</button>
        </div>}
        {error && <p className="inline-error">{error}</p>}
      </section>
      {profile && profile.origins.length > 0 && <section className="block">
        <h3>Top delayed inbound origins</h3>
        <div className="origins">{profile.origins.map(o => <div key={o.origin}><b>{o.origin}</b><span>{o.name}</span><small>{o.flights}</small></div>)}</div>
      </section>}
    </div>
  </aside>;
}
