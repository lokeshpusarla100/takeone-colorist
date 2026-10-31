import { useEffect, useRef, useState } from 'react';
import type { Recipe } from '../lib/types';
import { NEUTRAL_RECIPE } from '../lib/types';
import Icon from './Icon';
import type { IconName } from './Icon';

type Control = {
  key: keyof Recipe; label: string; icon?: IconName; min: number; max: number; step: number;
  format: (value: number) => string;
};
const controls: Control[] = [
  { key: 'exposure', label: 'Exposure', icon: 'sun', min: -2, max: 2, step: 0.05, format: value => (value > 0 ? '+' : '') + value.toFixed(2) + ' EV' },
  { key: 'contrast', label: 'Contrast', icon: 'contrast', min: 0.5, max: 1.5, step: 0.01, format: value => value.toFixed(2) + '×' },
  { key: 'saturation', label: 'Saturation', icon: 'droplet', min: 0, max: 2, step: 0.01, format: value => value.toFixed(2) + '×' },
];
const gains: Control[] = [
  { key: 'red', label: 'Red gain', min: 0.5, max: 1.5, step: 0.01, format: value => value.toFixed(2) + '×' },
  { key: 'green', label: 'Green gain', min: 0.5, max: 1.5, step: 0.01, format: value => value.toFixed(2) + '×' },
  { key: 'blue', label: 'Blue gain', min: 0.5, max: 1.5, step: 0.01, format: value => value.toFixed(2) + '×' },
];

function ControlRow({ control, value, disabled, onChange, onCommit }: {
  control: Control; value: number; disabled: boolean;
  onChange: (key: keyof Recipe, value: number) => void;
  onCommit: (label: string, from: number, to: number) => void;
}) {
  const committed = useRef(value);
  const editing = useRef(false);
  useEffect(() => { if (!editing.current) committed.current = value; }, [value]);
  const commit = () => {
    onCommit(control.label, committed.current, value);
    committed.current = value;
    editing.current = false;
  };
  const change = (next: number) => {
    editing.current = true;
    onChange(control.key, next);
  };
  return (
    <div className={'control-row ' + (disabled ? 'disabled' : '')}>
      <div className="control-label"><label htmlFor={'control-' + control.key}>
        {control.icon ? <Icon name={control.icon} size={14} /> : <span className={'channel-dot ' + control.key} />}
        {control.label}
      </label>
        <button className="control-value" disabled={disabled} title={'Reset ' + control.label}
          onClick={() => {
            onCommit(control.label, value, NEUTRAL_RECIPE[control.key]);
            committed.current = NEUTRAL_RECIPE[control.key];
            onChange(control.key, NEUTRAL_RECIPE[control.key]);
          }}>{control.format(value)}</button>
      </div>
      <input id={'control-' + control.key} type="range" min={control.min} max={control.max} step={control.step}
        value={value} disabled={disabled} aria-valuetext={control.format(value)}
        onPointerDown={() => { editing.current = true; committed.current = value; }}
        onChange={event => change(Number(event.target.value))}
        onPointerUp={commit} onBlur={commit}
        onKeyUp={event => { if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) commit(); }} />
    </div>
  );
}

export default function ControlsPanel({ recipe, direction, hasSource, onChange, onCommit, onReset, onSaveDraft, onSaveDirection, onStatus }: {
  recipe: Recipe; direction: string; hasSource: boolean;
  onChange: (key: keyof Recipe, value: number) => void;
  onCommit: (label: string, from: number, to: number) => void;
  onReset: () => void; onSaveDraft: () => boolean;
  onSaveDirection: (value: string) => void; onStatus: (message: string) => void;
}) {
  const [tab, setTab] = useState<'controls' | 'direction'>('controls');
  const [draftDirection, setDraftDirection] = useState(direction);
  useEffect(() => setDraftDirection(direction), [direction]);
  const isNeutral = Object.keys(recipe).every(key => recipe[key as keyof Recipe] === NEUTRAL_RECIPE[key as keyof Recipe]);
  return (
    <section className="edit-panel" aria-label="Editing settings">
      <div className="edit-tabs" role="tablist" aria-label="Edit panel">
        <button id="controls-tab" role="tab" aria-selected={tab === 'controls'} aria-controls="controls-panel" onClick={() => setTab('controls')}>
          <Icon name="sliders" size={15} />Controls</button>
        <button id="direction-tab" role="tab" aria-selected={tab === 'direction'} aria-controls="direction-panel" onClick={() => setTab('direction')}>
          <Icon name="spark" size={15} />Direction{direction && <span className="tab-dot" />}</button>
      </div>
      {tab === 'controls' ? <div id="controls-panel" role="tabpanel" aria-labelledby="controls-tab" className="controls-content">
        <div className="section-heading"><h2>Primary correction</h2><button className="text-button" disabled={!hasSource || isNeutral} onClick={onReset}><Icon name="reset" size={12} />Reset</button></div>
        <div className="draft-notice"><span className="status-dot amber" /><span>Recipe draft</span><span>Preview unchanged</span></div>
        {controls.map(control => <ControlRow key={control.key} control={control} value={recipe[control.key]}
          disabled={!hasSource} onChange={onChange} onCommit={onCommit} />)}
        <details className="balance-controls"><summary>RGB balance <Icon name="chevron" size={14} /></summary>
          {gains.map(control => <ControlRow key={control.key} control={control} value={recipe[control.key]}
            disabled={!hasSource} onChange={onChange} onCommit={onCommit} />)}
        </details>
        <button className="button secondary save-draft-button" onClick={() => { if (onSaveDraft()) onStatus('Draft settings saved locally. A render is needed to preview the grade.'); }} disabled={!hasSource}>
          <Icon name="save" size={15} />Save recipe draft<Icon name="plus" size={14} /></button>
        <p className="panel-footnote"><Icon name="info" size={12} />Preview rendering will be available when the editing backend is connected.</p>
      </div> : <div id="direction-panel" role="tabpanel" aria-labelledby="direction-tab" className="direction-content">
        <div className="section-heading"><h2>Set the feeling</h2><span className="small-label">CREATIVE BRIEF</span></div>
        <p className="direction-intro">Tell Muse what you want this footage to feel like, and what matters to you.</p>
        <label htmlFor="creative-direction" className="visually-hidden">Creative direction</label>
        <textarea id="creative-direction" rows={5} maxLength={4000} value={draftDirection}
          onChange={event => setDraftDirection(event.target.value)}
          placeholder="A quiet, cinematic evening. Keep the skin tones natural, soften the highlights, and preserve the warmth of the room." />
        <div className="direction-hint"><span>Include anything you want to preserve.</span><span>{draftDirection.length}/4000</span></div>
        <button className="button secondary" disabled={draftDirection.trim() === direction}
          onClick={() => { onSaveDirection(draftDirection); onStatus('Creative direction saved to this session.'); }}>
          <Icon name="check" size={15} />Save direction</button>
        <div className="direction-callout"><Icon name="spark" size={17} /><p>Your intent, kept in view.<span>Once connected, Muse will use this direction to propose edits and explain its decisions.</span></p></div>
      </div>}
    </section>
  );
}
