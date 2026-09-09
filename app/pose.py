"""Съёмка движения из видео: MediaPipe Pose Landmarker в режиме видео.

Берём world-координаты (метры относительно центра таза) — по ним считаются
направления костей. Экранные координаты не годятся: они зависят от плана и
перспективы, и скелет от них «плывёт».
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

MODEL_PATH = os.environ.get('POSE_MODEL', os.path.join(os.path.dirname(__file__), 'pose_landmarker.task'))

#: Индексы точек MediaPipe, которые нам нужны (33-точечная модель).
LM = {
    'nose': 0,
    'left_shoulder': 11, 'right_shoulder': 12,
    'left_elbow': 13, 'right_elbow': 14,
    'left_wrist': 15, 'right_wrist': 16,
    'left_hip': 23, 'right_hip': 24,
    'left_knee': 25, 'right_knee': 26,
    'left_ankle': 27, 'right_ankle': 28,
    'left_foot': 31, 'right_foot': 32,
}


@dataclass
class Capture:
    fps: float
    times: np.ndarray                  # секунды, по кадру
    points: dict[str, np.ndarray]      # точка → массив (кадры, 3)
    visibility: dict[str, np.ndarray]  # точка → уверенность по кадрам
    frames: int
    detected: int


def extract(video_path: str, max_seconds: float = 30.0, stride: int = 1,
            progress=None) -> Capture:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError('не смог открыть видео')
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    limit = int(min(total or 10 ** 9, max_seconds * fps))

    options = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.3,
        min_pose_presence_confidence=0.3,
        min_tracking_confidence=0.3,
    )

    times: list[float] = []
    series: dict[str, list[np.ndarray]] = {k: [] for k in LM}
    vis: dict[str, list[float]] = {k: [] for k in LM}
    detected = 0
    index = 0

    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        while index < limit:
            ok, frame = cap.read()
            if not ok:
                break
            if index % stride == 0:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                ts = int(index / fps * 1000)
                res = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
                if res.pose_world_landmarks:
                    detected += 1
                    world = res.pose_world_landmarks[0]
                    screen = res.pose_landmarks[0]
                    for key, i in LM.items():
                        p = world[i]
                        # MediaPipe: y вниз, z от камеры. Переводим в «y вверх, z на нас».
                        series[key].append(np.array([p.x, -p.y, -p.z], dtype=np.float64))
                        vis[key].append(float(getattr(screen[i], 'visibility', 1.0)))
                    times.append(index / fps)
                elif times:  # кадр без детекта — повторяем предыдущий, чтобы не рвать дорожку
                    for key in LM:
                        series[key].append(series[key][-1])
                        vis[key].append(0.0)
                    times.append(index / fps)
            index += 1
            if progress and index % 15 == 0:
                progress(min(0.99, index / max(1, limit)))

    cap.release()
    if not times:
        raise ValueError('на видео не нашлось ни одного человека')
    return Capture(
        fps=fps / stride,
        times=np.array(times),
        points={k: np.array(v) for k, v in series.items()},
        visibility={k: np.array(v) for k, v in vis.items()},
        frames=len(times),
        detected=detected,
    )


def smooth(capture: Capture, window: int = 5) -> Capture:
    """Скользящее среднее: MediaPipe заметно дрожит, на скелете это видно сразу."""
    if window < 2:
        return capture
    kernel = np.ones(window) / window
    out = {}
    for key, arr in capture.points.items():
        padded = np.pad(arr, ((window // 2, window // 2), (0, 0)), mode='edge')
        out[key] = np.stack([np.convolve(padded[:, i], kernel, mode='valid')[:len(arr)] for i in range(3)], axis=1)
    return Capture(capture.fps, capture.times, out, capture.visibility, capture.frames, capture.detected)


def probe(video_path: str) -> dict:
    """Длительность и частота кадров — чтобы UI знал, что показывать."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError('не смог открыть видео')
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return {'fps': fps, 'frames': frames, 'duration': frames / fps if fps else 0.0}


def extract_at(video_path: str, times: list[float]) -> Capture:
    """Снять позу на указанных секундах — по кадру на каждую отметку.

    Режим IMAGE, а не VIDEO: кадры выбраны вручную и идут не подряд, трекинг
    между ними только навредит.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError('не смог открыть видео')
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

    options = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.IMAGE,
        num_poses=1,
        min_pose_detection_confidence=0.3,
        min_pose_presence_confidence=0.3,
    )

    taken: list[float] = []
    series: dict[str, list[np.ndarray]] = {k: [] for k in LM}
    vis: dict[str, list[float]] = {k: [] for k in LM}

    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t)) * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            if not res.pose_world_landmarks:
                continue
            world, screen = res.pose_world_landmarks[0], res.pose_landmarks[0]
            for key, i in LM.items():
                p = world[i]
                series[key].append(np.array([p.x, -p.y, -p.z], dtype=np.float64))
                vis[key].append(float(getattr(screen[i], 'visibility', 1.0)))
            taken.append(float(t))

    cap.release()
    if not taken:
        raise ValueError('на выбранных кадрах не нашлось человека')
    return Capture(
        fps=fps,
        times=np.array(taken),
        points={k: np.array(v) for k, v in series.items()},
        visibility={k: np.array(v) for k, v in vis.items()},
        frames=len(taken),
        detected=len(taken),
    )


def extract_image(image_path: str) -> Capture:
    """Поза с фотографии — один кадр, тот же формат захвата, что и у видео."""
    frame = cv2.imread(image_path)
    if frame is None:
        raise ValueError('не смог открыть изображение')
    options = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.IMAGE,
        num_poses=1,
        min_pose_detection_confidence=0.3,
        min_pose_presence_confidence=0.3,
    )
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        res = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if not res.pose_world_landmarks:
        raise ValueError('на фотографии не нашлось человека')
    world, screen = res.pose_world_landmarks[0], res.pose_landmarks[0]
    points, vis = {}, {}
    for key, i in LM.items():
        p = world[i]
        points[key] = np.array([[p.x, -p.y, -p.z]], dtype=np.float64)
        vis[key] = np.array([float(getattr(screen[i], 'visibility', 1.0))])
    return Capture(fps=1.0, times=np.array([0.0]), points=points, visibility=vis,
                   frames=1, detected=1)
