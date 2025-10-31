Push performed: 2025-10-31

Actions taken:
- Staged all local changes and created commit `chore: commit local workspace changes`.
- Pushed branch `main` to remote `origin` at https://github.com/Tolu23456/statwise.git.

Verification:
- Remote `origin` has `refs/heads/main` pointing to the pushed commit.

If you want me to add a new remote or push to a different GitHub repository (or use SSH), provide the repository URL or SSH key/authorization details and I'll update the remote and push.

Fix applied: 2025-10-31

- Implemented `initializeSearchBar()` and `handleSearchCommand()` in `main.js` and wired it into `initializeHomePage()`.
- Features: Enter to search, commands starting with `/` (e.g. `/c75` filters by confidence >=75, `/odds` sorts by odds), clear button wired.

How to test:
1. Open the app and go to the Home page.
2. Type a team name (e.g., "Arsenal") and press Enter — results should filter to matching cards.
3. Type `/c75` and press Enter — results should show predictions with confidence >= 75%.
4. Type `/odds` and press Enter — results should sort by odds descending.

Additional improvements (2025-10-31):

- Clear button UX & accessibility:
	- Clear button now has `aria-label`, a tooltip (title), and supports pressing `Esc` to clear the input and blur.
	- Clicking the clear button focuses the input and resets results.

- New commands added:
	- `/league:<name>` — filter predictions by league name (partial match), e.g. `/league:premier`.
	- `/type:<win|draw|over|under|btts>` — filter by prediction type, e.g. `/type:win`.
	- `/top:<n>` — show the top `n` predictions sorted by confidence, e.g. `/top:5`.
	- `/clear` — reset search and filters.

Notes:
- Unknown commands now log a hint in the console and reset to showing all predictions.
- If you'd like combined filters (e.g., `/league:premier /c75`), I can extend the parser to support multiple tokens.
