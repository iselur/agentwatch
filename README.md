# agentwatch

Tail what your coding agent is doing, right now.

One line per action, live, across every session you have open. No message text, no
API key, no network, no daemon — it reads the session logs Claude Code and Codex
already write to your own disk.

```bash
pip install 'stillworks[all]'   # the whole family of agent tools, including this one
pip install agentwatch          # or just this one, zero dependencies
```

Or run it straight from a checkout — it is stdlib only:

```bash
git clone https://github.com/iselur/agentwatch
cd agentwatch && python3 -m agentwatch --help
```

---

## 30-second quickstart

```
$ agentwatch
  watching 3 session logs · Ctrl-C to stop
09:41:02  api-server    » you
09:41:07  api-server    $ pytest tests/test_auth.py -x
09:41:19  api-server    ✗ pytest tests/test_auth.py -x
09:41:24  api-server    ✎ src/auth/session.py
09:41:31  api-server    $ pytest tests/test_auth.py -x
09:41:44  web           ✎ components/Nav.tsx
09:41:52  api-server    » you
```

Five marks, and that is the whole vocabulary:

| mark | meaning |
|------|---------|
| `»`  | a turn started — you said something |
| `$`  | the agent ran a command |
| `✎`  | the agent wrote or edited a file |
| `✗`  | a call failed |
| `·`  | the agent read a file (off by default — `--reads`) |

---

## Why it exists

You hand a task to an agent and then you are outside the loop. The terminal you
launched it from scrolls too fast to read, a second agent in another window has no
terminal you are looking at at all, and "what has it actually been doing for the
last ten minutes" has no answer short of scrolling back through a transcript.

agentwatch answers exactly that question and nothing else. It is a `tail -f` for
agent activity: commands, file writes, failures, and where each turn began.

It is deliberately not a transcript viewer. **It never reads message text** — not
yours, not the agent's. What it extracts is the shape of the work: which command,
which file, which project, when.

---

## Usage

```bash
agentwatch                     # follow every active session, live
agentwatch --since 10m         # replay the last ten minutes, then keep following
agentwatch --once              # print what is there and exit
agentwatch --project api       # only projects whose name contains "api"
agentwatch --only cmd,error    # just the commands and the failures
agentwatch --reads             # include file reads (an agent reads a great deal)
agentwatch --claude            # Claude Code logs only
agentwatch --codex             # Codex logs only
agentwatch --json              # one JSON object per line, for scripts
```

`--since` takes `10m`, `2h`, `3d`, `1w`, or a date like `2026-08-03`.

### Things worth trying

```bash
# What has gone wrong across every project today?
agentwatch --since 1d --once --only error

# Every file any agent has touched in the last hour, deduplicated:
agentwatch --since 1h --once --only write --json | python3 -c '
import json,sys
print(*sorted({json.loads(l)["text"] for l in sys.stdin}), sep="\n")'

# Watch one project on a second monitor:
agentwatch --project api-server
```

### JSON

```
$ agentwatch --since 5m --once --json
{"at": "2026-08-04T09:41:07.412000+00:00", "kind": "cmd", "project": "api-server", "session": "4ef1361b-07e4-4bc9-bb29-1783b761d677", "source": "claude", "text": "pytest tests/test_auth.py -x"}
```

One object per line, sorted keys, stable field set: `at`, `kind`, `project`,
`session`, `source`, `text`. Notes ("watching 3 session logs") go to stderr, so
stdout stays machine-clean.

---

## Exit codes

| code | meaning |
|------|---------|
| `0`  | normal — including Ctrl-C, and including a broken pipe from `\| head` |
| `2`  | usage error, or `--home` pointed at a directory that is not there |

There is deliberately no exit `1`. agentwatch reports what an agent is doing; it
does not judge it. Nothing it can see is a failure of yours.

---

## What it reads

| agent | log location |
|-------|--------------|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**/*.jsonl` |

Both are append-only JSONL, which is the reason this tool needs no dependency and
no daemon: remember a byte offset per file, read from there, repeat. Symlinks are
never followed — a log reachable by two paths would otherwise print every event
twice.

It opens those files read-only and writes nothing to them. `--home DIR` (or
`AGENTWATCH_HOME`) points it somewhere else, which is how its own tests run.

Logs untouched for more than 15 minutes are treated as finished and skipped;
`--stale SECONDS` changes that.

---

## Privacy

- Never reads prompt or response text — only tool activity.
- Never sends anything anywhere. There is no network code in this package.
- Never writes to a session log.
- No API key, no account, no config file.

---

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Zero dependencies, so there is nothing to install first.

---

## Part of a small family

Four more zero-dependency tools for working with coding agents:

| tool | what it does |
|------|--------------|
| [stillworks](https://github.com/iselur/stillworks) | record what your code does now, catch when it changes |
| [unedit](https://github.com/iselur/unedit) | a safety net for letting an agent loose on your files |
| [agentdiff](https://github.com/iselur/agentdiff) | see what the agent actually changed, before you merge |
| [agentlog](https://github.com/iselur/agentlog) | what did your coding agent actually do today? |

```bash
pip install 'stillworks[all]'
```

---

MIT licensed.
