import { useEffect, useRef, useState } from 'react';
import type { SessionEvent } from '../lib/types';
import Icon from './Icon';

const actorLabels = { app: 'Take One', user: 'You', muse: 'Muse', renderer: 'Renderer' };

export default function SessionPanel({ events, onExport }: {
  events: SessionEvent[]; onExport: () => void;
}) {
  const [filter, setFilter] = useState<'all' | 'decision' | 'error'>('all');
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const previousCount = useRef(events.length);
  const decisions = events.filter(event => event.kind === 'decision').length;
  const filtered = events.filter(event =>
    (filter === 'all' || event.kind === filter)
    && (event.title + ' ' + (event.reason ?? '') + ' ' + actorLabels[event.actor])
      .toLowerCase().includes(query.toLowerCase()),
  );
  useEffect(() => {
    if (events.length > previousCount.current && list.current) {
      list.current.scrollTop = list.current.scrollHeight;
    }
    previousCount.current = events.length;
  }, [events.length]);

  return (
    <section className="session-panel" aria-labelledby="session-heading">
      <div className="section-heading session-heading">
        <div className="heading-with-icon"><span className="muse-mark">M</span>
          <h2 id="session-heading">Muse session</h2>
          <span className="count-badge">{events.length}</span>
        </div>
        <div className="button-group">
          <button className={'icon-button small ' + (searchOpen ? 'active' : '')}
            aria-label="Search session log" aria-pressed={searchOpen}
            onClick={() => { setSearchOpen(!searchOpen); setQuery(''); }}><Icon name="search" size={15} /></button>
          <button className="icon-button small" aria-label="Download session log" onClick={onExport}><Icon name="download" size={15} /></button>
        </div>
      </div>
      <div className="session-connection"><span className="status-dot idle" />Muse not connected<span>Local activity</span></div>
      <div className="log-filters" aria-label="Filter session log">
        <button className={filter === 'all' ? 'active' : ''} aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>All activity</button>
        <button className={filter === 'decision' ? 'active' : ''} aria-pressed={filter === 'decision'} onClick={() => setFilter('decision')}>Decisions <span>{decisions}</span></button>
        <button className={filter === 'error' ? 'active' : ''} aria-pressed={filter === 'error'} onClick={() => setFilter('error')}>Issues</button>
      </div>
      {searchOpen && <div className="log-search"><Icon name="search" size={14} /><input
        autoFocus aria-label="Search activity" placeholder="Find an action or decision…"
        value={query} onChange={event => setQuery(event.target.value)} /></div>}
      <div className="event-list" ref={list} role="log" aria-label="Session activity" aria-live="polite" aria-relevant="additions">
        {filtered.map(event => (
          <article className={'log-event ' + event.kind} key={event.id}>
            <div className="event-marker"><Icon name={event.kind === 'decision' ? 'spark' : event.kind === 'error' ? 'warning' : event.actor === 'user' ? 'sliders' : 'check'} size={12} /></div>
            <div className="event-content">
              <div className="event-meta"><span>{actorLabels[event.actor]}</span>
                <time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}</time>
              </div>
              <h3>{event.title}</h3>
              {event.reason && <p>{event.reason}</p>}
              {event.details && <details className="event-details"><summary>Details <Icon name="chevron" size={11} /></summary>
                <dl>{Object.entries(event.details).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl>
              </details>}
            </div>
          </article>
        ))}
        {filtered.length === 0 && <div className="log-empty"><Icon name={filter === 'error' ? 'check' : 'log'} size={23} />
          <h3>{query ? 'No matching activity' : filter === 'error' ? 'No issues recorded' : 'No editing decisions yet'}</h3>
          <p>{query ? 'Try a different search.' : filter === 'error' ? 'Any errors will appear here.' : 'Muse’s changes and short explanations will appear here when a session runs.'}</p>
        </div>}
        {filter === 'all' && !query && !events.some(event => event.actor === 'muse') &&
          <div className="muse-waiting"><Icon name="spark" size={16} /><p>A place for every decision.<span>Muse’s edits and their reasons will appear in this log.</span></p></div>}
      </div>
      <footer className="session-footer"><span><span className="status-dot" />{decisions} Muse decisions</span><span>Usage unavailable</span></footer>
    </section>
  );
}
