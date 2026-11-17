import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mountEventStream, readEvents } from '../ui/v2/event-stream.js';

const identity = '3e91a575-7ddc-4692-87e6-00b9e94d1f12';
const wire = (id, extra = {}) =>
  `id: ${id}\nevent: history\ndata: ${JSON.stringify({ count: 1, run_ids: [identity], has_more: false, ...extra })}\n\n`;
const turn = () => new Promise((resolve) => setImmediate(resolve));
function response(chunks, cancel = () => {}) {
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
        controller.close();
      },
      cancel,
    }),
    { headers: { 'content-type': 'text/event-stream; charset=utf-8' } },
  );
}

test('fragmented UTF-8 and CRLF frames retain only complete events; comments carry no event', async () => {
  const text = 'id: cursor_1\r\nevent: history\r\ndata: {"text":"中文"}\r\n\r\n: keepalive\r\n\r\n';
  const bytes = new TextEncoder().encode(text),
    seen = [];
  const stream = new Response(
    new ReadableStream({
      start(controller) {
        for (const value of bytes) controller.enqueue(new Uint8Array([value]));
        controller.close();
      },
    }),
  );
  await readEvents(stream, new AbortController().signal, (event) => seen.push(event));
  assert.deepEqual(seen, [{ id: 'cursor_1', event: 'history', data: { text: '中文' } }]);
  assert.equal(stream.body.locked, false);
});

test('oversized incomplete frames cancel the actual reader and release its lock', async () => {
  let cancelled = 0;
  const stream = new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`data: ${'x'.repeat(8192)}`));
      },
      cancel() {
        cancelled++;
      },
    }),
  );
  await assert.rejects(
    readEvents(stream, new AbortController().signal, () => {}),
    /frame_limit/,
  );
  assert.equal(cancelled, 1);
  assert.equal(stream.body.locked, false);
});

test('an aborted event handler cannot deliver remaining frames from the same network chunk', async () => {
  const controller = new AbortController(),
    seen = [];
  await readEvents(response([wire('cursor_1') + wire('cursor_2')]), controller.signal, (event) => {
    seen.push(event.id);
    controller.abort();
  });
  assert.deepEqual(seen, ['cursor_1']);
});

function environment() {
  const before = { fetch: globalThis.fetch, document: globalThis.document, setTimeout, clearTimeout };
  const pending = new Map(),
    requests = [];
  let sequence = 0;
  globalThis.document = { hidden: false };
  globalThis.setTimeout = (callback, delay) => {
    const id = ++sequence;
    pending.set(id, { callback, delay });
    return id;
  };
  globalThis.clearTimeout = (id) => pending.delete(id);
  return {
    pending,
    requests,
    fetch(next) {
      globalThis.fetch = async (url, options) => {
        requests.push({ url, options });
        return next(requests.length, options);
      };
    },
    async retry() {
      const [id, timer] = [...pending].find(([, value]) => value.delay !== 15000) || [];
      assert.ok(timer, 'reconnection timer must be owned and bounded');
      pending.delete(id);
      await timer.callback();
      await turn();
    },
    restore() {
      for (const [key, value] of Object.entries(before)) globalThis[key] = value;
    },
  };
}

test('reconnection acknowledges only processed cursors and never puts a token in the URL', async () => {
  const env = environment();
  let failures = 1;
  const seen = [],
    connection = { agent: identity, token: 'private-owner-token', generation: 1 };
  const mount = mountEventStream({
    getConnection: () => connection,
    async onEvents(data) {
      if (seen.length === 1 && failures-- > 0) throw new Error('consumer failed');
      seen.push(data);
    },
    onAuthLost() {
      assert.fail('fixture remains authorized');
    },
  });
  try {
    env.fetch((number) => response([number === 1 ? wire('cursor_1') + wire('cursor_2') : wire('cursor_2')]));
    mount.start();
    await turn();
    await turn();
    assert.equal(seen.length, 1);
    await env.retry();
    assert.equal(env.requests[1].options.headers['Last-Event-ID'], 'cursor_1');
    assert.equal(seen.length, 2);
    assert.ok(env.requests.every(({ url }) => !url.includes(connection.token) && !url.includes('?')));
    assert.ok(env.requests.every(({ options }) => options.headers.Authorization === 'Bearer private-owner-token'));
    assert.ok(env.requests.every(({ options }) => options.redirect === 'error' && options.cache === 'no-store'));
    mount.stop();
    assert.equal(env.pending.size, 0);
    assert.ok(env.requests.every(({ options }) => options.signal.aborted));
  } finally {
    mount.stop();
    env.restore();
  }
});

test('replacing an Agent aborts its pending fetch and rejects a late old response', async () => {
  const env = environment(),
    seen = [];
  let connection = { agent: identity, token: 'old-token', generation: 1 },
    finishOld;
  const mount = mountEventStream({
    getConnection: () => connection,
    onEvents: (value) => seen.push(value),
    onAuthLost() {},
  });
  try {
    env.fetch((number) =>
      number === 1 ? new Promise((resolve) => (finishOld = resolve)) : response([wire('new_cursor')]),
    );
    mount.start();
    connection = { agent: '5acd49cf-e739-4d26-b980-23845a258d7b', token: 'new-token', generation: 2 };
    mount.start();
    assert.equal(env.requests[0].options.signal.aborted, true);
    finishOld(response([wire('old_cursor')]));
    await turn();
    await turn();
    assert.equal(seen.length, 1);
    assert.equal(env.requests[1].options.headers['Last-Event-ID'], undefined);
    mount.stop();
    assert.equal(env.pending.size, 0);
  } finally {
    mount.stop();
    env.restore();
  }
});

test('cursor expiry replays invalidations explicitly; token revocation stops reconnection', async () => {
  const env = environment(),
    seen = [];
  let revoked = 0;
  const mount = mountEventStream({
    getConnection: () => ({ agent: identity, token: 'fixture-token', generation: 1 }),
    onEvents: (value) => seen.push(value),
    onAuthLost() {
      revoked++;
    },
  });
  try {
    env.fetch((number) => (number === 1 ? new Response('{}', { status: 410 }) : new Response('{}', { status: 401 })));
    mount.start();
    await turn();
    assert.deepEqual(seen, [{ run_ids: [], reset: true, has_more: false }]);
    await env.retry();
    assert.equal(revoked, 1);
    assert.equal(env.pending.size, 0);
  } finally {
    mount.stop();
    env.restore();
  }
});
