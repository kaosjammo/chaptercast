"use strict";

let chapterCastSessionAudio = null;
function updateChapterCastMediaSession(audio) {
  if (!("mediaSession" in navigator) || audio !== chapterCastSessionAudio) return;
  const session = navigator.mediaSession;
  session.playbackState = audio.ended || audio.error ? "none" : audio.paused ? "paused" : "playing";
  if (!session.setPositionState) return;
  try {
    if (!audio.error && Number.isFinite(audio.duration) && audio.duration > 0) {
      session.setPositionState({duration: audio.duration, playbackRate: audio.playbackRate,
        position: Math.max(0, Math.min(audio.duration, audio.currentTime))});
    } else session.setPositionState({});
  } catch { /* Individual browser implementations may omit position support. */ }
}
function setChapterCastMediaSession(audio, title, onPlayFailure) {
  if (!("mediaSession" in navigator)) return;
  chapterCastSessionAudio = audio;
  const session = navigator.mediaSession;
  if (typeof MediaMetadata !== "undefined") session.metadata = new MediaMetadata({title, artist: "ChapterCast"});
  const seekTo = seconds => {
    if (Number.isFinite(seconds) && Number.isFinite(audio.duration) && audio.duration > 0) {
      audio.currentTime = Math.max(0, Math.min(audio.duration, seconds));
      updateChapterCastMediaSession(audio);
    }
  };
  const handlers = {
    play: async () => { try { await audio.play(); } catch { onPlayFailure(); } },
    pause: () => audio.pause(),
    stop: () => { audio.pause(); seekTo(0); },
    seekbackward: details => seekTo(audio.currentTime - (details.seekOffset ?? 15)),
    seekforward: details => seekTo(audio.currentTime + (details.seekOffset ?? 15)),
    seekto: details => seekTo(details.seekTime),
  };
  for (const [name, handler] of Object.entries(handlers)) {
    try { session.setActionHandler(name, handler); }
    catch { /* Unsupported actions must not prevent ordinary playback. */ }
  }
  updateChapterCastMediaSession(audio);
}
