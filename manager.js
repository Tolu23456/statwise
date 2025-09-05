// manager.js

/**
 * Disables the right-click context menu to deter basic inspection.
 */
function disableContextMenu() {
    document.addEventListener('contextmenu', event => event.preventDefault());
}

/**
 * Disables common developer tools keyboard shortcuts.
 */
function disableDevToolsShortcuts() {
    document.addEventListener('keydown', function (e) {
        // Block F12
        if (e.keyCode === 123) {
            e.preventDefault();
        }
        // Block Ctrl+Shift+I (or Cmd+Option+I on Mac)
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.keyCode === 73) {
            e.preventDefault();
        }
        // Block Ctrl+Shift+J (or Cmd+Option+J on Mac)
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.keyCode === 74) {
            e.preventDefault();
        }
        // Block Ctrl+U (or Cmd+Option+U on Mac)
        if ((e.ctrlKey || e.metaKey) && e.keyCode === 85) {
            e.preventDefault();
        }
    });
}

/**
 * Periodically clears the console and displays a warning message.
 * Note: This is a deterrent and can be bypassed by a determined user.
 */
function runConsoleDeterrent() {
    setInterval(() => {
        console.clear();
        console.log("%cInspecting this area is not allowed.", "color:red; font-size:16px;");
    }, 3000); // Runs every 3 seconds
}

/**
 * Initializes all client-side security measures.
 */
export function initializeAppSecurity() {
    disableContextMenu();
    disableDevToolsShortcuts();
    runConsoleDeterrent();
    console.log("Client-side security manager initialized.");
}

/**
 * Determines the initial page to load and executes the loading function.
 * It checks if the main content area is empty and then decides the page
 * based on URL hash, localStorage, or a default value.
 * @param {string} userId - The current user's ID.
 * @param {function} loadPageCallback - The function to call to load the page, expecting (page, userId, addToHistory).
 */
export function manageInitialPageLoad(userId, loadPageCallback) {
    const mainContent = document.querySelector("main");
    const defaultPage = "home";

    // Check if the main content area is empty. On initial load of index.html, this will be true.
    if (mainContent && mainContent.innerHTML.trim() === '') {
        // Determine page load priority: URL hash > localStorage > default.
        const initialHash = window.location.hash.substring(1);
        const pageToLoad = initialHash || localStorage.getItem("lastPage") || defaultPage;

        console.log(`Manager: Main content is empty. Loading initial page: ${pageToLoad}`);
        // Load the page without adding a new entry to the browser's history.
        loadPageCallback(pageToLoad, userId, false);
    }
}