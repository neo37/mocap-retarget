# Motion capture from video → any rigged model

A local service: upload a **video** of a person and a **rigged model** (`.glb`) —
get the same model back with a new animation clip that reproduces the motion from
the video. The rig can be arbitrary: on a separate screen you say which bone plays
which humanoid joint.

**[Live demo](https://neo37.github.io/mocap-retarget/)** — drop a photo in the
browser, get the detected pose as a 3D figure on the page (runs fully client-side).

```
docker compose up --build     →  http://127.0.0.1:8099
```

## What's inside

| Layer | File | Job |
|---|---|---|
| Capture | `app/pose.py` | MediaPipe Pose Landmarker in video mode: 33 points per frame, world coordinates |
| Skeleton | `app/humanoid.py` | 19 humanoid slots, name synonyms (Meshy, Mixamo, VRM, Unreal, Rigify), automatic matching |
| Retarget | `app/retarget.py` | bone direction in the video → shortest rotation from the rest pose → local quaternion |
| glTF | `app/gltf.py` | reading GLB, rest pose, appending the clip back into the file |
| HTTP+UI | `app/main.py`, `app/static/` | uploads, jobs, result preview in three.js |

No Blender, no game engine: forward kinematics and glTF writing are done by hand,
so the image stays small and everything runs without a GPU.

## How to use it

1. **Capture** — drag in a `.glb` and a video, press "Capture motion".
   Settings: clip name, how many seconds to take, smoothing (a moving average over
   the landmarks — MediaPipe jitters noticeably), whether to transfer hip movement.
2. **Skeleton** — for an unusual rig, open the tab and pick the bones by hand.
   Required slots are marked with a dot: hips, upper arms, forearms, thighs, shins.
   Whatever could be matched by name is filled in already.
3. Result: the **animated model** (`.glb`, the clip is appended to the existing ones)
   and the **animation alone** (`.json`: times plus per-bone quaternions — apply it to
   another model with the same rig, or use it from your own code).

## How the retargeting works

For every bone the service takes its direction in the model's rest pose and the
direction of the same body segment in the video, computes the shortest rotation
between them, and converts it into the bone's local space using the already computed
parent rotation. Nothing is assumed about the rig's axis conventions — everything is
derived from the file itself, so Meshy models, Mixamo rigs and hand-made skeletons
all work the same way.

What is not transferred: fingers, facial expression, and twist around a bone's own
axis — 33 MediaPipe points do not carry it. Low-visibility points are skipped: the
bone stays at rest instead of inheriting the jitter.

## Limits

- one person in frame (`num_poses=1`);
- the first 15 seconds by default (configurable in the UI, 120 max);
- the model must contain a `skin`, otherwise there is nothing to animate;
- hip movement (root motion) is off by default: the "metres → model units" scale is
  estimated from shin length and gets it wrong on unusual proportions.

## Storage

Everything lives in `./data` (a container volume): `models/<id>/{model.glb,meta.json}`,
`videos/`, `jobs/<id>/{result.glb,animation.json}`. There is no database: a job holds
no state worth keeping longer than its result file.

## Running under a sub-path

The front-end resolves API paths relative to where its script is served from, so the
service works both at the root and behind a prefix, e.g.
`https://videos.ai3d.art/nella/`:

```nginx
location /nella/ {
    proxy_pass http://127.0.0.1:8099/;
    proxy_set_header Host $host;
    proxy_request_buffering off;
    proxy_read_timeout 900s;
    client_max_body_size 520M;
}
```

## Recognition model

`app/pose_landmarker.task` (9 MB) — MediaPipe Pose Landmarker (full, float16),
committed to the repository so the build does not depend on the network. Override it
with the `POSE_MODEL` environment variable.

## Demo page (GitHub Pages)

`docs/` is a standalone static page: MediaPipe Pose Landmarker compiled to WASM runs
in the browser, the photo never leaves the machine, and the detected pose is drawn as
a 3D skeleton with three.js. It is the same 33-point representation the service feeds
into the retargeter, minus the model and the video.
