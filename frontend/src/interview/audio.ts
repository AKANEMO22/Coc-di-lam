// Microphone capture (PCM16 16 kHz, 100 ms frames) and gapless playback of streamed PCM16 clips.

const WORKLET = `
class Pcm16k extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / 16000; this.pos = 0; this.out = new Int16Array(1600); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    // Linear-interpolating downsampler; the mic is band-limited enough for speech recognition.
    for (; this.pos < ch.length; this.pos += this.ratio) {
      const i = Math.floor(this.pos), f = this.pos - i;
      const s = ch[i] + ((i + 1 < ch.length ? ch[i + 1] : ch[i]) - ch[i]) * f;
      this.out[this.n++] = Math.max(-1, Math.min(1, s)) * 32767;
      if (this.n === this.out.length) { this.port.postMessage(this.out.buffer.slice(0)); this.n = 0; }
    }
    this.pos -= ch.length;
    return true;
  }
}
registerProcessor("pcm-16k", Pcm16k);
`;

let context: AudioContext | null = null;
let workletReady: Promise<void> | null = null;

function loadWorklet(ctx: AudioContext): Promise<void> {
  if (!workletReady) {
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    workletReady = ctx.audioWorklet.addModule(url)
      .catch(error => { workletReady = null; throw error; })
      .finally(() => URL.revokeObjectURL(url));
  }
  return workletReady;
}

/** One shared AudioContext; must be resumed from a user gesture. */
export function audioContext(): AudioContext {
  if (!context) context = new AudioContext({ latencyHint: "interactive" });
  if (context.state === "suspended") void context.resume();
  return context;
}

export class Microphone {
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private silentGain: GainNode | null = null;
  private opening: Promise<void> | null = null;
  private generation = 0;
  onFrame: ((frame: ArrayBuffer) => void) | null = null;
  level = 0; // 0..1 RMS of the last frame, for the input meter

  get ready(): boolean { return this.node !== null; }
  get mediaStream(): MediaStream | null { return this.stream; }

  async open(): Promise<void> {
    if (this.node) return;
    if (this.opening) return this.opening;
    const generation = this.generation;
    const pending = this.connect(generation);
    this.opening = pending;
    try { await pending; }
    finally { if (this.opening === pending) this.opening = null; }
  }

  private async connect(generation: number): Promise<void> {
    const ctx = audioContext();
    if (ctx.state === 'suspended') {
      await ctx.resume();
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    try { await loadWorklet(ctx); }
    catch (error) { stream.getTracks().forEach(track => track.stop()); throw error; }
    if (generation !== this.generation) {
      stream.getTracks().forEach(track => track.stop());
      throw new Error("Đã rời phòng phỏng vấn.");
    }
    this.stream = stream;
    const source = this.source = ctx.createMediaStreamSource(stream);
    this.node = new AudioWorkletNode(ctx, "pcm-16k");
    this.node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      const samples = new Int16Array(event.data);
      let sum = 0;
      for (let i = 0; i < samples.length; i += 4) sum += (samples[i] / 32768) ** 2;
      this.level = Math.min(1, Math.sqrt(sum / (samples.length / 4)) * 4);
      this.onFrame?.(event.data);
    };
    // In Chromium browsers, AudioWorkletProcessor.process() is only called if the node
    // is connected to the audio destination graph. Connect via a muted GainNode to pull
    // frames continuously without audio echo.
    const silentGain = ctx.createGain();
    silentGain.gain.value = 0;
    this.silentGain = silentGain;
    source.connect(this.node);
    this.node.connect(silentGain);
    silentGain.connect(ctx.destination);
  }

  close(): void {
    this.generation++;
    this.opening = null;
    this.source?.disconnect();
    this.source = null;
    this.node?.disconnect();
    this.silentGain?.disconnect();
    this.silentGain = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.node = null;
    this.stream = null;
    this.level = 0;
  }
}

/** Schedules PCM16 chunks back to back; `stop()` cuts playback immediately (barge-in). */
export class PcmPlayer {
  private rate = 24000;
  private next = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private analyser: AnalyserNode | null = null;
  private recordingDestination: MediaStreamAudioDestinationNode | null = null;
  private recordingMicrophone: MediaStreamAudioSourceNode | null = null;
  private samples = new Float32Array(512);
  onIdle: (() => void) | null = null;

  get playing(): boolean { return this.sources.size > 0; }

  setRate(rate: number): void { this.rate = rate; }

  /** Seconds until everything queued so far has been heard (0 when idle). */
  queuedSeconds(): number {
    return context && this.sources.size ? Math.max(0, this.next - context.currentTime) : 0;
  }

  /** 0..1 loudness of what is audible right now, for the avatar's mouth. */
  level(): number {
    if (!this.analyser || !this.sources.size) return 0;
    this.analyser.getFloatTimeDomainData(this.samples);
    let sum = 0;
    for (const sample of this.samples) sum += sample * sample;
    return Math.min(1, Math.sqrt(sum / this.samples.length) * 5);
  }

  private output(ctx: AudioContext): AudioNode {
    if (!this.analyser) {
      this.analyser = ctx.createAnalyser();
      this.analyser.fftSize = this.samples.length;
      this.analyser.connect(ctx.destination);
    }
    return this.analyser;
  }

  /** Mix Linh's WebAudio playback and the interview microphone for a local recording. */
  startRecordingAudio(microphone: MediaStream): MediaStream {
    this.stopRecordingAudio();
    const ctx = audioContext();
    const destination = ctx.createMediaStreamDestination();
    const output = this.output(ctx);
    output.connect(destination);
    this.recordingMicrophone = ctx.createMediaStreamSource(microphone);
    this.recordingMicrophone.connect(destination);
    this.recordingDestination = destination;
    return destination.stream;
  }

  stopRecordingAudio(): void {
    this.recordingMicrophone?.disconnect();
    this.recordingMicrophone = null;
    if (this.recordingDestination) {
      this.analyser?.disconnect(this.recordingDestination);
      this.recordingDestination.stream.getTracks().forEach((track) => track.stop());
      this.recordingDestination = null;
    }
  }

  push(data: ArrayBuffer): void {
    const ctx = audioContext();
    const pcm = new Int16Array(data);
    if (!pcm.length) return;
    const buffer = ctx.createBuffer(1, pcm.length, this.rate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.output(ctx));
    this.next = Math.max(this.next, ctx.currentTime + 0.05);
    source.start(this.next);
    this.next += buffer.duration;
    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
      if (!this.sources.size) this.onIdle?.();
    };
  }

  stop(): void {
    for (const source of this.sources) {
      source.onended = null;
      try { source.stop(); } catch { /* already stopped */ }
    }
    this.sources.clear();
    this.next = 0;
    this.onIdle?.();
  }
}
