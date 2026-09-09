"""Чтение GLB, разбор скелета и запись новой анимации обратно в файл.

Работаем с glTF напрямую, без движка: нам нужны только иерархия костей, их
поза покоя и возможность дописать в файл дорожки поворотов. Так проект не тащит
за собой ни Blender, ни браузер.
"""
from __future__ import annotations

import base64
import json
import struct

import numpy as np

TYPE_COUNT = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
COMPONENT_DTYPE = {5120: 'i1', 5121: 'u1', 5122: 'i2', 5123: 'u2', 5125: 'u4', 5126: 'f4'}


class GLB:
    def __init__(self, data: bytes):
        magic, _, _ = struct.unpack('<4sII', data[:12])
        if magic != b'glTF':
            raise ValueError('это не GLB-файл')
        off = 12
        self.json: dict = {}
        self.bin = b''
        while off < len(data):
            length, ctype = struct.unpack('<I4s', data[off:off + 8])
            chunk = data[off + 8: off + 8 + length]
            if ctype == b'JSON':
                self.json = json.loads(chunk.decode('utf-8'))
            elif ctype.rstrip(b'\x00') == b'BIN':  # тип чанка добит нулём, а не пробелом
                self.bin = chunk
            off += 8 + length + (-length % 4)

    # ---------- чтение ----------

    @classmethod
    def load(cls, path: str) -> 'GLB':
        with open(path, 'rb') as f:
            return cls(f.read())

    def accessor(self, index: int) -> np.ndarray:
        acc = self.json['accessors'][index]
        view = self.json['bufferViews'][acc['bufferView']]
        offset = view.get('byteOffset', 0) + acc.get('byteOffset', 0)
        n = acc['count'] * TYPE_COUNT[acc['type']]
        raw = np.frombuffer(self.bin, dtype=np.dtype('<' + COMPONENT_DTYPE[acc['componentType']]), count=n, offset=offset)
        return raw.reshape(acc['count'], TYPE_COUNT[acc['type']]).astype(np.float64)

    @property
    def nodes(self) -> list[dict]:
        return self.json.get('nodes', [])

    def skin_joints(self) -> list[int]:
        skins = self.json.get('skins') or []
        if not skins:
            return []
        return list(skins[0]['joints'])

    def bone_names(self) -> list[str]:
        return [self.nodes[i].get('name', f'node{i}') for i in self.skin_joints()]

    def parents(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for i, n in enumerate(self.nodes):
            for c in n.get('children', []):
                out[c] = i
        return out

    def rest_local(self, index: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        n = self.nodes[index]
        if 'matrix' in n:  # редкий случай: узел задан матрицей
            m = np.array(n['matrix'], float).reshape(4, 4).T
            t = m[:3, 3]
            s = np.linalg.norm(m[:3, :3], axis=0)
            r = matrix_to_quat(m[:3, :3] / np.where(s == 0, 1, s))
            return t, r, s
        return (
            np.array(n.get('translation', [0, 0, 0]), float),
            np.array(n.get('rotation', [0, 0, 0, 1]), float),
            np.array(n.get('scale', [1, 1, 1]), float),
        )

    def rest_world(self) -> dict[int, np.ndarray]:
        """Мировые матрицы всех узлов в позе покоя (как модель лежит в файле)."""
        parents = self.parents()
        cache: dict[int, np.ndarray] = {}

        def world(i: int) -> np.ndarray:
            if i in cache:
                return cache[i]
            t, r, s = self.rest_local(i)
            m = compose(t, r, s)
            p = parents.get(i)
            cache[i] = (world(p) @ m) if p is not None else m
            return cache[i]

        for i in range(len(self.nodes)):
            world(i)
        return cache

    # ---------- запись ----------

    def add_animation(self, name: str, times: np.ndarray, tracks: dict[int, np.ndarray],
                      translations: dict[int, np.ndarray] | None = None) -> None:
        """Дописать клип: `tracks` — кватернионы по узлам, `translations` — сдвиг таза."""
        bin_parts = [self.bin]
        offset = len(self.bin)

        def push(array: np.ndarray, comp_type: str, acc_type: str, extra: dict | None = None) -> int:
            nonlocal offset
            raw = array.astype(np.float32 if comp_type == 'f4' else comp_type).tobytes()
            pad = (-len(raw)) % 4
            bin_parts.append(raw + b'\x00' * pad)
            view = {'buffer': 0, 'byteOffset': offset, 'byteLength': len(raw)}
            offset += len(raw) + pad
            self.json.setdefault('bufferViews', []).append(view)
            acc = {
                'bufferView': len(self.json['bufferViews']) - 1,
                'componentType': 5126,
                'count': len(array),
                'type': acc_type,
            }
            if extra:
                acc.update(extra)
            self.json.setdefault('accessors', []).append(acc)
            return len(self.json['accessors']) - 1

        time_acc = push(times.reshape(-1, 1), 'f4', 'SCALAR',
                        {'min': [float(times.min())], 'max': [float(times.max())]})
        samplers, channels = [], []
        for node, quats in tracks.items():
            out = push(quats, 'f4', 'VEC4')
            samplers.append({'input': time_acc, 'output': out, 'interpolation': 'LINEAR'})
            channels.append({'sampler': len(samplers) - 1, 'target': {'node': int(node), 'path': 'rotation'}})
        for node, values in (translations or {}).items():
            out = push(values, 'f4', 'VEC3')
            samplers.append({'input': time_acc, 'output': out, 'interpolation': 'LINEAR'})
            channels.append({'sampler': len(samplers) - 1, 'target': {'node': int(node), 'path': 'translation'}})

        self.json.setdefault('animations', []).append({'name': name, 'samplers': samplers, 'channels': channels})
        self.bin = b''.join(bin_parts)
        self.json['buffers'] = [{'byteLength': len(self.bin)}]

    def to_bytes(self) -> bytes:
        raw_json = json.dumps(self.json, separators=(',', ':')).encode('utf-8')
        raw_json += b' ' * ((-len(raw_json)) % 4)
        raw_bin = self.bin + b'\x00' * ((-len(self.bin)) % 4)
        total = 12 + 8 + len(raw_json) + (8 + len(raw_bin) if raw_bin else 0)
        out = [struct.pack('<4sII', b'glTF', 2, total),
               struct.pack('<I4s', len(raw_json), b'JSON'), raw_json]
        if raw_bin:
            out += [struct.pack('<I4s', len(raw_bin), b'BIN\x00'), raw_bin]
        return b''.join(out)

    def animation_names(self) -> list[str]:
        return [a.get('name', f'клип {i}') for i, a in enumerate(self.json.get('animations', []))]


# ---------- кватернионы и матрицы ----------

def compose(t: np.ndarray, q: np.ndarray, s: np.ndarray) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = quat_to_matrix(q) * s
    m[:3, 3] = t
    return m


def quat_to_matrix(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def matrix_to_quat(m: np.ndarray) -> np.ndarray:
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = 0.5 / np.sqrt(tr + 1.0)
        q = np.array([(m[2, 1] - m[1, 2]) * s, (m[0, 2] - m[2, 0]) * s, (m[1, 0] - m[0, 1]) * s, 0.25 / s])
    else:
        i = int(np.argmax([m[0, 0], m[1, 1], m[2, 2]]))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = 2.0 * np.sqrt(max(1e-12, 1.0 + m[i, i] - m[j, j] - m[k, k]))
        q = np.zeros(4)
        q[3] = (m[k, j] - m[j, k]) / s
        q[i] = 0.25 * s
        q[j] = (m[j, i] + m[i, j]) / s
        q[k] = (m[k, i] + m[i, k]) / s
    return q / np.linalg.norm(q)


def quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ])


def quat_conj(q: np.ndarray) -> np.ndarray:
    return np.array([-q[0], -q[1], -q[2], q[3]])


def quat_from_to(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Кратчайший поворот, совмещающий направление a с направлением b."""
    a = a / (np.linalg.norm(a) or 1e-9)
    b = b / (np.linalg.norm(b) or 1e-9)
    d = float(np.dot(a, b))
    if d > 0.999999:
        return np.array([0.0, 0.0, 0.0, 1.0])
    if d < -0.999999:  # ровно противоположные: крутим вокруг любой перпендикулярной оси
        axis = np.cross(a, np.array([1.0, 0.0, 0.0]))
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, np.array([0.0, 1.0, 0.0]))
        axis /= np.linalg.norm(axis)
        return np.array([axis[0], axis[1], axis[2], 0.0])
    axis = np.cross(a, b)
    q = np.array([axis[0], axis[1], axis[2], 1.0 + d])
    return q / np.linalg.norm(q)


def slerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        q = a + (b - a) * t
    else:
        theta = np.arccos(np.clip(d, -1, 1))
        q = (np.sin((1 - t) * theta) * a + np.sin(t * theta) * b) / np.sin(theta)
    return q / np.linalg.norm(q)
