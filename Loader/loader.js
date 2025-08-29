// loader.js

// Create loader element
const loader = document.createElement("div");
loader.id = "globalLoader";
loader.innerHTML = `
  <div class="loader-overlay">
    <div class="loader-spinner"></div>
  </div>
`;
document.body.appendChild(loader);

// Function to show loader
export function showLoader() {
  const loader = document.getElementById("globalLoader");
  if (loader) loader.style.display = "flex";
}

// Function to hide loader
export function hideLoader() {
  const loader = document.getElementById("globalLoader");
  if (loader) loader.style.display = "none";
}

// Auto-hide loader once page fully loads
window.addEventListener("load", () => {
  hideLoader();
});
