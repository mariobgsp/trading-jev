// Tokens copied from web/app.css so the video is the same product, not a lookalike.
// Fonts resolve to what is installed locally - nothing is downloaded.

export const T = {
  bg: "#f5f5f6",
  paper: "#ffffff",
  ink: "#0a0a0b",
  ink2: "rgba(10,10,11,.64)",
  ink3: "rgba(10,10,11,.42)",
  ink4: "rgba(10,10,11,.26)",
  hair: "rgba(10,10,11,.085)",
  enter: "#0f6e52",
  skip: "#9a3b2f",
  enterBg: "rgba(15,110,82,.09)",
  skipBg: "rgba(154,59,47,.08)",
  sans: '"Noto Sans", "Liberation Sans", system-ui, sans-serif',
  mono: '"JetBrains Mono", "DejaVu Sans Mono", monospace',
  shadow: "0 2px 4px rgba(10,10,11,.04), 0 24px 60px -20px rgba(10,10,11,.20)",
};

// Real numbers, real session. trading-jev.db, session 20260925: Jev was shown
// 11 names and vetoed all of them.
export const ROWS = [
  { t: "CCSI", p: 0.0,  m: 0.26, c: 0.82 },
  { t: "SAPX", p: 0.07, m: 0.61, c: 1.29 },
  { t: "VICI", p: 0.09, m: 0.63, c: 1.34 },
  { t: "JAWA", p: 0.16, m: 0.63, c: 1.53 },
  { t: "AMRT", p: 0.19, m: 0.64, c: 1.48 },
  { t: "BRMS", p: 0.21, m: 0.66, c: 1.61 },
  { t: "MAPI", p: 0.23, m: 0.67, c: 1.55 },
  { t: "TRIS", p: 0.24, m: 0.68, c: 1.72 },
  { t: "KLBF", p: 0.26, m: 0.70, c: 1.80 },
  { t: "DGPT", p: 0.27, m: 0.71, c: 1.88 },
  { t: "SGRO", p: 0.28, m: 0.72, c: 1.94 },
];

export const CHIPS = [
  "RSI", "MACD", "ADX", "OBV", "ATR", "BBW", "STOCH", "ROC", "VOL", "VWAP",
  "EMA20", "EMA50", "DXY", "SKEW", "TURNOVER", "FREQ", "BETA", "IV",
  "GAP", "SHADOW", "CLOSE", "FLOOR", "BANDS",
];
