# agentwatch

Tail what your coding agent is doing, right now.

One line per action, live, across every session you have open. No message text, no
API key, no network, no daemon — it reads the session logs Claude Code and Codex
already write to your own disk.

```bash
pip install stillworks   # one install, all five agent tools, including this one
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
agentwatch --project api       # only projects whose name or path contains "api"
agentwatch --only cmd,error    # just the commands and the failures
agentwatch --reads             # include file reads (an agent reads a great deal)
agentwatch --claude            # Claude Code logs only
agentwatch --codex             # Codex logs only
agentwatch --json              # one JSON object per line, for scripts
agentwatch --interval 5        # look for new activity every 5s (default 1)
agentwatch --no-color          # never colourise, whatever the terminal is
agentwatch --version           # which agentwatch this is
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

A `·` is a `Read` tool call under Claude Code, where the path is a field.  Codex
has no read tool — it reads by running `sed -n '1,200p' notes.md` — so under
Codex the `·` is read out of the command text, by the same rule
[agentlog](https://github.com/iselur/agentlog) uses and from the same shared
module.  Before v0.1.1, `--reads` accepted a Codex log and showed nothing at
all, which reads as a quiet session rather than as a flag that was never wired
up.

The rule under-reports on purpose: only verbs that open everything handed to
them count, and nothing that searches counts at all, because `rg pattern src/`
puts a pattern, a glob and a directory where a path goes.  A `·` for a file that
was never opened is worse than a `·` that never appears.

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
four different things: a person typing, a tool result fed back into the loop, a
prompt the agent wrote for a subagent (marked `isSidechain: true`), and text
Claude Code puts into the conversation on its own account (marked `isMeta:
true`) — the caveat before a slash command's output, the body of a skill being
loaded, a message relayed from another session, a nudge to continue, the
placeholder standing in for a pasted image. Only the first is a turn. On 896
real session logs, counting the subagent prompts too put 678 extra `»` marks in
the feed against 2314 real ones, and the injected text a further 210 — and `»`
is the mark a person scrolls back to when finding where they left off, so a
false one points at the wrong place. That matters more in a feed than in a
digest: you are watching it because you stepped away, and a `»` you did not
write reads as the session having been handed an instruction you did not give.
The work around those records still streams: the commands ran, the files were
written, the failures failed, all in this session. A record with neither field
— an older log — is a turn; only an explicit `true` is machine text.

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

**Editing one file twice is two edits.** Older Codex builds name a patched file
in both records, so the second sighting has to be dropped or every edit shows
up twice. The rule for dropping it used to be thirty seconds on the clock — the
same file again, that soon, is the echo — and nothing in that rule asked whether
an echo was even possible. An agent that fixes a file, runs the tests and fixes
it again eight seconds later made two edits, and the second one silently did
not exist. On 1189 real session files that swallowed 133 of 742 successfully
patched paths, better than one in six, across 87 sessions; the gaps were spread
evenly from five seconds to thirty, which is what ordinary consecutive work
looks like, while a real echo lands in well under a second. What makes a report
an echo is the pairing, not the clock: one envelope buys exactly one
suppression, and two edits bring two envelopes however close together they
land. The window survives only as an expiry, so an envelope whose result never
came cannot sit waiting to swallow the next real edit.

**A script that failed is a failure whether or not it named a command.** Codex
runs everything through a JavaScript snippet, and a snippet that only sends a
patch has no command in it to be named after. Those failures used to be
dropped, on the grounds that the `patch_apply_end` had already reported the
same thing with the files and the reason in it. Measured against every rollout
on this machine, that was true almost none of the times it was used: of 67
failing snippets with no command to name, not one shared a call id with a
failed patch. Their patches had failed *inside* the script, so no end record
was ever written and nothing said anything at all — one real session showed `0
errors` against six failed patch attempts. They are reported now, named after
the files the envelope was trying to change, because `patch server.py` is a
line you can act on and a bare "something failed" is not. Five more had nothing
in them at all — a snippet that died of a syntax error before it ran anything —
and those say `script failed`, which is all there is to say.

The silence is kept for what it was meant for: a patch that failed to apply is
still reported once, by the record that names the files, and not a second time
by the script's own result. Working out which script a patch belongs to takes
two steps, because the end record usually does not carry the script's id — of
713 real patch results, 646 are named `exec-<uuid>`, which appears nowhere else
in the file. So the id is used when it matches, and otherwise the patch belongs
to the script that was running when it failed.

**A subagent's transcript is part of a session, not a session of its own.** When
Claude Code hands work to a subagent it writes that subagent's whole transcript
to its own file, in a `subagents/` directory named after the parent session.
Every `.jsonl` under the projects tree was being adopted as a separate sitting.
On the developer's machine 393 of 864 Claude log files are subagent transcripts
— 45% of them — so `watching N sessions` counted nearly twice the sittings that
existed, and every event a subagent produced carried a session id of
`agent-a0940e681059ff8ec`, which names nothing a person can look up. The work
itself was never the problem and has not changed: a `pytest -x` a subagent ran
is a command that ran on this machine during this sitting, and it still
streams. What goes is the claim that it happened somewhere else — the id now
comes from the directory, which names the session, rather than the filename,
which names nothing. A workflow's agents get a run directory of their own
inside `subagents/`, one level deeper again, so the session is the directory
holding `subagents` however far below it the transcript sits.

**Codex spawns subagents too**, and gives each one a rollout file of its own,
named after the thread rather than the sitting. The `session_meta` record on
the file's first line carries both — `id` is the thread, `session_id` is the
sitting that asked for the work — so that is where the name now comes from. On
the developer's machine 42 of 1189 Codex rollouts are subagent threads. For a
top-level session the two are equal, so nothing else moves. A rollout joined
partway through never sees that record and keeps the name it was opened with,
which is the best that is left.

Logs untouched for more than 15 minutes are treated as finished and skipped;
`--stale SECONDS` changes that.

**A log it cannot open is named, not counted.** A session log that will not
open — wrong permissions, a mount that went away — is easy to adopt anyway,
count in `watching 2 session logs`, and then silently never emit an event
from. That is the one failure this tool exists to prevent: a quiet screen
reads as an idle agent, and here it would mean a locked file. Such a log is
left out of the count and said out loud instead:

```
  note: 1 session log is not shown — could not be read
      /home/you/.claude/projects/p/bbb.jsonl
```

That is the same sentence `agentlog` prints about the same logs — one wording
in both commands, since they arrive in one install and a reader who runs both
should not have to work out that two notes are one problem. `agentwatch` names
the paths, up to three of them, because a live screen has no `--verbose` to
offer and the path is what you would act on.

The note goes to stderr, so `--json` stdout stays a clean stream of JSONL,
and it is a live property rather than a verdict stamped at startup — fix the
permissions mid-watch and the file is picked up on the next scan and drops
off the list. A file that *opens* and yields no events is deliberately not
reported: most records in a session log are not events, so that is the
ordinary case on every file all day. Exit stays `0`; agentwatch reports, it
does not gate.

**A record is shown once, however many files it is written into.** `claude
--resume` does not continue the old file. It opens a new session with a new id,
copies the earlier transcript into it verbatim — same uuids, same timestamps —
and only then starts appending new work. A watcher adopts that new file under
the rule "a file that appeared since we started is read whole, because all of
it is new to the user", and every command, write and error of the earlier
sitting scrolls past a second time as though it were happening now. The same
records also land in two files when a project directory is copied or moved: the
log exists under both names, neither is a symlink, and the walk finds both.

Both have the same answer: a record uuid names an event, and an event is shown
once. Files are adopted oldest first, by mtime, so the events appear under the
directory the work was actually done in. A replayed record is still read — it
is where a resumed session says which directory it is in — but it prints
nothing.

Codex records carry no uuid, and its several files for one session are parallel
workers doing separate work, so nothing there is deduplicated.

---

## Privacy

- Never reads prompt or response text — only tool activity.
- Never sends anything anywhere. There is no network code in this package.
- Never writes to a session log.
- No API key, no account, no config file.

The first line has to hold for the parts of a session file that are not the
conversation, and two of those carry message text where nobody looks for it: a
`queue-operation` record holds the whole of a prompt you typed while the agent
was busy, and a `frame-link` record holds a question of yours turned into a
heading. There are 4983 of the first and 104 of the second in this machine's
logs. agentwatch has no branch for either, and `tests/test_privacy_claims.py`
keeps them in its fixture so that the day somebody writes one — a queued-work
indicator is the obvious reason to — the tests are what they meet first.

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
pip install stillworks
stillworks tools
```

## License

MIT. Copyright (c) 2026 stillworks contributors.
