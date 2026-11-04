// Floating notifications: anything (React or not) can push one; <Toaster /> renders the stack.
import { useEffect, useRef } from "react";

export type ToastTone = "info" | "success" | "warn" | "error";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface Toast {
  id: string;
  message: string;
  tone: ToastTone;
  /** ms before it leaves on its own; 0 keeps it until dismissed */
  duration: number;
  action?: ToastAction;
}

export interface ToastInput {
  message: string;
  tone?: ToastTone;
  /** same id replaces the toast already shown instead of stacking a copy */
  id?: string;
  duration?: number;
  action?: ToastAction;
}

const DEFAULT_DURATION: Record<ToastTone, number> = { info: 4500, success: 3500, warn: 6000, error: 6500 };
const MAX_TOASTS = 4;

let toasts: Toast[] = [];
let counter = 0;
const listeners = new Set<(list: Toast[]) => void>();
const timers = new Map<string, number>();

function emit() {
  for (const listener of listeners) listener(toasts);
}

export function subscribeToasts(listener: (list: Toast[]) => void): () => void {
  listeners.add(listener);
  listener(toasts);
  return () => { listeners.delete(listener); };
}

export function dismissToast(id: string): void {
  const timer = timers.get(id);
  if (timer) window.clearTimeout(timer);
  timers.delete(id);
  if (!toasts.some(t => t.id === id)) return;
  toasts = toasts.filter(t => t.id !== id);
  emit();
}

export function toast(input: ToastInput | string): string {
  const options = typeof input === "string" ? { message: input } : input;
  const tone = options.tone ?? "info";
  const id = options.id ?? `toast-${++counter}`;
  const next: Toast = { id, message: options.message, tone, action: options.action,
                        duration: options.duration ?? DEFAULT_DURATION[tone] };
  const existing = toasts.findIndex(t => t.id === id);
  if (existing >= 0) toasts = toasts.map(t => (t.id === id ? next : t));
  else toasts = [...toasts, next].slice(-MAX_TOASTS);
  const old = timers.get(id);
  if (old) window.clearTimeout(old);
  timers.delete(id);
  if (next.duration > 0) timers.set(id, window.setTimeout(() => dismissToast(id), next.duration));
  emit();
  return id;
}

/** Pause/resume the auto-dismiss while the pointer rests on a toast. */
export function holdToast(id: string, hold: boolean): void {
  const item = toasts.find(t => t.id === id);
  if (!item || item.duration <= 0) return;
  const timer = timers.get(id);
  if (timer) window.clearTimeout(timer);
  timers.delete(id);
  if (!hold) timers.set(id, window.setTimeout(() => dismissToast(id), 2500));
}

/**
 * Shows `message` as a toast whenever it appears or changes, and removes it when the message
 * goes away (e.g. an error state cleared by the next successful step).
 */
export function useToast(message: string | null | undefined | false, options: Omit<ToastInput, "message" | "id"> & { id: string }): void {
  const latest = useRef(options);
  latest.current = options;
  const { id } = options;
  useEffect(() => {
    if (!message) { dismissToast(id); return; }
    toast({ ...latest.current, message });
  }, [message, id]);
  useEffect(() => () => dismissToast(id), [id]);
}
