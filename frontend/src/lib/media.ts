import type { MediaAsset } from './types';

export function inspectVideo(file: File): Promise<MediaAsset> {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith('video/') && !/\.(mp4|mov|webm|m4v|mkv)$/i.test(file.name)) {
      reject(new Error('Choose a video file, such as MP4, WebM or MOV.'));
      return;
    }
    const url = URL.createObjectURL(file);
    const video = document.createElement('video');
    video.preload = 'auto';
    video.muted = true;
    video.playsInline = true;
    const cleanUp = () => {
      clearTimeout(timeout);
      video.onloadeddata = null;
      video.onerror = null;
      video.removeAttribute('src');
      video.load();
      URL.revokeObjectURL(url);
    };
    const timeout = setTimeout(() => {
      cleanUp();
      reject(new Error('This video could not be opened. Try a browser-compatible H.264 MP4 or WebM.'));
    }, 20000);
    video.onerror = () => {
      cleanUp();
      reject(new Error('Your browser cannot play this video codec. Try an H.264 MP4 or WebM.'));
    };
    video.onloadeddata = () => {
      if (!Number.isFinite(video.duration) || !video.videoWidth || video.duration <= 0) {
        cleanUp();
        reject(new Error('The video has no readable duration or picture.'));
        return;
      }
      const canvas = document.createElement('canvas');
      canvas.width = 320;
      canvas.height = Math.round(320 * video.videoHeight / video.videoWidth);
      const context = canvas.getContext('2d');
      context?.drawImage(video, 0, 0, canvas.width, canvas.height);
      const asset: MediaAsset = {
        id: crypto.randomUUID(), name: file.name, size: file.size,
        type: file.type, duration: video.duration,
        width: video.videoWidth, height: video.videoHeight,
        thumbnail: context ? canvas.toDataURL('image/jpeg', 0.75) : '',
      };
      cleanUp();
      resolve(asset);
    };
    video.src = url;
    video.load();
  });
}
