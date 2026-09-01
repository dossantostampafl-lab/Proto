import "./terminal-runtime.css";
import "./operational-runtime";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

let cleanupDialog: (() => void) | null = null;

function bindDialog(dialog: HTMLElement) {
  if (dialog.dataset.runtimeBound === "true") return;
  dialog.dataset.runtimeBound = "true";

  const focusables = () => Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((node) => {
    const style = window.getComputedStyle(node);
    return style.visibility !== "hidden" && style.display !== "none";
  });

  if (!dialog.hasAttribute("tabindex")) dialog.setAttribute("tabindex", "-1");
  const initial = dialog.querySelector<HTMLElement>(".validationClose") ?? focusables()[0] ?? dialog;
  window.requestAnimationFrame(() => initial.focus());

  const onKeyDown = (event: KeyboardEvent) => {
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
    cleanupDialog = null;
  };
}

function bindSectionNavigation() {
  const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>(".topbar nav button"));
  const sections = Array.from(document.querySelectorAll<HTMLElement>("[data-section]"));
  if (!buttons.length || !sections.length) return () => {};

  const byName = new Map(buttons.map((button) => [button.textContent?.trim().toUpperCase() ?? "", button]));
  const setActive = (name: string) => {
    buttons.forEach((button) => {
      const active = button === byName.get(name);
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
  };
  setActive("COMMAND");

  const systemButton = byName.get("SYSTEM");
  const onSystemClick = () => {
    setActive("SYSTEM");
    window.requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>(".operationalSurface") ?? document.querySelector<HTMLElement>("footer[data-section='SYSTEM']");
      target?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        block: "start",
      });
    });
  };
  systemButton?.addEventListener("click", onSystemClick);

  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    const name = visible?.target instanceof HTMLElement ? visible.target.dataset.section : undefined;
    if (name) setActive(name);
  }, { rootMargin: "-72px 0px -55% 0px", threshold: [0.08, 0.2, 0.45, 0.7] });

  sections.forEach((section) => observer.observe(section));
  return () => {
    systemButton?.removeEventListener("click", onSystemClick);
    observer.disconnect();
  };
}

function startRuntime() {
  const stopNavigation = bindSectionNavigation();
  const observer = new MutationObserver(() => {
    const dialog = document.querySelector<HTMLElement>(".validationOverlay[role='dialog']");
    if (dialog) bindDialog(dialog);
    else if (cleanupDialog) cleanupDialog();
  });
  observer.observe(document.getElementById("root") ?? document.body, { childList: true, subtree: true });
  return () => {
    stopNavigation();
    observer.disconnect();
    if (cleanupDialog) cleanupDialog();
  };
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startRuntime, { once: true });
else startRuntime();
