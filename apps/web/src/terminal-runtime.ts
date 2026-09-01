import "./terminal-runtime.css";
import "./operational-runtime";
import "./paper-order-runtime";
import "./paper-mode-control";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

const COMMANDS = ["COMMAND", "MARKETS", "RESEARCH", "AUTOMATION", "PORTFOLIO", "RISK", "SYSTEM"] as const;
const ICONS: Record<(typeof COMMANDS)[number], string> = {
  COMMAND: "⌂",
  MARKETS: "⌁",
  RESEARCH: "◈",
  AUTOMATION: "⟳",
  PORTFOLIO: "▦",
  RISK: "△",
  SYSTEM: "⚙",
};

let cleanupDialog: (() => void) | null = null;

function targetFor(name: string) {
  if (name === "SYSTEM") {
    return document.querySelector<HTMLElement>(".operationalSurface") ?? document.querySelector<HTMLElement>("footer[data-section='SYSTEM']");
  }
  return document.querySelector<HTMLElement>(`[data-section='${name}']`);
}

function scrollToCommand(name: string) {
  const target = targetFor(name);
  target?.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    block: "start",
  });
}

function ensureCommandRail() {
  if (document.querySelector(".commandRail")) return;
  const rail = document.createElement("aside");
  rail.className = "commandRail";
  rail.setAttribute("aria-label", "PROTO command rail");

  const mark = document.createElement("button");
  mark.className = "railBrand";
  mark.type = "button";
  mark.textContent = "P";
  mark.setAttribute("aria-label", "Go to command workspace");
  mark.addEventListener("click", () => scrollToCommand("COMMAND"));
  rail.append(mark);

  const nav = document.createElement("nav");
  COMMANDS.forEach((name) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.command = name;
    button.setAttribute("aria-label", name[0] + name.slice(1).toLowerCase());
    const icon = document.createElement("span");
    icon.textContent = ICONS[name];
    const label = document.createElement("small");
    label.textContent = name;
    button.append(icon, label);
    button.addEventListener("click", () => scrollToCommand(name));
    nav.append(button);
  });
  rail.append(nav);

  const foot = document.createElement("div");
  foot.className = "railFoot";
  const indicator = document.createElement("i");
  indicator.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = "PROTO";
  foot.append(indicator, label);
  rail.append(foot);
  document.body.append(rail);
}

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
  const topButtons = Array.from(document.querySelectorAll<HTMLButtonElement>(".topbar nav button"));
  const railButtons = Array.from(document.querySelectorAll<HTMLButtonElement>(".commandRail [data-command]"));
  const observed = new Set<HTMLElement>();

  const setActive = (name: string) => {
    topButtons.forEach((button) => {
      const active = button.textContent?.trim().toUpperCase() === name;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    railButtons.forEach((button) => {
      const active = button.dataset.command === name;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
  };

  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    const name = visible?.target instanceof HTMLElement ? visible.target.dataset.section : undefined;
    if (name) setActive(name);
  }, { rootMargin: "-80px 0px -55% 0px", threshold: [0.08, 0.2, 0.45, 0.7] });

  const observeSection = (section: HTMLElement) => {
    if (observed.has(section) || !section.dataset.section) return;
    observed.add(section);
    observer.observe(section);
  };

  document.querySelectorAll<HTMLElement>("[data-section]").forEach(observeSection);
  setActive("COMMAND");

  const sectionWatcher = new MutationObserver((records) => {
    for (const record of records) {
      record.addedNodes.forEach((node) => {
        if (!(node instanceof HTMLElement)) return;
        if (node.matches("[data-section]")) observeSection(node);
        node.querySelectorAll<HTMLElement>("[data-section]").forEach(observeSection);
      });
    }
    const surface = document.querySelector<HTMLElement>(".operationalSurface");
    if (surface) {
      if (!surface.dataset.section) surface.dataset.section = "SYSTEM";
      observeSection(surface);
    }
  });
  sectionWatcher.observe(document.getElementById("root") ?? document.body, { childList: true, subtree: true });

  return () => {
    observer.disconnect();
    sectionWatcher.disconnect();
    observed.clear();
  };
}

function startRuntime() {
  ensureCommandRail();
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
