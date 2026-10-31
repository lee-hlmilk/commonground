import { useEffect, useState } from 'react';
import { Clock3 } from 'lucide-react';
import type { Run } from '../types';
import { decisionRuntimeSeconds, formatRuntime } from '../lib/negotiation';

export default function DecisionRuntime({run, compact = false}: {run: Run; compact?: boolean}) {
  const [currentTime, setCurrentTime] = useState(Date.now);
  const running = run.status === 'negotiating' && run.mode !== 'recorded_replay';
  useEffect(() => {
    if (!running) return;
    setCurrentTime(Date.now());
    const timer = window.setInterval(() => setCurrentTime(Date.now()), 200);
    return () => window.clearInterval(timer);
  }, [running, run.id, run.episode]);
  const seconds = decisionRuntimeSeconds(run, currentTime);
  const label = run.mode === 'recorded_replay' ? 'Recorded decision runtime' : run.mode === 'replay' ? 'Illustrative runtime' : run.mode === 'fallback' ? 'Recovery runtime' : 'Decision runtime';
  const detail = run.mode === 'recorded_replay' ? 'Original processing time; replay playback is excluded.'
    : running ? 'Planning, agent responses and final checks. Stops at the outcome.'
    : seconds === null ? 'No completed duration was recorded for this run.' : 'Planning, all agent turns and final checks.';
  return <div className={`decision-runtime ${compact ? 'compact' : ''} ${running ? 'running' : ''}`} role="timer" aria-label={label} aria-live="off" title={detail}>
    <Clock3 size={compact ? 13 : 17}/><div><span>{label}</span><strong>{seconds === null ? run.status === 'interrupted' ? 'Interrupted' : running ? 'Starting…' : 'Not recorded' : formatRuntime(seconds)}</strong></div>
    {!compact && <p>{detail}</p>}
  </div>;
}
