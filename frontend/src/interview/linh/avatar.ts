// Linh, the Live2D interviewer (model and motions from ami-interview/, served from public/linh/).
// Gestures are one-shot actions that return to "listen"; the mouth follows the interviewer audio.
import type { Gesture } from "../gestures";
import { interviewSceneLayout } from "../sceneLayout";

const BASE = "/linh/";
const MODEL_URL = `${BASE}model/ptit_sdk.model3.json`;
// Order matters: the Cubism core, then PixiJS, then pixi-live2d-display (all expose globals).
const SCRIPTS = ["vendor/live2dcubismcore.min.js", "vendor/pixi.min.js", "vendor/cubism4.min.js"];
const ZOOM = 1.17; // half-body framing chosen in ami-interview
const DISAGREE_SECONDS = 2.4;
// Gestures without a motion clip of their own: how long they hold before returning to listening.
const HOLD_MS: Partial<Record<Gesture, number>> = { smile: 3200, clarify: 3600, disagree: DISAGREE_SECONDS * 1000 + 150 };

export const GESTURE_INFO: Record<Gesture, { icon: string; label: string; hint: string }> = {
  listen: { icon: "◉", label: "Lắng nghe", hint: "Chờ bạn trả lời" },
  wave: { icon: "✋", label: "Chào", hint: "Vẫy tay" },
  nod: { icon: "↕", label: "Gật đầu", hint: "Ghi nhận" },
  smile: { icon: "☺", label: "Mỉm cười", hint: "Thân thiện" },
  think: { icon: "◌", label: "Suy nghĩ", hint: "Nhắm mắt, gật nhẹ" },
  write: { icon: "✎", label: "Ghi chép", hint: "Xem ghi chú" },
  clarify: { icon: "?", label: "Chưa rõ", hint: "Nghiêng đầu" },
  disagree: { icon: "↔", label: "Chưa đồng tình", hint: "Lắc đầu nhẹ" },
};

declare global {
  interface Window { PIXI?: any; Live2DCubismCore?: unknown }
}

let scripts: Promise<void> | null = null;

function loadScripts(): Promise<void> {
  scripts ??= SCRIPTS.reduce<Promise<void>>((previous, file) => previous.then(() => new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = BASE + file;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Không tải được ${file}`));
    document.head.appendChild(script);
  })), Promise.resolve()).catch((error) => { scripts = null; throw error; });
  return scripts;
}

/** Warm the HTTP cache (libraries, model, textures) before the interview room opens. */
export function preloadLinh(): void {
  void loadScripts().catch(() => {});
  void fetch(MODEL_URL).then((response) => response.json()).then((config) => {
    for (const file of [config.FileReferences.Moc, ...config.FileReferences.Textures]) void fetch(new URL(file, new URL(MODEL_URL, location.href)));
  }).catch(() => {});
}

/** Pure head pose of the disagreement shake, so a replay follows the same trajectory. */
export function disagreementPose(elapsed: number): { yaw: number; pitch: number } {
  if (elapsed <= 0 || elapsed >= DISAGREE_SECONDS) return { yaw: 0, pitch: 0 };
  const progress = elapsed / DISAGREE_SECONDS;
  const envelope = Math.sin(Math.PI * progress);
  return { yaw: Math.sin(progress * Math.PI * 4) * 8 * envelope, pitch: -3 * envelope };
}

type Parameter = { Id: string; Value: number; Blend?: string };

export class LinhAvatar {
  action: Gesture = "listen";
  onAction: ((action: Gesture) => void) | null = null;
  private app: any;
  private model: any;
  private core: any;
  private ready = false;
  private destroyed = false;
  private initialHeight = 1;
  private version = 0;
  private timer: number | undefined;
  private queued: Gesture | null = null;
  private startedAt = 0;
  private mouth = 0;
  private expressionData = new Map<string, Parameter[]>();
  private activeExpression: Parameter[] = [];
  private motionParameters = new Map<string, Set<string>>();
  private motionDurations = new Map<string, number>();
  private activeMotionIds = new Set<string>();
  private originalValues = new Map<string, number>();
  private resize = new ResizeObserver(() => this.frame());

  /** `mouthLevel` is polled every frame (0..1 loudness of the interviewer voice). */
  constructor(private stage: HTMLElement, private mouthLevel: () => number, private seated = false) {}

  async load(): Promise<void> {
    await loadScripts();
    const PIXI = window.PIXI;
    if (!PIXI?.live2d || !window.Live2DCubismCore) throw new Error("Thiếu thư viện hiển thị.");
    if (this.destroyed) return;
    // A canvas of its own: a remount (or React StrictMode) never reuses a released WebGL context.
    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-label", "Linh, người phỏng vấn");
    this.stage.appendChild(canvas);
    this.app = new PIXI.Application({ view: canvas, resizeTo: this.stage, backgroundAlpha: 0, antialias: true,
      autoDensity: true, resolution: Math.min(devicePixelRatio, 2) });
    const response = await fetch(MODEL_URL);
    if (this.destroyed) return;
    if (!response.ok) throw new Error("Không tải được cấu hình nhân vật.");
    const config = await response.json();
    if (this.destroyed) return;
    config.url = new URL(MODEL_URL, location.href).href;
    // No automatic random gesture when idle: interview gestures are intentional.
    const model = await PIXI.live2d.Live2DModel.from(config, { autoInteract: false, motionPreload: "ALL" });
    if (this.destroyed) { model.destroy(); return; }
    this.model = model;
    const core = (this.core = model.internalModel.coreModel);
    this.patchDrawables(core);
    this.initialHeight = model.height;
    model.anchor.set(0.5, 0);
    this.app.stage.addChild(model);
    await Promise.all(config.FileReferences.Expressions.map(async (expression: { Name: string; File: string }) => {
      const res = await fetch(new URL(expression.File, config.url));
      if (!res.ok) throw new Error("Thiếu biểu cảm " + expression.Name);
      this.expressionData.set(expression.Name, (await res.json()).Parameters || []);
    }));
    if (this.destroyed) return;
    await Promise.all(Object.entries(config.FileReferences.Motions as Record<string, { File: string }[]>).map(async ([group, motions]) => {
      const res = await fetch(new URL(motions[0].File, config.url));
      if (!res.ok) throw new Error("Thiếu động tác " + group);
      const data = await res.json();
      this.motionParameters.set(group, new Set(data.Curves.filter((c: any) => c.Target === "Parameter").map((c: any) => c.Id)));
      this.motionDurations.set(group, data.Meta.Duration);
    }));
    if (this.destroyed) return;
    // Restore a clean base before motion evaluation, not after it: nod/wave must keep their curves.
    const faceIds = new Set(["ParamMouthOpenY", "ParamMouthForm", "EyeL_Squint", "EyeR_Squint", "ParamBrowLY", "ParamBrowRY"]);
    for (const parameters of this.expressionData.values()) for (const p of parameters) faceIds.add(p.Id);
    for (const ids of this.motionParameters.values()) for (const id of ids) faceIds.add(id);
    for (const id of faceIds) this.originalValues.set(id, core.getParameterValueById(id));
    model.internalModel.on("beforeMotionUpdate", () => { for (const [id, value] of this.originalValues) this.set(id, value); });
    model.internalModel.on("afterMotionUpdate", () => this.updateTracking());
    model.internalModel.on("beforeModelUpdate", () => this.updateFace());
    if (this.destroyed) return;
    this.ready = true;
    this.resize.observe(this.stage);
    this.frame();
    await this.perform("listen");
  }

  captureCanvas(): HTMLCanvasElement | null {
    if (!this.ready || !this.app) return null;
    // Copy a freshly rendered frame: WebGL may discard its buffer after display.
    this.app.renderer.render(this.app.stage);
    return this.app.view as HTMLCanvasElement;
  }

  destroy(): void {
    this.destroyed = true;
    this.version++;
    this.ready = false;
    window.clearTimeout(this.timer);
    this.resize.disconnect();
    this.app?.destroy(true, { children: true });
    this.app = this.model = this.core = null;
  }

  /** Gesture chosen by the interviewer: never cuts the greeting wave short, plays right after it. */
  play(next: Gesture): void {
    if (this.action === "wave" && next !== "wave") this.queued = next;
    else void this.perform(next);
  }

  /** Background reaction (listening, waiting): only while idle, never over another gesture. */
  ambient(next: Gesture): void {
    if (this.action === "listen" && !this.queued) void this.perform(next);
  }

  /** Start a gesture now, interrupting the current one. */
  async perform(next: Gesture): Promise<void> {
    if (!this.ready) return;
    const version = ++this.version;
    this.queued = null;
    window.clearTimeout(this.timer);
    this.action = next;
    this.startedAt = performance.now();
    this.model.internalModel.motionManager.stopAllMotions();
    this.activeMotionIds = new Set();
    this.applyExpression("Default");
    this.onAction?.(next);
    let motion: string | undefined;
    if (next === "think") motion = "ThinkDouble";
    if (next === "smile") this.applyExpression("Happy");
    if (next === "wave") { this.applyExpression("Vay_Tay_1"); motion = "Checkin2"; }
    if (next === "nod") motion = "Head_Nod";
    if (next === "write") { this.applyExpression("Writting"); motion = "Reading"; }
    if (motion) {
      this.activeMotionIds = this.motionParameters.get(motion) || new Set();
      try {
        const clip = await this.model.internalModel.motionManager.loadMotion(motion, 0);
        if (version !== this.version) return;
        clip.setIsLoop(false);
        const started = await this.model.motion(motion, 0, 3);
        if (version !== this.version) return;
        if (!started) throw new Error("Không thể bắt đầu chuyển động");
      } catch (error) {
        console.error(error);
      }
    }
    if (version !== this.version || next === "listen") return;
    const duration = motion ? (this.motionDurations.get(motion) || 3) * 1000 + 250 : HOLD_MS[next] ?? 3000;
    this.timer = window.setTimeout(() => void this.perform(this.queued ?? "listen"), duration);
  }

  private frame(): void {
    if (!this.model) return;
    if (this.seated) {
      const { deskY } = interviewSceneLayout(this.stage.clientWidth, this.stage.clientHeight);
      // The waist/belt of this model is 36.9% down its uncropped model canvas.
      // Fit the upper body, then anchor the waist to the desktop (with a 2px overlap).
      const drawnHeight = this.stage.clientHeight * 2.05 * ZOOM * 0.91;
      this.model.scale.set(drawnHeight / this.initialHeight);
      this.model.x = this.stage.clientWidth / 2;
      this.model.y = deskY - drawnHeight * 0.369 + 2;
      return;
    }
    // The model canvas includes generous margins: fit by height and crop below the waist.
    this.model.scale.set(this.stage.clientHeight * 2.05 / this.initialHeight * ZOOM);
    this.model.x = this.stage.clientWidth / 2;
    this.model.y = this.stage.clientHeight * -0.04;
  }

  private set(id: string, value: number): void {
    this.core.setParameterValueById(id, value);
  }

  private applyExpression(name: string): void {
    this.activeExpression = this.expressionData.get(name) || [];
  }

  private blended(parameter: Parameter, base: number): number {
    return parameter.Blend === "Multiply" ? base * parameter.Value : parameter.Blend === "Overwrite" ? parameter.Value : base + parameter.Value;
  }

  /** Tracking parameters must reach physics before it computes the visible head/eye pose. */
  private updateTracking(): void {
    if (!this.ready) return;
    const t = performance.now() / 1000;
    for (const parameter of this.activeExpression) {
      if (this.activeMotionIds.has(parameter.Id)) continue;
      this.set(parameter.Id, this.blended(parameter, this.core.getParameterValueById(parameter.Id)));
    }
    if (this.action === "listen") {
      this.set("ParamAngleXTrack", Math.sin(t * 0.45) * 2);
      this.set("ParamAngleYTrack", Math.sin(t * 0.65) * 1.2);
    }
    const elapsed = (performance.now() - this.startedAt) / 1000;
    if (this.action === "disagree") {
      const { yaw, pitch } = disagreementPose(elapsed);
      this.set("ParamAngleXTrack", yaw);
      this.set("ParamAngleYTrack", pitch);
      this.set("ParamBodyAngleXTrack", yaw * 0.1);
    }
    if (this.action === "clarify") {
      this.set("ParamAngleZTrack", 7 + Math.sin(elapsed * 0.8) * 0.5);
      this.set("ParamAngleYTrack", -2);
    }
  }

  /** Clothing, eyes and mouth are direct parameters, held after motion/physics evaluation. */
  private updateFace(): void {
    if (!this.ready) return;
    const elapsed = (performance.now() - this.startedAt) / 1000;
    // Blink processing must not reopen eyes that an expression deliberately closes.
    for (const parameter of this.activeExpression) {
      if (parameter.Id.endsWith("Track") || this.activeMotionIds.has(parameter.Id)) continue;
      this.set(parameter.Id, this.blended(parameter, this.originalValues.get(parameter.Id) || 0));
    }
    this.set("Param_Jacket", 10);
    this.set("Param_Glasses", 0);
    const action = this.action;
    if (action === "think" || action === "disagree") {
      this.set("ParamEyeLOpen", 0);
      this.set("ParamEyeROpen", 0);
    }
    if (action === "disagree") {
      // Facial physics keeps momentum across replays: hold the main head pose explicitly.
      const { yaw, pitch } = disagreementPose(elapsed);
      this.set("ParamAngleX2", yaw * 0.85);
      this.set("ParamAngleX3", yaw * 0.9);
      this.set("ParamAngleY2", pitch);
      this.set("ParamAngleZ2", 0);
      this.set("ParamAngleZ3", 0);
    }
    if (action === "clarify") {
      this.set("ParamBrowLY", 0.35);
      this.set("ParamBrowRY", -0.1);
      this.set("ParamMouthForm", -0.15);
    }
    if (action === "smile") {
      this.set("ParamMouthForm", 1);
      this.set("ParamEyeLOpen", this.core.getParameterValueById("ParamEyeLOpen") * 0.85);
      this.set("ParamEyeROpen", this.core.getParameterValueById("ParamEyeROpen") * 0.85);
      this.set("EyeL_Squint", 0.12);
      this.set("EyeR_Squint", 0.12);
    }
    // Lip sync from the voice: fast attack, slower release, so syllables read without jitter.
    const target = Math.min(0.95, this.mouthLevel() * 1.4);
    this.mouth += (target - this.mouth) * (target > this.mouth ? 0.55 : 0.25);
    if (this.mouth > 0.02) this.set("ParamMouthOpenY", this.mouth);
  }

  private patchDrawables(core: any): void {
    // These decorations are separate meshes; preserve the underlying fabric and hair.
    const hiddenMeshes = new Set(["Shirt_Logo", "Head_Deco", "Head_Deco2"]);
    const drawableIds: string[] = core.getModel().drawables.ids;
    const drawableOpacity = core.getDrawableOpacity.bind(core);
    core.getDrawableOpacity = (index: number) => (hiddenMeshes.has(drawableIds[index]) ? 0 : drawableOpacity(index));
    // Widen the closed smile without changing its shape. Return a render-only copy; never mutate the model.
    const mouthMeshes = new Set(["Lower_Lip", "Upper_Lip"]);
    const lipBuffers = new Map<number, Float32Array>();
    const drawableVertices = core.getDrawableVertices.bind(core);
    core.getDrawableVertices = (index: number) => {
      const source: Float32Array = drawableVertices(index);
      if (this.action !== "smile" || this.mouth > 0.05 || !mouthMeshes.has(drawableIds[index])) return source;
      let target = lipBuffers.get(index);
      if (!target) { target = new Float32Array(source.length); lipBuffers.set(index, target); }
      let minX = Infinity, maxX = -Infinity;
      for (let i = 0; i < source.length; i += 2) { minX = Math.min(minX, source[i]); maxX = Math.max(maxX, source[i]); }
      const centerX = (minX + maxX) / 2;
      for (let i = 0; i < source.length; i += 2) {
        target[i] = centerX + (source[i] - centerX) * 1.12;
        target[i + 1] = source[i + 1];
      }
      return target;
    };
  }
}
