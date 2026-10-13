# Session Memory — 2026-10-13 (updated 2026-10-13)

## Project
- Repo: https://github.com/ALF-Mii/sva-python-scripts.git branch main
- Working dir: C:\Users\alfre\Documents\sva-python-scripts
- GitHub CLI (`gh`) is **not logged in**. Git push still pending — first action next session.

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
- Real high-poly test asset (currently primitive proxies)
- Auto-Ring axis/count defaults need tuning on a real asset
- Optional: curvature-adaptive density, freehand curve drawing, ribbon helper
- PolyRetopo benchmark comparison narrative for the demo

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

## Next session first steps
1. Run `gh auth login`, then `git add -A && git commit -m "Add QuadWrap ..."` and `git push`.
2. Confirm `python maya_bridge.py ping` → ALIVE.
3. Load a real high-poly sculpt (FBX/OBJ) and test Fit, presets, heat map, deform.
4. Tune Auto-Ring on that asset; capture demo material.
5. If time permits: curvature preview + ribbon helper.