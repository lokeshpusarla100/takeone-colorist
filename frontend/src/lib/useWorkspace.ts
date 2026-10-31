import { useCallback, useEffect, useRef, useState } from 'react';
import type { Project, Recipe, SessionEvent, Version } from './types';
import { makeEvent, makeProject, NEUTRAL_RECIPE } from './types';
import { getActiveId, getMedia, getProject, saveMedia, saveProject, setActiveId } from './storage';
import { inspectVideo } from './media';

export function useWorkspace() {
  const [project, setProject] = useState<Project | null>(null);
  const [saveState, setSaveState] = useState<'saving' | 'saved' | 'error'>('saving');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = useRef<Project | null>(null);
  current.current = project;

  useEffect(() => {
    let cancelled = false;
    const restore = async () => {
      try {
        const activeId = getActiveId();
        const restored = activeId ? await getProject(activeId) : undefined;
        if (!cancelled) {
          const next = restored ?? makeProject();
          setActiveId(next.id);
          setProject(next);
        }
      } catch {
        if (!cancelled) {
          setProject(makeProject());
          setError('Local storage is unavailable. This session cannot be saved in this browser.');
          setSaveState('error');
        }
      }
    };
    void restore();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    setSaveState('saving');
    const timer = setTimeout(() => {
      void saveProject(project).then(() => {
        if (!cancelled) setSaveState('saved');
      }).catch(() => {
        if (!cancelled) {
          setSaveState('error');
          setError('Could not save locally. Browser storage may be full. Export your session log to keep a copy.');
        }
      });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [project]);

  // Also flush the most recent edit when the browser backgrounds this page.
  useEffect(() => {
    const flush = () => {
      if (current.current) void saveProject(current.current).catch(() => {});
    };
    document.addEventListener('visibilitychange', flush);
    return () => document.removeEventListener('visibilitychange', flush);
  }, []);

  const update = useCallback((fn: (previous: Project) => Project) => {
    setProject(previous => previous
      ? { ...fn(previous), updatedAt: new Date().toISOString() }
      : previous);
  }, []);

  const appendEvent = useCallback((event: Omit<SessionEvent, 'id' | 'timestamp'>) => {
    const record = makeEvent(event);
    update(previous => ({ ...previous, events: [...previous.events, record] }));
  }, [update]);

  const newSession = async () => {
    try {
      if (current.current) await saveProject(current.current);
      const next = makeProject();
      await saveProject(next);
      setActiveId(next.id);
      setProject(next);
      setError(null);
    } catch {
      setError('Could not create a saved session. Export the current log and check browser storage.');
    }
  };

  const openSession = async (id: string) => {
    try {
      if (current.current) await saveProject(current.current);
      const next = await getProject(id);
      if (!next) throw new Error('Session not found.');
      setActiveId(next.id);
      setProject(next);
      setError(null);
    } catch {
      setError('This saved session could not be opened.');
    }
  };

  const importVideo = async (file: File, kind: 'source' | 'rendered') => {
    if (busy || !current.current) return;
    setBusy(true);
    setError(null);
    const previous = current.current;
    try {
      const asset = await inspectVideo(file);
      if (current.current?.id !== previous.id) return;
      if (kind === 'rendered') {
        if (!previous.source) throw new Error('Import your source video first.');
        if (asset.width !== previous.source.width || asset.height !== previous.source.height
          || Math.abs(asset.duration - previous.source.duration) > 0.15) {
          throw new Error('For comparison, import a render with the same dimensions and duration as the source.');
        }
      }
      await saveMedia(asset.id, file);
      // Keep any text/control changes made while browser metadata was loading.
      const latest = current.current;
      if (!latest || latest.id !== previous.id) return;
      await saveProject(latest);
      const next = kind === 'source' && latest.source ? makeProject() : latest;
      const version: Version = {
        id: crypto.randomUUID(),
        name: kind === 'source' ? 'Original' : 'Render ' + (next.versions.filter(v => v.kind === 'rendered').length + 1),
        kind, createdAt: new Date().toISOString(), assetId: asset.id,
        parentId: kind === 'rendered' ? next.versions.find(v => v.kind === 'source')?.id : undefined,
        // An externally imported render has no verified recipe.
        recipe: kind === 'source' ? { ...NEUTRAL_RECIPE } : null,
      };
      const result: Project = {
        ...next, name: next.name === 'Untitled session' ? file.name.replace(/\.[^.]+$/, '') : next.name,
        updatedAt: new Date().toISOString(),
        source: kind === 'source' ? asset : next.source,
        assets: [...next.assets, asset],
        versions: [...next.versions, version],
        selectedVersionId: version.id,
        events: [...next.events, makeEvent({
          actor: 'user', kind: 'activity',
          title: kind === 'source' ? 'Source video imported' : 'Rendered version imported',
          reason: kind === 'source'
            ? 'Ready for local playback. No footage has been sent to Muse.'
            : 'Imported an existing output for comparison. Its editing recipe is unknown.',
          versionId: version.id,
          details: { file: asset.name, width: asset.width, height: asset.height, duration: Number(asset.duration.toFixed(3)) },
        })],
      };
      await saveProject(result);
      setActiveId(result.id);
      setProject(result);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not import the video.';
      setError(message);
      if (current.current?.id === previous.id) {
        appendEvent({ actor: 'app', kind: 'error', title: 'Video import failed', reason: message });
      }
    } finally {
      setBusy(false);
    }
  };

  const saveDirection = (direction: string) => {
    if (direction.trim() === current.current?.direction) return;
    const record = makeEvent({
      actor: 'user', kind: 'activity', title: 'Creative direction saved',
      reason: direction.trim() || 'Creative direction cleared.',
    });
    update(previous => ({
      ...previous, direction: direction.trim(), events: [...previous.events, record],
    }));
  };

  const updateRecipe = (key: keyof Recipe, value: number) => {
    update(previous => ({ ...previous, recipe: { ...previous.recipe, [key]: value } }));
  };

  const logControl = (label: string, from: number, to: number) => {
    if (from === to) return;
    appendEvent({
      actor: 'user', kind: 'activity', title: label + ' draft changed',
      reason: 'Saved as a recipe setting. The displayed video has not been re-rendered.',
      details: { from, to },
    });
  };

  const resetRecipe = () => {
    const record = makeEvent({
      actor: 'user', kind: 'activity', title: 'Recipe controls reset',
      reason: 'All draft controls returned to neutral.',
    });
    update(previous => ({ ...previous, recipe: { ...NEUTRAL_RECIPE }, events: [...previous.events, record] }));
  };

  const saveDraft = () => {
    if (!current.current?.source) return false;
    const previous = current.current;
    const lastDraft = previous.versions.filter(v => v.kind === 'draft').at(-1);
    if (lastDraft && JSON.stringify(lastDraft.recipe) === JSON.stringify(previous.recipe)) {
      setError('These settings are already saved in ' + lastDraft.name + '. Change a control to create another draft.');
      return false;
    }
    const version: Version = {
      id: crypto.randomUUID(), name: 'Draft ' + (previous.versions.filter(v => v.kind === 'draft').length + 1),
      kind: 'draft', createdAt: new Date().toISOString(),
      parentId: previous.selectedVersionId ?? undefined, recipe: { ...previous.recipe },
    };
    const record = makeEvent({
      actor: 'user', kind: 'activity', title: version.name + ' saved',
      reason: 'Stored the full recipe. A rendered preview is still needed.',
      versionId: version.id, details: { ...version.recipe },
    });
    update(p => ({
      ...p, versions: [...p.versions, version], selectedVersionId: version.id,
      events: [...p.events, record],
    }));
    setError(null);
    return true;
  };

  const selectVersion = (version: Version) => {
    update(previous => ({
      ...previous, selectedVersionId: version.id,
      recipe: version.recipe ? { ...version.recipe } : previous.recipe,
    }));
  };

  return {
    project, saveState, busy, error, setError, update, appendEvent, newSession, openSession,
    importVideo, saveDirection, updateRecipe, logControl, resetRecipe, saveDraft, selectVersion,
  };
}

export function useAssetUrl(assetId?: string) {
  const [result, setResult] = useState<{ id: string; url: string } | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let url: string | undefined;
    setMissing(false);
    if (!assetId) { setResult(null); return; }
    void getMedia(assetId).then(blob => {
      if (cancelled) return;
      if (!blob) { setMissing(true); return; }
      url = URL.createObjectURL(blob);
      setResult({ id: assetId, url });
    }).catch(() => { if (!cancelled) setMissing(true); });
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [assetId]);
  return { url: result && result.id === assetId ? result.url : undefined, missing };
}
