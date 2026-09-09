"""Мимика: выражение лица с фото или видео → морф-таргеты модели.

MediaPipe Face Landmarker отдаёт 52 коэффициента в номенклатуре ARKit
(`jawOpen`, `eyeBlinkLeft`, …) — ровно то, чем принято называть морф-таргеты в
готовых головах (Meshy, VRoid, Ready Player Me, Character Creator). Поэтому
сопоставление обычно получается само по именам, а руками правится там же, где
и кости.
"""
from __future__ import annotations

import os
import re

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

MODEL_PATH = os.environ.get('FACE_MODEL', os.path.join(os.path.dirname(__file__),
                                                       'face_landmarker.task'))

#: Как ещё называют то же выражение в разных наборах морфов.
SYNONYMS: dict[str, tuple[str, ...]] = {
    'jawopen': ('mouthopen', 'openmouth', 'aa', 'visemeaa', 'jawdrop'),
    'mouthsmileleft': ('smileleft', 'happyleft'),
    'mouthsmileright': ('smileright', 'happyright'),
    'mouthfrownleft': ('sadleft', 'frownleft'),
    'mouthfrownright': ('sadright', 'frownright'),
    'mouthpucker': ('kiss', 'ou', 'visemeou'),
    'eyeblinkleft': ('blinkleft', 'eyecloseleft', 'closeeyeleft'),
    'eyeblinkright': ('blinkright', 'eyecloseright', 'closeeyeright'),
    'browinnerup': ('browsup', 'surprisedbrows'),
    'browdownleft': ('angrybrowleft',),
    'browdownright': ('angrybrowright',),
    'cheekpuff': ('puff', 'cheeksout'),
    'tongueout': ('tongue',),
}


def _norm(name: str) -> str:
    return re.sub(r'[^a-z0-9]', '', name.lower())


def _variants(name: str) -> list[str]:
    """Имя ARKit и его привычные написания: `…Left` ↔ `…_L`, синонимы."""
    base = _norm(name)
    out = [base, *SYNONYMS.get(base, ())]
    for variant in list(out):
        for long, short in (('left', 'l'), ('right', 'r')):
            if variant.endswith(long):
                out.append(variant[: -len(long)] + short)
    return out


def guess_mapping(target_names: list[str]) -> dict[str, str]:
    """ARKit-коэффициент → имя морф-таргета модели."""
    index = {_norm(n): n for n in target_names}
    mapping: dict[str, str] = {}
    for shape in SHAPES:
        for variant in _variants(shape):
            if variant in index:
                mapping[shape] = index[variant]
                break
    return mapping


def weights(target_names: list[str], mapping: dict[str, str | None],
            scores: dict[str, float]) -> list[float]:
    """Вектор весов в порядке морф-таргетов модели."""
    by_target = {mapping[s]: scores.get(s, 0.0) for s in mapping if mapping.get(s)}
    return [float(by_target.get(name, 0.0)) for name in target_names]


def _options(mode) -> vision.FaceLandmarkerOptions:
    return vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=mode,
        num_faces=1,
        output_face_blendshapes=True,
        min_face_detection_confidence=0.3,
        min_face_presence_confidence=0.3,
    )


def _scores(result) -> dict[str, float] | None:
    if not result.face_blendshapes:
        return None
    return {c.category_name: float(c.score) for c in result.face_blendshapes[0]}


def from_image(image_path: str) -> dict[str, float]:
    frame = cv2.imread(image_path)
    if frame is None:
        raise ValueError('не смог открыть изображение')
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    with vision.FaceLandmarker.create_from_options(_options(vision.RunningMode.IMAGE)) as fl:
        scores = _scores(fl.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)))
    if not scores:
        raise ValueError('на изображении не нашлось лица')
    return scores


def from_video(video_path: str, max_seconds: float = 15.0,
               progress=None) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Дорожки коэффициентов по кадрам: (времена, {имя: значения})."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError('не смог открыть видео')
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    limit = int(min(total or 10 ** 9, max_seconds * fps))

    times: list[float] = []
    series: dict[str, list[float]] = {}
    index = 0
    with vision.FaceLandmarker.create_from_options(_options(vision.RunningMode.VIDEO)) as fl:
        while index < limit:
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = fl.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb),
                                      int(index / fps * 1000))
            scores = _scores(res)
            if scores is None and not times:
                index += 1
                continue                      # лицо ещё не появилось — дорожку не начинаем
            if scores is None:                # моргнуло распознавание: держим прошлый кадр
                for key in series:
                    series[key].append(series[key][-1])
            else:
                for key, value in scores.items():
                    series.setdefault(key, [0.0] * len(times)).append(value)
            times.append(index / fps)
            index += 1
            if progress and index % 15 == 0:
                progress(min(0.99, index / max(1, limit)))
    cap.release()
    if not times:
        raise ValueError('на видео не нашлось лица')
    return np.array(times), {k: np.array(v) for k, v in series.items()}


#: Имена коэффициентов MediaPipe (они же ARKit), в порядке модели.
SHAPES: tuple[str, ...] = (
    '_neutral', 'browDownLeft', 'browDownRight', 'browInnerUp', 'browOuterUpLeft',
    'browOuterUpRight', 'cheekPuff', 'cheekSquintLeft', 'cheekSquintRight',
    'eyeBlinkLeft', 'eyeBlinkRight', 'eyeLookDownLeft', 'eyeLookDownRight',
    'eyeLookInLeft', 'eyeLookInRight', 'eyeLookOutLeft', 'eyeLookOutRight',
    'eyeLookUpLeft', 'eyeLookUpRight', 'eyeSquintLeft', 'eyeSquintRight',
    'eyeWideLeft', 'eyeWideRight', 'jawForward', 'jawLeft', 'jawOpen', 'jawRight',
    'mouthClose', 'mouthDimpleLeft', 'mouthDimpleRight', 'mouthFrownLeft',
    'mouthFrownRight', 'mouthFunnel', 'mouthLeft', 'mouthLowerDownLeft',
    'mouthLowerDownRight', 'mouthPressLeft', 'mouthPressRight', 'mouthPucker',
    'mouthRight', 'mouthRollLower', 'mouthRollUpper', 'mouthShrugLower',
    'mouthShrugUpper', 'mouthSmileLeft', 'mouthSmileRight', 'mouthStretchLeft',
    'mouthStretchRight', 'mouthUpperUpLeft', 'mouthUpperUpRight', 'noseSneerLeft',
    'noseSneerRight',
)


def from_video_at(video_path: str, times: list[float]) -> list[dict[str, float]]:
    """Коэффициенты на выбранных секундах — по кадру на отметку."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError('не смог открыть видео')
    out = []
    with vision.FaceLandmarker.create_from_options(_options(vision.RunningMode.IMAGE)) as fl:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t)) * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            scores = _scores(fl.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)))
            if scores:
                out.append(scores)
    cap.release()
    if not out:
        raise ValueError('на выбранных кадрах не нашлось лица')
    return out
