const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const events = [];
const listeners = {};
const window = {
    location: new URL('https://totish.ru/'),
    history: {},
    fetch: async (url, options) => {
        events.push(JSON.parse(options.body));
        return { ok: true };
    },
    addEventListener: (name, callback) => { listeners[name] = callback; },
};
for (const name of ['pushState', 'replaceState']) {
    window.history[name] = (state, title, url) => {
        if (url) window.location = new URL(url, window.location);
    };
}
const document = {
    getElementById: () => null,
    querySelector: () => ({ content: 'test-csrf' }),
};
const sessionStorage = { getItem: () => null, removeItem: () => {} };
const context = vm.createContext({ window, document, sessionStorage, console, URL });
const script = fs.readFileSync('static/js/splash.js', 'utf8');
vm.runInContext(script, context);
assert.deepEqual(events.map(e => e.properties.pathname), ['/']);
listeners.pageshow({ persisted: false });
assert.equal(events.length, 1, 'initial pageshow must not double count');
window.history.pushState({}, '', '/table?tid=5');
window.history.pushState({}, '', '/table?tid=6');
window.history.replaceState({}, '', '/table?tid=6');
window.history.replaceState({}, '', '/');
window.location = new URL('https://totish.ru/table?tid=6');
listeners.popstate({});
listeners.pageshow({ persisted: true });
vm.runInContext(script, context);
assert.deepEqual(events.map(e => e.properties.pathname), ['/', '/table', '/table', '/', '/table', '/table']);
assert.ok(events.every(e => e.event === '$pageview'));
assert.ok(events.every(e => Object.keys(e.properties).join() === 'pathname'));
console.log('analytics navigation OK');
