// Authenticated, scoped history invalidations. Tokens stay in headers and memory.
const MAX_FRAME = 8192,
  MAX_CHUNK = 65536,
  IDLE_MS = 15000;

export async function readEvents(response, signal, onEvent) {
  const reader = response.body.getReader(),
    decoder = new TextDecoder('utf-8', { fatal: true });
  let pending = '',
    event = '',
    id = '',
    data = [],
    frameSize = 0;
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) return;
      if (value.byteLength > MAX_CHUNK) throw new Error('event_stream_chunk_limit');
      pending += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = pending.indexOf('\n')) >= 0) {
        let line = pending.slice(0, newline);
        pending = pending.slice(newline + 1);
        if (line.endsWith('\r')) line = line.slice(0, -1);
        frameSize += line.length + 1;
        if (frameSize > MAX_FRAME) throw new Error('event_stream_frame_limit');
        if (!line) {
          if (data.length) await onEvent({ event, id, data: JSON.parse(data.join('\n')) });
          event = id = '';
          data = [];
          frameSize = 0;
          if (signal.aborted) return;
        } else if (!line.startsWith(':')) {
          const colon = line.indexOf(':'),
            field = colon < 0 ? line : line.slice(0, colon);
          let text = colon < 0 ? '' : line.slice(colon + 1);
          if (text.startsWith(' ')) text = text.slice(1);
          if (field === 'event') event = text;
          if (field === 'id') id = text;
          if (field === 'data') data.push(text);
        }
      }
      if (pending.length + frameSize > MAX_FRAME) throw new Error('event_stream_frame_limit');
    }
  } finally {
    // Cancellation/parse errors settle the actual reader before reconnection.
    try {
      await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}

export function mountEventStream({ getConnection, onEvents, onAuthLost }) {
  let owner = null;
  function stop() {
    const previous = owner;
    owner = null;
    if (!previous) return;
    clearTimeout(previous.retry);
    clearTimeout(previous.idle);
    previous.controller?.abort();
  }
  function start() {
    stop();
    const scope = getConnection();
    if (!scope.agent || !scope.token || document.hidden) return;
    const current = { ...scope, cursor: '', retry: null, idle: null, controller: null, failures: 0 };
    owner = current;
    const valid = () => {
      const latest = getConnection();
      return (
        owner === current &&
        !document.hidden &&
        latest.agent === current.agent &&
        latest.token === current.token &&
        latest.generation === current.generation
      );
    };
    async function connect() {
      if (!valid()) return;
      const controller = new AbortController();
      current.controller = controller;
      let stopped = false;
      const heartbeat = () => {
        clearTimeout(current.idle);
        current.idle = setTimeout(() => controller.abort(), IDLE_MS);
      };
      heartbeat();
      try {
        const response = await fetch(`/api/v1/agents/${encodeURIComponent(current.agent)}/events/stream`, {
          headers: {
            Authorization: `Bearer ${current.token}`,
            Accept: 'text/event-stream',
            ...(current.cursor ? { 'Last-Event-ID': current.cursor } : {}),
          },
          signal: controller.signal,
          cache: 'no-store',
          redirect: 'error',
        });
        if (!valid()) return;
        if (response.status === 401) {
          stopped = true;
          onAuthLost();
          return;
        }
        if (response.status === 410) {
          current.cursor = '';
          await onEvents({ run_ids: [], reset: true, has_more: false });
          return;
        }
        if (!response.ok || !/^text\/event-stream(?:;|$)/i.test(response.headers.get('content-type') || ''))
          throw new Error('event_stream_unavailable');
        if (!response.body) throw new Error('event_stream_unavailable');
        current.failures = 0;
        // Heartbeat comments must refresh the inactivity timer too, while the
        // bounded parser keeps partial frames private to this connection.
        const watched = new Response(
          response.body.pipeThrough(
            new TransformStream({
              transform(chunk, target) {
                heartbeat();
                target.enqueue(chunk);
              },
            }),
          ),
        );
        await readEvents(watched, controller.signal, async ({ event, id, data }) => {
          if (!valid()) return;
          if (event === 'unauthenticated') {
            stopped = true;
            onAuthLost();
            controller.abort();
          } else if (event === 'reset') {
            current.cursor = '';
            await onEvents({ run_ids: [], reset: true, has_more: false });
          } else if (event === 'history') {
            if (
              !/^[A-Za-z0-9_-]{1,1024}$/.test(id) ||
              !data ||
              !Array.isArray(data.run_ids) ||
              data.run_ids.length > 50 ||
              data.run_ids.some((value) => typeof value !== 'string' || value.length !== 36) ||
              typeof data.has_more !== 'boolean' ||
              !Number.isInteger(data.count) ||
              data.count < 0 ||
              data.count > 50
            )
              throw new Error('event_stream_invalid');
            if (data.count || data.history_trimmed) await onEvents(data);
            // Failed consumers retain the previous cursor, so reconnecting
            // cannot silently acknowledge notifications they did not process.
            if (valid()) current.cursor = id;
          } else if (!['end', 'closing', 'unavailable'].includes(event)) throw new Error('event_stream_invalid');
        });
      } catch {
        current.failures = Math.min(current.failures + 1, 5);
      } finally {
        clearTimeout(current.idle);
        controller.abort();
        if (valid() && !stopped) current.retry = setTimeout(connect, Math.min(1000 * 2 ** current.failures, 30000));
      }
    }
    void connect();
  }
  return { start, stop };
}
