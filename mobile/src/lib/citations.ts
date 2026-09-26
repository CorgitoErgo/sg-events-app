/** Split an /ask answer into text and [E123] citation segments; the app renders citations as event cards. */

export type AnswerSegment = { type: 'text'; text: string } | { type: 'event'; id: number };

const CITATION = /\[E(\d+)\]/g;

export function splitCitations(answer: string, knownIds?: ReadonlySet<number>): AnswerSegment[] {
  const segments: AnswerSegment[] = [];
  let last = 0;
  for (const match of answer.matchAll(CITATION)) {
    const id = Number(match[1]);
    const before = answer.slice(last, match.index);
    if (before) segments.push({ type: 'text', text: before });
    // Unknown ids (not in the response's events) are dropped rather than shown broken.
    if (!knownIds || knownIds.has(id)) segments.push({ type: 'event', id });
    last = (match.index ?? 0) + match[0].length;
  }
  const rest = answer.slice(last);
  if (rest) segments.push({ type: 'text', text: rest });
  return mergeText(segments);
}

function mergeText(segments: AnswerSegment[]): AnswerSegment[] {
  const out: AnswerSegment[] = [];
  for (const seg of segments) {
    const prev = out[out.length - 1];
    if (seg.type === 'text' && prev?.type === 'text') prev.text += seg.text;
    else out.push(seg);
  }
  return out;
}
