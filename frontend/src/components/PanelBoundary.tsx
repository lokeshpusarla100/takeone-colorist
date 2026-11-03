import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

type Props = { name: string; children: ReactNode; resetKey?: string | number };
type State = { error: Error | null; resetKey?: string | number };

/**
 * Contains a rendering failure to one section of the editor. A saved record the section cannot draw must not take the
 * video, the progress and the other sections down with it; the section says so and tries again when new data arrives.
 */
export default class PanelBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };
  static getDerivedStateFromError(error: Error): Partial<State> { return { error }; }
  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.resetKey ? { error: null, resetKey: props.resetKey } : null;
  }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error(`Take One: ${this.props.name} could not be shown`, error, info.componentStack); }
  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return <section className="panel-fallback" role="alert">
      <h2>{this.props.name} can’t be shown right now</h2>
      <p>Everything else in the editor keeps working, and your project and its files are untouched.</p>
      <div className="actions"><button onClick={() => this.setState({ error: null })}>Try again</button></div>
      <details><summary>What went wrong</summary><pre>{error.name}: {error.message}</pre></details>
    </section>;
  }
}
