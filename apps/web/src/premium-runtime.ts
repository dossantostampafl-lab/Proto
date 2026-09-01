import "./premium-runtime.css";

type NavTarget = { label: string; selector: string };

const NAV_TARGETS: NavTarget[] = [
  { label: "COMMAND", selector: ".protoShell" },
  { label: "MARKETS", selector: ".marketStrip" },
  { label: "RESEARCH", selector: ".fieldPanel" },
  { label: "AUTOMATION", selector: ".automationPanel" },
  { label: "PORTFOLIO", selector: ".portfolioPanel" },
  { label: "RISK", selector: ".riskPanel" },
  { label: "SYSTEM", selector: ".terminalFooter" },
];

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

let dialogCleanup: (() => void) | null = null;
let lastDialogOpener: HTMLElement | null = null;

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function setActiveNav(button: HTMLButtonElement) {
  document.querySelectorAll<HTMLButtonElement>(".primaryNav button").forEach((candidate) => {
    const active = candidate === button;
    candidate.classList.toggle("active", active);
    if (active) candidate.setAttribute("aria-current", "page");
    else candidate.removeAttribute("aria-current");
  });
}

function bindNavigation() {
  const nav = document.querySelector<HTMLElement>(".primaryNav");
  if (!nav || nav.dataset.runtimeBound === "true") return;
  nav.dataset.runtimeBound = "true";

  nav.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!button) return;
    const label = button.textContent?.trim().toUpperCase() ?? "";
    const config = NAV_TARGETS.find((item) => item.label === label);
    if (!config) return;
    const target = document.querySelector<HTMLElement>(config.selector);
    if (!target) return;

    setActiveNav(button);
    target.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
      block: "start",
      inline: "nearest",
    });
    target.setAttribute("tabindex", "-1");
    window.setTimeout(() => target.focus({ preventScroll: true }), prefersReducedMotion() ? 0 : 280);
  });
}

function bindDialog(dialog: HTMLElement) {
  if (dialog.dataset.runtimeBound === "true") return;
  dialog.dataset.runtimeBound = "true";

  lastDialogOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
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
    if (nodes.length === 0) {
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
  dialogCleanup = () => {
    dialog.removeEventListener("keydown", onKeyDown);
    document.body.style.overflow = previousOverflow;
    const opener = lastDialogOpener;
    lastDialogOpener = null;
    dialogCleanup = null;
    if (opener && document.contains(opener)) window.requestAnimationFrame(() => opener.focus());
  };
}

function observeRuntime() {
  bindNavigation();
  const observer = new MutationObserver(() => {
    bindNavigation();
    const dialog = document.querySelector<HTMLElement>(".validationOverlay[role='dialog']");
    if (dialog) bindDialog(dialog);
    else if (dialogCleanup) dialogCleanup();
  });

  observer.observe(document.getElementById("root") ?? document.body, { childList: true, subtree: true });
  return () => {
    observer.disconnect();
    if (dialogCleanup) dialogCleanup();
  };
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", observeRuntime, { once: true });
else observeRuntime();
