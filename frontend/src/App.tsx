import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import Icon from './components/Icon';
import ControlsPanel from './components/ControlsPanel';
import SessionPanel from './components/SessionPanel';
import Viewer from './components/Viewer';
import VersionStrip from './components/VersionStrip';
import WorkspaceDialog from './components/WorkspaceDialog';
import { downloadBlob, formatBytes, formatTime } from './lib/types';
import { getMedia, saveProject } from './lib/storage';
import { useWorkspace } from './lib/useWorkspace';

export default function App() {
  const workspace = useWorkspace();
  const { project, busy, saveState, error, setError } = workspace;
  const [dialog, setDialog] = useState<'sessions' | 'shortcuts' | null>(null);
  const [notice, setNotice] = useState('');
  const sourceInput = useRef<HTMLInputElement>(null);
  const renderInput = useRef<HTMLInputElement>(null);
  const nameAtFocus = useRef('');
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 4500);
    return () => clearTimeout(timer);
  }, [notice]);

  if (!project) return <div className="boot-screen"><img src="/take-one.svg" alt="" width="42" height="42" /><span className="spinner" /><p>Opening your workspace…</p></div>;
  const selectedVersion = project.versions.find(version => version.id === project.selectedVersionId);
  const selectedAsset = project.assets.find(asset => asset.id === selectedVersion?.assetId);
  const source = project.source;

  const fileChange = (event: ChangeEvent<HTMLInputElement>, kind: 'source' | 'rendered') => {
    const file = event.target.files?.[0];
    if (file) void workspace.importVideo(file, kind);
    event.target.value = '';
  };
  const exportLog = () => {
    const exported = {
      format: 'take-one-session', exportedAt: new Date().toISOString(),
      ...project,
      // Media is kept locally; exported logs contain metadata, not binary footage.
      source: source ? { ...source, thumbnail: undefined } : null,
      assets: project.assets.map(asset => ({ ...asset, thumbnail: undefined })),
    };
    const slug = project.name.replace(/[^a-z0-9_-]+/gi, '-').replace(/^-|-$/g, '').toLowerCase() || 'session';
    downloadBlob(new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' }), slug + '-session.json');
    setNotice('Session log downloaded. It contains activity and recipes; video files stay local.');
  };
  const downloadVideo = async () => {
    if (!selectedAsset || selectedVersion?.kind !== 'rendered') return;
    try {
      const blob = await getMedia(selectedAsset.id);
      if (!blob) throw new Error('Missing file');
      downloadBlob(blob, selectedAsset.name);
    } catch { setError('This rendered video is no longer available in browser storage.'); }
  };
  const openSavedSessions = async () => {
    try {
      await saveProject(project);
      setDialog('sessions');
    } catch { setError('The current session could not be saved. Export its log before switching sessions.'); }
  };
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-workspace">Skip to editor</a>
      <input ref={sourceInput} type="file" accept="video/*,.mov,.mkv,.m4v" className="visually-hidden" aria-label="Import source video"
        disabled={busy} onChange={event => fileChange(event, 'source')} />
      <input ref={renderInput} type="file" accept="video/*,.mov,.mkv,.m4v" className="visually-hidden" aria-label="Import rendered video"
        disabled={busy} onChange={event => fileChange(event, 'rendered')} />
      <header className="app-header">
        <a href="#main-workspace" className="brand" aria-label="Take One editor"><img src="/take-one.svg" width="31" height="31" alt="" /><span>take<span className="brand-one">one</span><span className="brand-period">.</span></span></a>
        <span className="header-separator" />
        <div className="project-title">
          <label htmlFor="project-name" className="visually-hidden">Session name</label>
          <input id="project-name" value={project.name} maxLength={80} spellCheck={false}
            onFocus={() => { nameAtFocus.current = project.name; }}
            onChange={event => workspace.update(previous => ({ ...previous, name: event.target.value }))}
            onBlur={() => {
              const name = project.name.trim() || 'Untitled session';
              workspace.update(previous => ({ ...previous, name }));
              if (name !== nameAtFocus.current) workspace.appendEvent({ actor: 'user', kind: 'activity', title: 'Session renamed', details: { name } });
            }} />
          <span className={'save-indicator ' + saveState} role="status">
            {saveState === 'saving' ? <span className="tiny-spinner" /> : saveState === 'saved' ? <Icon name="check" size={12} /> : <Icon name="warning" size={12} />}
            {saveState === 'saved' ? 'Saved locally' : saveState === 'saving' ? 'Saving…' : 'Not saved'}
          </span>
        </div>
        <div className="header-actions">
          <span className="workspace-badge"><span className="status-dot" />LOCAL WORKSPACE</span>
          <button className="button secondary import-header" onClick={() => sourceInput.current?.click()} disabled={busy}><Icon name="upload" size={15} /><span>{busy ? 'Opening…' : 'Import video'}</span></button>
          <button className="button primary export-log-button" onClick={exportLog}><Icon name="download" size={15} /><span>Export log</span></button>
        </div>
      </header>
      <nav className="side-rail" aria-label="Workspace navigation">
        <div className="rail-main">
          <a href="#main-workspace" className="rail-button active" aria-label="Editor" aria-current="page"><Icon name="sliders" size={21} /><span>Editor</span></a>
          <button className="rail-button" aria-label="Import media" onClick={() => sourceInput.current?.click()} disabled={busy}><Icon name="folder" size={21} /><span>Media</span></button>
          <button className={'rail-button ' + (dialog === 'sessions' ? 'active' : '')} aria-label="Open saved sessions" onClick={() => { void openSavedSessions(); }}><Icon name="clock" size={21} /><span>Sessions</span></button>
        </div>
        <div className="rail-bottom"><span className="rail-rule" /><button className="rail-button shortcut-button" aria-label="Keyboard shortcuts" onClick={() => setDialog('shortcuts')}><Icon name="keyboard" size={20} /><span>Shortcuts</span></button><span className="rail-version">v0.1</span></div>
      </nav>
      <main id="main-workspace" className="main-workspace">
        <div className="workspace-heading">
          <div><div className="workspace-eyebrow"><span className="eyebrow">THE COLOR WORKSPACE</span><span className="small-divider" /><span>ONE TAKE AT A TIME</span></div>
            <h1>{source ? project.name : 'Make the feeling visible.'}</h1>
          </div>
          <div className="source-info">{source ? <><span className="source-info-icon"><Icon name="film" size={17} /></span><div><strong>{source.width} × {source.height}<span> · {formatTime(source.duration)}</span></strong><small>{formatBytes(source.size)} · Browser preview</small></div></> : <span className="source-empty-label"><span className="status-dot" />Waiting for your first take</span>}</div>
        </div>
        {error && <div className="error-banner" role="alert"><Icon name="warning" size={17} /><span>{error}</span><button className="icon-button small" aria-label="Dismiss error" onClick={() => setError(null)}><Icon name="close" size={16} /></button></div>}
        <Viewer source={source} selectedVersion={selectedVersion} selectedAsset={selectedAsset} busy={busy}
          onChooseVideo={() => sourceInput.current?.click()} onDropVideo={file => { void workspace.importVideo(file, 'source'); }} onError={setError} />
        <div className="workspace-underbar">
          <span><Icon name="info" size={13} />{selectedVersion?.kind === 'draft' ? 'Draft settings are saved. The viewer shows your original until a preview is rendered.' : source ? 'Your source is preserved. Choose a version below to review it.' : 'Import footage to start. Your session activity is saved automatically.'}</span>
          {selectedVersion?.kind === 'rendered' ? <button className="text-button" onClick={() => { void downloadVideo(); }}><Icon name="download" size={13} />Download video</button> : <span className="fit-label">FIT TO VIEW</span>}
        </div>
        <VersionStrip project={project} onSelect={workspace.selectVersion} busy={busy} onImportRender={() => renderInput.current?.click()} />
        <div className="workspace-bottom-note"><span className="status-dot" />Session history is saved automatically.</div>
      </main>
      <aside className="context-panel">
        <ControlsPanel key={project.id} recipe={project.recipe} direction={project.direction} hasSource={!!source}
          onChange={workspace.updateRecipe} onCommit={workspace.logControl} onReset={workspace.resetRecipe}
          onSaveDraft={workspace.saveDraft} onSaveDirection={workspace.saveDirection} onStatus={setNotice} />
        <SessionPanel key={project.id + '-log'} events={project.events} onExport={exportLog} />
      </aside>
      <footer className="app-footer"><span><span className="status-dot" />All files stay in this browser</span><span className="footer-middle">TAKE ONE <span>CREATIVE DIRECTION / PRECISE COLOR</span></span><button className="mobile-sessions-button" onClick={() => { void openSavedSessions(); }}><Icon name="clock" size={12} />Sessions</button><span><span className="status-dot idle" />Muse & renderer: not connected</span></footer>
      {notice && <div className="toast" role="status"><Icon name="check" size={16} /><span>{notice}</span><button className="icon-button small" aria-label="Dismiss notification" onClick={() => setNotice('')}><Icon name="close" size={14} /></button></div>}
      {dialog && <WorkspaceDialog mode={dialog} activeId={project.id} onClose={() => setDialog(null)} onSelect={workspace.openSession} onNew={workspace.newSession} />}
    </div>
  );
}
