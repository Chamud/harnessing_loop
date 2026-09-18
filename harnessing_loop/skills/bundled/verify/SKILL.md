---
name: verify
description: Check the output against the request before declaring the job done.
when_to_use: before calling finish, and after any change that touched more than one file
---

# Verify

1. Re-read the original request. List every deliverable it names, one per line.
2. For each deliverable, find the file or output that satisfies it. Open it; do not rely on memory.
3. Run whatever check exists: tests, a build, a schema validation, a script that recomputes the numbers.
   If no check exists and one can be written in under twenty lines, write it and run it.
4. Record the result in `notes_append`: what was checked, what passed, what did not.
5. If anything failed, fix it and return to step 3. Do not call `finish` with known failures.
6. Only then call `finish` with a summary that states what was verified and how.
