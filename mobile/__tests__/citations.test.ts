import { splitCitations } from '@/lib/citations';

describe('splitCitations', () => {
  it('splits text and event citations in order', () => {
    expect(splitCitations('Try Pottery [E5], or Yoga [E7].')).toEqual([
      { type: 'text', text: 'Try Pottery ' },
      { type: 'event', id: 5 },
      { type: 'text', text: ', or Yoga ' },
      { type: 'event', id: 7 },
      { type: 'text', text: '.' },
    ]);
  });

  it('drops citations of events the response did not include', () => {
    expect(splitCitations('A [E5] and B [E999].', new Set([5]))).toEqual([
      { type: 'text', text: 'A ' },
      { type: 'event', id: 5 },
      { type: 'text', text: ' and B .' },
    ]);
  });

  it('leaves a half-streamed citation as text until it completes', () => {
    expect(splitCitations('Pottery [E1')).toEqual([{ type: 'text', text: 'Pottery [E1' }]);
  });
});
