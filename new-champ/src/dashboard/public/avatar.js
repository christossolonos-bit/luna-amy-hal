// Avatar studio: a 3D companion that talks through the bot's brain (Ollama) and voice (Fish Audio).
//
// The animation drivers are ports of GlbPlayer's C# ones (BlinkDriver, GazeDriver, HeadMotionDriver,
// the arm rest pose from GlbViewControl). The big difference is lip sync: SAPI reported visemes, but
// Fish Audio only gives us audio, so the mouth is driven by analysing the clip as it plays.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const $ = (s) => document.querySelector(s);
const clamp = (v, lo = 0, hi = 1) => (v < lo ? lo : v > hi ? hi : v);
const smoothstep = (t) => { t = clamp(t); return t * t * (3 - 2 * t); };
// Frame-rate independent easing: k is the fraction covered per 1/60 s.
const ease = (k, dt) => 1 - Math.pow(1 - k, dt * 60);

// ---------------------------------------------------------------- settings

const DEFAULTS = {
  name: '', voice: true, volume: 0.9, handsFree: false, subs: true,
  blink: true, gaze: true, head: true, intensity: 1, lips: 1, expr: 1, restSmile: 0.6,
  backdrop: 'studio', frame: 'bust', collapsed: false,
};
let opts = { ...DEFAULTS };
try { opts = { ...DEFAULTS, ...JSON.parse(localStorage.getItem('avatarOpts') || '{}') }; } catch {}
const save = () => { try { localStorage.setItem('avatarOpts', JSON.stringify(opts)); } catch {} };

// ---------------------------------------------------------------- scene

const canvas = $('#stage');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.55;

const key = new THREE.DirectionalLight(0xfff1e0, 1.7);
key.position.set(1.4, 2.4, 2.6);
const rim = new THREE.DirectionalLight(0x9fb4ff, 1.4);
rim.position.set(-2, 2.2, -2.2);
const fill = new THREE.HemisphereLight(0xdfe6ff, 0x302838, 0.35);
scene.add(key, rim, fill);

const camera = new THREE.PerspectiveCamera(26, 1, 0.05, 50);
camera.position.set(0, 1.5, 2);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.enablePan = false;
controls.minDistance = 0.35;
controls.maxDistance = 6;

function resize() {
  // The panel covers the right of the screen, so the avatar is framed in the space left of it.
  // Rendering a window of a larger virtual view shifts the centre without distorting anything.
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  const collapsed = document.body.classList.contains('collapsed');
  const phone = innerWidth <= 760;
  const panel = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--panel-w')) || 0;
  const side = collapsed || phone ? 0 : panel + 16;
  // Phone: the chat is a bottom sheet covering ~46% of the height, so centre higher up.
  const below = phone && !collapsed ? h * 0.32 : 0;
  camera.aspect = (w + side) / (h + below);
  camera.setViewOffset(w + side, h + below, side, below, w, h);
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);

// ---------------------------------------------------------------- model

let model = null;
const morphs = new Map(); // morph target name -> [{ mesh, index }]
const bones = {};
const rest = new Map(); // bone -> rest quaternion (after the arm pose)
let modelHeight = 1.7;

const loader = new GLTFLoader();
async function loadModel(url) {
  $('#loading').hidden = false;
  $('#loadingText').textContent = 'Loading avatar…';
  $('#loadingBar').style.width = '0';
  let gltf;
  try {
    gltf = await loader.loadAsync(url, (e) => {
      if (e.total) $('#loadingBar').style.width = `${(100 * e.loaded) / e.total}%`;
    });
  } catch (err) {
    $('#loadingText').textContent = `Couldn't load the avatar: ${err.message || err}`;
    setState('error', 'No avatar');
    return;
  }
  if (model) scene.remove(model);
  model = gltf.scene;
  morphs.clear();
  rest.clear();
  for (const k of Object.keys(bones)) delete bones[k];

  model.traverse((o) => {
    if (o.isBone) bones[o.name.replace(/^.*[:|]/, '')] ??= o;
    if (o.isMesh) {
      o.frustumCulled = false; // skinned meshes are often culled by their bind-pose bounds
      if (o.material) o.material.envMapIntensity = 1;
    }
    if (o.isMesh && o.morphTargetDictionary) {
      for (const [name, index] of Object.entries(o.morphTargetDictionary)) {
        if (!morphs.has(name)) morphs.set(name, []);
        morphs.get(name).push({ mesh: o, index });
      }
    }
  });
  scene.add(model);
  model.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(model);
  modelHeight = box.max.y - box.min.y || 1.7;

  poseArms();
  for (const name of ['Spine1', 'Spine2', 'Neck', 'Head', 'LeftShoulder', 'RightShoulder']) if (bones[name]) rest.set(bones[name], bones[name].quaternion.clone());

  $('#loading').hidden = true;
  frame(opts.frame, true);
  if (!morphs.has('eyeBlinkLeft') && !morphs.has('viseme_aa')) toast('This avatar has no ARKit or viseme face shapes, so it can’t blink or lip sync.');
  if (state === 'loading') setState('idle');
}

// Rotates a bone by a world-space rotation, keeping the rest of its local transform.
const _p = new THREE.Quaternion();
const _m = new THREE.Quaternion();
function rotateWorld(bone, worldRot) {
  bone.parent.getWorldQuaternion(_p);
  _m.copy(_p).invert().multiply(worldRot).multiply(_p);
  bone.quaternion.premultiply(_m);
}

// Ported from GlbViewControl.BuildArmPose: measure where each arm points and rotate it to hang
// down, rather than trusting per-rig Euler angles. Then a slight elbow bend so it looks relaxed.
function poseArms() {
  const chains = [
    ['LeftArm', 'LeftForeArm', 'LeftHand'],
    ['RightArm', 'RightForeArm', 'RightHand'],
  ];
  const a = new THREE.Vector3(), b = new THREE.Vector3();
  const aim = (bone, child, wanted) => {
    model.updateMatrixWorld(true);
    bone.getWorldPosition(a);
    child.getWorldPosition(b);
    const current = b.sub(a).normalize();
    if (current.dot(wanted) > 0.9999) return;
    rotateWorld(bone, new THREE.Quaternion().setFromUnitVectors(current, wanted));
  };
  for (const [arm, fore, hand] of chains) {
    if (!bones[arm] || !bones[fore]) continue;
    model.updateMatrixWorld(true);
    bones[arm].getWorldPosition(a);
    bones[fore].getWorldPosition(b);
    const out = Math.sign(b.x - a.x) || 1; // which side this arm is on
    aim(bones[arm], bones[fore], new THREE.Vector3(0.16 * out, -1, 0.05).normalize());
    if (bones[hand]) aim(bones[fore], bones[hand], new THREE.Vector3(0.1 * out, -1, 0.22).normalize());
  }
  model.updateMatrixWorld(true);
}

// ---------------------------------------------------------------- camera framing

let camTween = null;
function frame(kind, instant = false) {
  opts.frame = kind;
  save();
  document.querySelectorAll('#framing button').forEach((b) => b.classList.toggle('on', b.dataset.frame === kind));
  if (!model) return;
  const head = new THREE.Vector3();
  (bones.Head ?? model).getWorldPosition(head);
  // Distance that fits a span of `tall` x `wide` metres in the visible area (left of the panel).
  const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2);
  const visibleAspect = camera.view ? camera.view.width / camera.view.height : camera.aspect;
  const fit = (tall, wide) => Math.max(tall / 2 / tan, wide / 2 / (tan * visibleAspect));
  let target, dist;
  if (kind === 'face') { target = head.clone().add(new THREE.Vector3(0, 0.06, 0)); dist = fit(0.36, 0.3); }
  else if (kind === 'bust') { target = head.clone().add(new THREE.Vector3(0, -0.16, 0)); dist = fit(0.85, 0.65); }
  else { target = new THREE.Vector3(0, modelHeight / 2, 0); dist = fit(modelHeight * 1.12, 0.8); }
  const pos = target.clone().add(new THREE.Vector3(0, 0.03, dist));
  if (instant) {
    controls.target.copy(target);
    camera.position.copy(pos);
    controls.update();
    return;
  }
  camTween = { t: 0, fromT: controls.target.clone(), fromP: camera.position.clone(), toT: target, toP: pos };
}

// ---------------------------------------------------------------- face: one weight per target per frame

const weights = new Map();
const managed = new Set();
const add = (name, w) => { if (w > 0.0005) weights.set(name, (weights.get(name) || 0) + w); };
function applyWeights() {
  for (const name of weights.keys()) managed.add(name);
  for (const name of managed) {
    const w = clamp(weights.get(name) || 0);
    for (const { mesh, index } of morphs.get(name) ?? []) mesh.morphTargetInfluences[index] = w;
  }
  weights.clear();
}

// Blink (BlinkDriver): fast close, slower open, random interval, sometimes a double blink.
const blink = { until: 2000, elapsed: -1, double: false };
const CLOSE_MS = 90, OPEN_MS = 170;
function updateBlink(ms) {
  if (!opts.blink) return;
  if (blink.elapsed >= 0) {
    blink.elapsed += ms;
    if (blink.elapsed >= CLOSE_MS + OPEN_MS) {
      blink.elapsed = -1;
      blink.until = blink.double ? 120 : 2000 + Math.random() * 4000;
      blink.double = false;
      return;
    }
    const w = blink.elapsed <= CLOSE_MS ? smoothstep(blink.elapsed / CLOSE_MS) : smoothstep(1 - (blink.elapsed - CLOSE_MS) / OPEN_MS);
    add('eyeBlinkLeft', w);
    add('eyeBlinkRight', w);
    return;
  }
  blink.until -= ms;
  if (blink.until <= 0) {
    blink.elapsed = 0;
    blink.double = Math.random() < 0.18;
  }
}

// Gaze (GazeDriver): eyes ease toward the pointer, measured from the face on screen.
// "In"/"out" are relative to the nose, so the two eyes take opposite targets horizontally.
const gaze = { tx: 0, ty: 0, x: 0, y: 0, lastMove: 0, idleAt: 0 };
const pointer = { x: null, y: null };
addEventListener('pointermove', (e) => { pointer.x = e.clientX; pointer.y = e.clientY; gaze.lastMove = performance.now(); });
document.addEventListener('pointerleave', () => { pointer.x = null; });
const _head = new THREE.Vector3();
function updateGaze(dt, now) {
  if (!opts.gaze || !bones.Head) { gaze.tx = gaze.ty = 0; }
  else if (pointer.x !== null && now - gaze.lastMove < 4000) {
    bones.Head.getWorldPosition(_head).add(new THREE.Vector3(0, 0.07, 0)).project(camera);
    const ox = ((_head.x + 1) / 2) * innerWidth;
    const oy = ((1 - _head.y) / 2) * innerHeight;
    gaze.tx = clamp((pointer.x - ox) / 520, -1, 1);
    gaze.ty = clamp((pointer.y - oy) / 520, -1, 1);
  } else if (now > gaze.idleAt) {
    // Nobody's pointing: small random glances, mostly back toward the camera.
    const away = Math.random() < 0.35;
    gaze.tx = away ? (Math.random() - 0.5) * 0.8 : 0;
    gaze.ty = away ? (Math.random() - 0.5) * 0.4 : 0;
    gaze.idleAt = now + 1200 + Math.random() * 2600;
  }
  // Feelings pull the eyes too: shy looks down and away, thinking looks up.
  const k = ease(0.25, dt);
  gaze.x += (clamp(gaze.tx + fx.gx, -1, 1) - gaze.x) * k;
  gaze.y += (clamp(gaze.ty + fx.gy, -1, 1) - gaze.y) * k;
  if (fx.gaze) { gaze.x = fx.gaze.x; gaze.y = fx.gaze.y; } // eye roll

  const MAX = 0.55;
  const h = Math.abs(gaze.x) * MAX, v = Math.abs(gaze.y) * MAX;
  if (gaze.x > 0) { add('eyeLookOutLeft', h); add('eyeLookInRight', h); }
  else { add('eyeLookInLeft', h); add('eyeLookOutRight', h); }
  if (gaze.y > 0) { add('eyeLookDownLeft', v); add('eyeLookDownRight', v); }
  else { add('eyeLookUpLeft', v); add('eyeLookUpRight', v); }
}

// Head motion (HeadMotionDriver): layered sines at unrelated periods plus occasional glances.
// The head also turns a little toward where the eyes are looking, and nods with speech.
const headState = { t: 0, gYaw: 0, gPitch: 0, decay: 0, next: 5 + Math.random() * 8 };
const _q = new THREE.Quaternion();
const _e = new THREE.Euler();
function updateHead(dt) {
  for (const [bone, q] of rest) bone.quaternion.copy(q);
  const s = headState;
  s.t += dt;
  const t = s.t;

  // Breathing: a slow, tiny lift of the chest (deeper during a sigh).
  const breath = Math.sin(t * 1.6) * THREE.MathUtils.degToRad(0.7) * fx.breath;
  if (bones.Spine2) rotateWorld(bones.Spine2, _q.setFromEuler(_e.set(-breath, 0, 0)));

  // Shrugs and laughs lift the shoulders. Each shoulder bone points outward, so raising its far
  // end is a turn about Z in opposite directions on each side.
  if (fx.shoulders > 0.001) {
    const lift = THREE.MathUtils.degToRad(9) * fx.shoulders;
    if (bones.LeftShoulder) rotateWorld(bones.LeftShoulder, _q.setFromEuler(_e.set(0, 0, lift)));
    if (bones.RightShoulder) rotateWorld(bones.RightShoulder, _q.setFromEuler(_e.set(0, 0, -lift)));
  }

  const deg = THREE.MathUtils.degToRad;
  // Feeling and gesture poses apply even with idle motion off.
  const poseP = deg(fx.pitch), poseY = deg(fx.yaw), poseR = deg(fx.roll);
  if (!opts.head) {
    if (bones.Neck) rotateWorld(bones.Neck, _q.setFromEuler(_e.set(poseP * 0.4, poseY * 0.4, poseR * 0.4, 'YXZ')));
    if (bones.Head) rotateWorld(bones.Head, _q.setFromEuler(_e.set(poseP * 0.6, poseY * 0.6, poseR * 0.6, 'YXZ')));
    return;
  }
  let yaw = 0.55 * Math.sin(t * 0.23) + 0.3 * Math.sin(t * 0.61 + 1.7) + 0.15 * Math.sin(t * 1.3 + 0.4);
  let pitch = 0.5 * Math.sin(t * 0.19 + 2.1) + 0.32 * Math.sin(t * 0.53 + 0.9) + 0.18 * Math.sin(t * 1.1 + 2.7);
  const roll = 0.6 * Math.sin(t * 0.17 + 0.6) + 0.4 * Math.sin(t * 0.44 + 1.2);

  s.next -= dt;
  if (s.next <= 0) {
    s.gYaw = (Math.random() - 0.5) * 2;
    s.gPitch = (Math.random() - 0.5) * 1.2;
    s.decay = 1;
    s.next = 6 + Math.random() * 10;
  }
  if (s.decay > 0) s.decay = Math.max(0, s.decay - dt / 2.5);
  const g = smoothstep(s.decay);
  yaw += s.gYaw * g;
  pitch += s.gPitch * g;

  const speak = state === 'speaking' ? 1.45 : 1;
  const i = opts.intensity * speak * fx.motion;
  const y = deg(yaw * 3.2 * i) + gaze.x * deg(7) + poseY;
  const p = deg(pitch * 2 * i) + gaze.y * deg(4) + lips.level * deg(2.2) * opts.intensity + poseP;
  const r = deg(roll * 1.4 * i) + poseR;

  // Shared between neck and head so the bend isn't all at one joint.
  if (bones.Neck) rotateWorld(bones.Neck, _q.setFromEuler(_e.set(p * 0.4, y * 0.4, r * 0.4, 'YXZ')));
  if (bones.Head) rotateWorld(bones.Head, _q.setFromEuler(_e.set(p * 0.6, y * 0.6, r * 0.6, 'YXZ')));
}

// ---------------------------------------------------------------- feelings

// There are no presets. The server keeps her emotional state as a blend of feelings (0..1 each),
// updated from the conversation, and the face is the weighted sum of every feeling's expression.
// Each expression also biases head pose (degrees; +pitch is chin down), gaze, how much she moves,
// and how often she blinks, so "sad" droops and looks down while "excited" is wide-eyed and lively.
const LR = (base, v) => ({ [base + 'Left']: v, [base + 'Right']: v });
const X = (face, more = {}) => ({ face, pitch: 0, yaw: 0, roll: 0, gx: 0, gy: 0, motion: 1, blink: 1, ...more });
const EXPRESSIONS = {
  calm: X({ ...LR('mouthSmile', 0.08) }, { motion: 0.85 }),
  joy: X({ ...LR('mouthSmile', 0.62), ...LR('cheekSquint', 0.38), ...LR('eyeSquint', 0.15), browInnerUp: 0.1 }, { pitch: -1.5, motion: 1.2 }),
  amused: X({ mouthSmileLeft: 0.55, mouthSmileRight: 0.42, ...LR('cheekSquint', 0.45), ...LR('eyeSquint', 0.35), ...LR('mouthDimple', 0.2) }, { roll: 3, pitch: -1, motion: 1.25 }),
  fond: X({ ...LR('mouthSmile', 0.3), ...LR('eyeSquint', 0.12), ...LR('eyeBlink', 0.12), browInnerUp: 0.18 }, { roll: 4, pitch: 1.5, motion: 0.9 }),
  playful: X({ mouthSmileLeft: 0.48, mouthSmileRight: 0.18, browOuterUpLeft: 0.42, eyeSquintRight: 0.22, mouthLeft: 0.1 }, { roll: -5, yaw: 3, motion: 1.2 }),
  smug: X({ mouthSmileLeft: 0.5, mouthPressRight: 0.22, ...LR('eyeSquint', 0.3), browDownRight: 0.15, ...LR('eyeBlink', 0.1) }, { pitch: -4, roll: 2 }),
  excited: X({ ...LR('mouthSmile', 0.7), browInnerUp: 0.4, ...LR('browOuterUp', 0.45), ...LR('eyeWide', 0.35), jawOpen: 0.06 }, { pitch: -2, motion: 1.6, blink: 1.3 }),
  flirty: X({ mouthSmileLeft: 0.36, mouthSmileRight: 0.24, ...LR('eyeBlink', 0.22), browOuterUpLeft: 0.15, ...LR('cheekSquint', 0.12) }, { roll: 6, pitch: 2.5, gx: -0.22, gy: 0.05, motion: 0.9 }),
  confident: X({ ...LR('mouthSmile', 0.2), ...LR('eyeSquint', 0.1), ...LR('browDown', 0.06) }, { pitch: -3, motion: 0.9 }),
  curious: X({ browInnerUp: 0.3, browOuterUpLeft: 0.45, ...LR('eyeWide', 0.15), mouthPucker: 0.08 }, { roll: 7, yaw: 2, pitch: 1 }),
  pensive: X({ browInnerUp: 0.2, browDownLeft: 0.25, mouthPressLeft: 0.25, mouthRollLower: 0.2, mouthLeft: 0.15 }, { pitch: -2, gx: -0.4, gy: -0.45, motion: 0.7 }),
  confused: X({ browDownLeft: 0.45, browOuterUpRight: 0.4, browInnerUp: 0.15, mouthLeft: 0.2, mouthPressRight: 0.2, eyeSquintLeft: 0.2 }, { roll: -6 }),
  skeptical: X({ browDownLeft: 0.5, browOuterUpRight: 0.45, eyeSquintLeft: 0.35, mouthPressLeft: 0.3, mouthRight: 0.15, mouthSmileRight: 0.1 }, { pitch: -2, roll: 3 }),
  surprised: X({ browInnerUp: 0.7, ...LR('browOuterUp', 0.6), ...LR('eyeWide', 0.55), jawOpen: 0.12, mouthFunnel: 0.1 }, { pitch: -3, motion: 1.2 }),
  worried: X({ browInnerUp: 0.65, ...LR('browDown', 0.1), ...LR('eyeWide', 0.2), ...LR('mouthPress', 0.25), ...LR('mouthStretch', 0.15), ...LR('mouthFrown', 0.1) }, { pitch: 2, gy: 0.1, blink: 1.6 }),
  sad: X({ browInnerUp: 0.6, ...LR('mouthFrown', 0.45), ...LR('mouthPress', 0.15), mouthShrugLower: 0.2, ...LR('eyeBlink', 0.18) }, { pitch: 6, gy: 0.3, motion: 0.6 }),
  embarrassed: X({ ...LR('mouthSmile', 0.25), ...LR('mouthPress', 0.2), ...LR('eyeBlink', 0.2), browInnerUp: 0.25, ...LR('cheekSquint', 0.15) }, { pitch: 6, yaw: -6, gx: -0.35, gy: 0.35, blink: 1.5 }),
  annoyed: X({ ...LR('browDown', 0.45), ...LR('eyeSquint', 0.25), ...LR('mouthPress', 0.35), ...LR('mouthFrown', 0.15), ...LR('noseSneer', 0.1) }, { pitch: -1, motion: 0.9 }),
  angry: X({ ...LR('browDown', 0.85), ...LR('eyeWide', 0.12), ...LR('noseSneer', 0.4), ...LR('mouthPress', 0.4), ...LR('mouthFrown', 0.3), ...LR('mouthUpperUp', 0.15), jawForward: 0.1 }, { pitch: 3, motion: 1.1, blink: 0.6 }),
  disgusted: X({ ...LR('noseSneer', 0.6), ...LR('mouthUpperUp', 0.45), ...LR('browDown', 0.35), ...LR('eyeSquint', 0.3), ...LR('mouthFrown', 0.25) }, { pitch: -3, yaw: 6 }),
  bored: X({ ...LR('eyeBlink', 0.3), ...LR('mouthPress', 0.1), mouthLeft: 0.2, mouthRollLower: 0.1 }, { roll: -4, gx: 0.3, gy: -0.1, motion: 0.5, blink: 0.7 }),
  tired: X({ ...LR('eyeBlink', 0.45), browInnerUp: 0.2, ...LR('mouthFrown', 0.1), jawOpen: 0.03 }, { pitch: 5, motion: 0.5, blink: 0.6 }),
  determined: X({ ...LR('browDown', 0.35), ...LR('mouthPress', 0.3), ...LR('eyeSquint', 0.15), jawForward: 0.1 }, { pitch: 1 }),
  scared: X({ browInnerUp: 0.7, ...LR('eyeWide', 0.7), ...LR('mouthStretch', 0.35), jawOpen: 0.08 }, { pitch: 3, motion: 1.3, blink: 1.5 }),
};
const NEGATIVE = ['sad', 'annoyed', 'angry', 'disgusted', 'worried', 'scared', 'tired', 'bored', 'skeptical'];
const SPEECH_SAFE = /Smile|cheek|eye|brow|nose/; // mouth shapes that would fight lip sync get damped while talking

const feel = { target: {}, now: {} };
const HALF_LIFE = 90; // seconds, matching the server
function setFeelings(f) {
  feel.target = { ...(f || {}) };
  renderFeelings();
}

// Per-frame effects of feelings and gestures, read by gaze, blink and head motion.
let fx;
function updateFeelings(dt) {
  const decay = Math.pow(0.5, dt / HALF_LIFE);
  for (const k of Object.keys(feel.target)) if ((feel.target[k] *= decay) < 0.03) delete feel.target[k];
  const goal = { ...feel.target };
  if (state === 'thinking') goal.pensive = Math.max(goal.pensive || 0, 0.5);

  fx = { pitch: 0, yaw: 0, roll: 0, gx: 0, gy: 0, motion: 1, blink: 1, shoulders: 0, breath: 1, gaze: null };
  const k = ease(0.06, dt);
  const strength = opts.expr;
  const talking = clamp(lips.level * 3);
  let negative = 0;
  for (const name of new Set([...Object.keys(feel.now), ...Object.keys(goal)])) {
    const v = (feel.now[name] = (feel.now[name] || 0) + ((goal[name] || 0) - (feel.now[name] || 0)) * k);
    if (v < 0.005) { delete feel.now[name]; continue; }
    const e = EXPRESSIONS[name];
    if (!e) continue;
    if (NEGATIVE.includes(name)) negative += v;
    const w = v * strength;
    for (const [shape, amount] of Object.entries(e.face)) add(shape, amount * w * (SPEECH_SAFE.test(shape) ? 1 : 1 - 0.55 * talking));
    fx.pitch += e.pitch * w; fx.yaw += e.yaw * w; fx.roll += e.roll * w;
    fx.gx += e.gx * w; fx.gy += e.gy * w;
    fx.motion += (e.motion - 1) * v; fx.blink += (e.blink - 1) * v;
  }
  // A resting smile that fades when she's upset.
  const rest = opts.restSmile * 0.25 * clamp(1 - negative);
  add('mouthSmileLeft', rest);
  add('mouthSmileRight', rest);
  fx.motion = Math.max(0.3, fx.motion);
  fx.blink = Math.max(0.4, fx.blink);
}

// Gestures: short one-off motions, from the model's tags or the Expressions menu.
const bell = (t) => (t < 0.25 ? smoothstep(t / 0.25) : t > 0.7 ? smoothstep((1 - t) / 0.3) : 1);
const GESTURE_DEFS = {
  wink: [650, (t, o) => { const b = bell(t); add('eyeBlinkLeft', b); add('mouthSmileLeft', 0.35 * b); add('cheekSquintLeft', 0.3 * b); o.roll += 3 * b; }],
  nod: [900, (t, o) => { o.pitch += Math.sin(t * Math.PI * 4) * 7 * (1 - t); }],
  shake: [1100, (t, o) => { o.yaw += Math.sin(t * Math.PI * 4) * 9 * (1 - t); }],
  tilt: [1600, (t, o) => { o.roll += bell(t) * 9; }],
  laugh: [1400, (t, o) => {
    const b = bell(t), bounce = Math.abs(Math.sin(t * Math.PI * 7));
    for (const [s, v] of Object.entries({ ...LR('mouthSmile', 0.6), ...LR('cheekSquint', 0.45), ...LR('eyeSquint', 0.4) })) add(s, v * b);
    add('jawOpen', 0.2 * b * bounce);
    o.pitch += (-3 + bounce * 2.5) * b;
    o.shoulders += 0.3 * b * bounce;
  }],
  sigh: [1800, (t, o) => { const b = bell(t); add('eyeBlinkLeft', 0.35 * b); add('eyeBlinkRight', 0.35 * b); add('browInnerUp', 0.25 * b); o.pitch += 4 * b; o.breath += 4 * b; o.shoulders += 0.5 * Math.sin(Math.PI * t); }],
  eyeroll: [1200, (t, o) => { const b = bell(t); o.gaze = { x: Math.cos(Math.PI * (1 - t)) * 0.8, y: -0.95 * b }; add('browOuterUpLeft', 0.25 * b); add('browOuterUpRight', 0.25 * b); o.pitch -= 3 * b; o.roll += 3 * b; }],
  shrug: [1300, (t, o) => { const b = bell(t); o.shoulders += b; add('mouthShrugLower', 0.4 * b); add('browInnerUp', 0.3 * b); add('browOuterUpLeft', 0.3 * b); add('browOuterUpRight', 0.3 * b); o.roll += 5 * b; }],
  gasp: [800, (t, o) => { const b = bell(t); add('eyeWideLeft', 0.6 * b); add('eyeWideRight', 0.6 * b); add('browInnerUp', 0.5 * b); add('jawOpen', 0.25 * b); o.pitch -= 3 * b; }],
};
const activeGestures = [];
function playGesture(name, delay = 0) {
  if (GESTURE_DEFS[name]) activeGestures.push({ name, start: performance.now() + delay });
}
function updateGestures(now) {
  for (let i = activeGestures.length - 1; i >= 0; i--) {
    const g = activeGestures[i];
    const [ms, run] = GESTURE_DEFS[g.name];
    const t = (now - g.start) / ms;
    if (t < 0) continue;
    if (t >= 1) { activeGestures.splice(i, 1); continue; }
    run(t, fx);
  }
}

// ---------------------------------------------------------------- lip sync from audio

// Fish Audio returns only audio, so the mouth shape is estimated from it each frame: loudness opens
// the jaw, and where the energy sits in the spectrum picks the shape - dark (O/U), open (aa),
// bright (E/I), or hissy (S/SH). Coarser than real visemes, but it tracks the voice exactly.
let audioCtx = null, analyser = null, gainNode = null;
let timeBuf, freqBuf;
const lips = { level: 0, cur: {}, fake: null };
const VISEMES = ['viseme_aa', 'viseme_O', 'viseme_U', 'viseme_E', 'viseme_I', 'viseme_SS', 'viseme_CH', 'viseme_PP', 'jawOpen'];

function ensureAudio() {
  if (audioCtx) return audioCtx;
  audioCtx = new AudioContext();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.35;
  gainNode = audioCtx.createGain();
  gainNode.gain.value = opts.volume;
  analyser.connect(gainNode).connect(audioCtx.destination);
  timeBuf = new Float32Array(analyser.fftSize);
  freqBuf = new Uint8Array(analyser.frequencyBinCount);
  return audioCtx;
}

function updateLips(dt, now) {
  const target = {};
  let vol = 0;
  if (analyser && playing) {
    analyser.getFloatTimeDomainData(timeBuf);
    let sum = 0;
    for (const v of timeBuf) sum += v * v;
    const rms = Math.sqrt(sum / timeBuf.length);
    vol = clamp((rms - 0.012) * 7 * opts.lips);

    analyser.getByteFrequencyData(freqBuf);
    const hz = audioCtx.sampleRate / analyser.fftSize;
    const band = (lo, hi) => {
      let s = 0, n = 0;
      for (let i = Math.floor(lo / hz); i <= Math.ceil(hi / hz) && i < freqBuf.length; i++) { s += freqBuf[i]; n++; }
      return n ? s / n / 255 : 0;
    };
    const low = band(150, 900), mid = band(900, 2400), high = band(3200, 8000);
    const hiss = clamp((high / (low + mid + high + 1e-6) - 0.28) * 3.5);
    const bright = mid / (low + mid + 1e-6);
    const open = vol * (1 - hiss * 0.7);
    const wO = clamp(1 - bright / 0.38), wE = clamp((bright - 0.5) / 0.3), wA = clamp(1 - wO - wE);
    target.viseme_aa = open * wA * 0.85;
    target.viseme_O = open * wO * 0.55;
    target.viseme_U = open * wO * 0.35;
    target.viseme_E = open * wE * 0.55;
    target.viseme_I = open * wE * 0.35;
    target.jawOpen = open * 0.22;
    target.viseme_SS = vol * hiss * 0.6;
    target.viseme_CH = vol * hiss * 0.25;
  } else if (lips.fake && now < lips.fake.until) {
    // No audio (voice off or Fish failed): flap the mouth for roughly as long as the line takes to say.
    const t = (now - lips.fake.start) / 1000;
    vol = clamp(0.35 + 0.35 * Math.sin(t * 17) + 0.2 * Math.sin(t * 7.3 + 1));
    target.viseme_aa = vol * 0.5;
    target.viseme_O = vol * 0.2 * (Math.sin(t * 3.1) > 0.4 ? 1 : 0);
    target.jawOpen = vol * 0.15;
  }
  lips.level += (vol - lips.level) * ease(0.3, dt);
  for (const name of VISEMES) {
    const cur = lips.cur[name] || 0;
    const tgt = target[name] || 0;
    lips.cur[name] = cur + (tgt - cur) * ease(tgt > cur ? 0.55 : 0.3, dt);
    add(name, lips.cur[name]);
  }
}

// ---------------------------------------------------------------- speech queue

const speech = []; // { text, buffer, feelings, gestures }
let playing = null; // the BufferSource now playing
let speakToken = 0;

function enqueueSpeech(item) {
  speech.push(item);
  if (!playing && !lips.fake) playNext();
}

function playNext() {
  const item = speech.shift();
  if (!item) {
    caption(null);
    if (state === 'speaking') setState(streaming ? 'thinking' : 'idle');
    return;
  }
  // Feelings and gestures land as the sentence starts, not when it arrives.
  if (item.feelings) setFeelings(item.feelings);
  item.gestures?.forEach((g, i) => playGesture(g, i * 700));
  if (!item.text) return playNext(); // a tag on its own: nothing to say
  setState('speaking');
  caption(item.text);
  const token = speakToken;
  if (item.buffer) {
    const src = audioCtx.createBufferSource();
    src.buffer = item.buffer;
    src.connect(analyser);
    src.onended = () => {
      if (token !== speakToken) return;
      playing = null;
      playNext();
    };
    playing = src;
    src.start();
  } else {
    const ms = Math.max(900, item.text.length * 62);
    lips.fake = { start: performance.now(), until: performance.now() + ms };
    setTimeout(() => {
      if (token !== speakToken) return;
      lips.fake = null;
      playNext();
    }, ms + 150);
  }
}

function stopSpeaking() {
  speakToken++;
  speech.length = 0;
  lips.fake = null;
  if (playing) { try { playing.stop(); } catch {} playing = null; }
  caption(null);
}

function caption(text) {
  if (text && opts.subs) $('#captionText').textContent = text;
  $('#caption').classList.toggle('show', Boolean(text && opts.subs));
}

// ---------------------------------------------------------------- chat

let streaming = null; // AbortController for the reply in flight
let state = 'loading';

function setState(next, label) {
  state = next;
  const labels = { idle: 'Ready', thinking: 'Thinking…', speaking: 'Speaking', listening: 'Listening…', transcribing: 'Transcribing…', error: 'Error', loading: 'Loading' };
  $('#stateText').textContent = label ?? labels[next] ?? next;
  $('#stateDot').className = 'dot ' + ({ idle: 'good', error: 'bad' }[next] ?? 'busy');
}

const log = $('#log');
function bubble(role, text = '') {
  $('#empty')?.remove();
  const el = document.createElement('div');
  el.className = `bubble ${role}`;
  el.textContent = text;
  log.append(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  ensureAudio();
  if (audioCtx.state === 'suspended') audioCtx.resume();
  interrupt();
  bubble('user', text);
  const out = bubble('assistant');
  out.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  setState('thinking');

  const ctrl = new AbortController();
  streaming = ctrl;
  let gotText = false;
  try {
    const res = await fetch('/api/avatar/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, name: opts.name || undefined, speak: opts.voice }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        const msg = JSON.parse(line);
        if (msg.type === 'state') {
          setFeelings(msg.feelings); // her first reaction while she thinks
        } else if (msg.type === 'sentence') {
          if (msg.text) {
            if (!gotText) out.textContent = '';
            out.textContent += (gotText ? ' ' : '') + msg.text;
            gotText = true;
            log.scrollTop = log.scrollHeight;
          }
          let buffer = null;
          if (msg.audio) {
            try { buffer = await audioCtx.decodeAudioData(base64ToArrayBuffer(msg.audio)); } catch {}
          }
          if (streaming !== ctrl) return;
          enqueueSpeech({ text: msg.text, buffer: opts.voice ? buffer : null, feelings: msg.feelings, gestures: msg.gestures });
        } else if (msg.type === 'error') {
          toast(msg.message);
        }
      }
    }
    if (!gotText) out.textContent = '…';
  } catch (err) {
    if (err.name === 'AbortError') return;
    out.remove();
    bubble('err', err.message);
    setState('error', 'Error');
    setTimeout(() => state === 'error' && setState('idle'), 3000);
    return;
  } finally {
    if (streaming === ctrl) streaming = null;
  }
  if (!playing && !lips.fake && !speech.length) setState('idle');
}

function interrupt() {
  if (streaming) { streaming.abort(); streaming = null; }
  stopSpeaking();
}

function base64ToArrayBuffer(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

async function loadHistory() {
  try {
    const rows = await fetch('/api/conversations/avatar').then((r) => r.json());
    for (const m of rows.slice(-40)) bubble(m.role === 'assistant' ? 'assistant' : 'user', m.content);
  } catch {}
}

// ---------------------------------------------------------------- microphone

// Captures 48kHz mono PCM (what the bot's speech-to-text expects) for push-to-talk or hands-free.
const mic = { stream: null, ctx: null, node: null, rate: 48000, recording: false, chunks: [], preroll: [],
  voiced: 0, silent: 0, level: 0, busy: false };

async function startMic() {
  ensureAudio(); // called from a click, so the speaker side is unlocked too
  if (audioCtx.state === 'suspended') audioCtx.resume();
  if (mic.stream) return true;
  try {
    mic.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
  } catch (err) {
    toast('Microphone blocked: ' + err.message);
    return false;
  }
  try { mic.ctx = new AudioContext({ sampleRate: 48000 }); } catch { mic.ctx = new AudioContext(); }
  mic.rate = mic.ctx.sampleRate;
  const src = mic.ctx.createMediaStreamSource(mic.stream);
  mic.node = mic.ctx.createScriptProcessor(2048, 1, 1);
  mic.node.onaudioprocess = (e) => onMicAudio(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(mic.node);
  mic.node.connect(mic.ctx.destination); // ScriptProcessor only runs when connected; it outputs silence
  return true;
}

function stopMicStream() {
  mic.node?.disconnect();
  mic.stream?.getTracks().forEach((t) => t.stop());
  mic.ctx?.close();
  Object.assign(mic, { stream: null, ctx: null, node: null, recording: false, chunks: [] });
  updateMicButton();
}

function onMicAudio(data) {
  let sum = 0;
  for (const v of data) sum += v * v;
  const rms = Math.sqrt(sum / data.length);
  mic.level = rms;
  $('#micBtn').style.setProperty('--lvl', String(1 + clamp(rms * 8, 0, 0.35)));

  if (mic.recording) mic.chunks.push(data);
  if (!opts.handsFree || mic.busy || pushToTalk) return;
  // While the avatar talks, its own voice would trigger the mic, so hands-free waits.
  if (state === 'speaking' || state === 'thinking') { mic.voiced = 0; return; }

  const blockMs = (data.length / mic.rate) * 1000;
  if (!mic.recording) {
    mic.preroll.push(data);
    if (mic.preroll.length > 8) mic.preroll.shift();
    mic.voiced = rms > 0.02 ? mic.voiced + blockMs : 0;
    if (mic.voiced > 120) {
      mic.recording = true;
      mic.chunks = [...mic.preroll];
      mic.silent = 0;
      setState('listening');
      updateMicButton();
    }
  } else {
    mic.silent = rms < 0.012 ? mic.silent + blockMs : 0;
    if (mic.silent > 900) finishRecording();
  }
}

let pushToTalk = false;
async function beginTalk() {
  if (mic.busy) return;
  if (!(await startMic())) return;
  interrupt();
  pushToTalk = true;
  mic.recording = true;
  mic.chunks = [];
  setState('listening');
  updateMicButton();
}

async function finishRecording() {
  if (!mic.recording) return;
  mic.recording = false;
  pushToTalk = false;
  const chunks = mic.chunks;
  mic.chunks = [];
  mic.preroll = [];
  updateMicButton();
  const total = chunks.reduce((n, c) => n + c.length, 0);
  if (total / mic.rate < 0.35) { setState('idle'); return; }

  // Float32 at the mic's rate -> 16-bit at 48kHz.
  const flat = new Float32Array(total);
  let o = 0;
  for (const c of chunks) { flat.set(c, o); o += c.length; }
  const ratio = mic.rate / 48000;
  const n = Math.floor(total / ratio);
  const pcm = new Int16Array(n);
  for (let i = 0; i < n; i++) pcm[i] = clamp(flat[Math.floor(i * ratio)], -1, 1) * 0x7fff;

  mic.busy = true;
  setState('transcribing');
  updateMicButton();
  try {
    const res = await fetch('/api/avatar/listen', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: pcm.buffer });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    if (data.text) send(data.text);
    else { setState('idle'); toast("Didn't catch that."); }
  } catch (err) {
    setState('idle');
    toast('Transcription failed: ' + err.message);
  } finally {
    mic.busy = false;
    updateMicButton();
  }
}

function updateMicButton() {
  const b = $('#micBtn');
  b.classList.toggle('rec', mic.recording);
  b.classList.toggle('working', mic.busy);
  b.classList.toggle('armed', opts.handsFree && Boolean(mic.stream) && !mic.recording);
}

// Hold to talk; a quick click toggles instead.
let pressAt = 0;
$('#micBtn').addEventListener('pointerdown', async (e) => {
  e.preventDefault();
  if (mic.recording && pushToTalk) return; // second click of a toggle; handled on pointerup
  pressAt = performance.now();
  await beginTalk();
});
$('#micBtn').addEventListener('pointerup', () => {
  if (!mic.recording || !pushToTalk) return;
  const held = performance.now() - pressAt;
  if (held > 300 || pressAt === 0) finishRecording();
  else pressAt = 0; // short click: keep recording until the next click
});
addEventListener('keydown', (e) => {
  if (e.code !== 'Space' || e.repeat || e.target.closest('textarea, input, select')) return;
  e.preventDefault();
  pressAt = performance.now();
  beginTalk();
});
addEventListener('keyup', (e) => {
  if (e.code === 'Space' && pushToTalk && !e.target.closest('textarea, input, select')) finishRecording();
  if (e.code === 'Escape') { interrupt(); setState('idle'); }
});

// ---------------------------------------------------------------- UI wiring

const input = $('#input');
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  $('#sendBtn').disabled = !input.value.trim();
});
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
});
$('#sendBtn').addEventListener('click', submit);
function submit() {
  const text = input.value;
  input.value = '';
  input.dispatchEvent(new Event('input'));
  send(text);
}

$('#framing').addEventListener('click', (e) => e.target.dataset.frame && frame(e.target.dataset.frame));
// Live readout of her strongest feelings, in the dock.
let lastReadout = '';
function renderFeelings() {
  const top = Object.entries(feel.target).filter(([, v]) => v >= 0.08).sort((a, b) => b[1] - a[1]).slice(0, 3);
  const html = top.length
    ? top.map(([n, v]) => `<span class="feel" title="${n} ${v.toFixed(2)}">${n}<i style="--v:${v.toFixed(2)}"></i></span>`).join('')
    : '<span class="feel calm">calm</span>';
  if (html !== lastReadout) $('#feelings').innerHTML = lastReadout = html;
}
setInterval(renderFeelings, 1000); // feelings fade between replies

// Expressions menu: nudge how she feels (the conversation sees it too) or play a gesture.
async function nudgeFeeling(body) {
  try {
    const res = await fetch('/api/avatar/emotion', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    setFeelings((await res.json()).feelings);
  } catch (err) {
    toast(err.message);
  }
}
$('#exprBtn').addEventListener('click', (e) => {
  e.stopPropagation();
  $('#exprPanel').hidden = !$('#exprPanel').hidden;
  $('#exprBtn').classList.toggle('on', !$('#exprPanel').hidden);
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('#exprPanel')) { $('#exprPanel').hidden = true; $('#exprBtn').classList.remove('on'); }
});
$('#exprPanel').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.feel) nudgeFeeling({ feelings: { [b.dataset.feel]: 0.85 } });
  if (b.dataset.gesture) playGesture(b.dataset.gesture);
  if (b.id === 'calmBtn') nudgeFeeling({ reset: true });
});
$('#exprFeelings').innerHTML = Object.keys(EXPRESSIONS).map((n) => `<button data-feel="${n}">${n}</button>`).join('');
$('#exprGestures').innerHTML = Object.keys(GESTURE_DEFS).map((n) => `<button data-gesture="${n}">${n}</button>`).join('');

function setCollapsed(c) {
  opts.collapsed = c;
  save();
  document.body.classList.toggle('collapsed', c);
  $('#reopen').hidden = !c;
  setTimeout(resize, 260);
  resize();
}
$('#collapseBtn').addEventListener('click', () => setCollapsed(true));
$('#reopen').addEventListener('click', () => setCollapsed(false));
$('#settingsBtn').addEventListener('click', () => {
  const s = $('#settings');
  s.hidden = !s.hidden;
  $('#panelTitle').textContent = s.hidden ? 'Conversation' : 'Settings';
});
$('#clearBtn').addEventListener('click', async () => {
  if (!confirm('Clear this conversation? Long-term memories are kept.')) return;
  await fetch('/api/conversations/avatar', { method: 'DELETE' });
  log.innerHTML = '';
  toast('Conversation cleared');
});

// Settings controls, bound to opts.
const bind = (id, key, apply) => {
  const el = $(id);
  const isBox = el.type === 'checkbox';
  if (isBox) el.checked = opts[key]; else el.value = opts[key];
  el.addEventListener(isBox || el.tagName === 'SELECT' ? 'change' : 'input', () => {
    opts[key] = isBox ? el.checked : el.type === 'range' ? Number(el.value) : el.value;
    save();
    apply?.(opts[key]);
  });
  apply?.(opts[key], true);
};
bind('#optName', 'name');
bind('#optVoice', 'voice', (on) => !on && playing && stopSpeaking());
bind('#optVolume', 'volume', (v) => gainNode && (gainNode.gain.value = v));
bind('#optHandsFree', 'handsFree', async (on, init) => {
  if (init) return; // the mic needs a click before it can start
  if (on) { if (!(await startMic())) { opts.handsFree = false; $('#optHandsFree').checked = false; save(); } }
  else if (!pushToTalk) stopMicStream();
  updateMicButton();
});
bind('#optSubs', 'subs', (on) => !on && caption(null));
bind('#optBlink', 'blink');
bind('#optGaze', 'gaze');
bind('#optHead', 'head');
bind('#optIntensity', 'intensity');
bind('#optLips', 'lips');
bind('#optExpr', 'expr');
bind('#optRest', 'restSmile');
bind('#optBackdrop', 'backdrop', (v) => (document.body.dataset.backdrop = v));

// Swap the avatar by dropping a .glb on the page (this browser only).
$('#pickGlb').addEventListener('click', () => $('#glbFile').click());
$('#glbFile').addEventListener('change', (e) => e.target.files[0] && loadModel(URL.createObjectURL(e.target.files[0])));
let dragDepth = 0;
addEventListener('dragenter', (e) => { e.preventDefault(); dragDepth++; $('#drop').hidden = false; });
addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; $('#drop').hidden = true; } });
addEventListener('dragover', (e) => e.preventDefault());
addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  $('#drop').hidden = true;
  const file = [...e.dataTransfer.files].find((f) => /\.(glb|gltf)$/i.test(f.name));
  if (file) loadModel(URL.createObjectURL(file));
  else toast('Drop a .glb file');
});

let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 3500);
}

async function loadStatus() {
  try {
    const s = await fetch('/api/avatar/status').then((r) => r.json());
    $('#botName').textContent = s.botName;
    document.title = `${s.botName} · Avatar Studio`;
    $('#modelName').textContent = s.model;
    $('#voiceName').textContent = s.fishReady ? (s.fishVoiceId ? 'Fish Audio' : 'Fish (random voice)') : 'not set up';
    $('#fishDot').className = 'dot ' + (s.fishReady ? 'good' : 'bad');
    if (!s.fishReady) toast('FISH_API_KEY is missing in .env, so replies will be silent.');
  } catch {
    toast("Can't reach the bot. Is it running?");
  }
}

// ---------------------------------------------------------------- loop

const clock = new THREE.Clock();
function tick() {
  const dt = Math.min(clock.getDelta(), 0.1);
  const now = performance.now();
  if (camTween) {
    camTween.t = Math.min(1, camTween.t + dt / 0.7);
    const k = smoothstep(camTween.t);
    controls.target.lerpVectors(camTween.fromT, camTween.toT, k);
    camera.position.lerpVectors(camTween.fromP, camTween.toP, k);
    if (camTween.t >= 1) camTween = null;
  }
  controls.update();
  if (model) {
    updateFeelings(dt);
    updateGestures(now);
    updateBlink(dt * 1000 * fx.blink);
    updateGaze(dt, now);
    updateLips(dt, now);
    updateHead(dt);
    applyWeights();
  }
  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}

// ---------------------------------------------------------------- start

document.body.dataset.backdrop = opts.backdrop;
document.body.classList.toggle('collapsed', opts.collapsed);
$('#reopen').hidden = !opts.collapsed;
resize();
setState('loading');
updateFeelings(0);
tick();
loadStatus();
fetch('/api/avatar/emotion').then((r) => r.json()).then((d) => setFeelings(d.feelings)).catch(() => {});
loadHistory();
loadModel('/api/avatar/model');
