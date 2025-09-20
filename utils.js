
// utils.js
import { supabase } from './env.js';

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
 * Formats a timestamp string or Date into a "DD/MM/YYYY HH:mm" string.
 * @param {string|Date} timestamp - The timestamp to format.
 * @returns {string} The formatted date string or an empty string.
 */
export function formatTimestamp(timestamp) {
    try {
        if (!timestamp) return "";
        
        let date;
        if (typeof timestamp === 'string') {
            date = new Date(timestamp);
        } else if (timestamp instanceof Date) {
            date = timestamp;
        } else if (timestamp?.toDate) {
            // Firestore Timestamp object
            date = timestamp.toDate();
        } else {
            return "";
        }
        
        if (isNaN(date.getTime())) return "";
        
        const day = String(date.getDate()).padStart(2, "0");
        const month = String(date.getMonth() + 1).padStart(2, "0");
        const year = date.getFullYear();
        const hours = String(date.getHours()).padStart(2, "0");
        const mins = String(date.getMinutes()).padStart(2, "0");
        return `${day}/${month}/${year} ${hours}:${mins}`;
    } catch (error) {
        console.warn('Error formatting timestamp:', error);
        return "";
    }
}

/**
 * Adds a unique action to the user's history log via Supabase.
 * @param {string} userId - The user's ID.
 * @param {string} action - The action description to log.
 */
export async function addHistoryUnique(userId, action) {
    if (!userId || !action) return;
    
    try {
        // Get the user's last history entry to prevent duplicates
        const { data: lastEntry, error: fetchError } = await supabase
            .from('user_history')
            .select('action')
            .eq('user_id', userId)
            .order('created_at', { ascending: false })
            .limit(1)
            .single();
        
        // Prevent logging the exact same action back-to-back
        if (lastEntry && lastEntry.action === action) return;
        
        const ip = await getPublicIP();
        const { error } = await supabase
            .from('user_history')
            .insert({
                user_id: userId,
                action: action,
                ip_address: ip,
                created_at: new Date().toISOString()
            });
            
        if (error) {
            console.warn('Failed to add history:', error);
        }
    } catch (err) {
        console.error("Failed to add history:", err);
    }
}
