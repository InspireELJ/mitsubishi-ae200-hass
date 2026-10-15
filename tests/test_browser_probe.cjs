// Offline browser API simulation. Not a real browser or controller test.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { DOMParser } = require(process.env.AE200_QA_XMLDOM || '@xmldom/xmldom');
const source = fs.readFileSync(path.join(__dirname, '../tools/ae200_readonly_browser.js'), 'utf8');
const groupList = '<Packet><Command>getResponse</Command><DatabaseManager><ControlGroup><MnetList><MnetRecord Group="1"/><MnetRecord Group="2"/><MnetRecord Group="3"/></MnetList></ControlGroup></DatabaseManager></Packet>';

function browser(reply) {
  let elapsed = 0;
  const sends = [], gets = [], downloads = [], blobs = new Map();
  class Clock extends Date { static now() { return Date.now() + elapsed; } }
  class TestURL extends URL {
    static createObjectURL(blob) { const id = `blob:${blobs.size}`; blobs.set(id, blob); return id; }
    static revokeObjectURL(id) { blobs.delete(id); }
  }
  class Socket {
    constructor(url, protocol) {
      assert.equal(url, 'ws://controller.test/b_xmlproc/');
      assert.equal(protocol, 'b_xmlproc');
      queueMicrotask(() => this.onopen?.());
    }
    send(payload) {
      sends.push(payload);
      const answer = reply(payload, sends.length);
      queueMicrotask(() => answer === null ? this.onerror?.() : this.onmessage?.({ data: answer }));
    }
    close() { this.onclose?.(); }
  }
  const env = {
    DOMParser, Date: Clock, URL: TestURL, WebSocket: Socket, Blob, AbortSignal,
    location: { protocol: 'http:', host: 'controller.test', origin: 'http://controller.test' },
    console: { info() {}, warn() {} },
    setTimeout(fn, delay) {
      if (delay <= 1100) queueMicrotask(() => { elapsed += delay; fn(); });
      return 1;
    },
    clearTimeout() {},
    fetch: async (url, init) => {
      gets.push({ url, init });
      return { status: 200, headers: { get: () => 'text/html' }, text: async () => url.includes('init/') ? '<applet></applet>' : '<html></html>' };
    },
    document: {
      body: { appendChild() {} },
      createElement: () => ({ click() { downloads.push(blobs.get(this.href)); }, remove() {} }),
    },
  };
  env.window = env;
  return { env, sends, gets, downloads, run: () => vm.runInNewContext(source, env) };
}

test('probe sends only known reads and exports configured groups', async () => {
  const b = browser((xml, n) => {
    if (n === 1) return groupList;
    const gid = new DOMParser().parseFromString(xml, 'application/xml').getElementsByTagName('Mnet')[0].getAttribute('Group');
    return `<Packet><Command>getResponse</Command><DatabaseManager><Mnet Group="${gid}" Drive="OFF" InletHumidity="0.0" FanSpeed="MID2" Password="private-value" /></DatabaseManager></Packet>`;
  });
  await b.run();
  assert.equal(b.gets.length, 3);
  assert.ok(b.gets.every(x => x.init.method === 'GET' && x.init.credentials === 'omit'));
  assert.equal(b.sends.length, 4);
  assert.ok(b.sends.every(x => x.includes('<Command>getRequest</Command>') && !x.includes('setRequest')));
  const text = await b.downloads[0].text(), data = JSON.parse(text);
  assert.deepEqual(Object.keys(data.groups), ['1', '2', '3']);
  assert.ok(!text.includes('private-value'));
  assert.equal(data.groups['1'].raw_attributes.InletHumidity, '0.0');
  assert.equal(data.pages[2].has_java_markup, true);
  assert.equal(b.env.__ae200ReadProbe, false);
});

test('failed discovery exports evidence and never scans group slots', async () => {
  const b = browser(() => '<Packet><Command>getErrorResponse</Command><DatabaseManager><ERROR Code="0201" /></DatabaseManager></Packet>');
  await b.run();
  const data = JSON.parse(await b.downloads[0].text());
  assert.equal(b.sends.length, 1);
  assert.deepEqual(data.groups, {});
  assert.ok(data.errors[0].error.includes('0201'));
});

test('two consecutive failed group reads stop the probe', async () => {
  const b = browser((_, n) => n === 1 ? groupList : null);
  await b.run();
  const data = JSON.parse(await b.downloads[0].text());
  assert.equal(b.sends.length, 3);
  assert.equal(data.errors.length, 3);
  assert.equal(b.env.__ae200ReadProbe, false);
});

test('duplicate running guard prevents a second probe', async () => {
  const b = browser(() => { throw new Error('No traffic expected'); });
  b.env.__ae200ReadProbe = true;
  await b.run();
  assert.equal(b.sends.length, 0);
  assert.equal(b.gets.length, 0);
});
