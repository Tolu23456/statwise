// loader.js

// Create loader element
const loader = document.createElement("div");
loader.id = "globalLoader";
loader.innerHTML = `
  <div class="loader-overlay"></div>
  <div class="loader-spinner"></div>
`;
document.body.appendChild(loader);

let loaderTimeout;

// Function to show loader
export function showLoader() {
    // Only show the loader if the operation takes more than 300ms
    loaderTimeout = setTimeout(() => {
        const loader = document.getElementById("globalLoader");
        if (loader) loader.style.display = "flex";
    }, 300); // 300ms delay
}

// Function to hide loader
export function hideLoader() {
    // Clear the timeout so the loader doesn't appear if the action was fast
    clearTimeout(loaderTimeout);
    const loader = document.getElementById("globalLoader");
    if (loader) {
        loader.style.display = "none";
    }
}
