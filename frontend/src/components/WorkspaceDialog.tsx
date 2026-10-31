import { useEffect, useRef, useState } from 'react';
import type { Project } from '../lib/types';
import { getProjects } from '../lib/storage';
import Icon from './Icon';

export default function WorkspaceDialog({ mode, activeId, onClose, onSelect, onNew }: {
  mode: 'sessions' | 'shortcuts'; activeId: string;
  onClose: () => void; onSelect: (id: string) => Promise<void>; onNew: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
    const element = dialog.current;
    if (mode === 'sessions') void getProjects().then(setProjects).catch(() => setError(true));
    return () => element?.close();
  }, [mode]);
  return (
    <dialog ref={dialog} className="workspace-dialog" aria-labelledby="dialog-heading"
      onCancel={event => { event.preventDefault(); onClose(); }}
      onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="dialog-inner">
        <div className="section-heading"><div><span className="eyebrow">YOUR WORKSPACE</span><h2 id="dialog-heading">{mode === 'sessions' ? 'Saved sessions' : 'Keyboard shortcuts'}</h2></div>
          <button className="icon-button" aria-label="Close dialog" onClick={onClose}><Icon name="close" /></button></div>
        {mode === 'sessions' ? <>
          <p className="dialog-intro">Footage, drafts and activity saved in this browser.</p>
          <div className="saved-session-list">
            {projects.map(project => <button className={'saved-session ' + (project.id === activeId ? 'selected' : '')} key={project.id}
              onClick={() => { void onSelect(project.id).then(onClose); }}>
              <div className="saved-session-thumb">{project.source?.thumbnail ? <img src={project.source.thumbnail} alt="" /> : <Icon name="film" size={22} />}</div>
              <span><strong>{project.name}</strong><small>{project.events.length} events · {new Date(project.updatedAt).toLocaleDateString([], { month: 'short', day: 'numeric' })}</small></span>
              {project.id === activeId ? <Icon name="check" size={16} /> : <Icon name="arrow" size={16} />}
            </button>)}
            {error && <p className="inline-error">Saved sessions could not be read from this browser.</p>}
            {!error && projects.length === 0 && <p className="dialog-intro">Your current session will appear here after its first save.</p>}
          </div>
          <button className="button primary" onClick={() => { void onNew().then(onClose); }}><Icon name="plus" size={16} />New session</button>
        </> : <dl className="shortcut-list">
          <div><dt>Play / pause</dt><dd><kbd>Space</kbd></dd></div>
          <div><dt>Seek one second</dt><dd><kbd>←</kbd><kbd>→</kbd></dd></div>
          <div><dt>Mute / unmute</dt><dd><kbd>M</kbd></dd></div>
          <div><dt>Fullscreen viewer</dt><dd><kbd>F</kbd></dd></div>
          <div><dt>Close dialog</dt><dd><kbd>Esc</kbd></dd></div>
        </dl>}
      </div>
    </dialog>
  );
}
