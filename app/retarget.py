"""Перенос снятого движения на чужой риг.

Идея простая и потому устойчивая: для каждой кости берём её направление в позе
покоя модели и направление того же сегмента тела на видео, считаем кратчайший
поворот между ними и переводим его в локальные координаты кости. Никаких
предположений об ориентации осей рига — всё считается из самого файла, поэтому
подходит и Meshy, и Mixamo, и рукодельный скелет с экрана «Скелет».
"""
from __future__ import annotations

import numpy as np

from . import gltf
from .humanoid import CHAINS
from .pose import Capture

#: Слот скелета → пара точек MediaPipe, задающая направление кости.
SEGMENTS: dict[str, tuple[str, str]] = {
    'left_upper_arm': ('left_shoulder', 'left_elbow'),
    'left_lower_arm': ('left_elbow', 'left_wrist'),
    'right_upper_arm': ('right_shoulder', 'right_elbow'),
    'right_lower_arm': ('right_elbow', 'right_wrist'),
    'left_upper_leg': ('left_hip', 'left_knee'),
    'left_lower_leg': ('left_knee', 'left_ankle'),
    'left_foot': ('left_ankle', 'left_foot'),
    'right_upper_leg': ('right_hip', 'right_knee'),
    'right_lower_leg': ('right_knee', 'right_ankle'),
    'right_foot': ('right_ankle', 'right_foot'),
}


def _mid(capture: Capture, a: str, b: str, frame: int) -> np.ndarray:
    return (capture.points[a][frame] + capture.points[b][frame]) / 2


def _body_basis(capture: Capture, frame: int) -> np.ndarray:
    """Система координат тела на кадре: вправо, вверх, вперёд."""
    hips = _mid(capture, 'left_hip', 'right_hip', frame)
    shoulders = _mid(capture, 'left_shoulder', 'right_shoulder', frame)
    right = capture.points['right_hip'][frame] - capture.points['left_hip'][frame]
    up = shoulders - hips
    right = right - up * float(np.dot(right, up)) / float(np.dot(up, up) or 1e-9)
    right /= np.linalg.norm(right) or 1e-9
    up /= np.linalg.norm(up) or 1e-9
    forward = np.cross(right, up)
    return np.stack([right, up, forward], axis=1)


def _child_of(glb: gltf.GLB, mapping: dict[str, str | None], slot: str,
              name_to_node: dict[str, int]) -> int | None:
    node = mapping.get(slot)
    return name_to_node.get(node) if node else None


def retarget(glb: gltf.GLB, mapping: dict[str, str | None], capture: Capture,
             clip_name: str = 'Захват с видео', root_motion: bool = False) -> None:
    """Собрать клип из захвата и дописать его в модель."""
    name_to_node = {n.get('name'): i for i, n in enumerate(glb.nodes) if n.get('name')}
    rest_world = glb.rest_world()
    parents = glb.parents()

    # Направление кости в позе покоя: от неё к «ребёнку» по цепочке скелета.
    rest_dir: dict[str, np.ndarray] = {}
    for parent_slot, child_slot in CHAINS:
        a = _child_of(glb, mapping, parent_slot, name_to_node)
        b = _child_of(glb, mapping, child_slot, name_to_node)
        if a is None or b is None:
            continue
        d = rest_world[b][:3, 3] - rest_world[a][:3, 3]
        if np.linalg.norm(d) > 1e-6:
            rest_dir[parent_slot] = d / np.linalg.norm(d)

    # Порядок обхода — от таза вниз по иерархии, чтобы родитель считался раньше.
    ordered = [s for s, _ in CHAINS if s in rest_dir]
    frames = capture.frames
    tracks: dict[int, list[np.ndarray]] = {}
    translations: dict[int, list[np.ndarray]] = {}

    hips_node = _child_of(glb, mapping, 'hips', name_to_node)
    hips_rest_t = glb.rest_local(hips_node)[0] if hips_node is not None else None
    hips_scale = 1.0
    if hips_node is not None:
        # Масштаб «метры MediaPipe → единицы модели» берём по высоте бедро-щиколотка.
        knee = _child_of(glb, mapping, 'left_lower_leg', name_to_node)
        foot = _child_of(glb, mapping, 'left_foot', name_to_node)
        if knee is not None and foot is not None:
            model_len = np.linalg.norm(rest_world[foot][:3, 3] - rest_world[knee][:3, 3])
            human_len = np.linalg.norm(capture.points['left_ankle'][0] - capture.points['left_knee'][0])
            if human_len > 1e-6:
                hips_scale = float(model_len / human_len)

    for frame in range(frames):
        basis = _body_basis(capture, frame)
        world_q: dict[int, np.ndarray] = {}

        # Таз: поворот всего тела вокруг вертикали и наклон корпуса.
        if hips_node is not None:
            hips_rest = rest_world[hips_node][:3, :3]
            target = basis @ np.linalg.inv(_reference_basis())
            q_world = gltf.matrix_to_quat(_orthonormal(target))
            parent = parents.get(hips_node)
            parent_q = gltf.matrix_to_quat(_orthonormal(rest_world[parent][:3, :3])) if parent is not None else np.array([0, 0, 0, 1.0])
            local = gltf.quat_mul(gltf.quat_conj(parent_q), q_world)
            tracks.setdefault(hips_node, []).append(local)
            world_q[hips_node] = q_world
            if root_motion:
                hips_pos = _mid(capture, 'left_hip', 'right_hip', frame) * hips_scale
                translations.setdefault(hips_node, []).append(hips_rest_t + np.array([hips_pos[0], hips_pos[1], hips_pos[2]]) - _mid(capture, 'left_hip', 'right_hip', 0) * hips_scale)

        for slot in ordered:
            node = _child_of(glb, mapping, slot, name_to_node)
            seg = SEGMENTS.get(slot)
            if node is None or not seg:
                continue
            a, b = seg
            if min(capture.visibility[a][frame], capture.visibility[b][frame]) < 0.2:
                # Точку не видно — оставляем кость в покое, дрожь не переносим.
                tracks.setdefault(node, []).append(np.array([0, 0, 0, 1.0]))
                continue
            direction = capture.points[b][frame] - capture.points[a][frame]
            if np.linalg.norm(direction) < 1e-6:
                tracks.setdefault(node, []).append(np.array([0, 0, 0, 1.0]))
                continue
            direction = direction / np.linalg.norm(direction)
            rest = rest_dir[slot]
            q_world = gltf.quat_mul(gltf.quat_from_to(rest, direction), _rest_world_quat(rest_world, node))
            parent = parents.get(node)
            parent_q = world_q.get(parent, _rest_world_quat(rest_world, parent) if parent is not None else np.array([0, 0, 0, 1.0]))
            tracks.setdefault(node, []).append(gltf.quat_mul(gltf.quat_conj(parent_q), q_world))
            world_q[node] = q_world

    glb.add_animation(
        clip_name,
        capture.times.astype(np.float32),
        {node: np.array(values, dtype=np.float32) for node, values in tracks.items()},
        {node: np.array(values, dtype=np.float32) for node, values in translations.items()} or None,
    )


def _reference_basis() -> np.ndarray:
    """Опорная система тела: вправо +X, вверх +Y, вперёд +Z (как в glTF)."""
    return np.eye(3)


def _orthonormal(m: np.ndarray) -> np.ndarray:
    u, _, vt = np.linalg.svd(m)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    return r


def _rest_world_quat(rest_world: dict[int, np.ndarray], node: int) -> np.ndarray:
    m = rest_world[node][:3, :3]
    scale = np.linalg.norm(m, axis=0)
    scale[scale == 0] = 1
    return gltf.matrix_to_quat(m / scale)
