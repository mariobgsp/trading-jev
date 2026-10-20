// One timeline, read by both the Remotion compositions and the synth script
// (scripts/make-sound.mjs), so a click ripple and its sound land on the same frame.

export const FPS = 30;

// Length of each shot in the Story cut.
const STORY = { hook: 84, screen: 168, verdict: 120, tally: 108, journal: 138, close: 84 };

// Reels is the same story with tighter dwell: the feed is watched small, so
// cuts land faster and nothing waits to be read.
const REELS = { hook: 56, screen: 108, verdict: 84, tally: 78, journal: 96, close: 62 };

const ORDER = ["hook", "screen", "verdict", "tally", "journal", "close"];

export const LENS = { story: STORY, reels: REELS };

// Absolute frame ranges per shot, plus the cut lengths as the total duration.
export const timeline = (variant) => {
  const lens = LENS[variant];
  let at = 0;
  const shots = ORDER.map((id) => {
    const s = { id, at, len: lens[id] };
    at += lens[id];
    return s;
  });
  return { shots, duration: at, shot: Object.fromEntries(shots.map((s) => [s.id, s])) };
};

// Instagram Story is watched in segments behind the progress bar and the
// reply tray; Reels is watched in the feed under a caption and the action
// rail. Different safe bands, so different layouts.
export const SAFE = {
  story: { top: 214, bottom: 306, side: 76, type: 1.0 },
  reels: { top: 150, bottom: 430, side: 96, type: 1.16 },
};

export const VARIANTS = Object.keys(LENS);

// Inside the screen shot the 11 names stream in and each one takes a verdict.
// The synth script stamps a click on exactly these frames, so the sound and
// the SKIP pill land together instead of drifting.
export const NAMES = 11;

export const rowStep = (variant) =>
  Math.max(5, Math.round((LENS[variant].screen - 52) / NAMES));

export const rowAt = (variant, i) =>
  timeline(variant).shot.screen.at + 26 + i * rowStep(variant);

export const stampAt = (variant, i) =>
  rowAt(variant, i) + Math.round(rowStep(variant) * 0.62);

// Shot boundaries, as frame numbers. The bed lifts on these.
export const cuts = (variant) => timeline(variant).shots.slice(1).map((s) => s.at);

