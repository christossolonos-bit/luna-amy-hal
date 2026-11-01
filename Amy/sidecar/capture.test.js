import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { PassThrough } from "node:stream";
import test from "node:test";
import vm from "node:vm";

test("maximum utterance is sent and the same speaker can be captured again", async () => {
  // Run the actual capture function without connecting the Discord client.
  const source = readFileSync(new URL("./index.js", import.meta.url), "utf8");
  const captureCode = source.slice(source.indexOf("function capture("), source.indexOf("async function join("));
  const sent = [];
  const streams = [];
  const decoders = [];
  const context = vm.createContext({
    Buffer, Date, Math, SILENCE_MS: 900, MIN_UTTERANCE_MS: 0,
    MAX_UTTERANCE_MS: 10, BYTES_PER_MS: 192, LISTEN_TO_BOTS: true,
    EndBehaviorType: { AfterSilence: 1 },
    prism: { opus: { Decoder: class extends PassThrough {
      constructor() { super(); decoders.push(this); }
    } } },
    displayName: async () => ({ name: "Travis", bot: false }),
    downsampleForStt: pcm => pcm,
    send: msg => sent.push(msg), log: () => {},
  });
  vm.runInContext(captureCode, context);
  const session = {
    capturing: new Set(), listening: true, textChannelId: "t1",
    connection: { receiver: { subscribe() {
      const stream = new PassThrough(); streams.push(stream); return stream;
    } } },
  };
  for (let i = 0; i < 2; i++) {
    context.capture(session, { id: "g1" }, "u1");
    streams[i].write(Buffer.alloc(3840)); // exceeds the 10 ms limit in one chunk
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(session.capturing.has("u1"), false);
    assert.equal(streams[i].destroyed, true);
    assert.equal(decoders[i].destroyed, true);
    assert.equal(sent.length, i + 1); // end/close must not duplicate the utterance
    assert.equal(sent[i].durationMs, 10);
    assert.equal(Buffer.from(sent[i].pcm, "base64").length, 1920);
  }
});
