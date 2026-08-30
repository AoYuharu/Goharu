import React from "react";
import { render } from "ink";
import { App } from "./components/App.js";

const useMock = process.argv.includes("--mock");

// Clear BEFORE Ink's first render so the app owns the full screen and the
// mouse hit-map can safely assume the viewport starts at terminal row 1.
process.stdout.write("\x1b[2J\x1b[3J\x1b[H");

const { waitUntilExit } = render(<App mock={useMock} />, { exitOnCtrlC: false });

waitUntilExit().then(() => process.exit(0));
