import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { asAppError } from '../lib/errors';
import type { AppError } from '../lib/errors';
import ErrorNotice from './ErrorNotice';
export default class ErrorBoundary extends Component<{children:ReactNode},{error:AppError|null}> {
  state:{error:AppError|null}={error:null};
  static getDerivedStateFromError(cause:unknown) { return {error:asAppError(cause,'ui.render')}; }
  componentDidCatch(cause:Error,info:ErrorInfo) { console.error('Take One render failed',cause,info.componentStack); }
  render() {
    if(this.state.error)return <main className="startup"><h1>Editor unavailable</h1><ErrorNotice error={this.state.error}/><button onClick={()=>location.reload()}>Reload editor</button><p>Saved session records have not been reset.</p></main>;
    return this.props.children;
  }
}
