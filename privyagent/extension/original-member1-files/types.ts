export interface UIElement {
  id: number;
  tag: string;
  type?: string;
  text: string;
  placeholder?: string;
  name?: string;
  ariaLabel?: string;
  href?: string;
  bbox: [number, number, number, number];
}

export type BrowserAction = ClickAction | TypeAction | SelectAction | ScrollAction;

export interface ClickAction { action: "click"; element_id: number; }
export interface TypeAction { action: "type"; element_id: number; text: string; }
export interface SelectAction { action: "select"; element_id: number; value: string; }
export interface ScrollAction { action: "scroll"; direction: "up" | "down"; amount: number; }

export interface ActionExecutionResult { success: boolean; error?: string; }
export interface ScreenshotCapture { success: boolean; dataUrl?: string; error?: string; }
export interface PageCapture { elements: UIElement[]; screenshot: ScreenshotCapture; }
export interface VisionResponse { action?: unknown; [key: string]: unknown; }
