# Voice-agent handoff — run in terminal (Windows 11, PowerShell)

Goal from `voice-agent-prompt.txt`: TALK to OpenCode instead of typing.
This handoff gets `opencode` CLI installed so we can answer prompt steps 1-3 with real `--help` output, not guesses.

## 0. Fresh PowerShell (as user, not admin)
```powershell
# what we already know: Python 3.13.14 yes, Edge yes, mic OK, Node no, ffmpeg no, opencode CLI no
python --version
Get-PnpDevice -Class AudioEndpoint | Select FriendlyName,Status
```

## 1. Install Node LTS + opencode
```powershell
winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
# CLOSE + REOPEN terminal (PATH refresh), then:
node --version
npm --version
npm install -g opencode-ai
opencode --version
where.exe opencode
```

If `opencode` not recognized after reopen: `npm prefix -g` — that dir must be on user PATH (`%AppData%\npm` by default).

Docs say native alternative: `scoop install opencode` or `choco install opencode`. Pick ONE (npm is fine since you need Node anyway). OpenCode 1 vs 2 share the `opencode` name — don't install both.

## 2. Discover headless / continue / permissions (DO NOT guess — paste output back)
```powershell
opencode --help
opencode run --help
```

Find and paste back:
- one-shot without TUI: expected `opencode run "message"` — confirm from help
- continue same session: expected `--continue` / `-c` and `--session` / `-s` — confirm wording
- permissions in headless: expected `--auto` (auto-approve non-denied) — confirm, and check for `dangerously-skip-permissions` / `yolo`
- config file location for `permission` map (`opencode.json`)

Reference (VERIFY with your local --help, versions drift):
- `opencode run "hello"` = non-interactive one-shot, streams to stdout, exits on idle
- `opencode run --continue "next"` or `--session <id>` = same session keeps context
- without `--auto`, asks become `auto-rejecting` + run ends early (no hang)
- with `--auto`, non-denied asks are approved; explicit `deny` still enforced
- never use skip-all-permissions by default

## 3. Auth (needed once)
```powershell
opencode auth login
```

## 4. Paste back to agent
Paste the full output of `opencode --help` + `opencode run --help`, plus `node --version` and `opencode --version`. Then I write the plan and smallest build: Edge Web Speech (LISTEN) -> `opencode run --session` (SEND) -> text stream (SHOW).
```
