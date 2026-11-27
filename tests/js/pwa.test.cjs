// Original API fixtures. These checks do not prove real media playback.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function worker({failPath} = {}) {
  const handlers = {}, entries = new Map(), requests = [];
  let online = true;
  const cache = {put: async (key, response) => entries.set(String(key), response),
    match: async key => entries.get(String(key))};
  const context = {URL, Request, Response, caches: {open: async () => cache,
    keys: async () => ['unrelated', 'chaptercast-shell-old', 'chaptercast-shell-v5'],
    delete: async name => requests.push({deleted: name})},
    fetch: async request => {
      requests.push(request);
      if (!online || new URL(request.url).pathname === failPath) throw new Error('Offline fixture');
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
  assert.deepEqual(w.requests.filter(x => x.deleted), [{deleted: 'chaptercast-shell-old'}, {deleted: 'chaptercast-shell-v5'}]);
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

test('new shell activation removes v5 while preserving unrelated caches', async () => {
  const w = worker();
  let activation;
  w.handlers.activate({waitUntil: promise => activation = promise});
  await activation;
  assert.deepEqual(w.requests.filter(x => x.deleted).map(x => x.deleted).sort(),
    ['chaptercast-shell-old', 'chaptercast-shell-v5']);
});

test('clearing deleted media removes actions only for that active generation', () => {
  const handlers = {};
  const session = {setActionHandler: (name, fn) => handlers[name] = fn, setPositionState: () => {}};
  const context = {navigator: {mediaSession: session}, MediaMetadata: class {constructor(data) {Object.assign(this, data);}}};
  vm.runInNewContext(fs.readFileSync('src/chaptercast/static/media-session.js', 'utf8'), context);
  const audio = {duration: 30, currentTime: 10, playbackRate: 1, paused: true, ended: false};
  context.setChapterCastMediaSession(audio, 'Original deletion fixture', () => {});
  context.clearChapterCastMediaSession({});
  assert.equal(session.metadata.title, 'Original deletion fixture');
  context.clearChapterCastMediaSession(audio);
  assert.equal(session.metadata, null);
  assert.equal(session.playbackState, 'none');
  assert.ok(Object.values(handlers).every(handler => handler === null));
});


test('incomplete shell install retains the previously controlled version and never claims clients', async () => {
  const w = worker({failPath: '/static/app.js'});
  let install;
  w.handlers.install({waitUntil: promise => install = promise});
  await assert.rejects(install, /Offline fixture/);
  assert.deepEqual(w.requests.filter(x => x.deleted), [{deleted: 'chaptercast-shell-v6'}]);
});

test('a fresh library read supersedes a stale status at the same offset', async () => {
  const source = fs.readFileSync('src/chaptercast/static/app.js', 'utf8');
  const start = source.indexOf('async function refreshLibrary(');
  const end = source.indexOf('refreshButton.addEventListener', start);
  assert.ok(start >= 0 && end > start);

  const pending = [];
  const rendered = [];
  const context = {
    URLSearchParams,
    document: {hidden: true},
    refreshButton: {},
    libraryStatus: {},
    clearTimeout: () => {},
    importRequest: path => new Promise(resolve => pending.push({path, resolve})),
    renderLibrary: data => rendered.push(data),
  };
  vm.runInNewContext(`
    let libraryOffset = 0, libraryBusy = false, libraryPending = null, libraryRevision = 0;
    let pollTimer, selectionRestored = true;
    ${source.slice(start, end)}
  `, context);

  const previous = context.refreshLibrary();
  const fresh = context.refreshLibrary(true);
  assert.equal(pending.length, 1);
  pending[0].resolve({jobs: [{id: 1, status: 'queued'}]});
  await previous;
  assert.deepEqual(rendered, []);

  assert.equal(pending.length, 2);
  assert.equal(pending[1].path, pending[0].path);
  const completed = {jobs: [{id: 1, status: 'completed'}]};
  pending[1].resolve(completed);
  await fresh;
  assert.deepEqual(rendered, [completed]);
});

test('retry and format switching preserve position after a mid-chapter stream error', async () => {
  const source = fs.readFileSync('src/chaptercast/static/app.js', 'utf8');
  const start = source.indexOf('const player = document.getElementById("chapter-player");');
  assert.ok(start >= 0);

  for (const action of ['retry', 'switch format']) {
    const control = () => {
      const listeners = new Map();
      return {
        addEventListener(event, handler) {
          if (!listeners.has(event)) listeners.set(event, []);
          listeners.get(event).push(handler);
        },
        async emit(event) {
          for (const handler of listeners.get(event) || []) await handler();
        },
        setAttribute() {},
      };
    };
    const controls = new Map();
    const get = id => {
      if (!controls.has(id)) controls.set(id, control());
      return controls.get(id);
    };
    const player = Object.assign(get('chapter-player'), {
      currentTime: 0,
      duration: 100,
      readyState: 1,
      paused: true,
      error: null,
      load() { this.error = null; this.currentTime = 0; },
      play() { this.paused = false; },
      pause() { this.paused = true; },
    });
    get('playback-format').value = 'opus';
    get('playback-rate').value = '1';
    const storage = new Map();
    const context = vm.createContext({
      document: {getElementById: get, addEventListener() {}},
      window: {addEventListener() {}},
      previewAudio: Object.assign(control(), {pause() {}}),
      previewStatus: control(),
      clearTimeout() {},
      refreshLibrary() {},
      updateChapterCastMediaSession() {},
      localStorage: {setItem: (key, value) => storage.set(key, value)},
      HTMLMediaElement: {HAVE_METADATA: 1},
      Date: {now: () => 10000},
    });
    vm.runInContext(source.slice(start), context);
    vm.runInContext(`
      selected = {chapter: {id: 1}, job: {id: 2, outputs: [
        {format: "opus", url: "/audio.opus"}, {format: "m4a", url: "/audio.m4a"}
      ]}};
      loadMedia(12);
    `, context);
    await player.emit('loadedmetadata');
    assert.equal(player.currentTime, 12);
    player.currentTime = 67;
    await player.emit('timeupdate');
    player.currentTime = 68;
    await player.emit('timeupdate');
    assert.equal(JSON.parse(storage.get('chaptercast-position-1-job-2')).seconds, 67);

    // Some failed resources reset the media element before dispatching error.
    player.error = {code: 2};
    player.readyState = 0;
    player.currentTime = 0;
    await player.emit('error');
    assert.equal(get('player-toggle').textContent, 'Retry');

    if (action === 'retry') await vm.runInContext('playChapter()', context);
    else {
      get('playback-format').value = 'm4a';
      await get('playback-format').emit('change');
      assert.equal(player.src, '/audio.m4a?inline=true');
    }
    player.readyState = 1;
    await player.emit('loadedmetadata');
    assert.equal(player.currentTime, 68, action);
  }
});
