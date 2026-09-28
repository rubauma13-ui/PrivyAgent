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

export interface PageCapture {
  elements: UIElement[];
  screenshot: ScreenshotCapture;
  url: string;
  title: string;
}

/**
 * Reply from the local PrivyAgent server (POST /vision).
 *
 * `status` drives the loop in background.ts:
 *   "action"          -> execute `action`, then continue if `more_steps`
 *   "done"            -> the task finished
 *   "cannot_identify" -> the model would not guess; stop and report `reason`
 *   "error"           -> stop and report `reason`
 */
export interface VisionResponse {
  session_id?: string;
  status?: "action" | "done" | "cannot_identify" | "error";
  action?: unknown;
  reason?: string;
  more_steps?: boolean;
  step_index?: number;
  total_steps?: number;
  steps?: string[];
  backend?: string;
  privacy?: PrivacyReport;
  [key: string]: unknown;
}

/** Counts only - the server never returns the values it redacted. */
export interface PrivacyReport {
  redacted_values?: number;
  by_label?: Record<string, number>;
  ner_available?: boolean;
  ner_status?: string;
}

export interface TurnRecord {
  step?: string;
  status?: string;
  action?: BrowserAction;
  executed?: boolean;
  error?: string;
}

export interface TaskResult {
  sessionId?: string;
  turns: TurnRecord[];
  completed: boolean;
  privacy?: PrivacyReport;
  message: string;
}
