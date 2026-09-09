/** Трёхмерная часть: просмотр модели, скелет с кликом по костям, проигрывание клипа.
 *  Никакого состояния приложения здесь нет — только сцена и то, что в ней видно. */
import * as THREE from './vendor/three.module.js';
import { GLTFLoader } from './vendor/GLTFLoader.js';
import { DRACOLoader } from './vendor/DRACOLoader.js';
import { MeshoptDecoder } from './vendor/meshopt_decoder.module.js';

/** Готовые модели почти всегда сжаты (Meshy — meshopt, Sketchfab — draco),
 *  поэтому загрузчику сразу выдаём оба распаковщика. */
function makeLoader() {
  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  const draco = new DRACOLoader();
  draco.setDecoderPath(new URL('./vendor/draco/', import.meta.url).href);
  loader.setDRACOLoader(draco);
  return loader;
}

const COLORS = {
  bone: 0x8d8d86,
  boneMapped: 0xd8ff36,
  boneActive: 0xff4d17,
  joint: 0xf5f5f0,
  ground: 0x2a2a2e,
};

export function createViewer(host) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0f0f11);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 200);
  let renderer = null;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    host.appendChild(renderer.domElement);
  } catch (e) {
    host.innerHTML = '<p class="pad muted">В этом браузере нет WebGL — 3D не покажу.</p>';
    return { dead: true, dispose() {}, setSkeleton() {}, playRange() {}, pause() {},
             frameAt() {}, clear() {}, duration: 0, time: 0,
             load: () => Promise.resolve({ clips: [], bones: [], duration: 0 }) };
  }

  scene.add(new THREE.HemisphereLight(0xdfe9ff, 0x14141a, 1.5));
  const key = new THREE.DirectionalLight(0xffffff, 1.8);
  key.position.set(2.5, 4, 3);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xd8ff36, 0.6);
  rim.position.set(-3, 1.5, -2.5);
  scene.add(rim);
  const grid = new THREE.GridHelper(8, 32, COLORS.ground, COLORS.ground);
  scene.add(grid);

  const state = {
    model: null, mixer: null, action: null, clip: null,
    bones: [], markers: new THREE.Group(), dots: [], pick: null, hover: null,
    range: null, playing: true, spin: false,
  };
  scene.add(state.markers);

  const view = { yaw: 0.6, pitch: 0.12, radius: 3.2, target: new THREE.Vector3(0, 0.95, 0) };
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let drag = null, moved = 0;

  const canvas = renderer.domElement;
  canvas.addEventListener('pointerdown', (e) => {
    drag = { x: e.clientX, y: e.clientY };
    moved = 0;
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
    const rect = canvas.getBoundingClientRect();
    pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1,
                -((e.clientY - rect.top) / rect.height) * 2 + 1);
    if (!drag) return;
    moved += Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y);
    view.yaw -= (e.clientX - drag.x) * 0.008;
    view.pitch = Math.max(-1.1, Math.min(1.2, view.pitch + (e.clientY - drag.y) * 0.006));
    drag = { x: e.clientX, y: e.clientY };
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach((ev) =>
    canvas.addEventListener(ev, (e) => {
      if (drag && moved < 5 && state.pick) clickBone(e);
      drag = null;
    }));
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    view.radius = Math.max(0.4, Math.min(20, view.radius * (1 + Math.sign(e.deltaY) * 0.09)));
  }, { passive: false });

  function clickBone() {
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(state.markers.children, false)[0];
    if (hit && state.pick) state.pick(hit.object.userData.bone);
  }

  function clear() {
    if (state.model) {
      scene.remove(state.model);
      state.model.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) [].concat(o.material).forEach((m) => m.dispose());
      });
    }
    state.model = null;
    state.mixer = null;
    state.action = null;
    state.clip = null;
    state.bones = [];
    state.dots = [];
    state.markers.clear();
  }

  /** Загрузить .glb; возвращает {clips, bones} — список клипов и имён костей. */
  function load(url, { autoplay = true } = {}) {
    return new Promise((resolve, reject) => {
      makeLoader().load(url, (gltf) => {
        clear();
        const root = gltf.scene;
        const box = new THREE.Box3().setFromObject(root);
        const size = box.getSize(new THREE.Vector3());
        const scale = 1.7 / (size.y || 1);
        root.scale.setScalar(scale);
        const scaled = new THREE.Box3().setFromObject(root);
        const centre = scaled.getCenter(new THREE.Vector3());
        root.position.set(-centre.x, -scaled.min.y, -centre.z);
        scene.add(root);
        state.model = root;

        const bones = [];
        root.traverse((o) => { if (o.isBone) bones.push(o); });
        state.bones = bones;
        view.target.set(0, (scaled.max.y - scaled.min.y) * 0.52, 0);
        view.radius = 3.2;

        if (gltf.animations.length) {
          state.mixer = new THREE.AnimationMixer(root);
          state.clip = gltf.animations[gltf.animations.length - 1];
          state.action = state.mixer.clipAction(state.clip);
          if (autoplay) state.action.play();
        }
        resolve({ clips: gltf.animations.map((a) => a.name), bones: bones.map((b) => b.name),
                  duration: state.clip ? state.clip.duration : 0 });
      }, undefined, reject);
    });
  }

  /** Показать кости кликабельными шарами. `mapped` — имя кости → цвет-подсказка. */
  function setSkeleton(visible, { mapped = {}, active = null, onPick = null } = {}) {
    state.markers.clear();
    state.dots = [];
    state.pick = visible ? onPick : null;
    if (!visible) return;
    const world = new THREE.Vector3();
    const size = state.bones.length ? 0.028 : 0;
    for (const bone of state.bones) {
      bone.getWorldPosition(world);
      const colour = bone.name === active ? COLORS.boneActive
        : (mapped[bone.name] ? COLORS.boneMapped : COLORS.bone);
      const dot = new THREE.Mesh(
        new THREE.SphereGeometry(bone.name === active ? size * 1.7 : size, 16, 12),
        new THREE.MeshBasicMaterial({ color: colour, depthTest: false }));
      dot.position.copy(world);
      dot.renderOrder = 3;
      dot.userData.bone = bone.name;
      state.markers.add(dot);
      state.dots.push({ bone, mesh: dot });
      const parent = bone.parent;
      if (parent && parent.isBone) {
        const from = parent.getWorldPosition(new THREE.Vector3());
        const dir = new THREE.Vector3().subVectors(world, from);
        const len = dir.length();
        if (len > 1e-4) {
          const stick = new THREE.Mesh(
            new THREE.CylinderGeometry(size * 0.28, size * 0.28, len, 8),
            new THREE.MeshBasicMaterial({ color: colour, depthTest: false, transparent: true, opacity: 0.75 }));
          stick.position.copy(from).addScaledVector(dir, 0.5);
          stick.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize());
          stick.renderOrder = 2;
          stick.userData.bone = bone.name;
          state.markers.add(stick);
        }
      }
    }
  }

  /** Играть только отрезок клипа [from, to] — для режима обрезки. */
  function playRange(from = 0, to = null) {
    if (!state.action) return;
    state.range = { from, to: to === null ? state.clip.duration : to };
    state.action.paused = false;
    state.action.play();
    state.mixer.setTime(from);
  }

  function pause(on = true) {
    if (state.action) state.action.paused = on;
    state.playing = !on;
  }

  function frameAt(time) {
    if (!state.mixer) return;
    state.mixer.setTime(time);
    if (state.action) state.action.paused = true;
  }

  function resize() {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  const clock = new THREE.Clock();
  let alive = true;
  (function loop() {
    if (!alive) return;
    requestAnimationFrame(loop);
    const dt = clock.getDelta();
    if (state.mixer && state.playing) {
      state.mixer.update(dt);
      const r = state.range;
      if (r && state.action && state.mixer.time > r.to) state.mixer.setTime(r.from);
    }
    if (state.dots.length) {
      // Кости двигаются вместе с анимацией — маркеры едут за ними.
      const world = new THREE.Vector3();
      for (const { bone, mesh } of state.dots) mesh.position.copy(bone.getWorldPosition(world));
    }
    const cp = Math.cos(view.pitch);
    camera.position.set(
      view.target.x + view.radius * cp * Math.sin(view.yaw),
      view.target.y + view.radius * Math.sin(view.pitch),
      view.target.z + view.radius * cp * Math.cos(view.yaw));
    camera.lookAt(view.target);
    renderer.render(scene, camera);
  })();

  return {
    load, clear, setSkeleton, playRange, pause, frameAt,
    get duration() { return state.clip ? state.clip.duration : 0; },
    get time() { return state.mixer ? state.mixer.time : 0; },
    dispose() { alive = false; observer.disconnect(); clear(); renderer.dispose(); },
  };
}
