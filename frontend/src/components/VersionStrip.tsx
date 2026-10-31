import type { Project, Version } from '../lib/types';
import { formatTime } from '../lib/types';
import Icon from './Icon';

export default function VersionStrip({ project, onSelect, onImportRender, busy }: {
  project: Project; onSelect: (version: Version) => void; onImportRender: () => void; busy: boolean;
}) {
  return (
    <section className="versions-section" aria-labelledby="versions-heading">
      <div className="section-heading"><div className="heading-with-icon"><h2 id="versions-heading">Version history</h2>
        <span className="count-badge">{project.versions.length.toString().padStart(2, '0')}</span></div>
        <button className="text-button" onClick={onImportRender} disabled={!project.source || busy}
          title="Import an existing rendered output with the same timing and dimensions">
          <Icon name="plus" size={13} />Import render</button>
      </div>
      {project.versions.length ? <div className="version-list">
        {project.versions.map((version, index) => {
          const asset = project.assets.find(item => item.id === version.assetId);
          const selected = version.id === project.selectedVersionId;
          return <button className={'version-card ' + (selected ? 'selected ' : '') + version.kind}
            key={version.id} aria-pressed={selected} aria-label={'Select ' + version.name}
            onClick={() => onSelect(version)}>
            <div className="version-thumb">
              {asset?.thumbnail ? <img src={asset.thumbnail} alt="" /> : <div className="recipe-art"><Icon name="sliders" size={23} /><span>RECIPE DRAFT</span></div>}
              <span className="version-number">{String(index + 1).padStart(2, '0')}</span>
              {selected && <span className="version-selected"><Icon name="check" size={11} /></span>}
              {asset && <span className="version-duration">{formatTime(asset.duration)}</span>}
            </div>
            <div className="version-caption"><strong>{version.name}</strong><span>{version.kind === 'source' ? 'Source footage' : version.kind === 'draft' ? 'Not rendered' : 'Imported output'}</span></div>
          </button>;
        })}
        <button className="new-version-card" onClick={onImportRender} disabled={busy}><Icon name="plus" size={20} /><span>Import a render</span></button>
      </div> : <div className="versions-empty"><div className="empty-filmstrip" aria-hidden="true"><span /><span /><span /></div>
        <div><p>A little history goes a long way.</p><span>Your original, recipe drafts and rendered versions will live here.</span></div><span className="empty-version-index">01 —</span></div>}
    </section>
  );
}
