// Original API fixtures. These checks do not prove real media playback.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function worker() {
  const handlers = {}, entries = new Map(), requests = [];
  let online = true;
  const cache = {put: async (key, response) => entries.set(String(key), response),
    match: async key => entries.get(String(key))};
  const context = {URL, Request, Response, caches: {open: async () => cache,
    keys: async () => ['unrelated', 'chaptercast-shell-old', 'chaptercast-shell-v3'],
    delete: async name => requests.push({deleted: name})},
    fetch: async request => {
      requests.push(request);
      if (!online) throw new Error('Offline fixture');
      return new Response('Original public shell fixture');
    }, self: {location: {origin: 'https://example.test'},
      addEventListener: (name, handler) => handlers[name] = handler,
      clients: {claim: async () => {}}, skipWaiting: async () => {}}};
  vm.runInNewContext(fs.readFileSync('src/chaptercast/static/sw.js', 'utf8'), context);
  return {handlers, entries, requests, offline: () => online = false};
}

test('only fixed public shell GETs enter the cache, fetched without credentials', async () => {
  const w = worker();
  let installed;
  w.handlers.install({waitUntil: promise => installed = promise});
  await installed;
  assert.equal(w.entries.size, 8);
  for (const request of w.requests) {
    assert.equal(request.credentials, 'omit');
    assert.equal(request.redirect, 'error');
    assert.equal(request.headers.get('Authorization'), null);
  }
  for (const [path, init] of [
    ['/api/library', {}], ['/api/import/text', {method: 'POST'}],
    ['/api/jobs/1/outputs/opus', {}], ['/health', {}],
    ['/?token=private', {}], ['/static/app.js?token=private', {}],
    ['/', {headers: {Authorization: 'Bearer original-unit-fixture'}}],
    ['/', {headers: {Range: 'bytes=1-2'}}], ['/other', {}],
    ['https://other.test/static/app.js', {}], ['/', {method: 'POST'}],
  ]) {
    let intercepted = false;
    w.handlers.fetch({request: new Request(new URL(path, 'https://example.test'), init),
      respondWith: () => intercepted = true});
    assert.equal(intercepted, false, path);
  }
  w.offline();
  let response;
  w.handlers.fetch({request: new Request('https://example.test/'), respondWith: p => response = p});
  assert.equal(await (await response).text(), 'Original public shell fixture');
  assert.equal(w.entries.size, 8);
  let activation;
  w.handlers.activate({waitUntil: p => activation = p});
  await activation;
  assert.deepEqual(w.requests.filter(x => x.deleted), [{deleted: 'chaptercast-shell-old'}]);
});

test('Media Session actions use the active media, clamp seeks, and report actual state', async () => {
  const handlers = {}, positions = [];
  const session = {setActionHandler: (name, fn) => handlers[name] = fn,
    setPositionState: state => positions.push(state)};
  const context = {navigator: {mediaSession: session}, MediaMetadata: class {constructor(data) {Object.assign(this, data);}}};
  vm.runInNewContext(fs.readFileSync('src/chaptercast/static/media-session.js', 'utf8'), context);
  const audio = {duration: 100, currentTime: 40, playbackRate: 1.5, paused: true, ended: false,
    play: async function () { this.paused = false; }, pause: function () { this.paused = true; }};
  context.setChapterCastMediaSession(audio, 'Original chapter', () => assert.fail('unexpected failure'));
  assert.equal(session.metadata.title, 'Original chapter');
  await handlers.play();
  context.updateChapterCastMediaSession(audio);
  assert.equal(session.playbackState, 'playing');
  assert.equal(positions.at(-1).playbackRate, 1.5);
  handlers.seekforward({seekOffset: 90}); assert.equal(audio.currentTime, 100);
  handlers.seekbackward({seekOffset: 200}); assert.equal(audio.currentTime, 0);
  handlers.seekto({seekTime: 23}); assert.equal(audio.currentTime, 23);
  handlers.seekto({seekTime: NaN}); assert.equal(audio.currentTime, 23);
  handlers.pause(); assert.equal(audio.paused, true);
  context.updateChapterCastMediaSession(audio);
  assert.equal(session.playbackState, 'paused');
  const other = {...audio, currentTime: 0, paused: true};
  context.setChapterCastMediaSession(other, 'Original preview', () => {});
  handlers.seekforward({}); assert.equal(other.currentTime, 15); assert.equal(audio.currentTime, 23);
  context.updateChapterCastMediaSession(audio);
  assert.equal(positions.at(-1).position, 15);
  let failed = false;
  other.play = async () => {throw new Error('Original rejected-play fixture');};
  context.setChapterCastMediaSession(other, 'Original preview', () => failed = true);
  await handlers.play(); assert.equal(failed, true);
  other.duration = NaN;
  context.updateChapterCastMediaSession(other);
  assert.deepEqual(Object.keys(positions.at(-1)), []);
});
