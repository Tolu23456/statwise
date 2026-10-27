/**
 * Unit tests for the forum feed core (merge/reconciliation + envelope
 * builder). Pure inputs — plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/forumFeedCore.ts statwise/lib/forumFeedCore.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/forumFeedCore.test.js
 */
import {
  buildForumContent,
  cacheableForumRows,
  FORUM_CACHE_CAP,
  isOptimisticId,
  mergeForumRows,
  OPTIMISTIC_TTL_MS,
  replyPreview,
  SERVER_MESSAGE_CAP,
  tokenizeMentions,
} from './forumFeedCore';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

const NOW = Date.parse('2026-10-27T12:00:00Z');
const at = (iso: string) => Date.parse(iso);
const row = (id: string, user: string, message: string, created_at: string) => ({
  id, user_id: user, message, created_at,
});
const fresh = (offsetMs: number) => new Date(NOW - offsetMs).toISOString();

// --- merge: upsert, order, cap --------------------------------------------
const a1 = row('a1', 'u1', 'hello', '2026-10-27T10:00:00Z');
const a2 = row('a2', 'u2', 'hi', '2026-10-27T10:01:00Z');
check('merge upserts and sorts chronologically',
  mergeForumRows([a2], [a1], at('2026-10-27T12:00:00Z')).map(r => r.id), ['a1', 'a2']);
check('duplicate id keeps the incoming copy (last write wins)',
  mergeForumRows([{ ...a1, message: 'old' }], [{ ...a1, message: 'new' }], NOW).map(r => r.message), ['new']);
const beforeMerge = JSON.stringify([a1, a2]);
mergeForumRows([a1], [a2], NOW);
check('merge never mutates its inputs', JSON.stringify([a1, a2]), beforeMerge);

const manyBase = Date.parse('2026-10-27T10:00:00Z');
const many = Array.from({ length: FORUM_CACHE_CAP + 25 }, (_, i) =>
  row(`r${String(i).padStart(4, '0')}`, 'u1', `m${i}`, new Date(manyBase + i * 1000).toISOString()));
check(`merge caps at FORUM_CACHE_CAP (${FORUM_CACHE_CAP}) keeping the newest`,
  mergeForumRows([], many, NOW).length, FORUM_CACHE_CAP);
check('capped merge keeps the newest row', mergeForumRows([], many, NOW).slice(-1)[0].id,
  many[many.length - 1].id);

// --- optimistic reconciliation --------------------------------------------
const optimistic = row('optimistic_1000', 'u1', 'my message', fresh(2000));
const confirmedSame = row('real-1', 'u1', 'my message', fresh(1000));
check('confirmed row removes its optimistic twin',
  mergeForumRows([optimistic], [confirmedSame], NOW).map(r => r.id), ['real-1']);

const optimisticOther = row('optimistic_1001', 'u1', 'other message', fresh(2000));
check('confirmed row keeps a DIFFERENT optimistic message (no data loss)',
  mergeForumRows([optimisticOther], [confirmedSame], NOW).map(r => r.id).sort(),
  ['optimistic_1001', 'real-1']);

const staleOptimistic = row('optimistic_9', 'u1', 'never sent', fresh(OPTIMISTIC_TTL_MS + 1000));
check('stale optimistic row is dropped on merge (no eternal ghost bubble)',
  mergeForumRows([staleOptimistic], [a1], NOW).map(r => r.id), ['a1']);

check('isOptimisticId detects the prefix', [isOptimisticId('optimistic_1'), isOptimisticId('uuid-1')],
  [true, false]);
check('cacheable rows exclude optimistic bubbles',
  cacheableForumRows([a1, optimistic]).map(r => r.id), ['a1']);

// --- envelope builder (image AND video in one message) ---------------------
check('text-only message is sent raw (readable history)',
  buildForumContent('plain text'), { content: 'plain text', overflow: 0 });
check('image-only message becomes JSON envelope',
  buildForumContent('', 'https://cdn/x.jpg'), { content: '{"image":"https://cdn/x.jpg"}', overflow: 0 });

const both = buildForumContent('look at this', 'https://cdn/a.jpg', 'https://cdn/b.mp4');
const parsedBoth = JSON.parse(both.content);
check('image AND video share one message with the text',
  [parsedBoth.image, parsedBoth.video, parsedBoth.text],
  ['https://cdn/a.jpg', 'https://cdn/b.mp4', 'look at this']);
check('media-only message omits the empty text key',
  buildForumContent('  ', 'https://cdn/a.jpg').content, '{"image":"https://cdn/a.jpg"}');

// --- server cap (send_forum_message left(...,1000) would corrupt JSON) -----
const longText = 'x'.repeat(SERVER_MESSAGE_CAP + 1);
check('plain text over the cap reports overflow, not truncation',
  buildForumContent(longText).overflow, 1);
const fatMedia = buildForumContent(
  'y'.repeat(900),
  'https://cdn/' + 'a'.repeat(80) + '.jpg',
  'https://cdn/' + 'b'.repeat(80) + '.mp4',
);
check('media envelope counts JSON length against the cap', fatMedia.overflow > 0, true);
check('in-cap media envelope passes cleanly', buildForumContent('ok', 'https://c/x.jpg').overflow, 0);

// --- mentions (mirrors the SQL notify trigger's regex) ----------------------
check('single mention is its own segment',
  tokenizeMentions('hi @tolu look'), [
    { text: 'hi ', mention: false },
    { text: '@tolu', mention: true },
    { text: ' look', mention: false },
  ]);
check('mentions at the start and end still parse',
  tokenizeMentions('@abc x @def').map(s => `${s.mention ? 'M' : 'T'}:${s.text}`),
  ['M:@abc', 'T: x ', 'M:@def']);
check('handles with digits/underscores only (SQL pattern parity)',
  tokenizeMentions('@user_09 x').map(s => s.mention), [true, false]);
check('too-short handles are NOT mentions (SQL needs 3+ chars)',
  tokenizeMentions('cc @ab end'), [{ text: 'cc @ab end', mention: false }]);
check('bare @ or trailing punctuation is plain text',
  tokenizeMentions('mail me @ x'), [{ text: 'mail me @ x', mention: false }]);
check('empty text tokenizes to nothing', tokenizeMentions(''), []);

// --- reply previews (quote bar) ---------------------------------------------
check('plain text preview is trimmed and flattened',
  replyPreview('  hello   there  '), 'hello there');
check('long preview is capped with an ellipsis',
  replyPreview('x'.repeat(80)).length, 60);
check('image envelope previews as [Image] plus its caption',
  replyPreview('{"image":"https://c/a.jpg","text":"wow"}'), '[Image] wow');
check('video envelope previews as [Video]',
  replyPreview('{"video":"https://c/b.mp4"}'), '[Video]');
check('image AND video envelope shows both markers',
  replyPreview('{"image":"https://c/a.jpg","video":"https://c/b.mp4"}'), '[Image] [Video]');
check('broken JSON degrades to a safe marker, not raw source',
  replyPreview('{"image":'), '[Message]');

console.log(`forumFeedCore.test: ${passed} checks passed`);
