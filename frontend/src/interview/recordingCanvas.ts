import type { Message } from "./api";
import { interviewSceneLayout } from "./sceneLayout";

export interface RecordingFrameState {
  messages: Message[];
  interim: string;
  candidateName?: string;
  speaking: boolean;
}

function drawCover(
  context: CanvasRenderingContext2D,
  source: CanvasImageSource,
  sourceWidth: number,
  sourceHeight: number,
  x: number,
  y: number,
  width: number,
  height: number,
): void {
  const scale = Math.max(width / sourceWidth, height / sourceHeight);
  const drawWidth = sourceWidth * scale;
  const drawHeight = sourceHeight * scale;
  context.drawImage(source, x + (width - drawWidth) / 2, y + (height - drawHeight) / 2, drawWidth, drawHeight);
}

function roundedRect(context: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number): void {
  const r = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + r, y);
  context.arcTo(x + width, y, x + width, y + height, r);
  context.arcTo(x + width, y + height, x, y + height, r);
  context.arcTo(x, y + height, x, y, r);
  context.arcTo(x, y, x + width, y, r);
  context.closePath();
}

function wrapText(context: CanvasRenderingContext2D, text: string, maxWidth: number, maxLines: number): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let line = "";
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (line && context.measureText(next).width > maxWidth) {
      lines.push(line);
      line = word;
      if (lines.length === maxLines - 1) break;
    } else line = next;
  }
  if (line && lines.length < maxLines) lines.push(line);
  if (lines.length === maxLines && words.length && lines.join(" ").length < text.length) {
    let last = lines[maxLines - 1];
    while (last && context.measureText(`${last}…`).width > maxWidth) last = last.slice(0, -1);
    lines[maxLines - 1] = `${last.trimEnd()}…`;
  }
  return lines;
}

export function drawInterviewRecordingFrame(
  context: CanvasRenderingContext2D,
  background: HTMLImageElement | null,
  avatar: HTMLCanvasElement | null,
  camera: HTMLVideoElement | null,
  state: RecordingFrameState,
): void {
  const width = context.canvas.width;
  const height = context.canvas.height;
  const headerHeight = 76;
  const dockHeight = 104;
  const dockTop = height - dockHeight;
  const stageWidth = Math.round(width * 0.64);
  const panelX = stageWidth;
  const panelWidth = width - panelX;

  context.clearRect(0, 0, width, height);
  const fallback = context.createLinearGradient(0, 0, width, height);
  fallback.addColorStop(0, "#c8daf4");
  fallback.addColorStop(0.52, "#f6d4c4");
  fallback.addColorStop(1, "#ffc88e");
  context.fillStyle = fallback;
  context.fillRect(0, 0, width, height);
  // Compose all three layers in the live canvas coordinates, then crop the whole
  // scene into the recording pane. Resizing must not separate Linh from the desk.
  const sceneWidth = avatar?.width || stageWidth;
  const sceneHeight = avatar?.height || dockTop - headerHeight;
  const sceneScale = Math.max(stageWidth / sceneWidth, (dockTop - headerHeight) / sceneHeight);
  const scene = interviewSceneLayout(sceneWidth, sceneHeight);
  context.save();
  context.beginPath();
  context.rect(0, headerHeight, stageWidth, dockTop - headerHeight);
  context.clip();
  context.translate((stageWidth - sceneWidth * sceneScale) / 2,
    headerHeight + (dockTop - headerHeight - sceneHeight * sceneScale) / 2);
  context.scale(sceneScale, sceneScale);
  const drawRoom = () => {
    if (background?.complete && background.naturalWidth > 0) {
      context.drawImage(background, scene.x, scene.y, scene.imageWidth, scene.imageHeight);
    }
  };
  drawRoom();
  if (avatar?.width && avatar.height) context.drawImage(avatar, 0, 0, sceneWidth, sceneHeight);
  if (background?.complete && background.naturalWidth > 0) {
    context.beginPath();
    scene.foreground.forEach(([x, y], i) => i === 0 ? context.moveTo(x, y) : context.lineTo(x, y));
    context.closePath();
    context.clip();
    drawRoom();
  }
  context.restore();

  context.fillStyle = "rgba(255, 250, 245, .96)";
  context.fillRect(panelX, headerHeight, panelWidth, dockTop - headerHeight);
  context.fillStyle = "rgba(255, 250, 245, .88)";
  context.fillRect(0, 0, width, headerHeight);
  context.fillStyle = "#152f50";
  context.font = "800 24px Nunito, Segoe UI, sans-serif";
  context.fillText("Cóc Đi Làm", 34, 48);
  context.font = "700 21px Nunito, Segoe UI, sans-serif";
  context.fillText(state.candidateName || "Phỏng vấn với Linh", 275, 46);
  context.fillStyle = "#637a97";
  context.font = "16px Nunito, Segoe UI, sans-serif";
  context.fillText("Buổi phỏng vấn", 275, 66);

  context.fillStyle = "#17304e";
  context.font = "800 23px Nunito, Segoe UI, sans-serif";
  context.fillText("Câu hỏi hiện tại", panelX + 30, headerHeight + 44);
  context.fillStyle = "#8491a5";
  context.font = "15px Nunito, Segoe UI, sans-serif";
  context.fillText("Hội thoại với Linh", panelX + 30, headerHeight + 69);

  const chatX = panelX + 24;
  const chatWidth = panelWidth - 48;
  const lineHeight = 23;
  const visibleMessages = state.messages.slice(-4);
  let y = headerHeight + 98;
  for (const message of visibleMessages) {
    context.font = "14px Nunito, Segoe UI, sans-serif";
    const lines = wrapText(context, message.text, chatWidth - 34, 4);
    const bubbleHeight = 34 + Math.max(1, lines.length) * lineHeight;
    if (y + bubbleHeight > dockTop - 30) break;
    const candidate = message.role === "candidate";
    context.fillStyle = candidate ? "#edf5ff" : "#fff0e2";
    context.strokeStyle = candidate ? "#d7e8ff" : "#f1dfd0";
    roundedRect(context, chatX + (candidate ? 16 : 0), y, chatWidth - (candidate ? 16 : 0), bubbleHeight, 15);
    context.fill();
    context.stroke();
    context.fillStyle = candidate ? "#4077bb" : "#ed7923";
    context.font = "700 12px Nunito, Segoe UI, sans-serif";
    context.fillText(candidate ? "Bạn" : "Linh", chatX + 14 + (candidate ? 16 : 0), y + 19);
    context.fillStyle = "#2c4566";
    context.font = "14px Nunito, Segoe UI, sans-serif";
    lines.forEach((line, index) => context.fillText(line, chatX + 14 + (candidate ? 16 : 0), y + 42 + index * lineHeight));
    y += bubbleHeight + 11;
  }
  if (state.interim.trim() && y + 62 < dockTop - 10) {
    const lines = wrapText(context, state.interim, chatWidth - 32, 2);
    const bubbleHeight = 38 + lines.length * lineHeight;
    context.fillStyle = "#edf5ff";
    roundedRect(context, chatX + 16, y, chatWidth - 16, bubbleHeight, 14);
    context.fill();
    context.fillStyle = "#4077bb";
    context.font = "700 12px Nunito, Segoe UI, sans-serif";
    context.fillText("Bạn đang nói", chatX + 30, y + 19);
    context.fillStyle = "#2c4566";
    context.font = "14px Nunito, Segoe UI, sans-serif";
    lines.forEach((line, index) => context.fillText(line, chatX + 30, y + 42 + index * lineHeight));
  }

  if (camera && camera.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
    const cameraWidth = 205;
    const cameraHeight = 154;
    const cameraX = 24;
    const cameraY = dockTop - cameraHeight - 20;
    context.save();
    roundedRect(context, cameraX, cameraY, cameraWidth, cameraHeight, 14);
    context.clip();
    context.translate(cameraX + cameraWidth, cameraY);
    context.scale(-1, 1);
    drawCover(context, camera, camera.videoWidth, camera.videoHeight, 0, 0, cameraWidth, cameraHeight);
    context.restore();
    context.fillStyle = "rgba(22, 42, 63, .72)";
    roundedRect(context, cameraX + 9, cameraY + cameraHeight - 31, cameraWidth - 18, 23, 7);
    context.fill();
    context.fillStyle = "#fff";
    context.font = "12px Nunito, Segoe UI, sans-serif";
    context.fillText(state.candidateName || "Bạn", cameraX + 18, cameraY + cameraHeight - 15);
  }

  context.fillStyle = "rgba(61, 43, 31, .84)";
  roundedRect(context, 18, dockTop + 12, width - 36, dockHeight - 24, 22);
  context.fill();
  context.fillStyle = "#fff";
  context.font = "800 20px Nunito, Segoe UI, sans-serif";
  context.fillText("Linh", 46, dockTop + 52);
  context.fillStyle = "#f5e9dc";
  context.font = "15px Nunito, Segoe UI, sans-serif";
  context.fillText(state.speaking ? "Linh đang nói" : "Đang ghi lại buổi phỏng vấn", 46, dockTop + 77);
  context.fillStyle = "#ef5044";
  context.beginPath();
  context.arc(width - 58, dockTop + 52, 9, 0, Math.PI * 2);
  context.fill();
  context.fillStyle = "#fff";
  context.font = "700 14px Nunito, Segoe UI, sans-serif";
  context.fillText("REC", width - 42, dockTop + 57);
}
