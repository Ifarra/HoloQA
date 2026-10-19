import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

export const native = isTauri();
export interface Project { name: string; path: string; framework: string; plans: string[]; scanLimited: boolean }
export interface Step { id: string; stage: string; title: string; do?: string; route?: string; expect?: Record<string, unknown>[]; depends_on?: string[]; verdict?: string; note?: string; observations?: Observation[]; assertions?: unknown[] }
export interface Observation { file: string; kind: string; sha256?: string }
export interface Plan { path: string; sha256: string; app: string; target: string; stages: {id: string; title: string}[]; steps: Step[]; warnings: string[] }
export interface Provider { installed: boolean; authentication: string }
export interface Hello { protocol: number; recent: string[]; project: Project | null; provider: Provider; browserInstalled: boolean }
export interface Snapshot { path: string; lifecycle: string; steps: Step[]; status: { run_id: string; total: number; counts: Record<string, number>; pending: string[]; integrity: string; decision: string } }
export interface Historical { path: string; id: string; app: string; counts: Record<string, number>; decision: string; integrity: string; total: number }
export interface Approval { id: string; command: string; cwd: string; reason?: string; network?: unknown; permissions?: unknown }
export async function rpc<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  if (!native) throw new Error('Open this interface in HoloQA Desktop to access local projects. This browser view has no engine connection.');
  return invoke<T>('engine_request', { method, params });
}
export async function openFolder(): Promise<Project | null> {
  if (!native) throw new Error('The native folder picker is available in the Windows desktop app.');
  return invoke<Project | null>('open_project');
}
export async function events(callback: (event: {event: string; data: Record<string, unknown>}) => void) {
  return native ? listen<{event: string; data: Record<string, unknown>}>('engine-event', e => callback(e.payload)) : () => {};
}
