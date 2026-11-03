import { CONTROLS, activeControls, cellFor, layersOf } from '../lib/recipeView';
import type { Cell, Control, Layer } from '../lib/recipeView';

const pct = (n: number) => `${(n * 100).toFixed(2)}%`;

/** A track from the control's minimum to its maximum, a tick where nothing changes, and a fill from the tick to the value. */
function Meter({ cell, label }: { cell: Cell; label: string }) {
  const from = Math.min(cell.at, cell.neutralAt), to = Math.max(cell.at, cell.neutralAt);
  return <span className="meter-track" role="img" aria-label={`${label}: ${cell.text}, ${cell.description}`}>
    <span className="meter-fill" style={{ left: pct(from), width: pct(to - from) }} />
    <span className="meter-tick" style={{ left: pct(cell.neutralAt) }} />
    <span className="meter-dot" style={{ left: pct(cell.at) }} />
  </span>;
}

type Recipe = Parameters<typeof layersOf>[0];

/** What the grade does, control by control and area by area, on the ranges the grader is allowed to use. */
export default function RecipeMatrix({ recipe }: { recipe: Recipe }) {
  const layers: Layer[] = layersOf(recipe);
  if (!layers.length) return null;
  const all: Control[] = layers.length === 1 ? CONTROLS.filter(c => cellFor(c, layers[0].values[c.key])) : activeControls(layers);
  const controls = layers.length === 1 ? all.filter(c => cellFor(c, layers[0].values[c.key])!.changed) : all;
  const still = layers.length === 1 ? all.filter(c => !controls.includes(c)) : [];
  if (!controls.length) return <p className="help">This grade leaves every control at its neutral value.</p>;
  if (layers.length === 1) {
    return <div><dl className="meter-list">{controls.map(c => { const cell = cellFor(c, layers[0].values[c.key])!;
      return <div key={c.key} title={c.hint}><dt>{c.label}</dt><dd><Meter cell={cell} label={c.label} /><span className="meter-num">{cell.text}</span><small>{cell.description}</small></dd></div>; })}</dl>
      {still.length > 0 && <p className="note">Left unchanged: {still.map(c => c.label.toLowerCase()).join(', ')}.</p>}</div>;
  }
  return <div className="matrix-wrap" role="region" aria-label="Grade controls by area" tabIndex={0}>
    <table className="matrix">
      <thead><tr><th scope="col"><span className="visually-hidden">Control</span></th>
        {layers.map(l => <th scope="col" key={l.id} className={'role-' + l.role}>{l.name}{l.role === 'subject' && <small>subject</small>}</th>)}</tr></thead>
      <tbody>{controls.map(c => <tr key={c.key}>
        <th scope="row" title={c.hint}>{c.label}</th>
        {layers.map(l => { const cell = cellFor(c, l.values[c.key]);
          return <td key={l.id} className={cell?.changed ? 'moved' : 'still'}>{cell ? <><span className="cell-num">{cell.text}</span><Meter cell={cell} label={`${c.label}, ${l.name}`} /></> : '—'}</td>; })}
      </tr>)}</tbody>
    </table>
    <p className="note">Each bar spans what the grader may do with that control; the tick marks “unchanged”.</p>
  </div>;
}
