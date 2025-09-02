// utils.js
import { db } from '/env.js';
import { collection, addDoc, query, orderBy, serverTimestamp, limit, getDocs } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

/**
 * Fetches the user's public IP address.
 * @returns {Promise<string>} The public IP or "Unknown".
 */
export async function getPublicIP() {
    try {
        const res = await fetch("https://api.ipify.org?format=json");
        if (!res.ok) return "Unknown";
        const data = await res.json();
        return data.ip || "Unknown";
    } catch {
        return "Unknown";
    }
}

/**
 * Formats a Firestore Timestamp into a "DD/MM/YYYY HH:mm" string.
 * @param {object} timestamp - The Firestore Timestamp object.
 * @returns {string} The formatted date string or an empty string.
 */
export function formatTimestamp(timestamp) {
    if (!timestamp?.toDate) return "";
    const date = timestamp.toDate();
    const day = String(date.getDate()).padStart(2, "0");
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const year = date.getFullYear();
    const hours = String(date.getHours()).padStart(2, "0");
    const mins = String(date.getMinutes()).padStart(2, "0");
    return `${day}/${month}/${year} ${hours}:${mins}`;
}

/**
 * Adds a unique action to the user's history log, preventing immediate duplicates.
 * @param {string} userId - The user's ID.
 * @param {string} action - The action description to log.
 */
export async function addHistoryUnique(userId, action) {
    if (!userId) return;
    try {
        const historyRef = collection(db, "users", userId, "history");
        const q = query(historyRef, orderBy("createdAt", "desc"), limit(1));
        const snap = await getDocs(q);

        // Prevent logging the exact same action back-to-back
        if (!snap.empty && snap.docs[0].data().action === action) return;

        const ip = await getPublicIP();
        await addDoc(historyRef, { action, ip, createdAt: serverTimestamp() });
    } catch (err) {
        console.error("Failed to add history:", err);
    }
}