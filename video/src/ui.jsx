import React from "react";
import { Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { T } from "./theme.js";

export const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" };
export const OUT = Easing.bezier(0.32, 0.72, 0, 1);
export const IN_OUT = Easing.bezier(0.4, 0, 0.2, 1);

// Progress of a segment as a pure function of the frame. Nothing here reads a
// clock, so a still at frame N renders exactly what the video shows at frame N.
export const seg = (f, at, dur, easing = OUT) =>
  interpolate(f, [at, at + dur], [0, 1], { ...clamp, easing });

// Opacity in, hold, opacity out - the shape of most beats here.
export const window01 = (f, at, hold, fade = 8) =>
  Math.min(seg(f, at, fade), 1 - seg(f, at + hold, fade));

/* ---------------------------------------------------------------- chrome -- */

export const Chrome = ({ url, w, h, zoom = 1, origin = "50% 42%", children }) => (
  <div
    style={{
      width: w,
      height: h,
      borderRadius: 28,
      overflow: "hidden",
      background: "#1e1e20",
      border: "1px solid rgba(255,255,255,.10)",
      boxShadow: T.shadow,
      transform: "scale(" + zoom + ")",
      transformOrigin: origin,
    }}
  >
    <div
      style={{
        height: 56,
        display: "flex",
        alignItems: "center",
        gap: 9,
        padding: "0 20px",
        background: "#26262b",
      }}
    >
      {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
        <span key={c} style={{ width: 13, height: 13, borderRadius: 7, background: c, flex: "0 0 auto" }} />
      ))}
      <div
        style={{
          marginLeft: 18,
          flex: 1,
          height: 32,
          borderRadius: 10,
          background: "#313138",
          color: "#9aa0a6",
          fontSize: 16,
          fontFamily: T.mono,
          display: "flex",
          alignItems: "center",
          padding: "0 14px",
        }}
      >
        {url}
      </div>
    </div>
    <div style={{ height: h - 56, background: T.bg, position: "relative", overflow: "hidden" }}>
      {children}
    </div>
  </div>
);

/* ------------------------------------------------------------- app chrome -- */

export const AppNav = ({ active = "scan" }) => {
  const tabs = [
    ["scan", "Scan"],
    ["watch", "Watchlist"],
    ["cohort", "Cohort"],
    ["deepdive", "Deepdive"],
  ];
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 22,
        padding: "18px 24px",
        borderBottom: "1px solid " + T.hair,
        background: T.paper,
      }}
    >
      <div style={{ fontFamily: T.mono, fontSize: 17, fontWeight: 700, letterSpacing: "-.01em" }}>
        trading<span style={{ color: T.ink3 }}>-jev</span>
      </div>
      <div style={{ display: "flex", gap: 6, marginLeft: "auto" }}>
        {tabs.map(([id, label]) => (
          <div
            key={id}
            style={{
              padding: "7px 14px",
              borderRadius: 999,
              fontSize: 14,
              fontFamily: T.mono,
              color: id === active ? T.ink : T.ink3,
              background: id === active ? "rgba(10,10,11,.06)" : "transparent",
            }}
          >
            {label}
          </div>
        ))}
      </div>
    </div>
  );
};

export const Pill = ({ label, tone = "skip", w = 78, fs = 13, h = 34 }) => {
  const on = tone === "enter";
  return (
    <div
      style={{
        width: w,
        height: h,
        flex: "0 0 auto",
        borderRadius: 9,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: T.mono,
        fontSize: fs,
        fontWeight: 700,
        letterSpacing: ".09em",
        color: on ? T.enter : T.skip,
        background: on ? T.enterBg : T.skipBg,
        border: "1px solid " + (on ? "rgba(15,110,82,.20)" : "rgba(154,59,47,.18)"),
      }}
    >
      {label}
    </div>
  );
};

export const Meter = ({ v, color = T.skip, h = 7, w = "100%" }) => (
  <div style={{ height: h, width: w, borderRadius: h, background: "rgba(10,10,11,.07)", overflow: "hidden" }}>
    <div style={{ width: Math.round(v * 100) + "%", height: "100%", borderRadius: h, background: color }} />
  </div>
);

/* ------------------------------------------------------------------ rows -- */

// One journalled decision: ticker, p(enter), momentum, conviction, verdict.
export const Row = ({ d, o = 1, dy = 0, stamp = 1 }) => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      gap: 16,
      padding: "14px 22px",
      background: T.paper,
      borderBottom: "1px solid " + T.hair,
      opacity: o,
      transform: "translateY(" + dy + "px)",
    }}
  >
    <div style={{ width: 86, fontFamily: T.mono, fontSize: 20, fontWeight: 650, letterSpacing: ".01em" }}>
      {d.t}
    </div>
    <div style={{ flex: 1, minWidth: 0 }}>
      <Meter v={d.m} />
      <div style={{ marginTop: 7, fontFamily: T.mono, fontSize: 12.5, color: T.ink3, whiteSpace: "nowrap" }}>
        conviction {d.c.toFixed(2)} &nbsp;·&nbsp; risk medium
      </div>
    </div>
    <div
      style={{
        width: 66,
        textAlign: "right",
        fontFamily: T.mono,
        fontSize: 18,
        fontWeight: 600,
        color: d.p < 0.1 ? T.skip : T.ink2,
      }}
    >
      {d.p.toFixed(2)}
    </div>
    <div style={{ opacity: stamp, transform: "scale(" + (0.86 + 0.14 * stamp) + ")" }}>
      <Pill label="SKIP" />
    </div>
  </div>
);

/* --------------------------------------------------------- cursor + call -- */

// The click ripple and the state change it causes fire on the same frame, so
// cause and effect line up instead of drifting a few frames apart.
export const Cursor = ({ x, y, clickAt }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const since = f - clickAt;
  const show = since >= 0 && since < 22;
  const r = show ? spring({ frame: since, fps, config: { damping: 20 }, durationInFrames: 22 }) : 0;
  const size = 14 + r * 84;
  return (
    <>
      {show && (
        <div
          style={{
            position: "absolute",
            left: x - size / 2,
            top: y - size / 2,
            width: size,
            height: size,
            borderRadius: 999,
            border: "3px solid rgba(154,59,47,.9)",
            opacity: 1 - r,
          }}
        />
      )}
      <svg
        width="30"
        height="30"
        viewBox="0 0 28 28"
        style={{
          position: "absolute",
          left: x - 3,
          top: y - 3,
          filter: "drop-shadow(0 3px 6px rgba(10,10,11,.45))",
        }}
      >
        <path
          d="M3 2 L3 21 L9 15 L13 24 L16 23 L12 14 L20 14 Z"
          fill="#ffffff"
          stroke="#0a0a0b"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
      </svg>
    </>
  );
};

export const Svg = ({ w, h, children, style }) => (
  <svg width={w} height={h} style={{ position: "absolute", left: 0, top: 0, ...style }}>
    {children}
  </svg>
);

// Dims the screen and cuts a hole over the focal element.
export const Spotlight = ({ w, h, x, y, rx, ry, k }) => (
  <svg width={w} height={h} style={{ position: "absolute", left: 0, top: 0 }}>
    <defs>
      <mask id="jev-spot">
        <rect width={w} height={h} fill="#000" />
        <ellipse cx={x} cy={y} rx={rx * (0.5 + 0.5 * k)} ry={ry * (0.5 + 0.5 * k)} fill="#fff" />
      </mask>
    </defs>
    <rect width={w} height={h} fill="rgba(10,10,11,.46)" mask="url(#jev-spot)" opacity={k} />
  </svg>
);

// A label plus a connector that grows into the element it points at.
export const Callout = ({ from, to, k, color = T.ink, w = 2.4, label, sub, side = "right" }) => {
  const x = from[0] + (to[0] - from[0]) * k;
  const y = from[1] + (to[1] - from[1]) * k;
  const ang = (Math.atan2(to[1] - from[1], to[0] - from[0]) * 180) / Math.PI;
  return (
    <g opacity={Math.min(1, k * 2)}>
      <line x1={from[0]} y1={from[1]} x2={x} y2={y} stroke={color} strokeWidth={w} strokeLinecap="round" />
      <polygon points="0,0 -14,-6.5 -14,6.5" fill={color} transform={"translate(" + x + " " + y + ") rotate(" + ang + ")"} />
      <text
        x={side === "right" ? from[0] : from[0] - 6}
        y={from[1] - 16}
        textAnchor={side === "right" ? "start" : "end"}
        fontFamily={T.sans}
        fontSize="27"
        fontWeight="650"
        fill={color}
      >
        {label}
      </text>
      {sub && (
        <text
          x={side === "right" ? from[0] : from[0] - 6}
          y={from[1] + 16}
          textAnchor={side === "right" ? "start" : "end"}
          fontFamily={T.mono}
          fontSize="18"
          fill={T.ink3}
        >
          {sub}
        </text>
      )}
    </g>
  );
};

/* ------------------------------------------------------------------ type -- */

export const Eyebrow = ({ children, color = T.ink3, size = 19 }) => (
  <div
    style={{
      fontFamily: T.mono,
      fontSize: size,
      letterSpacing: ".2em",
      textTransform: "uppercase",
      color,
      whiteSpace: "nowrap",
    }}
  >
    {children}
  </div>
);

export const Caption = ({ k, size = 46, sub, color = T.ink, max = 900, lh = 1.16, children }) => (
  <div
    style={{
      opacity: Math.min(1, k * 2.2),
      transform: "translateY(" + (1 - k) * 26 + "px)",
      maxWidth: max,
    }}
  >
    <div
      style={{
        fontFamily: T.sans,
        fontSize: size,
        fontWeight: 650,
        lineHeight: lh,
        letterSpacing: "-.018em",
        color,
      }}
    >
      {children}
    </div>
    {sub && (
      <div
        style={{
          marginTop: 14,
          fontFamily: T.mono,
          fontSize: Math.round(size * 0.4),
          color: T.ink3,
          letterSpacing: ".01em",
        }}
      >
        {sub}
      </div>
    )}
  </div>
);

// Word-by-word reveal, so a line lands like someone said it out loud.
export const Words = ({ f, at, step = 3, size, color = T.ink, weight = 650, lh = 1.14, align = "center", children }) => {
  const words = String(children).trim().split(/\s+/);
  // Normal inline flow with real spaces between the words, not flex items with a
  // gap: a flex gap leaves no break opportunity, so the line refuses to wrap.
  return (
    <div
      style={{
        fontFamily: T.sans,
        fontSize: size,
        fontWeight: weight,
        letterSpacing: "-.018em",
        lineHeight: lh,
        color,
        textAlign: align,
      }}
    >
      {words.map((w, i) => {
        const k = seg(f, at + i * step, 10);
        return (
          <React.Fragment key={i}>
            <span
              style={{
                opacity: k,
                transform: "translateY(" + (1 - k) * 18 + "px)",
                display: "inline-block",
              }}
            >
              {w}
            </span>
            {i < words.length - 1 ? " " : ""}
          </React.Fragment>
        );
      })}
    </div>
  );
};
