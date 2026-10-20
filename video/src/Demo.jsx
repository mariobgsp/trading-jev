import React from "react";
import { Audio, staticFile, useCurrentFrame } from "remotion";
import { CHIPS, ROWS, T } from "./theme.js";
import { SAFE, rowAt, stampAt, timeline } from "./timeline.js";
import {
  AppNav,
  Callout,
  Caption,
  Chrome,
  Cursor,
  Eyebrow,
  Meter,
  Pill,
  Row,
  Svg,
  Words,
  seg,
} from "./ui.jsx";

const W = 1080;
const H = 1920;

const CHROME_W = 960;
const NAV_H = 64;
const HEAD_H = 58;
const CHIPS_H = 150;
const DIV_H = 42;
const ROW_H = 92;

// One transition language for the whole piece: a push. The outgoing shot stays
// opaque until the next one has arrived and fades as it slides, so two shots
// are never both at half strength. Only the last shot fades out.
const PUSH = 12;
const TAIL = 14;

/* --------------------------------------------------------------- layout -- */

const useLayout = (variant) => {
  const S = SAFE[variant];
  const chromeTop = S.top + 26;
  const chromeH = NAV_H + HEAD_H + CHIPS_H + DIV_H + ROW_H * 6 + 56;
  return {
    ...S,
    chromeTop,
    chromeH,
    chromeLeft: (W - CHROME_W) / 2,
    captionTop: chromeTop + chromeH + 54,
  };
};

/* ------------------------------------------------------------ app screen -- */

const AppBody = ({ f, variant, rows, cursor, revealTo = 6 }) => (
  <>
    <AppNav />
    <div
      style={{
        height: HEAD_H,
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "0 22px",
      }}
    >
      <div>
        <div style={{ fontFamily: T.mono, fontSize: 15, color: T.ink3, letterSpacing: ".06em" }}>
          gorengan &middot; session 20260925
        </div>
        <div style={{ fontSize: 23, fontWeight: 650, letterSpacing: "-.02em", marginTop: 3 }}>
          Shortlist
        </div>
      </div>
      <div style={{ marginLeft: "auto", textAlign: "right" }}>
        <div style={{ fontFamily: T.mono, fontSize: 20, fontWeight: 650, color: T.skip }}>
          11 names &middot; 0 enter
        </div>
        <div style={{ fontFamily: T.mono, fontSize: 12.5, color: T.ink3, marginTop: 3 }}>
          23 categories computed in Python
        </div>
      </div>
    </div>

    {/* 23 categories, revealed fast enough to read as a count */}
    <div
      style={{
        height: CHIPS_H,
        padding: "0 22px",
        display: "flex",
        flexWrap: "wrap",
        gap: 9,
        alignContent: "flex-start",
        overflow: "hidden",
      }}
    >
      {CHIPS.map((c, i) => {
        const k = seg(f, 6 + i * 1.4, 9);
        return (
          <div
            key={c}
            style={{
              fontFamily: T.mono,
              fontSize: 15,
              padding: "7px 13px",
              borderRadius: 8,
              color: T.ink2,
              background: T.paper,
              border: "1px solid " + T.hair,
              opacity: k,
              transform: "translateY(" + (1 - k) * 10 + "px)",
            }}
          >
            {c}
          </div>
        );
      })}
    </div>

    <div
      style={{
        height: DIV_H,
        display: "flex",
        alignItems: "center",
        padding: "0 22px",
        borderTop: "1px solid " + T.hair,
        background: T.paper,
      }}
    >
      <div style={{ fontFamily: T.mono, fontSize: 13, letterSpacing: ".18em", color: T.ink3 }}>
        DECISION
      </div>
    </div>

    <div>
      {rows.slice(0, revealTo).map((d, i) => {
        const k = seg(f, rowAt(variant, i), 9);
        const sa = stampAt(variant, i);
        const press = Math.sin(Math.max(0, Math.min(1, (f - sa) / 7)) * Math.PI);
        return (
          <Row
            key={d.t}
            d={d}
            o={k}
            dy={(1 - k) * 22}
            stamp={f >= sa ? 1 - press * 0.18 : 0}
          />
        );
      })}
    </div>

    {cursor && <Cursor x={cursor[0]} y={cursor[1]} clickAt={cursor[2]} />}
  </>
);

/* ------------------------------------------------------------- 1 / hook -- */

const Hook = ({ f, variant }) => {
  const L = useLayout(variant);
  const s = timeline(variant).shot.hook;
  const kA = seg(f, s.at + 4, 12);
  const kB = seg(f, s.at + 26, 12);
  const kC = seg(f, s.at + Math.round(s.len * 0.46), 12);
  const tape = ROWS.map((d) => d.t).join("   ");
  return (
    <div style={{ padding: "0 " + L.side + "px", height: H, display: "flex", flexDirection: "column" }}>
      <div style={{ marginTop: L.top + 40, opacity: kA, transform: "translateY(" + (1 - kA) * 14 + "px)" }}>
        <Eyebrow>IDX &middot; gorengan &middot; 20260925</Eyebrow>
      </div>
      <div style={{ marginTop: 74 }}>
        <Words f={f} at={s.at + 8} step={2} size={86 * L.type} color={T.ink}>
          It was shown eleven stocks.
        </Words>
      </div>
      <div style={{ marginTop: 46, opacity: kB, transform: "translateY(" + (1 - kB) * 20 + "px)" }}>
        <Words f={f} at={s.at + 30} step={2} size={86 * L.type} color={T.skip}>
          It bought none.
        </Words>
      </div>
      <div
        style={{
          marginTop: 64,
          opacity: kC * 0.9,
          transform: "translateY(" + (1 - kC) * 16 + "px)",
          fontFamily: T.mono,
          fontSize: 21 * L.type,
          color: T.ink3,
          letterSpacing: ".16em",
          lineHeight: 1.9,
        }}
      >
        {tape}
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ paddingBottom: L.bottom + 30 + "px", opacity: kC }}>
        <div style={{ fontFamily: T.sans, fontSize: 40 * L.type, fontWeight: 650, letterSpacing: "-.02em" }}>
          Then it wrote down why.
        </div>
      </div>
    </div>
  );
};

/* ----------------------------------------------------------- 2 / screen -- */

const Screen = ({ f, variant }) => {
  const L = useLayout(variant);
  const s = timeline(variant).shot.screen;
  const k = seg(f, s.at + 2, 14);
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: L.chromeLeft,
          top: L.chromeTop,
          opacity: k,
          transform: "translateY(" + (1 - k) * 30 + "px)",
        }}
      >
        <Chrome url="localhost:8765/scan" w={CHROME_W} h={L.chromeH}>
          <AppBody f={f} variant={variant} rows={ROWS} />
        </Chrome>
      </div>
      <div
        style={{
          position: "absolute",
          left: L.side + "px",
          right: L.side + "px",
          top: L.captionTop,
          opacity: seg(f, s.at + 18, 12),
        }}
      >
        <Caption k={seg(f, s.at + 18, 12)} size={50 * L.type}>
          Python screens 23 categories. Jev picks the shortlist.
        </Caption>
      </div>
    </>
  );
};

/* ---------------------------------------------------------- 3 / verdict -- */

const Verdict = ({ f, variant }) => {
  const L = useLayout(variant);
  const s = timeline(variant).shot.verdict;
  const local = f - s.at;
  const CARD_W = 900;
  const cardLeft = (W - CARD_W) / 2;
  const cardTop = L.top + 380;
  // The same first row of the shortlist, blown up enough to read on a phone.
  // Zooming the whole window just shrinks the type and collides with the
  // caption, so the shot is the row itself.
  const pillX = cardLeft + CARD_W - 40 - 75;
  const pillY = cardTop + 65;
  const clickAt = s.at + 30;
  const travel = seg(local, 14, 14);
  const cx = cardLeft + 120 + (pillX - 40 - (cardLeft + 120)) * travel;
  const cy = cardTop + 300 + (pillY - (cardTop + 300)) * travel;
  const press = Math.sin(Math.max(0, Math.min(1, (f - clickAt) / 8)) * Math.PI);
  const stamped = f >= clickAt ? 1 - press * 0.16 : 0;
  const kCard = seg(f, s.at, 14);
  const kCall = seg(f, s.at + 36, 14);
  const kCap = seg(f, s.at + 52, 14);
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: cardLeft,
          top: cardTop,
          width: CARD_W,
          opacity: kCard,
          transform: "translateY(" + (1 - kCard) * 26 + "px)",
        }}
      >
        <div
          style={{
            background: T.paper,
            border: "1px solid " + T.hair,
            borderRadius: 22,
            padding: "38px 40px",
            boxShadow: T.shadow,
          }}
        >
          <div style={{ display: "flex", alignItems: "center" }}>
            <div style={{ fontFamily: T.mono, fontSize: 64, fontWeight: 650, letterSpacing: "-.01em" }}>
              CCSI
            </div>
            <div style={{ marginLeft: "auto", transform: "scale(" + (0.92 + 0.08 * stamped) + ")" }}>
              <Pill label="SKIP" w={150} fs={21} h={54} />
            </div>
          </div>
          <div style={{ marginTop: 28 }}>
            <Meter v={0.26} h={16} />
          </div>
          <div style={{ marginTop: 24, display: "flex", alignItems: "baseline" }}>
            <div style={{ fontFamily: T.mono, fontSize: 22, color: T.ink3 }}>
              conviction 0.82 &nbsp;&middot;&nbsp; medium risk
            </div>
            <div
              style={{
                marginLeft: "auto",
                fontFamily: T.mono,
                fontSize: 60,
                fontWeight: 650,
                color: T.skip,
              }}
            >
              0.00
            </div>
          </div>
        </div>
      </div>

      {/* label and connector sit below the card, so neither is covered by it */}
      <Svg w={W} h={H}>
        <Callout
          from={[cardLeft + 180, cardTop + 400]}
          to={[cardLeft + 700, cardTop + 200]}
          k={kCall}
          color={T.skip}
          label="p(enter) 0.00"
          sub="below the gate: no stop, no target"
          side="right"
        />
      </Svg>

      <Cursor x={cx} y={cy} clickAt={clickAt} />

      <div
        style={{
          position: "absolute",
          left: L.side + "px",
          right: L.side + "px",
          top: cardTop + 540 + "px",
          opacity: kCap,
        }}
      >
        <Caption k={kCap} size={50 * L.type}>
          It scores every name. ENTER, or SKIP.
        </Caption>
      </div>
    </>
  );
};

/* ----------------------------------------------------------- 4 / tally -- */

// Smoothstep, so the count-up accelerates and settles instead of ticking linearly.
const countTo = (f, a, b, v) => {
  const t = Math.max(0, Math.min(1, (f - a) / (b - a)));
  return t * t * (3 - 2 * t) * v;
};

const Tally = ({ f, variant }) => {
  const L = useLayout(variant);
  const s = timeline(variant).shot.tally;
  const line = (label, value, at, color) => {
    const k = seg(f, s.at + at, 14);
    return (
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 26,
          opacity: k,
          transform: "translateY(" + (1 - k) * 24 + "px)",
        }}
      >
        <div
          style={{
            fontFamily: T.mono,
            fontSize: 128 * L.type,
            fontWeight: 700,
            letterSpacing: "-.04em",
            color,
            lineHeight: 1,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {Math.round(countTo(f, s.at + at, s.at + at + 20, value))}
        </div>
        <div style={{ fontFamily: T.sans, fontSize: 40 * L.type, fontWeight: 600, color: T.ink2 }}>
          {label}
        </div>
      </div>
    );
  };
  const kCap = seg(f, s.at + 44, 14);
  return (
    <div
      style={{
        padding: "0 " + L.side + "px",
        height: H,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
      }}
    >
      <div style={{ opacity: seg(f, s.at + 2, 12) }}>
        <Eyebrow>session 20260925 &middot; one journal</Eyebrow>
      </div>
      <div style={{ marginTop: 64, display: "flex", flexDirection: "column", gap: 40 }}>
        {line("shown", 11, 8, T.ink)}
        {line("SKIP", 11, 20, T.skip)}
        {line("ENTER", 0, 32, T.ink4)}
      </div>
      <div style={{ marginTop: 78, opacity: kCap, transform: "translateY(" + (1 - kCap) * 22 + "px)" }}>
        <Caption k={kCap} size={52 * L.type}>
          Zero buys. That is the correct output, not a bug.
        </Caption>
      </div>
    </div>
  );
};

/* --------------------------------------------------------- 5 / journal -- */

const JOURNAL = [
  ["CCSI", "0.00", "SKIP", "—"],
  ["SAPX", "0.07", "SKIP", "—"],
  ["VICI", "0.09", "SKIP", "—"],
  ["JAWA", "0.16", "SKIP", "—"],
  ["HYGN", "—", "DROPPED", "ADV20 0.80bn"],
];

const Journal = ({ f, variant }) => {
  const L = useLayout(variant);
  const s = timeline(variant).shot.journal;
  const k = seg(f, s.at + 2, 14);
  const kCap = seg(f, s.at + 34, 14);
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: L.chromeLeft,
          top: L.chromeTop,
          opacity: k,
          transform: "translateY(" + (1 - k) * 30 + "px)",
        }}
      >
        <Chrome url="localhost:8765/cohort" w={CHROME_W} h={L.chromeH}>
          <AppNav active="cohort" />
          <div style={{ height: HEAD_H, display: "flex", alignItems: "center", padding: "0 22px" }}>
            <div>
              <div style={{ fontFamily: T.mono, fontSize: 15, color: T.ink3, letterSpacing: ".06em" }}>
                journal &middot; keyed on (session, ticker)
              </div>
              <div style={{ fontSize: 23, fontWeight: 650, letterSpacing: "-.02em", marginTop: 3 }}>
                Every decision, ENTER and SKIP
              </div>
            </div>
          </div>

          <div style={{ padding: "0 22px" }}>
            <div
              style={{
                display: "flex",
                padding: "14px 0 10px",
                fontFamily: T.mono,
                fontSize: 13,
                letterSpacing: ".16em",
                color: T.ink3,
                borderBottom: "1px solid " + T.hair,
              }}
            >
              <div style={{ flex: 1 }}>TICKER</div>
              <div style={{ width: 150 }}>p(ENTER)</div>
              <div style={{ width: 190 }}>VERDICT</div>
              <div style={{ flex: 1, textAlign: "right" }}>OUTCOME</div>
            </div>
            {JOURNAL.map((row, i) => {
              const rk = seg(f, s.at + 12 + i * 5, 9);
              return (
                <div
                  key={row[0]}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    padding: "16px 0",
                    borderBottom: "1px solid " + T.hair,
                    opacity: rk,
                    transform: "translateX(" + (1 - rk) * 18 + "px)",
                  }}
                >
                  <div style={{ flex: 1, fontFamily: T.mono, fontSize: 19, fontWeight: 650 }}>
                    {row[0]}
                  </div>
                  <div style={{ width: 150, fontFamily: T.mono, fontSize: 18, color: T.ink2 }}>
                    {row[1]}
                  </div>
                  <div style={{ width: 190 }}>
                    <Pill label={row[2]} w={110} />
                  </div>
                  <div
                    style={{
                      flex: 1,
                      textAlign: "right",
                      fontFamily: T.mono,
                      fontSize: 15,
                      color: T.ink3,
                    }}
                  >
                    {row[3]}
                  </div>
                </div>
              );
            })}
          </div>

          {/* the honest panel: no ENTER, so no performance figure exists yet */}
          <div
            style={{
              margin: "26px 22px 0",
              padding: "22px 24px",
              borderRadius: 16,
              background: T.paper,
              border: "1px solid " + T.hair,
            }}
          >
            <Eyebrow size={15}>performance</Eyebrow>
            <div style={{ marginTop: 12, fontFamily: T.mono, fontSize: 34, fontWeight: 650 }}>
              n/a
            </div>
            <div style={{ marginTop: 8, fontSize: 19, color: T.ink3, lineHeight: 1.35 }}>
              No ENTER yet, so there is nothing to score. The journal waits.
            </div>
          </div>
        </Chrome>
      </div>

      <div
        style={{
          position: "absolute",
          left: L.side + "px",
          right: L.side + "px",
          top: L.captionTop,
          opacity: kCap,
        }}
      >
        <Caption k={kCap} size={50 * L.type}>
          It logs the no, so it has to earn the yes.
        </Caption>
      </div>
    </>
  );
};

/* ----------------------------------------------------------- 6 / close -- */

const Close = ({ f, variant }) => {
  const L = useLayout(variant);
  const s = timeline(variant).shot.close;
  const kA = seg(f, s.at + 2, 14);
  const kB = seg(f, s.at + 18, 14);
  const kC = seg(f, s.at + 38, 14);
  return (
    <div
      style={{
        padding: "0 " + L.side + "px",
        height: H,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
      }}
    >
      <div style={{ opacity: kA }}>
        <Eyebrow>trading-jev</Eyebrow>
      </div>
      <div style={{ marginTop: 46 }}>
        <Words f={f} at={s.at + 6} step={2} size={80 * L.type}>
          A model that has to prove it was right.
        </Words>
      </div>
      <div style={{ marginTop: 44, opacity: kB, transform: "translateY(" + (1 - kB) * 20 + "px)" }}>
        <div style={{ fontFamily: T.sans, fontSize: 40 * L.type, fontWeight: 550, color: T.ink2, lineHeight: 1.28 }}>
          No orders are placed. A human presses the buy.
        </div>
      </div>
      <div
        style={{
          marginTop: 60,
          display: "inline-flex",
          alignSelf: "flex-start",
          alignItems: "center",
          padding: "18px 32px",
          borderRadius: 999,
          background: T.ink,
          color: "#fff",
          opacity: kC,
          transform: "translateY(" + (1 - kC) * 18 + "px)",
        }}
      >
        <div style={{ fontFamily: T.sans, fontSize: 32 * L.type, fontWeight: 650 }}>
          Follow the journal
        </div>
      </div>
    </div>
  );
};

/* ---------------------------------------------------------------- shell -- */

const SHOT = { hook: Hook, screen: Screen, verdict: Verdict, tally: Tally, journal: Journal, close: Close };

export const Demo = ({ variant }) => {
  const f = useCurrentFrame();
  const tl = timeline(variant);
  return (
    <div style={{ position: "relative", width: W, height: H, background: T.bg, overflow: "hidden" }}>
      {tl.shots.map((s, i) => {
        const C = SHOT[s.id];
        const next = tl.shots[i + 1];
        const kIn = seg(f, s.at, PUSH);
        const kOut = next ? seg(f, next.at, PUSH) : seg(f, tl.duration - TAIL, TAIL);
        const live = f >= s.at ? 1 - kOut : 0;
        return (
          <div
            key={s.id}
            style={{
              position: "absolute",
              inset: 0,
              opacity: live,
              transform: "translateY(" + (live ? 0 : (1 - kIn) * 80) - kOut * 80 + "px)",
            }}
          >
            <C f={f} variant={variant} />
          </div>
        );
      })}
      <Audio src={staticFile("audio/" + variant + ".wav")} />
    </div>
  );
};
