"""Run `autoeval.py run EVAL` again after each 5-hour session reset, until every run is done.

    python3 autorun.py eval-2026-10-19 [any other autoeval.py run options]

autoeval.py stops a batch at stop_at_plan_pct (config.toml) of your 5-hour session, or before a run when weekly
usage is past stop_at_weekly_pct, and says why in its exit code: 0 done, 3 stopped for usage, 4 runs deferred to the
next session, 5 another agent batch (autoeval or techtest) holds the lock, 1 error. This waits for the 5-hour session
to reset after 3 or 4, waits and retries after 5, and stops on 0, 1, a weekly stop, or a batch that makes no progress
in a fresh session (something other than usage is wrong). A run stopped mid-way for usage (run_agent.py's
stop_mid_run_plan_pct, or Claude Code's own limit message) is paused, not lost: the next batch resumes its session.
Detached from the terminal:
    setsid nohup python3 autorun.py EVAL > runs/EVAL-autorun.log 2>&1 &
"""
import datetime, subprocess, sys, time
from pathlib import Path

import harness as H
import run_agent as RA
import autoeval as A

HERE = Path(__file__).resolve().parent
LOCK_WAIT_S = 600       # another batch holds the lock: look again this often
USAGE_WAIT_S = 600      # can't read plan usage: first wait, doubling up to USAGE_WAIT_MAX_S
USAGE_WAIT_MAX_S = 3600


def left(a):
    """Runs still missing for exactly what this autorun asked for (--only, --just, --reps)."""
    ws = A.only_ws(A.workspaces(a.eval, a.ws), a.just)
    ids = a.only.split(",") if a.only else list(H.load_tasks())
    return sum(1 for rep in range(1, a.reps + 1) for t in ids for name in ws
               if rep not in H.rep_list(A.version(a.eval, name), t))


def main():
    ev, extra = sys.argv[1], sys.argv[2:]
    a = A.parser().parse_args(["run", ev, *extra])
    usage_wait = USAGE_WAIT_S
    while True:
        before = left(a)
        start_pct = (RA.plan_usage() or {}).get("five_hour")
        print(f"\n=== {datetime.datetime.now():%Y-%m-%d %H:%M} batch start: {before} run(s) left, session at "
              f"{start_pct}%", flush=True)
        code = subprocess.run([sys.executable, "-u", str(HERE / "autoeval.py"), "run", ev, *extra], cwd=HERE).returncode
        now = left(a)
        if code == A.EXIT_DONE:
            print("=== all runs done" if now == 0 else f"=== autoeval.py finished; {now} run(s) still missing "
                  "(see the log above)", flush=True)
            return
        if code == A.EXIT_LOCK:
            print(f"=== another agent batch holds the lock; trying again in {LOCK_WAIT_S // 60} min", flush=True)
            time.sleep(LOCK_WAIT_S)
            continue
        if code not in (A.EXIT_USAGE, A.EXIT_DEFERRED):
            sys.exit(f"=== autoeval.py exited with code {code} (an error; {now} run(s) left); stopping, see the log "
                     "above. Re-running retries failed runs.")
        u = RA.plan_usage()
        if not u:
            print(f"=== can't read plan usage; trying again in {usage_wait // 60} min", flush=True)
            time.sleep(usage_wait)
            usage_wait = min(usage_wait * 2, USAGE_WAIT_MAX_S)
            continue
        usage_wait = USAGE_WAIT_S
        weekly = A.AUTO.get("stop_at_weekly_pct", a.stop_at)
        if u["seven_day"] >= weekly:
            sys.exit(f"=== weekly usage at {u['seven_day']:g}% (stop line {weekly:g}%); not continuing")
        if now == before and start_pct is not None and start_pct < 20:
            sys.exit("=== no progress in a fresh session; stopping so it doesn't loop (see the log above)")
        if not u["resets_at"]:  # no session window open (e.g. just after a limit reset): try again soon
            time.sleep(600)
            continue
        reset = datetime.datetime.fromisoformat(u["resets_at"])
        wait = max(60, (reset - datetime.datetime.now(datetime.timezone.utc)).total_seconds() + 120)
        print(f"=== {now} run(s) left; session at {u['five_hour']:g}%; waiting until "
              f"{reset.astimezone():%H:%M} ({wait / 60:.0f} min)", flush=True)
        time.sleep(wait)


if __name__ == "__main__":
    main()
