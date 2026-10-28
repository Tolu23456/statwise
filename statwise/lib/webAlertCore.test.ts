/**
 * Unit tests for the web-alert core (plan 5.1 support): normalisation
 * defaults, the three-button cap, and FIFO queue semantics. Pure — runs on
 * plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/webAlertCore.ts statwise/lib/webAlertCore.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/webAlertCore.test.js
 */
import {
  MAX_ALERT_BUTTONS,
  createAlertQueue,
  currentAlert,
  dismissAlert,
  enqueueAlert,
  normalizeAlert,
} from './webAlertCore';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

// --- normalisation ---------------------------------------------------------
const bare = normalizeAlert({ title: 'Hi' });
check('no buttons -> default OK', bare.buttons.map((b) => b.text), ['OK']);
check('no message -> empty string', bare.message, '');
check('title preserved', bare.title, 'Hi');

const nulled = normalizeAlert({ title: null, message: null, buttons: null });
check('null title -> empty string', nulled.title, '');
check('null message -> empty string', nulled.message, '');
check('null buttons -> default OK', nulled.buttons.length, 1);

const withMsg = normalizeAlert({ title: 'T', message: 'line1\nline2' });
check('multiline message preserved', withMsg.message, 'line1\nline2');

// button normalisation: missing text defaults to OK, handler preserved,
// style preserved, and unnamed handlers are omitted (not set to undefined).
const handler = (): void => {};
const normed = normalizeAlert({
  title: 'T',
  buttons: [
    { text: 'Yes', onPress: handler, style: 'destructive' },
    { onPress: handler },
    { text: 'Cancel', style: 'cancel' },
    { text: 'Fourth' },
  ],
});
check('button cap at three', normed.buttons.length, MAX_ALERT_BUTTONS);
check('named button kept', normed.buttons[0].text, 'Yes');
check('handler reference kept', normed.buttons[0].onPress === handler, true);
check('style kept', normed.buttons[0].style, 'destructive');
check('textless button defaults to OK', normed.buttons[1].text, 'OK');
check('button without onPress has no key', 'onPress' in normed.buttons[2], false);

check('default button has no handler key', 'onPress' in normalizeAlert({ title: 'x' }).buttons[0], false);

// --- queue semantics -------------------------------------------------------
let q = createAlertQueue();
check('empty queue has no current', currentAlert(q), null);

q = enqueueAlert(q, { title: 'first' });
q = enqueueAlert(q, { title: 'second' });
q = enqueueAlert(q, { title: 'third' });
check('queue length after three enqueues', q.entries.length, 3);
check('FIFO: first is current', currentAlert(q)?.title, 'first');
check('ids are unique', new Set(q.entries.map((e) => e.id)).size, 3);

q = dismissAlert(q);
check('after dismiss, next is current', currentAlert(q)?.title, 'second');
q = dismissAlert(q);
check('after second dismiss, third is current', currentAlert(q)?.title, 'third');
q = dismissAlert(q);
check('drained queue has no current', currentAlert(q), null);
q = dismissAlert(q);
check('dismiss on empty queue is safe', currentAlert(q), null);

// enqueue keeps seq monotonic across dismissals (ids never reused).
const q2 = enqueueAlert(dismissAlert(q), { title: 'later' });
check('seq survives dismissal (new id)', q2.entries[0].id, 4);

console.log(`webAlertCore.test: ${passed} checks passed`);
