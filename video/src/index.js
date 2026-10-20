import React from "react";
import { Composition, registerRoot } from "remotion";
import { Demo } from "./Demo.jsx";
import { FPS, VARIANTS, timeline } from "./timeline.js";

// Both cuts are the same 1080x1920 timeline at a different pace and safe area.
// Instagram Story wears the profile header and reply tray; Reels is watched in
// the feed under a caption and the action rail.
const Root = () => (
  <>
    {VARIANTS.map((variant) => (
      <Composition
        key={variant}
        id={variant === "story" ? "JevStory" : "JevReels"}
        component={Demo}
        durationInFrames={timeline(variant).duration}
        fps={FPS}
        width={1080}
        height={1920}
        defaultProps={{ variant }}
      />
    ))}
  </>
);

registerRoot(Root);
