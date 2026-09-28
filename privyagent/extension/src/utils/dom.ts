import { UIElement } from "../shared/types";

const INTERACTIVE_SELECTOR = [
  "button", "input", "textarea", "select", "a",
  "[contenteditable]:not([contenteditable='false'])",
  "[role='button']", "[role='link']", "[role='checkbox']", "[role='radio']", "[role='tab']", "[role='menuitem']"
].join(", ");
const MAX_TEXT_LENGTH = 100;

export interface PageElementTarget {
  id: number;
  element: Element;
  descriptor: UIElement;
}

export function isVisible(element: Element, rect = element.getBoundingClientRect()): boolean {
  if (!Number.isFinite(rect.width) || !Number.isFinite(rect.height) || rect.width <= 0 || rect.height <= 0) return false;
  if (rect.right <= 0 || rect.bottom <= 0 || rect.left >= window.innerWidth || rect.top >= window.innerHeight) return false;

  // A parent may hide a child even when the child's own opacity remains 1.
  for (let current: Element | null = element; current; current = current.parentElement) {
    const style = window.getComputedStyle(current);
    if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse" ||
      Number.parseFloat(style.opacity) === 0 || current.getAttribute("aria-hidden") === "true") return false;
  }
  return true;
}

export function isInteractable(element: Element): element is HTMLElement {
  return element instanceof HTMLElement && isVisible(element) && !element.matches(":disabled") &&
    element.getAttribute("aria-disabled") !== "true" && window.getComputedStyle(element).pointerEvents !== "none";
}

export function extractPageElements(): UIElement[] {
  return extractPageElementTargets().map((target) => target.descriptor);
}

export function extractPageElementTargets(): PageElementTarget[] {
  const targets: PageElementTarget[] = [];
  for (const element of document.querySelectorAll(INTERACTIVE_SELECTOR)) {
    if (!isInteractable(element)) continue;
    const id = targets.length + 1;
    targets.push({ id, element, descriptor: describeElement(element, id) });
  }
  return targets;
}

function describeElement(element: Element, id: number): UIElement {
  const rect = element.getBoundingClientRect();
  const result: UIElement = {
    id, tag: element.tagName.toLowerCase(), text: getMeaningfulText(element),
    bbox: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)]
  };
  if (element instanceof HTMLInputElement) {
    result.type = element.type;
    result.placeholder = trimText(element.placeholder);
    result.name = trimText(element.name);
  } else if (element instanceof HTMLTextAreaElement) {
    result.placeholder = trimText(element.placeholder);
    result.name = trimText(element.name);
  } else if (element instanceof HTMLSelectElement) {
    result.name = trimText(element.name);
  } else if (element instanceof HTMLAnchorElement) {
    result.href = element.href;
  }
  result.ariaLabel = getAccessibleLabel(element);
  return result;
}

function getMeaningfulText(element: Element): string {
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) return "";
  return trimText(element instanceof HTMLElement ? element.innerText : element.textContent ?? "") ?? "";
}

function getAccessibleLabel(element: Element): string | undefined {
  const directLabel = trimText(element.getAttribute("aria-label"));
  if (directLabel) return directLabel;
  const labelledBy = element.getAttribute("aria-labelledby");
  if (!labelledBy) return undefined;
  const label = labelledBy.split(/\s+/)
    .map((id) => document.getElementById(id))
    .filter((candidate): candidate is HTMLElement => candidate instanceof HTMLElement && isVisible(candidate))
    .map((candidate) => trimText(candidate.innerText) ?? "")
    .filter(Boolean).join(" ");
  return trimText(label);
}

function trimText(value: string | null): string | undefined {
  if (value === null) return undefined;
  const text = value.replace(/\s+/g, " ").trim();
  return text ? text.slice(0, MAX_TEXT_LENGTH) : undefined;
}
