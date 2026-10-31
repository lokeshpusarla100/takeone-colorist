import { useEffect, useRef, useState } from 'react';
import type { DragEvent } from 'react';
import type { MediaAsset, Version } from '../lib/types';
import { formatTime } from '../lib/types';
import { useAssetUrl } from '../lib/useWorkspace';
import Icon from './Icon';

export default function Viewer({ source, selectedVersion, selectedAsset, busy, onChooseVideo, onDropVideo, onError }: {
  source: MediaAsset | null; selectedVersion?: Version; selectedAsset?: MediaAsset;
  busy: boolean; onChooseVideo: () => void; onDropVideo: (file: File) => void; onError: (message: string) => void;
}) {
  const [showOriginal, setShowOriginal] = useState(false);
  const [compare, setCompare] = useState(false);
  const [wipe, setWipe] = useState(50);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [loop, setLoop] = useState(false);
  const [rate, setRate] = useState(1);
  const [time, setTime] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const mainVideo = useRef<HTMLVideoElement>(null);
  const beforeVideo = useRef<HTMLVideoElement>(null);
  const fullscreen = useRef<HTMLElement>(null);
  const position = useRef(0);
  const hasRender = selectedVersion?.kind === 'rendered' && !!selectedAsset;
  const displayAsset = !showOriginal && hasRender ? selectedAsset : source;
  const { url, missing } = useAssetUrl(displayAsset?.id);
  const { url: sourceUrl } = useAssetUrl(source?.id);
  const duration = displayAsset?.duration ?? 0;
  const isDraft = selectedVersion?.kind === 'draft' && !showOriginal;

  useEffect(() => { setCompare(false); setShowOriginal(false); }, [selectedVersion?.id]);
  useEffect(() => {
    position.current = 0;
    setTime(0);
    setPlaying(false);
  }, [source?.id]);
  useEffect(() => { setLoading(!!url); setPlaying(false); }, [url]);
  useEffect(() => {
    if (mainVideo.current) mainVideo.current.playbackRate = rate;
    if (beforeVideo.current) beforeVideo.current.playbackRate = rate;
  }, [rate, url, compare]);

  const seek = (next: number) => {
    const video = mainVideo.current;
    if (!video || !url) return;
    const clamped = Math.max(0, Math.min(duration, next));
    video.currentTime = clamped;
    if (beforeVideo.current) beforeVideo.current.currentTime = clamped;
    position.current = clamped;
    setTime(clamped);
  };
  const togglePlay = () => {
    const video = mainVideo.current;
    if (!video || !url) return;
    if (video.paused) {
      void video.play().catch(() => onError('Playback could not start. Try importing the video again.'));
    } else video.pause();
  };
  const toggleFullscreen = () => {
    const action = document.fullscreenElement ? document.exitFullscreen() : fullscreen.current?.requestFullscreen();
    void action?.catch(() => onError('Fullscreen is unavailable in this browser.'));
  };
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (document.querySelector('dialog[open]') || event.ctrlKey || event.metaKey || event.altKey
        || target.closest('input, textarea, select, button, [contenteditable]')) return;
      if (event.code === 'Space' && url) { event.preventDefault(); togglePlay(); }
      if (event.key === 'ArrowLeft' && url) { event.preventDefault(); seek(position.current - 1); }
      if (event.key === 'ArrowRight' && url) { event.preventDefault(); seek(position.current + 1); }
      if (event.key.toLowerCase() === 'm') setMuted(value => !value);
      if (event.key.toLowerCase() === 'f' && url) toggleFullscreen();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  });

  const syncBefore = () => {
    if (!beforeVideo.current || !mainVideo.current) return;
    const before = beforeVideo.current;
    const main = mainVideo.current;
    if (Math.abs(before.currentTime - main.currentTime) > 0.08) before.currentTime = main.currentTime;
    before.playbackRate = main.playbackRate;
  };
  useEffect(() => {
    if (compare && beforeVideo.current && mainVideo.current) {
      syncBefore();
      if (!mainVideo.current.paused) void beforeVideo.current.play().catch(() => {});
    }
  }, [compare, sourceUrl]);

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    if (!busy && event.dataTransfer.files[0]) onDropVideo(event.dataTransfer.files[0]);
  };

  return (
    <section id="video-workspace" ref={fullscreen} className="viewer-shell" aria-label="Video viewer">
      <div className="viewer-toolbar">
        <div className="viewer-title"><Icon name="eye" size={15} /><span>Viewer</span><span className="viewer-divider" />
          <span className="viewer-version">{isDraft ? selectedVersion?.name + ' · source preview' : displayAsset ? showOriginal || !hasRender ? 'Original' : selectedVersion?.name : 'No video loaded'}</span>
        </div>
        <div className="viewer-actions">
          <div className="view-toggle" aria-label="Preview version">
            <button className={showOriginal || !hasRender ? 'active' : ''} disabled={!source}
              aria-pressed={showOriginal || !hasRender}
              onClick={() => { setShowOriginal(true); setCompare(false); }}>Original</button>
            <button className={!showOriginal && hasRender ? 'active' : ''} disabled={!hasRender}
              aria-pressed={!showOriginal && hasRender} onClick={() => setShowOriginal(false)}>Render</button>
          </div>
          <button className={'icon-button compare-button ' + (compare ? 'active' : '')} aria-label="Toggle before and after comparison"
            title={hasRender ? 'Compare original and rendered version' : 'Import a rendered version to compare'}
            disabled={!hasRender} aria-pressed={compare}
            onClick={() => { setShowOriginal(false); setCompare(!compare); }}><Icon name="compare" size={17} /></button>
        </div>
      </div>
      <div className={'video-stage ' + (!source ? 'empty' : '') + (dragging ? ' drag-over' : '')}
        onDragOver={event => { event.preventDefault(); setDragging(true); }}
        onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
        onDrop={onDrop}>
        <div className="frame-guide top-left" /><div className="frame-guide top-right" />
        <div className="frame-guide bottom-left" /><div className="frame-guide bottom-right" />
        {!source ? <div className="upload-state">
          <div className="upload-art" aria-hidden="true"><div className="art-frame back" /><div className="art-frame front"><Icon name="film" size={33} /></div><span className="art-plus"><Icon name="plus" size={13} /></span></div>
          <span className="eyebrow">EVERY GOOD GRADE STARTS WITH A TAKE</span>
          <h2>Your footage.<br /><em>A new perspective.</em></h2>
          <p>Drop a video into your workspace<br />and make room for your creative direction.</p>
          <button className="button primary" onClick={onChooseVideo} disabled={busy}>
            <Icon name="plus" size={17} />{busy ? 'Opening video…' : 'Import your footage'}</button>
          <span className="upload-formats">MP4, WebM, MOV <span>·</span> Local browser playback</span>
        </div> : missing ? <div className="viewer-message"><Icon name="warning" size={27} /><h2>Video unavailable</h2>
          <p>The browser no longer has this file. Import the footage again to continue.</p><button className="button secondary" onClick={onChooseVideo}>Import video</button></div>
          : <>
            {url && <video ref={mainVideo} className="preview-video" src={url} playsInline preload="auto" muted={muted} loop={loop}
              aria-label={hasRender && !showOriginal ? 'Selected rendered video' : 'Original video'}
              onClick={togglePlay}
              onLoadedMetadata={() => {
                const video = mainVideo.current;
                if (video) {
                  video.currentTime = Math.min(position.current, video.duration);
                  video.playbackRate = rate;
                }
              }}
              onCanPlay={() => setLoading(false)}
              onWaiting={() => setLoading(true)}
              onTimeUpdate={() => {
                if (!mainVideo.current) return;
                setTime(mainVideo.current.currentTime);
                position.current = mainVideo.current.currentTime;
                if (compare) syncBefore();
              }}
              onSeeked={() => { if (compare) syncBefore(); }}
              onPlay={() => {
                setPlaying(true);
                if (compare) { syncBefore(); void beforeVideo.current?.play().catch(() => {}); }
              }}
              onPause={() => { setPlaying(false); beforeVideo.current?.pause(); }}
              onEnded={() => { setPlaying(false); beforeVideo.current?.pause(); }}
              onError={() => { setLoading(false); onError('This video could not be played. Try an H.264 MP4 or WebM.'); }}
            />}
            {compare && sourceUrl && <video ref={beforeVideo} className="preview-video before-video" src={sourceUrl}
              style={{ clipPath: 'inset(0 ' + (100 - wipe) + '% 0 0)' }} muted playsInline preload="auto" loop={loop}
              aria-label="Original comparison video" onClick={togglePlay}
              onLoadedData={() => { syncBefore(); if (playing) void beforeVideo.current?.play().catch(() => {}); }} />}
            {compare && <>
              <span className="comparison-label before">ORIGINAL</span><span className="comparison-label after">{selectedVersion?.name.toUpperCase()}</span>
              <div className="wipe-line" style={{ left: wipe + '%' }}><span><Icon name="compare" size={16} /></span></div>
              <input className="wipe-input" type="range" min="0" max="100" value={wipe}
                aria-label="Before and after wipe position" onChange={event => setWipe(Number(event.target.value))} />
            </>}
            {isDraft && <div className="draft-preview-label"><span className="status-dot amber" />Recipe draft — showing original footage</div>}
            {loading && <div className="video-loading" role="status"><span className="spinner" />Loading video…</div>}
          </>}
        {!source && <span className="stage-caption"><span className="status-dot" />YOUR ORIGINAL STAYS YOUR ORIGINAL</span>}
        {dragging && <div className="drop-overlay"><Icon name="upload" size={34} /><strong>Drop to import footage</strong>{source && <span>Your current session will be kept.</span>}</div>}
      </div>
      <div className="transport">
        <div className="transport-buttons">
          <button className="icon-button" aria-label="Go to beginning" disabled={!url} onClick={() => seek(0)}><Icon name="rewind" size={15} /></button>
          <button className="play-button" aria-label={playing ? 'Pause video' : 'Play video'} disabled={!url} onClick={togglePlay}><Icon name={playing ? 'pause' : 'play'} size={17} /></button>
          <button className="icon-button" aria-label={muted ? 'Unmute video' : 'Mute video'} disabled={!url} aria-pressed={muted} onClick={() => setMuted(!muted)}><Icon name={muted ? 'mute' : 'volume'} size={16} /></button>
        </div>
        <span className="time-display">{formatTime(time)}<span>/ {formatTime(duration)}</span></span>
        <input className="seek-bar" aria-label="Video position" aria-valuetext={formatTime(time) + ' of ' + formatTime(duration)}
          type="range" min="0" max={duration || 1} step="0.01" value={Math.min(time, duration)} disabled={!url}
          style={{ background: 'linear-gradient(to right, #aab99e ' + (duration ? time / duration * 100 : 0) + '%, #373c3d 0)' }}
          onChange={event => seek(Number(event.target.value))} />
        <div className="transport-end">
          <select aria-label="Playback speed" className="speed-select" value={rate} onChange={event => setRate(Number(event.target.value))} disabled={!url}>
            <option value="0.5">0.5×</option><option value="1">1×</option><option value="1.5">1.5×</option><option value="2">2×</option>
          </select>
          <button className={'icon-button loop-button ' + (loop ? 'active' : '')} aria-label="Loop playback" aria-pressed={loop} disabled={!url} onClick={() => setLoop(!loop)}><Icon name="loop" size={16} /></button>
          <button className="icon-button" aria-label="Fullscreen viewer" disabled={!url} onClick={toggleFullscreen}><Icon name="expand" size={16} /></button>
        </div>
      </div>
    </section>
  );
}
