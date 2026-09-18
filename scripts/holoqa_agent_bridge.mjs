#!/usr/bin/env node

// Lightweight client-side monitor bridge. The AI coder owns agent-browser;
// this process only forwards heartbeats, screenshots, and browser URL state.
import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

const args = Object.fromEntries(process.argv.slice(2).map((value, index, list) => value.startsWith("--") ? [value.slice(2), list[index + 1] && !list[index + 1].startsWith("--") ? list[index + 1] : true] : []).filter(Boolean));
const server = String(args.server || "http://localhost:8000").replace(/\/$/, "");
const runId = String(args.run || "");
const token = String(args.token || "");
const session = args.session ? String(args.session) : "";
if (!runId || !token) throw new Error("Usage: node holoqa_agent_bridge.mjs --run run_x --token session_token [--server http://host:8000] [--session name]");

const wsUrl = `${server.replace(/^http/, "ws")}/api/runs/${encodeURIComponent(runId)}/live?role=agent&token=${encodeURIComponent(token)}`;
const browserArgs = (command, extra = []) => [...(session ? ["--session", session] : []), command, ...extra];
const runBrowser = (command, extra = []) => new Promise((resolve, reject) => execFile("agent-browser", browserArgs(command, extra), { windowsHide: true, maxBuffer: 1024 * 1024 * 4 }, (error, stdout, stderr) => error ? reject(new Error(stderr || error.message)) : resolve(stdout.trim())));
const framePath = join(tmpdir(), `holoqa-${randomUUID()}.png`);
const socket = new WebSocket(wsUrl);
let closed = false;
let busy = false;

const send = (message) => { if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message)); };
const capture = async () => {
  if (busy || closed || socket.readyState !== WebSocket.OPEN) return;
  busy = true;
  try {
    await runBrowser("screenshot", [framePath]);
    const data = (await fs.readFile(framePath)).toString("base64");
    let url = "";
    try { url = await runBrowser("get", ["url"]); } catch { /* URL is optional telemetry. */ }
    send({ type: "frame", data, mime: "image/png", url, captured_at: Date.now() / 1000 });
  } catch (error) {
    console.error("agent-browser capture failed", error.message || error);
  } finally { busy = false; }
};

socket.addEventListener("open", () => {
  send({ type: "heartbeat" });
  capture();
  setInterval(() => { send({ type: "heartbeat" }); capture(); }, Number(args.interval || 1200));
  console.log(`HoloQA live browser bridge connected for ${runId}`);
});
socket.addEventListener("close", () => { closed = true; fs.rm(framePath, { force: true }).catch(() => {}); process.exit(0); });
socket.addEventListener("error", (error) => { console.error("HoloQA live browser bridge error", error); });
process.on("SIGINT", () => { closed = true; socket.close(); });
