import { useEffect, useState } from 'react';

type Listener = () => void;

/** Mutable simulation clock shared by the 3D scene (read per frame) and the HUD (throttled). */
class SimClock {
  t = 0;
  horizon = 120;
  playing = false;
  speed = 2;
  private listeners = new Set<Listener>();
  private last = 0;
  private frame = 0;

  subscribe(fn: Listener) { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; }
  private emit() { this.listeners.forEach(fn => fn()); }

  set(t: number) { this.t = Math.max(0, Math.min(this.horizon, t)); this.emit(); }
  setHorizon(h: number) { this.horizon = h; if (this.t > h) this.t = h; this.emit(); }
  setSpeed(s: number) { this.speed = s; this.emit(); }
  play() {
    if (this.playing) return;
    if (this.t >= this.horizon) this.t = 0;
    this.playing = true; this.last = performance.now();
    const tick = (now: number) => {
      if (!this.playing) return;
      this.t = Math.min(this.horizon, this.t + (now - this.last) / 1000 * this.speed);
      this.last = now;
      if (this.t >= this.horizon) this.playing = false;
      this.emit();
      if (this.playing) this.frame = requestAnimationFrame(tick);
    };
    this.frame = requestAnimationFrame(tick);
    this.emit();
  }
  pause() { this.playing = false; cancelAnimationFrame(this.frame); this.emit(); }
  toggle() { if (this.playing) this.pause(); else this.play(); }
}

export const simClock = new SimClock();

/** React view of the clock, re-rendering at most every `interval` ms. */
export function useSimClock(interval = 120) {
  const [snapshot, setSnapshot] = useState(() => ({t: simClock.t, playing: simClock.playing, speed: simClock.speed, horizon: simClock.horizon}));
  useEffect(() => {
    let pending = 0;
    const update = () => setSnapshot({t: simClock.t, playing: simClock.playing, speed: simClock.speed, horizon: simClock.horizon});
    const unsubscribe = simClock.subscribe(() => {
      if (!simClock.playing) { window.clearTimeout(pending); pending = 0; update(); return; }
      if (!pending) pending = window.setTimeout(() => { pending = 0; update(); }, interval);
    });
    return () => { unsubscribe(); window.clearTimeout(pending); };
  }, [interval]);
  return snapshot;
}
