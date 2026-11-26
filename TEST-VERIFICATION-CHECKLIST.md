# Test Verification Checklist

Run before claiming any task complete. Answer from output, not from memory.

## Did it run at all

- [ ] The test command exited zero, and I read the exit code rather than the summary line.
      Backend: `python -m pytest -q` in `ai-engine/`. Frontend: `npm test -- --watch=false`
      in `frontend/`.
- [ ] The collection count matches or exceeds the recorded baseline: **303 backend tests**
      (302 passed and 1 skipped on 2026-11-26) and **81 frontend tests** in 16 files.
- [ ] No test was skipped that was not skipped before.
- [ ] The backend ran in an environment installed from the lock
      (`pip install --require-hashes -r requirements-dev.txt`), not only the long-lived
      local venv. That venv once hid an undeclared dependency that kept CI red for every push.

## Did it test the change

- [ ] A test exists that fails without this change. **I ran it against the old code and
      watched it fail**, rather than assuming it would.
- [ ] The assertion checks the value, not merely that something was returned.
- [ ] No test calls a real AI provider; it uses the fake clients.

## Did the gates run

- [ ] `pip-audit` and `npm audit --omit=dev` ran and passed. (There is no lint or format gate
      yet; see `docs/decisions.md`.)
- [ ] CI is green **on the remote**, on the pushed commit. Local green is not remote green.

## What I did not check

- <Anything untested, named plainly. An empty section here is almost always false.>
