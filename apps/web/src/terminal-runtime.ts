import "./terminal-runtime.css";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

let cleanupDialog: (() => void) | null = null;
let opener: HTMLElement | null = null;

function bindDialog(dialog: HTMLElement) {
  if (dialog.dataset.runtimeBound === "true") return;
  dialog.dataset.runtimeBound = "true";
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";

  const focusables = () => Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((node) => {
    const style = window.getComputedStyle(node);
    return style.visibility !== "hidden" && style.display !== "none";
  });

  if (!dialog.hasAttribute("tabindex")) dialog.setAttribute("tabindex", "-1");
  const initial = dialog.querySelector<HTMLElement>(".validationClose") ?? focusables()[0] ?? dialog;
  window.requestAnimationFrame(() => initial.focus());

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      const close = dialog.querySelector<HTMLButtonElement>(".validationClose");
      if (close) {
        event.preventDefault();
        close.click();
      }
      return;
    }
    if (event.key !== "Tab") return;
    const nodes = focusables();
    if (!nodes.length) {
      event.preventDefault();
      dialog.focus();
      return;
    }
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  dialog.addEventListener("keydown", onKeyDown);
  cleanupDialog = () => {
    dialog.removeEventListener("keydown", onKeyDown);
    document.body.style.overflow = previousOverflow;
    const target = opener;
    opener = null;
    cleanupDialog = null;
    if (target && document.contains(target)) window.requestAnimationFrame(() => target.focus());
  };
}

function startRuntime() {
  const observer = new MutationObserver(() => {
    const dialog = document.querySelector<HTMLElement>(".validationOverlay[role='dialog']");
    if (dialog) bindDialog(dialog);
    else if (cleanupDialog) cleanupDialog();
  });
  observer.observe(document.getElementById("root") ?? document.body, { childList: true, subtree: true });
  return () => {
    observer.disconnect();
    if (cleanupDialog) cleanupDialog();
  };
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startRuntime, { once: true });
else startRuntime();
