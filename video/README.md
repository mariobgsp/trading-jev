# video

Two 9:16 cuts of the web app, built entirely from vector shapes. No screenshots,
no screen recorder, no downloaded assets: the UI is redrawn in JSX from the same
tokens as `web/app.css`, and the soundtrack is synthesised by a script in this
folder.

| Composition | Frames | Length | Safe area |
| --- | --- | --- | --- |
| `JevStory`  | 702 | 23.4s | 214 top / 306 bottom — Instagram Story |
| `JevReels`  | 484 | 16.1s | 150 top / 430 bottom — Reels feed |

Both are 1080x1920 at 30fps. The cut is the same story at a different pace:
Reels dwells less on each beat and sets type 16% larger, because the feed is
watched small and unattended.

## The story it tells

The app is one session deep and has zero ENTER decisions — Jev was shown 11 names
and vetoed all of them. That is the honest hook, so the video uses it: 11 shown,
11 SKIP, 0 ENTER, and a journal that says `n/a` because there is nothing to score
yet. The numbers are the real ones out of `trading-jev.db`.

## Build

```bash
npm install
npm run sound                     # writes public/audio/{story,reels}.wav
npm run studio                    # preview
npm run render JevStory out/trading-jev-story.mp4
npm run render JevReels  out/trading-jev-reels.mp4
```

`npm run sound` must run before the first render; the WAVs are gitignored.

## Layout

- `src/timeline.js` — shot lengths, safe areas, and the frame each verdict pill
  stamps on. Read by both the compositions and the synth, so the click and the
  pill cannot drift apart.
- `src/theme.js` — colour and type tokens copied from `web/app.css`, plus the
  eleven real rows.
- `src/ui.jsx` — vector primitives: browser chrome, app nav, row, pill, meter,
  cursor with its click ripple, spotlight, callout.
- `src/Demo.jsx` — the six shots and the transition shell.
- `scripts/make-sound.mjs` — the synth. Seeded noise, so re-running gives
  byte-identical output.
- `scripts/check-stills.py` — pixel checks on rendered stills.
- `scripts/probe-mp4.sh` — asserts the encode: size, fps, frame count, audio.

## Verifying

```bash
npm run render JevStory out/f.png -- --frame=300   # or: npx remotion still
python3 scripts/check-stills.py "out/*.png"
./scripts/probe-mp4.sh out/trading-jev-story.mp4 1080x1920 30
```

`check-stills.py` reports ink coverage, overall contrast, the bounding box of
dark pixels, and whether the skip/enter palette colours reached the frame. Its
`strength` column is mean deviation from the page background, which stays
meaningful through the low-opacity middle of a transition — a hard ink threshold
reads those frames as blank when they are not.

## Conventions worth keeping

- Every animated value is a pure function of `useCurrentFrame()`. No CSS
  transitions, timers, `Date.now()` or `Math.random()` in a component, so a
  still at frame N is exactly what the video shows at frame N.
- One transition language: a 12-frame push. The outgoing shot stays opaque until
  the next one has arrived and fades as it slides, so two shots are never both
  at half strength. The last shot fades out over 14 frames.
- The spotlight, cursor and callout live inside the zoomed container in the
  verdict shot, so they stay locked to the row as the scale changes.
