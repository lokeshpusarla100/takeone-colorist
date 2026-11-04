import { useEffect, useRef, useState } from 'react';
import type { BackendState } from '../../lib/api';
import { asAppError } from '../../lib/errors';
import Callout from '../../components/Callout';
import Card from '../../components/Card';
import Chip from '../../components/Chip';
import Icon from '../../components/Icon';
import * as colorApi from './api';
import { cardView, levelsBar, levelsSentence, percent, readiness } from './inputProfile';
import type { Choice, LutInfo, ProfileInfo, StillPair } from './inputProfile';

type Failure = { message: string; action: string };
const failure = (cause: unknown): Failure => { const e = asAppError(cause, 'color.input-profile'); return { message: e.message, action: e.action }; };

/**
 * How to read footage that was shot in a camera's log or flat profile. Quiet for ordinary video, prominent for video that
 * looks flat, read-only once the footage has been analysed. Nothing is applied until the person has seen the result.
 */
export default function InputProfileCard({ state, online, sending, onApply }: {
  state: BackendState | null; online: boolean; sending: boolean; onApply: (choice: Choice) => Promise<boolean>;
}) {
  const id = state?.id, status = state?.status ?? '', applied = parseSignature(state);
  const [info, setInfo] = useState<ProfileInfo | null>(null);
  const [choice, setChoice] = useState<Choice>({ kind: 'none' });
  const [lut, setLut] = useState<LutInfo | null>(null);
  const [stills, setStills] = useState<StillPair | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [problem, setProblem] = useState<Failure | null>(null);
  const [open, setOpen] = useState<boolean | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const look = useRef<AbortController | null>(null);

  // What the backend offers for this project; asked again whenever the project's stage or its profile changes.
  useEffect(() => {
    setInfo(null); setChoice({ kind: 'none' }); setLut(null); setStills(null); setProblem(null); setOpen(null);
  }, [id]);
  useEffect(() => {
    if (!id || !online || status === 'ingesting') return;   // also after analysis, when the answer is that the choice is locked
    const aborter = new AbortController();
    colorApi.getInfo(id, aborter.signal).then(value => { if (!aborter.signal.aborted) setInfo(value); }).catch(() => { /* the card simply stays hidden */ });
    return () => aborter.abort();
  }, [id, online, status, applied]);
  // The selection follows what the backend says is applied (not the project's copy, which can arrive first).
  const appliedNow = info?.profile?.signature ?? '';
  useEffect(() => { setChoice({ kind: info?.profile?.kind ?? 'none' }); setStills(null); }, [appliedNow]);

  if (!state || !id) return null;
  const view = cardView(info, status);
  if (view.mode === 'hidden' || !info) return null;
  if (view.mode === 'locked') {
    return <Callout tone="info" title={'Footage color: ' + view.profile.label}>
      <p>This choice was used for the preview, Muse’s view of the video and the grade, so it can’t change now. Import the video again to try another.</p>
      {view.profile.steps.length > 0 && <details><summary>What happens to your video</summary><ul className="plain-list">{view.profile.steps.map((s, i) => <li key={i}>{s}</li>)}</ul></details>}
    </Callout>;
  }

  const expanded = open ?? view.open;
  const error = problem ?? (info.error ? { message: info.error.message, action: info.error.action } : null);
  const ready = readiness(choice, info, stills);
  const busy = sending || working !== null;

  const choose = async (next: Choice) => {
    look.current?.abort();
    setChoice(next); setStills(null); setProblem(null);
    if (next.kind === 'none' || (next.kind === 'lut' && !next.lut_id)) return;
    const aborter = new AbortController();
    look.current = aborter;
    setWorking('Rendering a still to compare…');
    try {
      const pair = await colorApi.stills(id, next, aborter.signal);
      if (!aborter.signal.aborted) setStills(pair);
    } catch (cause) {
      if (!aborter.signal.aborted) setProblem(failure(cause));
    } finally {
      if (!aborter.signal.aborted) setWorking(null);
    }
  };

  const pickFile = async (file: File | undefined) => {
    if (!file) return;
    setWorking('Checking the LUT…'); setProblem(null);
    try {
      const uploaded = await colorApi.uploadLut(id, file);
      setLut(uploaded);
      setWorking(null);
      await choose({ kind: 'lut', lut_id: uploaded.lut_id, lut_name: uploaded.name });
    } catch (cause) {
      setLut(null); setStills(null); setProblem(failure(cause)); setWorking(null);
    } finally {
      if (picker.current) picker.current.value = '';
    }
  };

  const apply = async () => { setProblem(null); if (await onApply(choice)) setStills(null); };
  const bar = info.levels ? levelsBar(info.levels) : null;

  return <Card title={view.title} className="profile-card" icon={<Icon name="sliders" size={20} />}
    status={view.tone === 'ok' ? { tone: 'ok', label: 'Applied', icon: 'check' } : view.tone === 'warn' ? { tone: 'warn', label: 'Needs a choice', icon: 'alert' } : undefined}
    actions={<button className="icon-button" aria-expanded={expanded} aria-label={expanded ? 'Hide footage color options' : 'Show footage color options'} onClick={() => setOpen(!expanded)}>
      <Icon name="chevron" size={18} /></button>}>
    <p className="help">{view.lead}</p>
    {expanded && <>
      {bar && info.levels && <figure className="range" aria-label={levelsSentence(info.levels)}>
        <div className="range-track" aria-hidden="true"><i style={{ left: bar.left + '%', width: bar.width + '%' }} /></div>
        <figcaption><span>Dark</span><span>{view.tone === 'ok' ? 'As recorded: ' : ''}{levelsSentence(info.levels)}</span><span>Bright</span></figcaption>
      </figure>}
      {error && <Callout tone="bad" title="That didn’t work"><p>{error.message}</p>{error.action && <p className="help"><strong>What to do: </strong>{error.action}</p>}</Callout>}
      <div className="profile-options" role="radiogroup" aria-label="How to read this video’s colors">
        {view.options.map(o => {
          const selected = choice.kind === o.kind;
          return <label key={o.kind} className={'profile-option' + (selected ? ' selected' : '') + (o.disabled ? ' disabled' : '')}>
            <input type="radio" name="input-profile" checked={selected} disabled={!!o.disabled || busy}
              onChange={() => void choose(o.kind === 'lut' ? { kind: 'lut', ...(lut ? { lut_id: lut.lut_id, lut_name: lut.name } : {}) } : { kind: o.kind })} />
            <span className="profile-option-text"><strong>{o.label}</strong>{o.badge && <Chip tone="accent">{o.badge}</Chip>}
              <span className="help">{o.disabled ?? o.hint}</span></span>
          </label>;
        })}
      </div>
      {choice.kind === 'lut' && <div className="profile-lut">
        <input ref={picker} type="file" accept=".cube,text/plain" className="visually-hidden" aria-label="Camera LUT file" onChange={e => void pickFile(e.target.files?.[0])} />
        <button disabled={busy} onClick={() => picker.current?.click()}><Icon name="upload" size={16} />{lut ? 'Choose a different file' : 'Choose the .cube file'}</button>
        {lut && <span className="help"><strong>{lut.name}</strong> · {lut.kind.toUpperCase()} {lut.kind === '3d' ? `${lut.size}×${lut.size}×${lut.size}` : `${lut.size} points`}{lut.title ? ` · “${lut.title}”` : ''}</span>}
      </div>}
      {working && <p className="help" role="status"><span className="spinner" /> {working}</p>}
      {stills && <div className="profile-stills">
        <figure><img src={stills.before} alt="The middle of your video as it is read now" /><figcaption>Now</figcaption></figure>
        <figure><img src={stills.after} alt="The middle of your video with this choice" /><figcaption>With this choice{stills.afterLevels ? ` · dark ${percent(stills.afterLevels.black)}, bright ${percent(stills.afterLevels.white)}` : ''}</figcaption></figure>
      </div>}
      {stills?.warnings.map(w => <Callout key={w} tone="warn"><p>{w}</p></Callout>)}
      <div className="actions">
        <button className="primary" disabled={!ready.ok || busy} onClick={() => void apply()}><Icon name="check" size={16} />
          {choice.kind === 'none' ? 'Go back to the video’s own colors' : 'Use this'}</button>
        {!ready.ok && ready.reason && <span className="help">{ready.reason}</span>}
      </div>
    </>}
  </Card>;
}

function parseSignature(state: BackendState | null): string {
  const p = state?.source?.profile as { signature?: unknown } | null | undefined;
  return typeof p?.signature === 'string' ? p.signature : '';
}
