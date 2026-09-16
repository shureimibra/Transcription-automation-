import { useCallback, useEffect, useRef, useState } from 'react';
import { taskLabel } from '../lib/format.js';

/**
 * A page-anchored "you just claimed this" confirmation, shared by every
 * screen that renders a grid or list of claimable task cards.
 *
 * WHY THIS IS A SHARED HOOK, NOT A COPY IN EACH SCREEN. It started as
 * logic written directly into WorkerDashboard, after a worker claimed two
 * tasks in one motion there: click Claim, the grid silently reshuffles (the
 * card gone, the next one sliding up to fill the exact screen position),
 * read as "nothing happened", click what is now a DIFFERENT task's Claim
 * button sitting in that same spot. The button itself was never
 * unresponsive -- TaskActions already sets a `loading`/disabled state the
 * instant it is clicked -- the reshuffle just outran the worker's eye.
 *
 * CONFIRMED LIVE: the same incident nearly recurred on a SECOND screen
 * (TaskQueue, the dedicated /tasks/translation and /tasks/transcription
 * list pages) that renders the exact same TaskCard/TaskActions and has the
 * exact same silent-refetch-then-reshuffle shape -- because the fix lived
 * only in WorkerDashboard's own local state, the two screens shared no
 * code for this and patching one taught the other nothing. Pulling it out
 * here is what actually closes the gap: every screen that calls this hook
 * gets the fix, including ones written after this comment.
 *
 * `destination` is the one thing that legitimately differs per screen --
 * where the claimed task actually went -- so it is the only parameter.
 */
export function useClaimNotice(destination) {
  const [message, setMessage] = useState('');
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  // Only the claim transition ever lands a task ON `claimed` -- every other
  // action in TaskActions' ACTIONS map targets a different status, so this
  // is a reliable "a claim just succeeded" signal without threading a
  // second callback through TaskCard/TaskActions for it.
  const noteChange = useCallback((updated) => {
    if (updated?.status !== 'claimed') return;
    setMessage(`Claimed ${taskLabel(updated)} — ${destination}`);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setMessage(''), 8000);
  }, [destination]);

  return { claimedNotice: message, noteChange };
}
