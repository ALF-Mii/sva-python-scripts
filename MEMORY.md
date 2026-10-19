# Session Memory — 2026-10-13 (updated 2026-10-19)

## Project
- Repo: https://github.com/ALF-Mii/sva-python-scripts.git branch main
- Working dir: C:\Users\alfre\Documents\sva-python-scripts
- Last commit `a3c0936` **is pushed** to origin/main. `gh` CLI is still not
  logged in, but plain `git push` works — no need for `gh`.
- **Submission repo:** https://github.com/ALF-Mii/pfa-week06.git (course repo
  "pfa", week 06 / midterm). QuadWrap was uploaded there 2026-10-19 with a
  full README (install/run, what works, what doesn't, peer-run section).
  It also holds the test assets `TARGET_MESH.fbx` (5,077 faces) and
  `CAGE_MESH.fbx` (400 faces) — **use these for real testing**, they are the
  only real meshes on the machine.
- Peer-run section in that README is a DRAFT — the user must replace the
  classmate's name and the three sentences with what they actually said.

## Verified on the real FBX assets (2026-10-19)
- Cage max deviation from target: **7.0922 before fit → 0.0000 after fit**
- Commit preserved topology exactly: 400 faces / 382 verts / 0 non-manifold
- Heat map, deform test, guide strip, auto-ring, mirror all OK on real assets
- Maya's shrinkWrap `.projection` enum has only 5 values; "Closest if no hit"
  is a separate boolean, not a mode

## Machine / Maya
- Windows, Maya 2027 at C:\Program Files\Autodesk\Maya2027\bin
- Maya bridge port 54321. Bridge is **READY** and bootstraps itself:
  C:\Users\alfre\Documents\maya\2027\scripts\userSetup.py now appends the repo
  path and calls maya_setup.open_port(54321). If you delete that file, fall
  back to the manual Script Editor paste in maya_setup.py.
- Verify with: `python maya_bridge.py ping` (expect ALIVE).

## Scripts / artifacts
- `quadwrap.py` — QuadWrap mid-term tool (week-1 skeleton + heat map + deform
  test). Maya cmd + OpenMaya2. UI via show_ui().
- `QUADWRAP_DESIGN.md` — deep design doc + resolved decisions + 3-week plan.
- `chandelier_generator.py` — earlier class tool (reference style).
- `maya_bridge.py` — socket client (`run`, `run_file`, `ping`; CLI now
  `ping`, `run <code>`, `run_file <path.py>`).
- `maya_setup.py` — Maya-side port opener.
- `.gitignore` — ignores `__pycache__/`, `*.pyc`, `.quadwrap/`.

## QuadWrap status (live-verified 2026-10-13)
Works:
- Settings persistence + 3 presets (Prop/Character/Hard-Surface) + Save Current
- Selection validation + bad-input handling + Undo/Clear (safe, registry-based)
- Draw Strip from ordered vertex row; Close Loop; Auto-Ring; Mirror
- Fit to Surface via Maya shrinkWrap deformer; live slider updates (envelope etc.)
- Preview ghost mesh; Commit bakes deformed mesh in one undo chunk
- Stats readout incl. non-manifold edge count
- Heat map: per-vertex distance to target → green/red CPV (OpenMaya)
- Deform test: bind committed cage to 2 joints and bend 45°

Not done yet:
- Real high-poly test asset (currently primitive proxies) — **nothing usable
  on disk yet**
- Auto-Ring axis/count defaults need tuning on a real asset
- Optional: curvature-adaptive density, freehand curve drawing, ribbon helper
- PolyRetopo benchmark comparison narrative for the demo

## Fixes applied 2026-10-19
- Removed the invalid "Closest if no hit" item from the Projection Mode
  dropdown (Maya's enum has 5 entries). Added a real **Closest If No
  Intersection** checkbox bound to `.closestIfNoIntersection`.
- `_sanitize_settings` now clamps `projection_mode` to 0–4 (was 0–5).
- `_ui_setting_changed` re-sanitizes on every change, so the UI can't write
  an illegal value.
- `maya_bridge.run_file` now execs scripts in one shared namespace so
  module-level imports are visible inside functions.
- Verified live: cage r=6 → snaps to r=5 sculpt across all 5 projection modes;
  preview ghost and commit both sit on the surface; heat map + deform OK.

## Decisions made
- Projection via Shrink Wrap deformer (Isaac) instead of custom code.
- Guide strips created from selected vertex rows (not freehand drawing).
- Tool fits a user-provided cage; does not auto-mesh arbitrary strips.
- Extras priority: heat map → deform test → curvature (stretch).
- Settings under ~/.quadwrap/settings.json; presets in code + custom via JSON.

## Corrections / rules for next time
- PowerShell has no `tail`/`head`; use Select-Object / Select-String.
- Bridge `run_file` returns useful prints on separate lines; Maya logs are noisy.
  Grep output for the marker values.
- mayapy compile check: `mayapy -m py_compile file.py`.
- In Maya standalone, `cmds.xform("mesh.vtx[*]", ...)` returns flattened positions.
- `_shape_of()` returns the first non-intermediate mesh shape.
- Keep classes/projects as one self-contained .py per tool (matches class style).
- **Maya's shrinkWrap `.projection` enum has only 5 values** (0 Toward Inner,
  1 Toward Center, 2 Parallel To Axes, 3 Vertex Normals, 4 Closest). Setting
  5+ raises "Cannot set the attribute past its maximum value of 4".
  "Closest if no hit" is NOT a mode — it is the separate
  `.closestIfNoIntersection` boolean.
- **A floating cage over a flat target makes shrinkWrap look broken.** Use a
  cage that overlaps the sculpt (e.g. r=6 cage over r=5 sculpt) when testing;
  then all 5 projection modes snap correctly.
- After editing `quadwrap.py`, the running Maya still has the old module.
  Reload it: `importlib.reload(quadwrap)` before testing.
- `run_file` scripts used to lose module-level imports inside functions
  (separate globals/locals). Fixed 2026-10-19 — the file now execs with one
  shared namespace, so `import math` at the top works inside functions.

## Next session first steps
1. Confirm `python maya_bridge.py ping` → ALIVE (Maya must be running; the
   `userSetup.py` in `Documents\maya\2027\scripts` opens the port on launch).
2. Run the smoke test: `python maya_bridge.py run_file <qw_smoke.py>`.
3. Build/import a real high-poly sculpt (FBX/OBJ) — none exists on disk yet —
   and test Fit / presets / heat map / deform against it.
4. PolyRetopo benchmark vs QuadWrap on that asset → numbers for the demo.
5. Curvature-adaptive density (the last instructor suggestion not yet built).