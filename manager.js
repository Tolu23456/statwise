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
 * Initializes all client-side security measures.
 */
export function initializeAppSecurity() {
    disableContextMenu();
    disableDevToolsShortcuts();
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
    const defaultPage = "home";

    // Determine page load priority: URL hash > localStorage > default.
    const initialHash = window.location.hash.substring(1);
    const pageToLoad = initialHash || localStorage.getItem("lastPage") || defaultPage;

    // Load the page without adding a new entry to the browser's history.
    loadPageCallback(pageToLoad, userId, false);
    return pageToLoad;
}