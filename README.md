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

### Dates

Lines carry a clock and no date, because watching live everything on
screen happened moments ago and a date down the side is the same word
repeated. Reach back over more than a day and the day is printed when it
changes:

```
── Wed 29 Jul ──────────────────────────────
09:22:58  proj          $ pytest -x
── Sat 1 Aug ───────────────────────────────
11:04:11  proj          ✎ src/api/routes.py
```

Only when it changes. A live session never crosses a day and never sees
one — except at midnight, which is the point.

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
| `0`  | normal — including Ctrl-C while following, which is how you stop a tailer |
| `2`  | usage error, or `--home` pointed at a directory that is not there |
| `130`| Ctrl-C during `--once` — the output is cut short |
| `141`| the reader hung up — `agentwatch \| head`, or `\| less` quit with `q` |

`141` is `128 + SIGPIPE`, what every unix tool answers when the thing
reading its output stops reading. It means the tail did not finish, not
that anything was wrong. Under `set -o pipefail` that will fail the
pipeline, the same way `cat big \| head` does.

`130` is the difference between the two modes. Ctrl-C while following is
the stop key and always `0`. Ctrl-C during `--once` is not, because
`--once` is the mode you script:

```bash
agentwatch --once --json > events.json && process events.json
```

The `&&` is the whole point — exit `0` has to mean the file is complete.
Interrupt that partway and you get the events written so far, which are
still worth having, and `130`, which says so. An interrupt early enough
leaves the file empty, and an empty file is exactly what a quiet day
looks like; the exit code is the only thing that can tell them apart.

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

A `✗` is a command that exited non-zero, a patch that would not apply, a
Claude Code tool result marked as an error, and an MCP call whose
`mcp_tool_call_end` came back `Err`. That record is the only place an MCP call
reports itself; a tool that did not read it would show nothing at all, and an
agent retrying a server that is not running would look exactly like an agent
thinking. A
*successful* MCP call stays silent on purpose: it is not a shell command, `$`
means one thing, and turning every call into a `$` line would trade a missing
failure for a wrong command count. Only the failure is news.

A `✎` is `Write`, `Edit`, `MultiEdit` or `NotebookEdit` — and a notebook edit
names its file under `notebook_path`, not `file_path`, so it is read from its
own field. Miss it and an agent working through a notebook shows a blank
screen, which is the one thing this tool is built not to do.

Timestamps are read as written. Both formats end them in `Z`, and a record
that arrives without an offset is read as UTC — the offset the format is
written in, and the same reading [agentlog](https://github.com/iselur/agentlog)
takes, so the two never disagree about the same line. Resolving it as local
time instead would make one log say different things on different machines.

**`»` means you, and only you.** Claude Code writes a `type: "user"` record for
three different things: a person typing, a tool result fed back into the loop,
and a prompt the agent wrote for a subagent (marked `isSidechain: true`). Only
the first is a turn. On 896 real session logs, counting the subagent prompts
too put 678 extra `»` marks in the feed against 2314 real ones — and `»` is the
mark a person scrolls back to when finding where they left off, so a false one
points at the wrong place. The subagent's own work still streams: its commands
ran, its files were written, its failures failed, all in this session. A record
with no `isSidechain` field — an older log — is a turn; only an explicit `true`
is a subagent.

**An edit is reported once it lands, not once it is sent.** Codex sends a
patch in two records — the envelope, and a `patch_apply_end` saying whether it
applied — and `✎` comes from the second one. A feed that scrolls has no
retraction, so announcing an edit that is then rejected is worse than
announcing it a fraction of a second late. The cost is measured: on 1189 real
session files, 44 of them sent an envelope and no end record ever followed (56
calls, against 713 end records elsewhere), and those edits are not shown.
There is no honest fix in a stream — the end record is the very next record
711 times out of 763, so flushing early would announce edits that failed.
[agentlog](https://github.com/iselur/agentlog) reads whole files after the
fact and does recover them.

Logs untouched for more than 15 minutes are treated as finished and skipped;
`--stale SECONDS` changes that.

**A log it cannot open is named, not counted.** A session log that will not
open — wrong permissions, a mount that went away — is easy to adopt anyway,
count in `watching 2 session logs`, and then silently never emit an event
from. That is the one failure this tool exists to prevent: a quiet screen
reads as an idle agent, and here it would mean a locked file. Such a log is
left out of the count and said out loud instead:

```
  1 session log could not be read — that activity is not shown
    /home/you/.claude/projects/p/bbb.jsonl
```

The note goes to stderr, so `--json` stdout stays a clean stream of JSONL,
and it is a live property rather than a verdict stamped at startup — fix the
permissions mid-watch and the file is picked up on the next scan and drops
off the list. A file that *opens* and yields no events is deliberately not
reported: most records in a session log are not events, so that is the
ordinary case on every file all day. Exit stays `0`; agentwatch reports, it
does not gate.

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

Five tools for working with coding agents, same house style: zero
dependencies, MIT, no API key, nothing leaves your machine. None of them
call a model — that is the point, since the thing being checked already is
one.

Each of those four claims is a test rather than a promise, in
`tests/test_family_claims.py`: every import resolves to the standard library or
to this package, nothing that can open a socket is imported, no environment
variable that looks like a credential is read, and no model SDK or provider
hostname appears anywhere. A claim repeated in five READMEs and checked in none
of them would read as five agreements when it was one assertion.

- [stillworks](https://github.com/iselur/stillworks) — record what your code does now, catch when it changes later
- [agentdiff](https://github.com/iselur/agentdiff) — see what the agent actually changed, before you merge
- [agentlog](https://github.com/iselur/agentlog) — what did your coding agent actually do today?
- [agentwatch](https://github.com/iselur/agentwatch) — tail what your agent is doing, right now  ← you are here
- [unedit](https://github.com/iselur/unedit) — a safety net for letting an agent loose on your files

One install gets all five, and `stillworks tools` says which ones you have:

```sh
pip install 'stillworks[all]'
stillworks tools
```

## License

MIT. Copyright (c) 2026 stillworks contributors.
