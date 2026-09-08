// Non-verbal annotation tags, shared between the general dashboard tool
// (views/Dashboard.jsx) and the Eagle worker review screen
// (worker/ReviewWorkspace.jsx). One list, one insertion function -- a
// tasker and a general-tool user should never see two different
// vocabularies for the same thing, and a fix to how insertion works
// should never need to be made twice.

export const ANNOTATION_TAGS = [
  '[laughter]', '[music]', '[applause]', '[silence]', '[pause]',
  '[background noise]', '[crosstalk]', '[inaudible]', '[phone ringing]', '[door closes]',
];

/**
 * Splices `tag` into `text` at `cursor` ({start, end}), or appends it at
 * the end when no cursor is known at all.
 *
 * PURE AND STANDALONE ON PURPOSE. This is the exact logic that used to be
 * inline in Dashboard.jsx's handleInsertAnnotation, checking
 * `document.activeElement === textarea` at insert time -- which is
 * essentially never true, since clicking the tag dropdown itself moves
 * focus off the textarea before this ever runs. The actual fix is in the
 * CALLER (remembering the cursor position continuously via onSelect,
 * before focus moves away, not reading it live here); this function only
 * does the splice once a position -- real or absent -- is already known,
 * which is what makes it possible to unit-test without mounting a
 * component or simulating focus/DOM selection at all.
 *
 * Returns `{ text, cursor }`: the new text, and where the cursor should
 * land next (right after the inserted tag) so a second tag picked
 * immediately after the first lands after it, not back at the same spot.
 */
export function insertTagAtPosition(text, cursor, tag) {
  const source = text || '';
  if (!cursor) {
    const newText = `${source} ${tag}`.trim();
    return { text: newText, cursor: { start: newText.length, end: newText.length } };
  }
  // Clamped, not trusted outright: the text may have changed (another
  // edit, a live update) since the position was recorded.
  const len = source.length;
  const start = Math.min(Math.max(0, cursor.start), len);
  const end = Math.min(Math.max(start, cursor.end), len);
  const newText = source.slice(0, start) + tag + source.slice(end);
  const nextPos = start + tag.length;
  return { text: newText, cursor: { start: nextPos, end: nextPos } };
}
