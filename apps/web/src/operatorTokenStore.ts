// The operator token in the browser. Remembered on this device (localStorage) at the operator's
// request, so a new tab or session does not ask for it again; or for this browser session only
// (sessionStorage). Every access is guarded: blocked storage just means the token is not kept.
const KEY = "proto.operatorToken";

export function readOperatorToken(): string {
  try { const kept = window.localStorage.getItem(KEY); if (kept) return kept; } catch { /* storage blocked */ }
  try { return window.sessionStorage.getItem(KEY) ?? ""; } catch { return ""; }
}

export function rememberedOnDevice(): boolean {
  try { return Boolean(window.localStorage.getItem(KEY)); } catch { return false; }
}

export function writeOperatorToken(token: string, remember: boolean): void {
  try { if (token && remember) window.localStorage.setItem(KEY, token); else window.localStorage.removeItem(KEY); } catch { /* storage blocked */ }
  try { if (token && !remember) window.sessionStorage.setItem(KEY, token); else window.sessionStorage.removeItem(KEY); } catch { /* storage blocked */ }
}
