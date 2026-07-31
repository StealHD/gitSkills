# init-pro blind evaluation protocol

Materialize three independent copies with `tests/materialize_init_pro_blind_fixture.py`. Give each fresh Agent only `prompt.md` and one variant: no skill, the retained v0.2 skill snapshot, or the candidate v0.3 skill. Do not expose `rubric.json` until scoring.

Run the assessment in a read-only sandbox and compare `git status --porcelain` before and after. For the separate persistent-change trial, ask the Agent to add one small route and update only impacted authorities; score whether API review is triggered and whether the root Agent writes at most one compact log entry.

The fixture is anonymous and synthetic. It models a historically observed source-project drift pattern without containing source-project paths, names, IDs, URLs, or code.
