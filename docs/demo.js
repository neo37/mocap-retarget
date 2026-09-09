/** Demo page: photo in → detected pose as a 3D figure, all client-side. */
import * as THREE from './vendor/three.module.js';
import { FilesetResolver, PoseLandmarker }
  from 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs';

const $ = (id) => document.getElementById(id);
const say = (text, isError = false) => {
  const el = $('status');
  el.textContent = text;
  el.classList.toggle('err', isError);
};

/* MediaPipe world landmarks: metres, origin at the hip midpoint, y pointing down. */
const BONES = [
  [11, 12], [11, 23], [12, 24], [23, 24],                      // torso
  [11, 13], [13, 15], [12, 14], [14, 16],                      // arms
  [23, 25], [25, 27], [24, 26], [26, 28],                      // legs
  [27, 29], [29, 31], [27, 31], [28, 30], [30, 32], [28, 32],  // feet
];
const JOINTS = [11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28];
const NOSE = 0, L_SHOULDER = 11, R_SHOULDER = 12;
const MIN_VISIBILITY = 0.35;

/* A plain standing pose, so the stage is not empty before the first photo. */
const IDLE_POSE = {
  0: [0, -0.68, 0.06],
  11: [0.18, -0.50, 0], 12: [-0.18, -0.50, 0],
  13: [0.24, -0.24, 0], 14: [-0.24, -0.24, 0],
  15: [0.27, 0.02, 0], 16: [-0.27, 0.02, 0],
  23: [0.10, 0, 0], 24: [-0.10, 0, 0],
  25: [0.11, 0.44, 0], 26: [-0.11, 0.44, 0],
  27: [0.11, 0.88, 0], 28: [-0.11, 0.88, 0],
  29: [0.11, 0.93, -0.03], 30: [-0.11, 0.93, -0.03],
  31: [0.11, 0.93, 0.13], 32: [-0.11, 0.93, 0.13],
};
const idleLandmarks = Array.from({ length: 33 }, (_, i) => {
  const p = IDLE_POSE[i];
  return p ? { x: p[0], y: p[1], z: p[2], visibility: 1 }
           : { x: 0, y: 0, z: 0, visibility: 0 };
});

/* ---------- scene ---------- */
const stage = $('stage');
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14181d);
const camera = new THREE.PerspectiveCamera(38, 1, 0.05, 100);

let renderer = null;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  stage.appendChild(renderer.domElement);
} catch (e) {
  /* No WebGL: the detector still works, there is just nothing to draw with. */
  const note = document.createElement('p');
  note.id = 'no-webgl';
  note.textContent = 'This browser has no WebGL, so the 3D view stays empty.';
  note.style.cssText = 'position:absolute;inset:0;display:grid;place-items:center;color:#8b95a3;padding:24px;text-align:center';
  stage.appendChild(note);
}

scene.add(new THREE.HemisphereLight(0xdfe9ff, 0x1a2026, 1.4));
const key = new THREE.DirectionalLight(0xffffff, 1.7);
key.position.set(2.5, 4, 3);
scene.add(key);
const rim = new THREE.DirectionalLight(0x6ee7b7, 0.8);
rim.position.set(-3, 1.5, -2.5);
scene.add(rim);

const grid = new THREE.GridHelper(6, 24, 0x39424d, 0x232a31);
grid.material.transparent = true;
grid.material.opacity = 0.5;
scene.add(grid);

const boneMat = new THREE.MeshStandardMaterial({ color: 0xd7dee7, roughness: 0.55, metalness: 0.05 });
const jointMat = new THREE.MeshStandardMaterial({ color: 0x6ee7b7, roughness: 0.35, metalness: 0.1 });
const idleMat = new THREE.MeshStandardMaterial({ color: 0x59636f, roughness: 0.8, metalness: 0 });

let figure = null;

const vec = (lm) => new THREE.Vector3(lm.x, -lm.y, -lm.z);   // y down, z towards camera
const seen = (lm) => lm && (lm.visibility === undefined || lm.visibility >= MIN_VISIBILITY);

function segment(a, b, radius, material) {
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = dir.length();
  if (len < 1e-4) return null;
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, len, 14), material);
  mesh.position.copy(a).addScaledVector(dir, 0.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
  return mesh;
}

function buildFigure(landmarks, { dim = false } = {}) {
  const bm = dim ? idleMat : boneMat;
  const jm = dim ? idleMat : jointMat;
  const group = new THREE.Group();

  for (const [a, b] of BONES) {
    if (!seen(landmarks[a]) || !seen(landmarks[b])) continue;
    const mesh = segment(vec(landmarks[a]), vec(landmarks[b]), 0.028, bm);
    if (mesh) group.add(mesh);
  }
  for (const i of JOINTS) {
    if (!seen(landmarks[i])) continue;
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.042, 18, 14), jm);
    dot.position.copy(vec(landmarks[i]));
    group.add(dot);
  }
  /* Neck and head: MediaPipe has no neck joint, the shoulder midpoint stands in. */
  if (seen(landmarks[L_SHOULDER]) && seen(landmarks[R_SHOULDER]) && seen(landmarks[NOSE])) {
    const neck = vec(landmarks[L_SHOULDER]).lerp(vec(landmarks[R_SHOULDER]), 0.5);
    const nose = vec(landmarks[NOSE]);
    const head = neck.clone().lerp(nose, 1.25);
    const mesh = segment(neck.clone(), head.clone(), 0.03, bm);
    if (mesh) group.add(mesh);
    const skull = new THREE.Mesh(new THREE.SphereGeometry(0.105, 24, 18), bm);
    skull.position.copy(head);
    group.add(skull);
  }
  if (!group.children.length) return null;

  /* Stand the figure on the grid and centre it horizontally. */
  const box = new THREE.Box3().setFromObject(group);
  const centre = box.getCenter(new THREE.Vector3());
  group.position.set(-centre.x, -box.min.y, -centre.z);
  group.userData.height = box.max.y - box.min.y;
  return group;
}

function showFigure(landmarks, opts) {
  const next = buildFigure(landmarks, opts);
  if (!next) return false;
  if (figure) {
    scene.remove(figure);
    figure.traverse((o) => o.geometry && o.geometry.dispose());
  }
  figure = next;
  scene.add(figure);
  view.target.set(0, (figure.userData.height || 1.7) * 0.52, 0);
  return true;
}

/* ---------- camera control ---------- */
const view = { yaw: 0.5, pitch: 0.12, radius: 3.4, target: new THREE.Vector3(0, 0.9, 0), spin: true };
const HOME = { yaw: 0.5, pitch: 0.12, radius: 3.4 };

let dragging = null;
if (renderer) bindPointer();
function bindPointer() {
renderer.domElement.addEventListener('pointerdown', (e) => {
  dragging = { x: e.clientX, y: e.clientY };
  renderer.domElement.setPointerCapture(e.pointerId);
});
renderer.domElement.addEventListener('pointermove', (e) => {
  if (!dragging) return;
  view.yaw -= (e.clientX - dragging.x) * 0.008;
  view.pitch = Math.max(-1.1, Math.min(1.2, view.pitch + (e.clientY - dragging.y) * 0.006));
  dragging = { x: e.clientX, y: e.clientY };
  setSpin(false);
});
['pointerup', 'pointercancel', 'pointerleave'].forEach((ev) =>
  renderer.domElement.addEventListener(ev, () => { dragging = null; }));
renderer.domElement.addEventListener('wheel', (e) => {
  e.preventDefault();
  view.radius = Math.max(1.2, Math.min(9, view.radius * (1 + Math.sign(e.deltaY) * 0.09)));
}, { passive: false });
}

function setSpin(on) {
  view.spin = on;
  $('spin').setAttribute('aria-pressed', String(on));
}
$('spin').onclick = () => setSpin(!view.spin);
$('reset').onclick = () => { Object.assign(view, HOME); setSpin(true); };

function resize() {
  const w = stage.clientWidth, h = stage.clientHeight;
  if (!renderer || !w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);
resize();

let last = performance.now();
if (renderer) (function loop(now = performance.now()) {
  requestAnimationFrame(loop);
  const dt = Math.min((now - last) / 1000, 0.05);
  last = now;
  if (view.spin) view.yaw += dt * 0.35;
  const cp = Math.cos(view.pitch);
  camera.position.set(
    view.target.x + view.radius * cp * Math.sin(view.yaw),
    view.target.y + view.radius * Math.sin(view.pitch),
    view.target.z + view.radius * cp * Math.cos(view.yaw),
  );
  camera.lookAt(view.target);
  renderer.render(scene, camera);
})();

showFigure(idleLandmarks, { dim: true });

/* ---------- detector ---------- */
let landmarker = null;
try {
  const vision = await FilesetResolver.forVisionTasks(
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm');
  landmarker = await PoseLandmarker.createFromOptions(vision, {
    baseOptions: { modelAssetPath: './pose_landmarker.task' },
    runningMode: 'IMAGE',
    numPoses: 1,
  });
  say('ready — drop a photo');
} catch (e) {
  say('could not load the detector: ' + e.message, true);
}

/* ---------- input ---------- */
const drop = $('drop'), fileInput = $('file'), photo = $('photo');

drop.onclick = () => fileInput.click();
fileInput.onchange = () => fileInput.files[0] && handle(fileInput.files[0]);
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.add('over');
}));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove('over')));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  const f = e.dataTransfer.files[0];
  if (f) handle(f);
});
addEventListener('paste', (e) => {
  const f = [...(e.clipboardData?.files || [])][0];
  if (f) handle(f);
});

async function handle(file) {
  if (!landmarker) return say('the detector is still loading', true);
  if (!file.type.startsWith('image/')) return say('that is not an image', true);
  say('looking for a person…');

  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    photo.src = url;
    $('photo-wrap').style.display = 'block';
    try {
      const res = landmarker.detect(img);
      const world = res.worldLandmarks?.[0];
      if (!world) { say('no person found — try a photo with the whole body in frame', true); return; }
      const visible = world.filter((lm) => seen(lm)).length;
      if (!showFigure(world)) { say('the pose came out empty', true); return; }
      setSpin(true);
      say(`pose detected · ${visible} of 33 landmarks confident`);
    } catch (e) {
      say('detection failed: ' + e.message, true);
    }
  };
  img.onerror = () => say('could not read the image', true);
  img.src = url;
}
