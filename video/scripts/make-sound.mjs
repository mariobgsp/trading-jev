// Synthesises the two beds locally - no samples, no network, no dependency.
// Deterministic: a seeded noise source, so re-running gives byte-identical WAVs.
// Reads src/timeline.js, so the SKIP click lands on the frame the pill lands on.
//
//   node scripts/make-sound.mjs

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { NAMES, cuts, stampAt, timeline } from "../src/timeline.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "../public/audio");

const SR = 44100;
const BPM = 92;
const BEAT = 60 / BPM;
const BAR = BEAT * 4;

const mtof = (m) => 440 * 2 ** ((m - 69) / 12);

let seed = 20260925;
const rnd = () => {
  seed = (seed * 1103515245 + 12345) & 0x7fffffff;
  return (seed / 0x7fffffff) * 2 - 1;
};

/* ------------------------------------------------------------- envelopes -- */

const env = (t, a, d, sus, dur, rel) => {
  if (t < 0 || t > dur + rel) return 0;
  if (t < a) return t / a;
  if (t < a + d) return 1 + (sus - 1) * ((t - a) / d);
  if (t < dur) return sus;
  return sus * Math.max(0, 1 - (t - dur) / rel);
};

const osc = (ph, type) => {
  const p = ph % 1;
  if (type === "sine") return Math.sin(2 * Math.PI * p);
  if (type === "tri") return 4 * Math.abs(p - 0.5) - 1;
  if (type === "saw") return 2 * p - 1;
  return 0;
};

const put = (buf, j, v) => {
  if (j >= 0 && j < buf.length) buf[j] += v;
};

/* ---------------------------------------------------------------- voices -- */

function tone(buf, t0, dur, midi, o = {}) {
  const { type = "sine", gain = 0.2, a = 0.005, d = 0.14, sus = 0, rel = 0.14, lp = 0, detune = 0 } = o;
  const i0 = Math.round(t0 * SR);
  const n = Math.round((dur + rel) * SR);
  const inc = mtof(midi) / SR;
  const k = lp ? 1 - Math.exp((-2 * Math.PI * lp) / SR) : 0;
  let ph = 0;
  let y = 0;
  for (let i = 0; i < n; i++) {
    const t = i / SR;
    let s = osc(ph, type);
    if (k) {
      y += k * (s - y);
      s = y;
    }
    put(buf, i0 + i, s * env(t, a, d, sus, dur, rel) * gain);
    ph += inc * (1 + detune);
  }
}

function kick(buf, t0, gain) {
  const i0 = Math.round(t0 * SR);
  const n = Math.round(0.36 * SR);
  let ph = 0;
  for (let i = 0; i < n; i++) {
    const t = i / SR;
    ph += (2 * Math.PI * (46 + 92 * Math.exp(-t / 0.026))) / SR;
    put(buf, i0 + i, Math.sin(ph) * Math.exp(-t / 0.085) * gain);
  }
}

function hat(buf, t0, gain, decay = 0.032) {
  const i0 = Math.round(t0 * SR);
  const n = Math.round(0.14 * SR);
  let prev = 0;
  let hp = 0;
  for (let i = 0; i < n; i++) {
    const x = rnd() - prev;
    prev = rnd();
    hp += x * 0.5;
    put(buf, i0 + i, (hp - x * 0.5) * Math.exp(-(i / SR) / decay) * gain);
  }
}

// The UI click: a bright noise tick with a short pitched body.
function stamp(buf, t0, gain) {
  const i0 = Math.round(t0 * SR);
  const n = Math.round(0.1 * SR);
  const k = 1 - Math.exp((-2 * Math.PI * 2600) / SR);
  let prev = 0;
  let lp = 0;
  for (let i = 0; i < n; i++) {
    const t = i / SR;
    const x = rnd() - prev;
    prev = rnd();
    lp += k * (x - lp);
    const body = Math.sin(2 * Math.PI * 1180 * t);
    put(buf, i0 + i, (lp * 0.55 + body * 0.45) * Math.exp(-t / 0.021) * gain);
  }
}

// A cut in the edit gets a low boom under it.
function impact(buf, t0, gain) {
  const i0 = Math.round(t0 * SR);
  const n = Math.round(1.1 * SR);
  let ph = 0;
  for (let i = 0; i < n; i++) {
    const t = i / SR;
    const f = 70 * Math.exp(-t / 0.09) + 32;
    ph += (2 * Math.PI * f) / SR;
    const sweep = Math.exp(-t / 0.16) * rnd() * 0.12 * (t / 0.16);
    put(buf, i0 + i, (Math.sin(ph) * 0.8 + sweep) * Math.exp(-t / 0.30) * gain);
  }
}

/* ------------------------------------------------------------------ wav -- */

function writeWav(path, samples) {
  const n = samples.length;
  const b = Buffer.alloc(44 + n * 2);
  b.write("RIFF", 0);
  b.writeUInt32LE(36 + n * 2, 4);
  b.write("WAVE", 8);
  b.write("fmt ", 12);
  b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20);
  b.writeUInt16LE(1, 22);
  b.writeUInt32LE(SR, 24);
  b.writeUInt32LE(SR * 2, 28);
  b.writeUInt16LE(2, 32);
  b.writeUInt16LE(16, 34);
  b.write("data", 36);
  b.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) {
    b.writeInt16LE(Math.round(Math.max(-1, Math.min(1, samples[i])) * 32000), 44 + i * 2);
  }
  writeFileSync(path, b);
}

/* ---------------------------------------------------------------- score -- */

// i - VI - III - VII, two bars each. Pads, root bass, a plucked arp, hats.
const PROG = [
  { pad: [57, 60, 64], bass: 33, arp: [69, 72, 76, 81, 84] },
  { pad: [53, 57, 60], bass: 29, arp: [65, 69, 72, 77, 81] },
  { pad: [55, 60, 64], bass: 36, arp: [72, 76, 79, 84, 88] },
  { pad: [55, 59, 62], bass: 31, arp: [67, 71, 74, 79, 83] },
];
const ARP = [0, 1, 2, 3, 4, 3, 2, 1];
const BASS_HITS = [0, 0.75, 2, 2.75];
const ARC = [0.72, 0.82, 0.94, 1.0];

function render(variant) {
  const { duration } = timeline(variant);
  const fps = 30;
  const secs = duration / fps;
  const buf = new Float32Array(Math.ceil(secs * SR));
  const bars = Math.ceil(secs / BAR);

  // Everything after the midpoint gets a brighter, louder arrangement.
  const lift = cuts(variant).map((f) => f / fps / BAR);

  for (let bar = 0; bar < bars; bar++) {
    const t0 = bar * BAR;
    if (t0 > secs) break;
    const ch = PROG[Math.floor(bar / 2) % 4];
    const up = lift.some((b) => bar >= b);
    const arc = (ARC[Math.floor(bar / 2) % 4] ?? 1) * (up ? 1.16 : 1);

    // pad: detuned saws, low-passed, held for the bar
    for (const m of ch.pad) {
      tone(buf, t0, BAR * 0.92, m, { type: "saw", gain: 0.052 * arc, a: 0.5, d: 0.5, sus: 0.75, rel: 0.7, lp: 900 });
      tone(buf, t0, BAR * 0.92, m, { type: "saw", gain: 0.052 * arc, a: 0.5, d: 0.5, sus: 0.75, rel: 0.7, lp: 900, detune: 0.004 });
    }

    // bass: root, on 1 and the "and of 2"
    for (const h of BASS_HITS) {
      tone(buf, t0 + h * BEAT, BEAT * 0.45, ch.bass, { type: "sine", gain: 0.3 * arc, a: 0.008, d: 0.18, sus: 0.35, rel: 0.2, lp: 260 });
    }

    // kick on 1 and 3
    kick(buf, t0, 0.42 * arc);
    kick(buf, t0 + 2 * BEAT, 0.36 * arc);

    // arp on eighths, hats on the offbeat
    for (let e = 0; e < 8; e++) {
      const t = t0 + e * BEAT * 0.5;
      if (t > secs) break;
      tone(buf, t, 0.16, ch.arp[ARP[e]], { type: "tri", gain: (up ? 0.15 : 0.115) * arc, a: 0.004, d: 0.13, sus: 0, rel: 0.12, lp: 3200 });
      if (e % 2 === 1 || up) hat(buf, t, (up ? 0.05 : 0.036) * arc);
    }
  }

  // the edit cuts, and the clicks that match the verdict pills
  for (const f of cuts(variant)) impact(buf, (f - 4) / 30, 0.5);
  for (let i = 0; i < NAMES; i++) stamp(buf, stampAt(variant, i) / 30, 0.3);

  // trim, fade, soft clip
  const fadeIn = Math.round(0.12 * SR);
  const fadeOut = Math.round(1.1 * SR);
  for (let i = 0; i < buf.length; i++) {
    let g = 1;
    if (i < fadeIn) g *= i / fadeIn;
    if (i > buf.length - fadeOut) g *= Math.max(0, (buf.length - i) / fadeOut);
    buf[i] = Math.tanh(buf[i] * g * 1.25 * 1.05);
  }

  mkdirSync(OUT, { recursive: true });
  const path = resolve(OUT, variant + ".wav");
  writeWav(path, buf);
  return { path, secs: buf.length / SR };
}

for (const v of ["story", "reels"]) {
  const r = render(v);
  console.log(v.padEnd(6), r.path, r.secs.toFixed(2) + "s");
}
