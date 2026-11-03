import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { asAppError } from '../lib/errors';
import type { AppError } from '../lib/errors';
import { clearActiveId } from '../lib/storage';
import ErrorNotice from './ErrorNotice';
export default class ErrorBoundary extends Component<{children:ReactNode},{error:AppError|null}> {
  state:{error:AppError|null}={error:null};
  static getDerivedStateFromError(cause:unknown) { return {error:asAppError(cause,'ui.render')}; }
  componentDidCatch(cause:Error,info:ErrorInfo) { console.error('Take One render failed',cause,info.componentStack); }
  render() {
    if(this.state.error){
      const reason=String(this.state.error.details.nativeMessage??'');
      return <main className="startup"><h1>The editor hit a problem</h1>
        <p className="startup-lead">Your sessions and videos are safe. Nothing was deleted or reset.</p>
        {reason&&<p className="inline-help">What happened: {reason}</p>}
        <ErrorNotice error={this.state.error}/>
        <div className="actions"><button className="primary" onClick={()=>location.reload()}>Reload editor</button>
          <button onClick={()=>{clearActiveId();location.reload();}}>Start a new session</button></div>
        <p className="inline-help">If reloading shows this again, the saved session itself is what the editor can’t draw. Start a new session, then reopen the old one from Sessions after the editor has been updated.</p>
      </main>;
    }
    return this.props.children;
  }
}
