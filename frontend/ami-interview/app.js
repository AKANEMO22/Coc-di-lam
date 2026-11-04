'use strict';

const stage = document.querySelector('#stage');
const canvas = document.querySelector('#avatar');
const loading = document.querySelector('#loading');
const buttons = [...document.querySelectorAll('[data-action]')];
const labels = {
  listen: ['Đang lắng nghe', 'Ami chờ bạn trả lời, giữ ánh nhìn và chuyển động nhẹ.'],
  think: ['Đang suy nghĩ', 'Nhắm mắt, gật hai lần với nhịp chậm và nhẹ nhàng.'],
  speak: ['Đang nói thử', 'Mô phỏng khẩu hình trong 8 giây · chưa có âm thanh.'],
  wave: ['Chào bạn!', 'Rất vui được gặp bạn. Chúng ta bắt đầu nhé.'],
  nod: ['Gật đầu ghi nhận', 'Ami ghi nhận và theo dõi câu trả lời của bạn.'],
  smile: ['Mỉm cười', 'Một chút khích lệ để cuộc trò chuyện thoải mái hơn.'],
  write: ['Đang ghi chép', 'Ami xem lại ghi chú của cuộc phỏng vấn.'],
  disagree: ['Chưa đồng tình', 'Ami nhắm mắt và lắc đầu nhẹ, rồi trở về lắng nghe.'],
  clarify: ['Muốn làm rõ hơn', 'Ami hơi nghiêng đầu, chờ bạn giải thích thêm.'],
  reset: ['Trung tính', 'Sẵn sàng cho câu hỏi tiếp theo.']
};
let app, model, core, timer, action = 'listen', actionVersion = 0;
const DEFAULT_ZOOM = 1.17;
let zoom = DEFAULT_ZOOM, ready = false, actionStartedAt = 0;
let initialWidth = 1, initialHeight = 1;
let expressionData = new Map(), activeExpression = [];
const motionParameters = new Map();
const motionDurations = new Map();
let activeMotionIds = new Set();
const originalValues = new Map();

function frame() {
  if (!model) return;
  // Model canvas includes generous margins. Fit by height and crop below the waist.
  const scale = stage.clientHeight * 2.05 / initialHeight * zoom;
  model.scale.set(scale);
  model.x = stage.clientWidth / 2;
  model.y = stage.clientHeight * -.04;
}

function setParameter(id, value) {
  core.setParameterValueById(id, value);
}

function applyExpression(name) {
  activeExpression = expressionData.get(name) || [];
}

function updateExpression() {
  if (!ready) return;
  const t = performance.now() / 1000;
  // Tracking parameters must reach physics before it computes the visible head/eye pose.
  for (const parameter of activeExpression) {
    if (activeMotionIds.has(parameter.Id)) continue;
    const base = core.getParameterValueById(parameter.Id);
    const value = parameter.Blend === 'Multiply' ? base * parameter.Value :
      parameter.Blend === 'Overwrite' ? parameter.Value : base + parameter.Value;
    setParameter(parameter.Id, value);
  }
  if (action === 'listen') {
    setParameter('ParamAngleXTrack', Math.sin(t * .45) * 2);
    setParameter('ParamAngleYTrack', Math.sin(t * .65) * 1.2);
  }
  const elapsed = (performance.now() - actionStartedAt) / 1000;
  if (action === 'disagree') {
    const {yaw, pitch} = InterviewGestures.disagreementPose(elapsed);
    setParameter('ParamAngleXTrack', yaw);
    setParameter('ParamAngleYTrack', pitch);
    setParameter('ParamBodyAngleXTrack', yaw * .1);
  }
  if (action === 'clarify') {
    setParameter('ParamAngleZTrack', 7 + Math.sin(elapsed * .8) * .5);
    setParameter('ParamAngleYTrack', -2);
  }
}

function updateCharacter() {
  if (!ready) return;
  const elapsed = (performance.now() - actionStartedAt) / 1000;
  // Clothing and mouth are direct model parameters, held after motion/physics evaluation.
  // Blink processing must not reopen eyes that an expression deliberately closes.
  for (const parameter of activeExpression) {
    if (parameter.Id.endsWith('Track') || activeMotionIds.has(parameter.Id)) continue;
    const base = originalValues.get(parameter.Id) || 0;
    setParameter(parameter.Id, parameter.Blend === 'Multiply' ? base * parameter.Value :
      parameter.Blend === 'Overwrite' ? parameter.Value : base + parameter.Value);
  }
  setParameter('Param_Jacket', 10);
  setParameter('Param_Glasses', 0);
  if (action === 'think' || action === 'disagree') {
    setParameter('ParamEyeLOpen', 0);
    setParameter('ParamEyeROpen', 0);
  }
  if (action === 'disagree') {
    // Facial physics retains momentum across clicks. Hold the main head pose explicitly
    // so the first click and an immediate replay have the same trajectory.
    const {yaw, pitch} = InterviewGestures.disagreementPose(elapsed);
    setParameter('ParamAngleX2', yaw * .85);
    setParameter('ParamAngleX3', yaw * .9);
    setParameter('ParamAngleY2', pitch);
    setParameter('ParamAngleZ2', 0);
    setParameter('ParamAngleZ3', 0);
  }
  if (action === 'clarify') {
    setParameter('ParamBrowLY', .35);
    setParameter('ParamBrowRY', -.1);
    setParameter('ParamMouthForm', -.15);
  }
  if (action === 'smile') {
    setParameter('ParamMouthForm', 1);
    setParameter('ParamEyeLOpen', core.getParameterValueById('ParamEyeLOpen') * .85);
    setParameter('ParamEyeROpen', core.getParameterValueById('ParamEyeROpen') * .85);
    setParameter('EyeL_Squint', .12);
    setParameter('EyeR_Squint', .12);
  }
  if (action === 'speak') setParameter('ParamMouthOpenY', InterviewGestures.speechMouth(elapsed));
}

async function perform(next) {
  if (!ready || !Object.hasOwn(labels, next)) return;
  const version = ++actionVersion;
  clearTimeout(timer);
  action = next;
  actionStartedAt = performance.now();
  model.internalModel.motionManager.stopAllMotions();
  activeMotionIds = new Set();
  applyExpression('Default');
  document.querySelector('#status').textContent = labels[next][0];
  document.querySelector('#description').textContent = labels[next][1];
  document.body.classList.toggle('speaking', next === 'speak');
  buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.action === next)));
  let motion;
  if (next === 'think') motion = 'ThinkDouble';
  if (next === 'smile') applyExpression('Happy');
  if (next === 'wave') { applyExpression('Vay_Tay_1'); motion = 'Checkin2'; }
  if (next === 'nod') motion = 'Head_Nod';
  if (next === 'write') { applyExpression('Writting'); motion = 'Reading'; }
  if (motion) {
    activeMotionIds = motionParameters.get(motion) || new Set();
    try {
      const clip = await model.internalModel.motionManager.loadMotion(motion, 0);
      if (version !== actionVersion) return;
      clip.setIsLoop(false);
      const started = await model.motion(motion, 0, 3);
      if (version !== actionVersion) return;
      if (!started) throw new Error('Không thể bắt đầu chuyển động');
    } catch (error) {
      if (version === actionVersion) document.querySelector('#description').textContent = 'Không phát được động tác này. Hãy thử lại.';
      console.error(error);
    }
  }
  if (version !== actionVersion) return;
  if (['wave', 'nod', 'write', 'speak', 'disagree', 'think'].includes(next)) {
    const duration = motion ? (motionDurations.get(motion) || 3) * 1000 + 250 : next === 'disagree' ? InterviewGestures.DISAGREE_SECONDS * 1000 + 150 : 8000;
    timer = setTimeout(() => perform('listen'), duration);
  }
}

async function start() {
  try {
    if (!window.PIXI?.live2d || !window.Live2DCubismCore) throw new Error('Thiếu thư viện hiển thị.');
    app = new PIXI.Application({view: canvas, resizeTo: stage, backgroundAlpha: 0, antialias: true, autoDensity: true, resolution: Math.min(devicePixelRatio, 2)});
    const url = 'assets/model/ptit_sdk.model3.json';
    const response = await fetch(url);
    if (!response.ok) throw new Error('Không tải được cấu hình nhân vật.');
    const config = await response.json();
    // No automatic random gesture when idle: interview gestures are intentional.
    config.url = new URL(url, location.href).href;
    model = await PIXI.live2d.Live2DModel.from(config, {autoInteract: false, motionPreload: 'ALL'});
    core = model.internalModel.coreModel;
    // These decorations are separate meshes; preserve the underlying fabric and hair.
    const hiddenMeshes = new Set(['Shirt_Logo', 'Head_Deco', 'Head_Deco2']);
    const drawableIds = core.getModel().drawables.ids;
    const drawableOpacity = core.getDrawableOpacity.bind(core);
    core.getDrawableOpacity = index => hiddenMeshes.has(drawableIds[index]) ? 0 : drawableOpacity(index);
    // Widen the closed smile without changing its shape or stretching the face.
    // Return a render-only copy; never mutate the model's vertices across frames.
    const mouthMeshes = new Set(['Lower_Lip', 'Upper_Lip']);
    const lipBuffers = new Map();
    const drawableVertices = core.getDrawableVertices.bind(core);
    core.getDrawableVertices = index => {
      const source = drawableVertices(index);
      if (action !== 'smile' || !mouthMeshes.has(drawableIds[index])) return source;
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
    initialWidth = model.width;
    initialHeight = model.height;
    model.anchor.set(.5, 0);
    app.stage.addChild(model);
    await Promise.all(config.FileReferences.Expressions.map(async expression => {
      const res = await fetch(new URL(expression.File, config.url));
      if (!res.ok) throw new Error('Thiếu biểu cảm ' + expression.Name);
      expressionData.set(expression.Name, (await res.json()).Parameters || []);
    }));
    await Promise.all(Object.entries(config.FileReferences.Motions).map(async ([group, motions]) => {
      const res = await fetch(new URL(motions[0].File, config.url));
      if (!res.ok) throw new Error('Thiếu động tác ' + group);
      const data = await res.json();
      motionParameters.set(group, new Set(data.Curves.filter(c => c.Target === 'Parameter').map(c => c.Id)));
      motionDurations.set(group, data.Meta.Duration);
    }));
    // Restore a clean base before motion evaluation, not after it: nod/wave must keep their curves.
    const faceIds = new Set();
    for (const parameters of expressionData.values()) for (const p of parameters) faceIds.add(p.Id);
    for (const ids of motionParameters.values()) for (const id of ids) faceIds.add(id);
    faceIds.add('ParamMouthOpenY');
    faceIds.add('ParamMouthForm');
    faceIds.add('EyeL_Squint');
    faceIds.add('EyeR_Squint');
    faceIds.add('ParamBrowLY');
    faceIds.add('ParamBrowRY');
    for (const id of faceIds) originalValues.set(id, core.getParameterValueById(id));
    model.internalModel.on('beforeMotionUpdate', () => {
      for (const [id, value] of originalValues) setParameter(id, value);
    });
    model.internalModel.on('afterMotionUpdate', updateExpression);
    model.internalModel.on('beforeModelUpdate', updateCharacter);
    ready = true;
    frame();
    loading.hidden = true;
    document.querySelector('#connection').textContent = 'Sẵn sàng';
    for (const input of document.querySelectorAll('button, input')) input.disabled = false;
    await perform('listen');
  } catch (error) {
    console.error(error);
    document.querySelector('#connection').textContent = 'Chưa tải được';
    loading.querySelector('strong').textContent = 'Chưa tải được nhân vật';
    loading.querySelector('span:last-of-type').textContent = 'Kiểm tra các file assets rồi bấm thử lại.';
    loading.querySelector('.spinner').hidden = true;
    document.querySelector('#retry').hidden = false;
  }
}

buttons.forEach(button => button.addEventListener('click', () => perform(button.dataset.action)));
document.querySelector('#retry').addEventListener('click', () => location.reload());
document.querySelector('#zoom').addEventListener('input', event => {zoom = Number(event.target.value) / 100; frame();});
new ResizeObserver(frame).observe(stage);
document.addEventListener('visibilitychange', () => {
  if (document.hidden && ready) perform('listen');
});
start();
