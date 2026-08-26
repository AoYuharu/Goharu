import React from "react";
import { render } from "ink";
import { App } from "./components/App.js";

const useMock = process.argv.includes("--mock");

const { waitUntilExit } = render(<App mock={useMock} />, { exitOnCtrlC: false });

waitUntilExit().then(() => process.exit(0));
