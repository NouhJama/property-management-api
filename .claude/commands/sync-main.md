Automate the post-merge cleanup routine for a feature branch that has already been merged and deleted on the remote. The branch to clean up is **$ARGUMENTS**.

Run the steps below **in order**. After each step, check the exit code. If any step fails, **stop immediately and report** the failed step, its output, and the error — do not continue to the next step.

## Steps

### 1. Switch to main
```
git checkout main
```
This must succeed before anything else runs. If it fails (e.g. uncommitted changes blocking the switch), report the error and stop.

### 2. Pull the merged changes
```
git pull origin main
```
Brings main up to date with the remote. If this fails (e.g. network error, merge conflict), report the error and stop.

### 3. Delete the local branch (safe delete only)
```
git branch -d $ARGUMENTS
```
Use lowercase `-d` — git will refuse to delete the branch if it has not been fully merged into the current HEAD. **Never use `-D`** (force delete). If git refuses because the branch is not merged, stop and report — do not override.

If the branch does not exist locally (already deleted or never checked out), note that to the user and continue to the next step.

### 4. Prune stale remote-tracking references
```
git remote prune origin
```
Removes references to remote branches that no longer exist (e.g. the branch deleted on GitHub after the PR was merged). Report any output but do not treat this step as a failure if no refs were pruned.

### 5. Confirm main is clean and up to date
```
git status
git log -1 --oneline
```
Run both commands and display their output. The user should see a clean working tree and the latest merged commit at the top of the log.

## After all steps complete

Report a short summary:
- Which branch was cleaned up
- The current HEAD commit (from `git log -1 --oneline`)
- Confirm that the working tree is clean
- Remind the user to create a new feature branch before starting the next task: `git checkout -b feature/your-next-task`
